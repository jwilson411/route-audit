"""Offline outage and capability simulation over a parsed RouteGraph.

A graph that parses and lints clean can still strand a request: the
primary is down, the only node with `tools` is rate limited, the request
is larger than every context window left in the chain. Those are also
decidable from the document alone, given two more inert documents - a
request fixture and an outage scenario.

The walk is pure. Same graph, same request, same scenario, same result,
every time. Nothing here talks to a provider, and the request fixture
carries no prompt text: keys that look like prompt payload are rejected
as schema errors.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from route_audit.checks import build_adjacency, entry_node
from route_audit.diagnostics import CODE_INVALID_SCHEMA, Diagnostic, sort_diagnostics
from route_audit.model import Node, Route, RouteGraph
from route_audit.parser import (
    load_yaml_path,
    optional_int,
    optional_str,
    optional_str_list,
    require_str,
    type_name,
)
from route_audit.resolve import Resolver

#: Why a node was passed over. Evaluated in this order, first match wins.
REASON_UNAVAILABLE = "unavailable"
REASON_RATE_LIMITED = "rate_limited"
REASON_PROVIDER_NOT_ALLOWED = "provider_not_allowed"
REASON_CAPABILITY_MISMATCH = "capability_mismatch"
REASON_CONTEXT_OVERFLOW = "context_overflow"

#: Every rejection reason, in evaluation order.
ALL_REASONS = (
    REASON_UNAVAILABLE,
    REASON_RATE_LIMITED,
    REASON_PROVIDER_NOT_ALLOWED,
    REASON_CAPABILITY_MISMATCH,
    REASON_CONTEXT_OVERFLOW,
)

#: Why a walk ended with no node selected.
FAILURE_EXHAUSTION = "exhaustion"
FAILURE_UNKNOWN_ROUTE = "unknown_route"
FAILURE_EMPTY_ROUTE = "empty_route"
FAILURE_ROUTE_MISMATCH = "route_mismatch"
FAILURE_UNKNOWN_SCENARIO_NODE = "unknown_scenario_node"
FAILURE_CYCLE = "cycle"

#: Every terminal failure code, sorted.
ALL_FAILURES = (
    FAILURE_CYCLE,
    FAILURE_EMPTY_ROUTE,
    FAILURE_EXHAUSTION,
    FAILURE_ROUTE_MISMATCH,
    FAILURE_UNKNOWN_ROUTE,
    FAILURE_UNKNOWN_SCENARIO_NODE,
)

#: Keys a request fixture must not carry. A request describes shape, not payload.
PROMPT_KEYS = (
    "content",
    "input",
    "messages",
    "prompt",
    "prompts",
    "query",
    "system",
    "text",
    "user",
)


@dataclass(frozen=True, slots=True)
class Request:
    """What a request needs, with no trace of what it says."""

    route: str
    required_capabilities: tuple[str, ...] = ()
    context_tokens: int = 0
    allowed_providers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class NodeFault:
    """What a scenario does to one node. All defaults mean "healthy"."""

    unavailable: bool = False
    rate_limited: bool = False
    over_context: bool = False
    missing_capabilities: tuple[str, ...] = ()


HEALTHY = NodeFault()


@dataclass(frozen=True, slots=True)
class Scenario:
    """Named faults, applied to nodes by id or by alias.

    `faults` is a sorted tuple of `(name, NodeFault)` pairs rather than a
    mapping so the scenario stays frozen and hashable.
    """

    route: str | None = None
    faults: tuple[tuple[str, NodeFault], ...] = ()

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self.faults)


@dataclass(frozen=True, slots=True)
class Attempt:
    """One node the walk passed over, and why."""

    node_id: str
    reason: str
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"node_id": self.node_id, "reason": self.reason, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class SimulationResult:
    """The exact path a request would take. No prompt text, by construction."""

    route_id: str
    attempted: tuple[Attempt, ...] = ()
    selected: str | None = None
    failure: str | None = None

    @property
    def ok(self) -> bool:
        return self.selected is not None

    @property
    def attempted_nodes(self) -> tuple[str, ...]:
        return tuple(item.node_id for item in self.attempted)

    def format(self) -> str:
        """A stable headline plus one indented line per rejected node."""
        if self.selected is not None:
            head = f"selected {self.route_id}.{self.selected}"
        else:
            head = f"failed {self.route_id}: {self.failure}"
        lines = [head]
        lines.extend(
            f"  rejected {self.route_id}.{item.node_id}: {item.reason}"
            + (f", {item.detail}" if item.detail else "")
            for item in self.attempted
        )
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "route": self.route_id,
            "ok": self.ok,
            "selected": self.selected,
            "failure": self.failure,
            "attempted": [item.to_dict() for item in self.attempted],
        }


def simulate(
    graph: RouteGraph, request: Request, scenario: Scenario | None = None
) -> SimulationResult:
    """Walk one request through the graph under one scenario.

    Fallback edges are followed in declaration order. A node is attempted
    at most once; arriving back at a node already on the current path is
    a `cycle` failure, which `lint` would have reported first.
    """
    scenario = scenario or Scenario()
    resolver = Resolver(graph)

    target = resolver.resolve(request.route)
    if target is None:
        return SimulationResult(request.route, failure=FAILURE_UNKNOWN_ROUTE)
    route = graph.route(target.route_id)
    if route is None or not route.nodes:
        return SimulationResult(target.route_id, failure=FAILURE_EMPTY_ROUTE)

    if scenario.route is not None:
        scenario_target = resolver.resolve(scenario.route)
        if scenario_target is None or scenario_target.route_id != route.id:
            return SimulationResult(route.id, failure=FAILURE_ROUTE_MISMATCH)

    faults, unknown = _resolve_faults(route, resolver, scenario)
    if unknown:
        return SimulationResult(route.id, failure=FAILURE_UNKNOWN_SCENARIO_NODE)

    # A name that resolved to a node starts there; a route starts at its entry.
    start = target.node_id or entry_node(route, resolver)[0]
    if start is None:
        return SimulationResult(route.id, failure=FAILURE_EMPTY_ROUTE)

    adjacency = build_adjacency(route, resolver)[0]
    attempted, selected, failure = _walk(route, adjacency, start, request, faults)
    return SimulationResult(route.id, tuple(attempted), selected, failure)


@dataclass(slots=True)
class _Cursor:
    """A rejected node and how many of its fallbacks have been tried."""

    node_id: str
    index: int = 0


def _walk(
    route: Route,
    adjacency: dict[str, list[str]],
    start: str,
    request: Request,
    faults: dict[str, NodeFault],
) -> tuple[list[Attempt], str | None, str | None]:
    attempted: list[Attempt] = []
    cursors: list[_Cursor] = []
    on_path: set[str] = set()
    seen: set[str] = set()

    current: str | None = start
    while current is not None:
        node = route.node(current)
        if node is None:  # build_adjacency only yields local nodes
            return attempted, None, FAILURE_EXHAUSTION
        seen.add(current)
        rejection = _reject(node, request, faults.get(current, HEALTHY))
        if rejection is None:
            return attempted, current, None
        attempted.append(Attempt(current, *rejection))
        cursors.append(_Cursor(current))
        on_path.add(current)
        current, failure = _next_node(cursors, adjacency, seen, on_path)
        if failure is not None:
            return attempted, None, failure
    return attempted, None, FAILURE_EXHAUSTION


def _next_node(
    cursors: list[_Cursor],
    adjacency: dict[str, list[str]],
    seen: set[str],
    on_path: set[str],
) -> tuple[str | None, str | None]:
    """The next unused fallback, backtracking through already-tried nodes."""
    while cursors:
        cursor = cursors[-1]
        targets = adjacency.get(cursor.node_id, ())
        while cursor.index < len(targets):
            target = targets[cursor.index]
            cursor.index += 1
            if target in on_path:
                return None, FAILURE_CYCLE
            if target not in seen:
                return target, None
        on_path.discard(cursors.pop().node_id)
    return None, FAILURE_EXHAUSTION


def _reject(
    node: Node, request: Request, fault: NodeFault
) -> tuple[str, str] | None:
    """The first reason this node cannot serve the request, or None."""
    if fault.unavailable:
        return REASON_UNAVAILABLE, "scenario marks the node unavailable"
    if fault.rate_limited:
        return REASON_RATE_LIMITED, "scenario marks the node rate limited"
    if request.allowed_providers and node.provider not in request.allowed_providers:
        return (
            REASON_PROVIDER_NOT_ALLOWED,
            f"provider {node.provider!r} is not in allowed_providers",
        )
    available = set(node.capabilities) - set(fault.missing_capabilities)
    missing = sorted(set(request.required_capabilities) - available)
    if missing:
        return REASON_CAPABILITY_MISMATCH, f"missing capabilities: {', '.join(missing)}"
    if fault.over_context:
        return REASON_CONTEXT_OVERFLOW, "scenario marks the node over context"
    if (
        request.context_tokens > 0
        and node.context_limit is not None
        and request.context_tokens > node.context_limit
    ):
        return (
            REASON_CONTEXT_OVERFLOW,
            f"context_tokens {request.context_tokens} exceeds "
            f"context_limit {node.context_limit}",
        )
    return None


def _resolve_faults(
    route: Route, resolver: Resolver, scenario: Scenario
) -> tuple[dict[str, NodeFault], tuple[str, ...]]:
    """Map scenario node names onto node ids in this route."""
    faults: dict[str, NodeFault] = {}
    unknown: list[str] = []
    for name, fault in scenario.faults:
        target = resolver.resolve_in_route(route, name)
        if target is None or target.node_id is None or target.route_id != route.id:
            unknown.append(name)
            continue
        faults[target.node_id] = fault
    return faults, tuple(sorted(unknown))


# --- loading ---------------------------------------------------------------

RequestResult = tuple[Request | None, list[Diagnostic]]
ScenarioResult = tuple[Scenario | None, list[Diagnostic]]


def load_request_document(data: Any, *, path: str = "<request>") -> RequestResult:
    """Validate a request fixture. Prompt-like keys are a schema error."""
    diagnostics: list[Diagnostic] = []
    if not isinstance(data, dict):
        return None, [
            Diagnostic(
                CODE_INVALID_SCHEMA,
                path,
                f"request must be a mapping, got {type_name(data)}",
            )
        ]

    for key in PROMPT_KEYS:
        if key in data:
            diagnostics.append(
                Diagnostic(
                    CODE_INVALID_SCHEMA,
                    f"{path}.{key}",
                    f"request must not carry prompt text: remove key {key!r}",
                )
            )

    route = require_str(data.get("route"), path, "route", diagnostics)
    capabilities = optional_str_list(
        data.get("required_capabilities"), path, "required_capabilities", diagnostics
    )
    providers = optional_str_list(
        data.get("allowed_providers"), path, "allowed_providers", diagnostics
    )
    tokens = optional_int(data.get("context_tokens"), path, "context_tokens", diagnostics)
    if tokens is not None and tokens < 0:
        diagnostics.append(
            Diagnostic(
                CODE_INVALID_SCHEMA, path, "'context_tokens' must not be negative"
            )
        )

    if diagnostics or route is None:
        return None, sort_diagnostics(diagnostics)
    return (
        Request(
            route=route,
            required_capabilities=capabilities,
            context_tokens=tokens or 0,
            allowed_providers=providers,
        ),
        [],
    )


def load_request_path(path: str | Path) -> RequestResult:
    """Read and validate a request fixture from disk."""
    data, diagnostics = load_yaml_path(path)
    if diagnostics:
        return None, diagnostics
    return load_request_document(data, path=str(path))


def load_scenario_document(data: Any, *, path: str = "<scenario>") -> ScenarioResult:
    """Validate an outage scenario."""
    diagnostics: list[Diagnostic] = []
    if not isinstance(data, dict):
        return None, [
            Diagnostic(
                CODE_INVALID_SCHEMA,
                path,
                f"scenario must be a mapping, got {type_name(data)}",
            )
        ]

    route = optional_str(data.get("route"), path, "route", diagnostics)
    faults = _parse_faults(data.get("nodes"), path, diagnostics)
    if diagnostics:
        return None, sort_diagnostics(diagnostics)
    return Scenario(route=route, faults=faults), []


def load_scenario_path(path: str | Path) -> ScenarioResult:
    """Read and validate a scenario from disk."""
    data, diagnostics = load_yaml_path(path)
    if diagnostics:
        return None, diagnostics
    return load_scenario_document(data, path=str(path))


def _parse_faults(
    raw: Any, path: str, diagnostics: list[Diagnostic]
) -> tuple[tuple[str, NodeFault], ...]:
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        diagnostics.append(
            Diagnostic(
                CODE_INVALID_SCHEMA,
                f"{path}.nodes",
                f"'nodes' must be a mapping of node name to faults, "
                f"got {type_name(raw)}",
            )
        )
        return ()

    faults: dict[str, NodeFault] = {}
    for name, value in raw.items():
        where = f"{path}.nodes.{name}"
        if not isinstance(name, str) or not name:
            diagnostics.append(
                Diagnostic(
                    CODE_INVALID_SCHEMA,
                    f"{path}.nodes",
                    "node name must be a non-empty string",
                )
            )
            continue
        if value is None:
            faults[name] = HEALTHY
            continue
        if not isinstance(value, dict):
            diagnostics.append(
                Diagnostic(
                    CODE_INVALID_SCHEMA,
                    where,
                    f"node faults must be a mapping, got {type_name(value)}",
                )
            )
            continue
        faults[name] = NodeFault(
            unavailable=_flag(value.get("unavailable"), where, "unavailable", diagnostics),
            rate_limited=_flag(
                value.get("rate_limited"), where, "rate_limited", diagnostics
            ),
            over_context=_flag(
                value.get("over_context"), where, "over_context", diagnostics
            ),
            missing_capabilities=optional_str_list(
                value.get("missing_capabilities"),
                where,
                "missing_capabilities",
                diagnostics,
            ),
        )
    return tuple(sorted(faults.items()))


def _flag(value: Any, where: str, key: str, diagnostics: list[Diagnostic]) -> bool:
    if value is None:
        return False
    if not isinstance(value, bool):
        diagnostics.append(
            Diagnostic(
                CODE_INVALID_SCHEMA,
                where,
                f"'{key}' must be a boolean, got {type_name(value)}",
            )
        )
        return False
    return value


# --- rendering -------------------------------------------------------------


def format_simulation_text(result: SimulationResult) -> str:
    """The headline and attempted path, one node per line."""
    return f"{result.format()}\n"


def format_simulation_json(
    result: SimulationResult, *, graph: str, request: str, scenario: str | None = None
) -> str:
    """A single JSON object, keys in a fixed order, no trailing newline."""
    payload = {
        "graph": graph,
        "request": request,
        "scenario": scenario,
        "exit_code": simulation_exit_code(result),
        **result.to_dict(),
    }
    return json.dumps(payload, indent=2, sort_keys=False)


def simulation_exit_code(result: SimulationResult) -> int:
    """0 when a node was selected, 1 when the walk ended in failure."""
    return 0 if result.ok else 1
