"""Coverage matrix over an audited policy: what the cases actually reached.

`audit` answers "was any crossing forbidden". This module answers the
question that comes right after it: which parts of the graph did those
cases ever touch. A policy that passes because half its fallback edges
were never walked is not a policy that passed.

Nothing here re-simulates. It reads the `AuditReport` the audit already
produced - one row per declared class against one scenario - and folds
the walks back onto the graph to find the fallback edges and nodes no
case reached.

Like every other report route-audit prints, this one carries
configuration metadata only: route ids, node ids, scenario names,
diagnostic codes, and the file names it was given. It validates
architecture and does not send or proxy prompts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from route_audit.audit import AuditCase, AuditReport, audit_exit_code
from route_audit.checks import build_adjacency
from route_audit.model import RouteGraph
from route_audit.resolve import Resolver
from route_audit.simulate import FAILURE_UNKNOWN_ROUTE

#: Matrix columns, in the one order both renderers use.
COLUMNS = (
    "class",
    "scenario",
    "route",
    "ok",
    "selected",
    "failure",
    "hops",
    "violations",
    "attempted",
)

#: What an absent value looks like in a Markdown cell.
EMPTY_CELL = "-"

#: What an empty Markdown section looks like, so golden output stays stable.
EMPTY_SECTION = "none"


@dataclass(frozen=True, slots=True)
class MatrixRow:
    """One request class against one scenario, as one row."""

    class_name: str
    scenario_name: str
    route_id: str
    ok: bool
    selected: str | None = None
    failure: str | None = None
    hops: int = 0
    violations: tuple[str, ...] = ()
    attempted: tuple[str, ...] = ()

    @property
    def sort_key(self) -> tuple[str, str]:
        return (self.class_name, self.scenario_name)

    @property
    def walked(self) -> tuple[str, ...]:
        """The node ids this case touched, in walk order."""
        nodes = list(self.attempted)
        if self.selected is not None:
            nodes.append(self.selected)
        return tuple(nodes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "class": self.class_name,
            "scenario": self.scenario_name,
            "route": self.route_id,
            "ok": self.ok,
            "selected": self.selected,
            "failure": self.failure,
            "hops": self.hops,
            "violations": list(self.violations),
            "attempted": list(self.attempted),
        }

    def cells(self) -> tuple[str, ...]:
        """One Markdown cell per column, in `COLUMNS` order."""
        return (
            self.class_name,
            self.scenario_name,
            self.route_id or EMPTY_CELL,
            "yes" if self.ok else "no",
            self.selected or EMPTY_CELL,
            self.failure or EMPTY_CELL,
            str(self.hops),
            ", ".join(self.violations) or EMPTY_CELL,
            " -> ".join(self.attempted) or EMPTY_CELL,
        )


@dataclass(frozen=True, slots=True)
class UntestedEdge:
    """A fallback edge no case walked."""

    route_id: str
    source: str
    target: str

    @property
    def sort_key(self) -> tuple[str, str, str]:
        return (self.route_id, self.source, self.target)

    def format(self) -> str:
        return f"{self.route_id}: {self.source} -> {self.target}"

    def to_dict(self) -> dict[str, str]:
        return {"route": self.route_id, "from": self.source, "to": self.target}


@dataclass(frozen=True, slots=True)
class UntestedNode:
    """A node no case attempted and no case selected."""

    route_id: str
    node_id: str

    @property
    def sort_key(self) -> tuple[str, str]:
        return (self.route_id, self.node_id)

    def format(self) -> str:
        return f"{self.route_id}.{self.node_id}"

    def to_dict(self) -> dict[str, str]:
        return {"route": self.route_id, "node": self.node_id}


@dataclass(frozen=True, slots=True)
class CoverageReport:
    """The matrix, plus everything the matrix never reached.

    `graph` and `policy` are the file names as they were given, kept as
    metadata. No file contents travel in a report.
    """

    graph: str
    policy: str
    ok: bool = True
    rows: tuple[MatrixRow, ...] = ()
    untested_edges: tuple[UntestedEdge, ...] = ()
    untested_nodes: tuple[UntestedNode, ...] = ()
    routes_without_selection: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "graph": self.graph,
            "policy": self.policy,
            "ok": self.ok,
            "matrix": [row.to_dict() for row in self.rows],
            "untested_edges": [item.to_dict() for item in self.untested_edges],
            "untested_nodes": [item.to_dict() for item in self.untested_nodes],
            "routes_without_selection": list(self.routes_without_selection),
        }


def build_report(
    graph: RouteGraph, audit: AuditReport, *, graph_path: str, policy_path: str
) -> CoverageReport:
    """Fold an audit back onto the graph. Pure: nothing is re-simulated."""
    rows = tuple(sorted((_row(case) for case in audit.cases), key=lambda r: r.sort_key))
    return CoverageReport(
        graph=graph_path,
        policy=policy_path,
        ok=audit.ok,
        rows=rows,
        untested_edges=_untested_edges(graph, rows),
        untested_nodes=_untested_nodes(graph, rows),
        routes_without_selection=_routes_without_selection(graph, rows),
    )


def _row(case: AuditCase) -> MatrixRow:
    result = case.result
    # A route the request never resolved is not a route id, so it is not
    # reported as one.
    route_id = "" if result.failure == FAILURE_UNKNOWN_ROUTE else result.route_id
    return MatrixRow(
        class_name=case.class_name,
        scenario_name=case.scenario_name,
        route_id=route_id,
        ok=case.ok,
        selected=result.selected,
        failure=result.failure,
        hops=case.hops,
        violations=case.codes,
        attempted=result.attempted_nodes,
    )


def _untested_edges(
    graph: RouteGraph, rows: tuple[MatrixRow, ...]
) -> tuple[UntestedEdge, ...]:
    """Declared fallback edges minus the ones a walk stepped over."""
    resolver = Resolver(graph)
    walked: set[tuple[str, str, str]] = set()
    for row in rows:
        nodes = row.walked
        for source, target in zip(nodes, nodes[1:]):
            walked.add((row.route_id, source, target))

    edges: list[UntestedEdge] = []
    for route in graph.routes:
        adjacency = build_adjacency(route, resolver)[0]
        for source, targets in adjacency.items():
            edges.extend(
                UntestedEdge(route.id, source, target)
                for target in targets
                if (route.id, source, target) not in walked
            )
    return tuple(sorted(edges, key=lambda item: item.sort_key))


def _untested_nodes(
    graph: RouteGraph, rows: tuple[MatrixRow, ...]
) -> tuple[UntestedNode, ...]:
    touched = {(row.route_id, node) for row in rows for node in row.walked}
    return tuple(
        sorted(
            (
                UntestedNode(route.id, node.id)
                for route in graph.routes
                for node in route.nodes
                if (route.id, node.id) not in touched
            ),
            key=lambda item: item.sort_key,
        )
    )


def _routes_without_selection(
    graph: RouteGraph, rows: tuple[MatrixRow, ...]
) -> tuple[str, ...]:
    """Routes nothing in the matrix ever came out of. A route with no case counts."""
    serving = {row.route_id for row in rows if row.selected is not None}
    return tuple(sorted(route.id for route in graph.routes if route.id not in serving))


# --- rendering -------------------------------------------------------------


def format_report_markdown(report: CoverageReport) -> str:
    """Heading, matrix table, then one section per kind of coverage hole."""
    lines = [
        "# Coverage matrix",
        "",
        f"graph: {report.graph}",
        f"policy: {report.policy}",
        "",
        "| " + " | ".join(COLUMNS) + " |",
        "| " + " | ".join("---" for _ in COLUMNS) + " |",
    ]
    lines.extend("| " + " | ".join(row.cells()) + " |" for row in report.rows)
    lines.extend(
        _section("Untested edges", [item.format() for item in report.untested_edges])
    )
    lines.extend(
        _section("Untested nodes", [item.format() for item in report.untested_nodes])
    )
    lines.extend(
        _section("Routes without selection", list(report.routes_without_selection))
    )
    return "\n".join(lines) + "\n"


def _section(title: str, entries: list[str]) -> list[str]:
    """An empty section still prints, so golden output stays stable."""
    body = [f"- {entry}" for entry in entries] or [EMPTY_SECTION]
    return ["", f"## {title}", "", *body]


def format_report_json(report: CoverageReport) -> str:
    """A single JSON object, keys in a fixed order, no trailing newline."""
    payload = report.to_dict()
    return json.dumps(
        {
            "graph": payload["graph"],
            "policy": payload["policy"],
            "ok": payload["ok"],
            "exit_code": 0 if report.ok else 1,
            "matrix": payload["matrix"],
            "untested_edges": payload["untested_edges"],
            "untested_nodes": payload["untested_nodes"],
            "routes_without_selection": payload["routes_without_selection"],
        },
        indent=2,
        sort_keys=False,
    )


def report_exit_code(audit: AuditReport) -> int:
    """The audit's code: a coverage hole is not a governance failure."""
    return audit_exit_code(audit)
