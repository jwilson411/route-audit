"""The structural checks. Pure functions over a parsed RouteGraph.

Each check owns one diagnostic code. None of them look at the network,
the filesystem, or any provider catalogue - a routing graph is either
internally coherent or it is not, and that is decidable from the
document alone.
"""

from __future__ import annotations

from collections import defaultdict

from route_audit.diagnostics import (
    CODE_CYCLE,
    CODE_DANGLING_ALIAS,
    CODE_DUPLICATE_PRIORITY,
    CODE_NO_TERMINAL_MODEL,
    CODE_UNREACHABLE_NODE,
    Diagnostic,
    sort_diagnostics,
)
from route_audit.model import Node, Route, RouteGraph
from route_audit.resolve import Resolver

Adjacency = dict[str, list[str]]


def check_graph(graph: RouteGraph) -> list[Diagnostic]:
    """Run every check. Returns diagnostics in canonical order."""
    resolver = Resolver(graph)
    diagnostics: list[Diagnostic] = []

    diagnostics.extend(check_dangling_aliases(graph, resolver))
    diagnostics.extend(check_duplicate_priority(graph))

    for route in graph.routes:
        adjacency, edge_diagnostics = build_adjacency(route, resolver)
        diagnostics.extend(edge_diagnostics)
        entry, entry_diagnostics = entry_node(route, resolver)
        diagnostics.extend(entry_diagnostics)
        diagnostics.extend(check_cycles(route, adjacency))
        diagnostics.extend(check_unreachable_nodes(route, adjacency, entry))
        diagnostics.extend(check_terminal_model(route, adjacency))

    return sort_diagnostics(diagnostics)


def check_dangling_aliases(graph: RouteGraph, resolver: Resolver) -> list[Diagnostic]:
    """An alias whose target names no route and no node."""
    diagnostics: list[Diagnostic] = []
    for alias in graph.aliases:
        if resolver.resolve(alias.target) is None:
            diagnostics.append(
                Diagnostic(
                    CODE_DANGLING_ALIAS,
                    alias.path,
                    f"alias {alias.name!r} points at {alias.target!r}, "
                    f"which is not a known route or node",
                )
            )
    return diagnostics


def check_duplicate_priority(graph: RouteGraph) -> list[Diagnostic]:
    """Two routes claiming the same priority have no defined order."""
    by_priority: dict[int, list[str]] = defaultdict(list)
    for route in graph.routes:
        if route.priority is not None:
            by_priority[route.priority].append(route.id)

    diagnostics: list[Diagnostic] = []
    for priority, route_ids in by_priority.items():
        if len(route_ids) > 1:
            listed = ", ".join(sorted(route_ids))
            diagnostics.append(
                Diagnostic(
                    CODE_DUPLICATE_PRIORITY,
                    "routes",
                    f"priority {priority} is shared by routes: {listed}",
                )
            )
    return diagnostics


def check_cycles(route: Route, adjacency: Adjacency) -> list[Diagnostic]:
    """Fallback edges that loop back on themselves never terminate."""
    diagnostics: list[Diagnostic] = []
    for component in _strongly_connected_components(route.node_ids, adjacency):
        if len(component) > 1 or component[0] in adjacency.get(component[0], ()):
            listed = ", ".join(component)
            diagnostics.append(
                Diagnostic(
                    CODE_CYCLE,
                    route.path,
                    f"fallback edges form a cycle among nodes: {listed}",
                )
            )
    return diagnostics


def check_unreachable_nodes(
    route: Route, adjacency: Adjacency, entry: str | None
) -> list[Diagnostic]:
    """A node no fallback path can arrive at is dead configuration."""
    if entry is None:
        return []
    reachable = {entry}
    stack = [entry]
    while stack:
        current = stack.pop()
        for target in adjacency.get(current, ()):
            if target not in reachable:
                reachable.add(target)
                stack.append(target)

    return [
        Diagnostic(
            CODE_UNREACHABLE_NODE,
            f"{route.path}.nodes.{node.id}",
            f"node {node.id!r} is not the entry node {entry!r} and is not "
            f"reachable from it via fallbacks",
        )
        for node in route.nodes
        if node.id not in reachable
    ]


