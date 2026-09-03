"""Name resolution for routes, nodes, and aliases.

One name space, one precedence order, documented in the README:

1. a route id
2. a qualified node id, `<route_id>.<node_id>`
3. an alias name (declared on a route, on a node, or in the top-level
   `aliases:` block) - chased until it lands on a route or node
4. a bare node id, only if exactly one route defines it

Alias names are unique across the document; the parser rejects
duplicates as `invalid_schema`, so resolution never has to break a tie.
"""

from __future__ import annotations

from dataclasses import dataclass

from route_audit.model import Route, RouteGraph


@dataclass(frozen=True, slots=True)
class Target:
    """What a name resolved to. `node_id` is None for a route."""

    route_id: str
    node_id: str | None = None

    @property
    def is_route(self) -> bool:
        return self.node_id is None


class Resolver:
    def __init__(self, graph: RouteGraph) -> None:
        self._routes = {route.id: route for route in graph.routes}
        self._aliases = {alias.name: alias.target for alias in graph.aliases}
        self._qualified: dict[str, Target] = {}
        bare: dict[str, list[Target]] = {}
        for route in graph.routes:
            for node in route.nodes:
                target = Target(route.id, node.id)
                self._qualified[f"{route.id}.{node.id}"] = target
                bare.setdefault(node.id, []).append(target)
        self._bare = {
            node_id: targets[0] for node_id, targets in bare.items() if len(targets) == 1
        }

    def resolve(self, name: str) -> Target | None:
        """Resolve a name to a route or node, chasing alias chains."""
        seen: set[str] = set()
        current = name
        while current not in seen:
            seen.add(current)
            if current in self._routes:
                return Target(current)
            if current in self._qualified:
                return self._qualified[current]
            if current in self._aliases:
                current = self._aliases[current]
                continue
            return self._bare.get(current)
        return None

    def resolve_in_route(self, route: Route, name: str) -> Target | None:
        """Resolve a fallback endpoint. Node ids local to `route` win."""
        local = route.node(name)
        if local is not None:
            return Target(route.id, local.id)
        return self.resolve(name)
