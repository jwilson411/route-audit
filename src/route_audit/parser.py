"""YAML document -> RouteGraph, or a list of schema diagnostics.

The parser is strict about shape and quiet about everything else. It
does not know which providers exist, which model names are real, or
whether a context limit is plausible. Unknown keys are ignored so the
schema stays generic and forward-compatible.

Schema errors are collected rather than raised: a bad document reports
every problem it has in one pass. If any are found, no graph is built,
because the checks downstream would otherwise report noise derived from
half-parsed input.
"""

from __future__ import annotations

from typing import Any

import yaml

from route_audit.diagnostics import (
    CODE_INVALID_SCHEMA,
    CODE_PARSE_ERROR,
    Diagnostic,
    sort_diagnostics,
)
from route_audit.model import Alias, FallbackEdge, Node, Route, RouteGraph

ParseResult = tuple[RouteGraph | None, list[Diagnostic]]


def parse_text(text: str, *, path: str = "<document>") -> ParseResult:
    """Parse YAML source. `path` only labels document-level diagnostics."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return None, [Diagnostic(CODE_PARSE_ERROR, path, _yaml_message(exc))]
    return parse_document(data, path=path)


def parse_document(data: Any, *, path: str = "<document>") -> ParseResult:
    """Validate an already-loaded YAML document and build the graph."""
    diagnostics: list[Diagnostic] = []

    if data is None:
        diagnostics.append(Diagnostic(CODE_INVALID_SCHEMA, path, "document is empty"))
        return None, diagnostics
    if not isinstance(data, dict):
        diagnostics.append(
            Diagnostic(
                CODE_INVALID_SCHEMA,
                path,
                f"document must be a mapping, got {_type_name(data)}",
            )
        )
        return None, diagnostics

    raw_routes = data.get("routes")
    if raw_routes is None:
        diagnostics.append(
            Diagnostic(CODE_INVALID_SCHEMA, path, "missing required key 'routes'")
        )
        return None, diagnostics
    if not isinstance(raw_routes, list):
        diagnostics.append(
            Diagnostic(
                CODE_INVALID_SCHEMA,
                "routes",
                f"'routes' must be a list, got {_type_name(raw_routes)}",
            )
        )
        return None, diagnostics

    routes: list[Route] = []
    aliases: list[Alias] = []
    seen_route_ids: set[str] = set()

    for position, raw_route in enumerate(raw_routes):
        route = _parse_route(raw_route, position, diagnostics, aliases)
        if route is None:
            continue
        if route.id in seen_route_ids:
            diagnostics.append(
                Diagnostic(
                    CODE_INVALID_SCHEMA,
                    route.path,
                    f"duplicate route id {route.id!r}",
                )
            )
            continue
        seen_route_ids.add(route.id)
        routes.append(route)

    aliases.extend(_parse_top_level_aliases(data.get("aliases"), diagnostics))
    _check_alias_names(aliases, diagnostics)

    if diagnostics:
        return None, sort_diagnostics(diagnostics)
    return RouteGraph(routes=tuple(routes), aliases=tuple(aliases)), []


def _parse_route(
    raw: Any,
    position: int,
    diagnostics: list[Diagnostic],
    aliases: list[Alias],
) -> Route | None:
    where = f"routes[{position}]"
    if not isinstance(raw, dict):
        diagnostics.append(
            Diagnostic(
                CODE_INVALID_SCHEMA,
                where,
                f"route must be a mapping, got {_type_name(raw)}",
            )
        )
        return None

    route_id = _require_id(raw.get("id"), where, "route", diagnostics)
    if route_id is None:
        return None
    where = f"routes.{route_id}"

    priority = _optional_int(raw.get("priority"), where, "priority", diagnostics)
    context_limit = _optional_int(
        raw.get("context_limit"), where, "context_limit", diagnostics
    )
    capabilities = _optional_str_list(
        raw.get("capabilities"), where, "capabilities", diagnostics
    )
    entry = _optional_str(raw.get("entry"), where, "entry", diagnostics)

    for name in _alias_names(raw, where, diagnostics):
        aliases.append(Alias(name=name, target=route_id, path=f"{where}.alias"))

    nodes = _parse_nodes(raw.get("nodes"), route_id, where, diagnostics, aliases)
    fallbacks = _parse_fallbacks(raw.get("fallbacks"), where, diagnostics)

    return Route(
        id=route_id,
        priority=priority,
        entry=entry,
        capabilities=capabilities,
        context_limit=context_limit,
        nodes=nodes,
        fallbacks=fallbacks,
    )


def _parse_nodes(
    raw_nodes: Any,
    route_id: str,
    route_path: str,
    diagnostics: list[Diagnostic],
    aliases: list[Alias],
) -> tuple[Node, ...]:
    if raw_nodes is None:
        diagnostics.append(
            Diagnostic(
                CODE_INVALID_SCHEMA, route_path, "route is missing required key 'nodes'"
            )
        )
        return ()
    if not isinstance(raw_nodes, list) or not raw_nodes:
        diagnostics.append(
            Diagnostic(
                CODE_INVALID_SCHEMA,
                f"{route_path}.nodes",
                "'nodes' must be a non-empty list",
            )
        )
        return ()

    nodes: list[Node] = []
    seen: set[str] = set()
    for position, raw in enumerate(raw_nodes):
        where = f"{route_path}.nodes[{position}]"
        if not isinstance(raw, dict):
            diagnostics.append(
                Diagnostic(
                    CODE_INVALID_SCHEMA,
                    where,
                    f"node must be a mapping, got {_type_name(raw)}",
                )
            )
            continue
        node_id = _require_id(raw.get("id"), where, "node", diagnostics)
        if node_id is None:
            continue
        where = f"{route_path}.nodes.{node_id}"
        if node_id in seen:
            diagnostics.append(
                Diagnostic(
                    CODE_INVALID_SCHEMA, where, f"duplicate node id {node_id!r} in route"
                )
            )
            continue
        seen.add(node_id)

        provider = _require_str(raw.get("provider"), where, "provider", diagnostics)
        model = _require_str(raw.get("model"), where, "model", diagnostics)
        terminal = _optional_bool(raw.get("terminal"), where, "terminal", diagnostics)
        capabilities = _optional_str_list(
            raw.get("capabilities"), where, "capabilities", diagnostics
        )
        context_limit = _optional_int(
            raw.get("context_limit"), where, "context_limit", diagnostics
        )
        for name in _alias_names(raw, where, diagnostics):
            aliases.append(
                Alias(
                    name=name,
                    target=f"{route_id}.{node_id}",
                    path=f"{where}.alias",
                )
            )
        nodes.append(
            Node(
                id=node_id,
                provider=provider or "",
                model=model or "",
                terminal=terminal,
                capabilities=capabilities,
                context_limit=context_limit,
            )
        )
    return tuple(nodes)


def _parse_fallbacks(
    raw_fallbacks: Any, route_path: str, diagnostics: list[Diagnostic]
) -> tuple[FallbackEdge, ...]:
    if raw_fallbacks is None:
        return ()
    if not isinstance(raw_fallbacks, list):
        diagnostics.append(
            Diagnostic(
                CODE_INVALID_SCHEMA,
                f"{route_path}.fallbacks",
                f"'fallbacks' must be a list, got {_type_name(raw_fallbacks)}",
            )
        )
        return ()

    edges: list[FallbackEdge] = []
    for position, raw in enumerate(raw_fallbacks):
        where = f"{route_path}.fallbacks[{position}]"
        if not isinstance(raw, dict):
            diagnostics.append(
                Diagnostic(
                    CODE_INVALID_SCHEMA,
                    where,
                    f"fallback must be a mapping, got {_type_name(raw)}",
                )
            )
            continue
        source = _require_str(raw.get("from"), where, "from", diagnostics)
        target = _require_str(raw.get("to"), where, "to", diagnostics)
        on = _optional_str(raw.get("on"), where, "on", diagnostics)
        if source is None or target is None:
            continue
        edges.append(FallbackEdge(source=source, target=target, on=on, index=position))
    return tuple(edges)


def _parse_top_level_aliases(raw: Any, diagnostics: list[Diagnostic]) -> list[Alias]:
    if raw is None:
        return []
    aliases: list[Alias] = []
    if isinstance(raw, dict):
        for name, target in raw.items():
            where = f"aliases.{name}"
            if not isinstance(name, str) or not name:
                diagnostics.append(
                    Diagnostic(
                        CODE_INVALID_SCHEMA, "aliases", "alias name must be a string"
                    )
                )
                continue
            if not isinstance(target, str) or not target:
                diagnostics.append(
                    Diagnostic(
                        CODE_INVALID_SCHEMA,
                        where,
                        f"alias target must be a non-empty string, got "
                        f"{_type_name(target)}",
                    )
                )
                continue
            aliases.append(Alias(name=name, target=target, path=where))
        return aliases
    if isinstance(raw, list):
        for position, item in enumerate(raw):
            where = f"aliases[{position}]"
            if not isinstance(item, dict):
                diagnostics.append(
                    Diagnostic(
                        CODE_INVALID_SCHEMA,
                        where,
                        f"alias must be a mapping, got {_type_name(item)}",
                    )
                )
                continue
            name = item.get("id", item.get("name"))
            if not isinstance(name, str) or not name:
                diagnostics.append(
                    Diagnostic(
                        CODE_INVALID_SCHEMA,
                        where,
                        "alias is missing a non-empty 'id'",
                    )
                )
                continue
            target = item.get("target")
            where = f"aliases.{name}"
            if not isinstance(target, str) or not target:
                diagnostics.append(
                    Diagnostic(
                        CODE_INVALID_SCHEMA,
                        where,
                        "alias is missing a non-empty 'target'",
                    )
                )
                continue
            aliases.append(Alias(name=name, target=target, path=where))
        return aliases
    diagnostics.append(
        Diagnostic(
            CODE_INVALID_SCHEMA,
            "aliases",
            f"'aliases' must be a mapping or a list, got {_type_name(raw)}",
        )
    )
    return aliases


def _check_alias_names(aliases: list[Alias], diagnostics: list[Diagnostic]) -> None:
    seen: dict[str, Alias] = {}
    for alias in aliases:
        first = seen.get(alias.name)
        if first is None:
            seen[alias.name] = alias
            continue
        diagnostics.append(
            Diagnostic(
                CODE_INVALID_SCHEMA,
                alias.path,
                f"duplicate alias {alias.name!r}, already declared at {first.path}",
            )
        )


def _alias_names(raw: dict, where: str, diagnostics: list[Diagnostic]) -> list[str]:
    """Read the `alias:` scalar and `aliases:` list off a route or node."""
    names: list[str] = []
    single = raw.get("alias")
    if single is not None:
        if isinstance(single, str) and single:
            names.append(single)
        else:
            diagnostics.append(
                Diagnostic(
                    CODE_INVALID_SCHEMA,
                    where,
                    f"'alias' must be a non-empty string, got {_type_name(single)}",
                )
            )
    many = raw.get("aliases")
    if many is not None:
        values = _optional_str_list(many, where, "aliases", diagnostics)
        names.extend(values)
    return names


def _require_id(
    value: Any, where: str, kind: str, diagnostics: list[Diagnostic]
) -> str | None:
    if isinstance(value, str) and value:
        return value
    diagnostics.append(
        Diagnostic(
            CODE_INVALID_SCHEMA,
            where,
            f"{kind} is missing a non-empty string 'id'",
        )
    )
    return None


def _require_str(
    value: Any, where: str, key: str, diagnostics: list[Diagnostic]
) -> str | None:
    if isinstance(value, str) and value:
        return value
    diagnostics.append(
        Diagnostic(
            CODE_INVALID_SCHEMA,
            where,
            f"'{key}' must be a non-empty string, got {_type_name(value)}",
        )
    )
    return None


def _optional_str(
    value: Any, where: str, key: str, diagnostics: list[Diagnostic]
) -> str | None:
    if value is None:
        return None
    return _require_str(value, where, key, diagnostics)


def _optional_int(
    value: Any, where: str, key: str, diagnostics: list[Diagnostic]
) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        diagnostics.append(
            Diagnostic(
                CODE_INVALID_SCHEMA,
                where,
                f"'{key}' must be an integer, got {_type_name(value)}",
            )
        )
        return None
    return value


def _optional_bool(
    value: Any, where: str, key: str, diagnostics: list[Diagnostic]
) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        diagnostics.append(
            Diagnostic(
                CODE_INVALID_SCHEMA,
                where,
                f"'{key}' must be a boolean, got {_type_name(value)}",
            )
        )
        return None
    return value


def _optional_str_list(
    value: Any, where: str, key: str, diagnostics: list[Diagnostic]
) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        diagnostics.append(
            Diagnostic(
                CODE_INVALID_SCHEMA,
                where,
                f"'{key}' must be a list of non-empty strings",
            )
        )
        return ()
    return tuple(value)


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    return {
        bool: "boolean",
        int: "integer",
        float: "number",
        str: "string",
        list: "list",
        dict: "mapping",
    }.get(type(value), type(value).__name__)


def _yaml_message(exc: yaml.YAMLError) -> str:
    """Flatten a PyYAML error to one stable line."""
    mark = getattr(exc, "problem_mark", None)
    problem = getattr(exc, "problem", None) or "invalid YAML"
    if mark is not None:
        return f"{problem} (line {mark.line + 1}, column {mark.column + 1})"
    return str(problem)