def check_terminal_model(route: Route, adjacency: Adjacency) -> list[Diagnostic]:
    """A route needs at least one node that can end the chain."""
    if not route.nodes:
        return []
    if any(is_terminal(node, adjacency) for node in route.nodes):
        return []
    return [
        Diagnostic(
            CODE_NO_TERMINAL_MODEL,
            route.path,
            "route has no terminal model: mark a node 'terminal: true' or "
            "leave one node without outgoing fallbacks",
        )
    ]


def is_terminal(node: Node, adjacency: Adjacency) -> bool:
    """`terminal:` wins when set. Otherwise a model with no exit terminates."""
    if node.terminal is not None:
        return node.terminal
    return bool(node.model) and not adjacency.get(node.id)


def entry_node(
    route: Route, resolver: Resolver
) -> tuple[str | None, list[Diagnostic]]:
    """The entry node id, defaulting to the first declared node."""
    default = route.node_ids[0] if route.nodes else None
    if route.entry is None:
        return default, []
    target = resolver.resolve_in_route(route, route.entry)
    if target is None or target.node_id is None or target.route_id != route.id:
        return default, [
            Diagnostic(
                CODE_DANGLING_ALIAS,
                f"{route.path}.entry",
                f"entry {route.entry!r} does not name a node in route {route.id!r}",
            )
        ]
    return target.node_id, []


def build_adjacency(
    route: Route, resolver: Resolver
) -> tuple[Adjacency, list[Diagnostic]]:
    """Resolve fallback endpoints to local node ids. Bad edges are dropped.

    Targets keep declaration order, which is the order the simulator
    walks fallbacks in.
    """
    adjacency: Adjacency = {}
    diagnostics: list[Diagnostic] = []
    for edge in route.fallbacks:
        where = f"{route.path}.fallbacks[{edge.index}]"
        source = _endpoint(route, resolver, edge.source, where, "from", diagnostics)
        target = _endpoint(route, resolver, edge.target, where, "to", diagnostics)
        if source is None or target is None:
            continue
        neighbours = adjacency.setdefault(source, [])
        if target not in neighbours:
            neighbours.append(target)
    return adjacency, diagnostics


def _endpoint(
    route: Route,
    resolver: Resolver,
    name: str,
    where: str,
    key: str,
    diagnostics: list[Diagnostic],
) -> str | None:
    target = resolver.resolve_in_route(route, name)
    if target is not None and target.node_id is not None and target.route_id == route.id:
        return target.node_id
    if target is not None and target.node_id is not None:
        detail = f"resolves to a node in route {target.route_id!r}"
    elif target is not None:
        detail = f"resolves to route {target.route_id!r}, not a node"
    else:
        detail = "is not a known node or alias"
    diagnostics.append(
        Diagnostic(
            CODE_DANGLING_ALIAS,
            where,
            f"fallback '{key}' {name!r} {detail}; it must name a node in "
            f"route {route.id!r}",
        )
    )
    return None


def _strongly_connected_components(
    nodes: tuple[str, ...], adjacency: Adjacency
) -> list[list[str]]:
    """Kosaraju, iterative. Components and their members come back sorted."""
    order: list[str] = []
    visited: set[str] = set()
    for start in nodes:
        if start in visited:
            continue
        visited.add(start)
        stack = [(start, iter(adjacency.get(start, ())))]
        while stack:
            node, neighbours = stack[-1]
            for neighbour in neighbours:
                if neighbour not in visited:
                    visited.add(neighbour)
                    stack.append((neighbour, iter(adjacency.get(neighbour, ()))))
                    break
            else:
                order.append(node)
                stack.pop()

    reverse: Adjacency = {}
    for source, targets in adjacency.items():
        for target in targets:
            reverse.setdefault(target, []).append(source)

    components: list[list[str]] = []
    assigned: set[str] = set()
    for start in reversed(order):
        if start in assigned:
            continue
        assigned.add(start)
        group = [start]
        stack_ids = [start]
        while stack_ids:
            node = stack_ids.pop()
            for source in reverse.get(node, ()):
                if source not in assigned:
                    assigned.add(source)
                    group.append(source)
                    stack_ids.append(source)
        components.append(sorted(group))
    return sorted(components)
