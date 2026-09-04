"""Seeded batch mode: a matrix of requests by scenarios, plus coverage.

The seed does not inject faults. Nothing here is random - a simulation
is fully determined by the graph, the request, and the scenario. The
seed fixes the run order (cases are sorted by name) and is recorded in
the report, so the same batch file against the same graph produces a
byte-identical report every time.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from route_audit.diagnostics import CODE_INVALID_SCHEMA, Diagnostic, sort_diagnostics
from route_audit.model import RouteGraph
from route_audit.parser import load_yaml_path, optional_int, type_name
from route_audit.simulate import (
    Request,
    Scenario,
    SimulationResult,
    load_request_document,
    load_scenario_document,
    simulate,
)

#: The scenario name used when a case declares no faults.
NO_SCENARIO = "none"


@dataclass(frozen=True, slots=True)
class BatchCase:
    """One request against one scenario, under a stable name."""

    name: str
    request_name: str
    scenario_name: str
    request: Request
    scenario: Scenario


@dataclass(frozen=True, slots=True)
class BatchPlan:
    """A validated batch document. `cases` is already in run order."""

    seed: int
    cases: tuple[BatchCase, ...] = ()


@dataclass(frozen=True, slots=True)
class CaseResult:
    name: str
    request_name: str
    scenario_name: str
    result: SimulationResult

    @property
    def ok(self) -> bool:
        return self.result.ok

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "request": self.request_name,
            "scenario": self.scenario_name,
            **self.result.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class RouteCoverage:
    """What a batch touched in one route."""

    route_id: str
    cases: int = 0
    selections: int = 0
    attempted: tuple[str, ...] = ()
    selected: tuple[str, ...] = ()

    @property
    def exercised(self) -> bool:
        return self.cases > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "route": self.route_id,
            "cases": self.cases,
            "selections": self.selections,
            "attempted": list(self.attempted),
            "selected": list(self.selected),
        }


@dataclass(frozen=True, slots=True)
class BatchReport:
    seed: int
    cases: tuple[CaseResult, ...] = ()
    coverage: tuple[RouteCoverage, ...] = ()

    @property
    def ok(self) -> bool:
        return all(case.ok for case in self.cases)

    @property
    def routes_without_selection(self) -> tuple[str, ...]:
        return tuple(item.route_id for item in self.coverage if not item.selections)

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "ok": self.ok,
            "cases": [case.to_dict() for case in self.cases],
            "coverage": {
                "routes": [item.to_dict() for item in self.coverage],
                "routes_without_selection": list(self.routes_without_selection),
            },
        }


def run_batch(graph: RouteGraph, plan: BatchPlan) -> BatchReport:
    """Run every case in order and summarise route coverage."""
    cases = tuple(
        CaseResult(
            case.name,
            case.request_name,
            case.scenario_name,
            simulate(graph, case.request, case.scenario),
        )
        for case in plan.cases
    )
    return BatchReport(seed=plan.seed, cases=cases, coverage=_coverage(graph, cases))


def _coverage(
    graph: RouteGraph, cases: tuple[CaseResult, ...]
) -> tuple[RouteCoverage, ...]:
    counts: dict[str, int] = {}
    selections: dict[str, int] = {}
    attempted: dict[str, set[str]] = {}
    selected: dict[str, set[str]] = {}

    for case in cases:
        route_id = case.result.route_id
        counts[route_id] = counts.get(route_id, 0) + 1
        attempted.setdefault(route_id, set()).update(case.result.attempted_nodes)
        chosen = case.result.selected
        if chosen is not None:
            selections[route_id] = selections.get(route_id, 0) + 1
            selected.setdefault(route_id, set()).add(chosen)

    return tuple(
        RouteCoverage(
            route_id=route.id,
            cases=counts.get(route.id, 0),
            selections=selections.get(route.id, 0),
            attempted=tuple(sorted(attempted.get(route.id, ()))),
            selected=tuple(sorted(selected.get(route.id, ()))),
        )
        for route in sorted(graph.routes, key=lambda route: route.id)
    )


# --- loading ---------------------------------------------------------------

BatchResult = tuple[BatchPlan | None, list[Diagnostic]]


def load_batch_document(data: Any, *, path: str = "<batch>") -> BatchResult:
    """Validate a batch document: `seed`, `requests`, `scenarios`, `matrix`."""
    diagnostics: list[Diagnostic] = []
    if not isinstance(data, dict):
        return None, [
            Diagnostic(
                CODE_INVALID_SCHEMA,
                path,
                f"batch must be a mapping, got {type_name(data)}",
            )
        ]

    raw_seed = data.get("seed")
    seed = None
    if raw_seed is None:
        diagnostics.append(
            Diagnostic(CODE_INVALID_SCHEMA, path, "batch is missing required key 'seed'")
        )
    else:
        seed = optional_int(raw_seed, path, "seed", diagnostics)

    raw_requests = data.get("requests")
    if raw_requests is None:
        diagnostics.append(
            Diagnostic(
                CODE_INVALID_SCHEMA, path, "batch is missing required key 'requests'"
            )
        )
    requests = _load_named(
        raw_requests, "requests", path, load_request_document, diagnostics
    )
    scenarios = _load_named(
        data.get("scenarios"), "scenarios", path, load_scenario_document, diagnostics
    )
    scenarios.setdefault(NO_SCENARIO, Scenario())

    cases = _load_matrix(data.get("matrix"), path, requests, scenarios, diagnostics)
    if diagnostics or seed is None:
        return None, sort_diagnostics(diagnostics)
    return BatchPlan(seed=seed, cases=cases), []


def load_batch_path(path: str | Path) -> BatchResult:
    """Read and validate a batch document from disk."""
    data, diagnostics = load_yaml_path(path)
    if diagnostics:
        return None, diagnostics
    return load_batch_document(data, path=str(path))


def _load_named(
    raw: Any,
    key: str,
    path: str,
    loader: Any,
    diagnostics: list[Diagnostic],
) -> dict[str, Any]:
    """Load a `name: document` block through one of the simulate loaders."""
    loaded: dict[str, Any] = {}
    if raw is None:
        return loaded
    if not isinstance(raw, dict):
        diagnostics.append(
            Diagnostic(
                CODE_INVALID_SCHEMA,
                f"{path}.{key}",
                f"'{key}' must be a mapping of name to document, got {type_name(raw)}",
            )
        )
        return loaded
    for name, document in raw.items():
        where = f"{path}.{key}.{name}"
        if not isinstance(name, str) or not name:
            diagnostics.append(
                Diagnostic(
                    CODE_INVALID_SCHEMA,
                    f"{path}.{key}",
                    f"{key[:-1]} name must be a non-empty string",
                )
            )
            continue
        value, problems = loader(document, path=where)
        diagnostics.extend(problems)
        if value is not None:
            loaded[name] = value
    return loaded


def _load_matrix(
    raw: Any,
    path: str,
    requests: dict[str, Request],
    scenarios: dict[str, Scenario],
    diagnostics: list[Diagnostic],
) -> tuple[BatchCase, ...]:
    if raw is None:
        return _cross_product(requests, scenarios)
    if not isinstance(raw, list) or not raw:
        diagnostics.append(
            Diagnostic(
                CODE_INVALID_SCHEMA,
                f"{path}.matrix",
                "'matrix' must be a non-empty list of cases",
            )
        )
        return ()

    cases: dict[str, BatchCase] = {}
    for position, entry in enumerate(raw):
        where = f"{path}.matrix[{position}]"
        if not isinstance(entry, dict):
            diagnostics.append(
                Diagnostic(
                    CODE_INVALID_SCHEMA,
                    where,
                    f"case must be a mapping, got {type_name(entry)}",
                )
            )
            continue
        request_name = _named(
            entry.get("request"), requests, "request", where, diagnostics
        )
        scenario_name = (
            NO_SCENARIO
            if entry.get("scenario") is None
            else _named(entry.get("scenario"), scenarios, "scenario", where, diagnostics)
        )
        if request_name is None or scenario_name is None:
            continue
        name = entry.get("name", f"{request_name}+{scenario_name}")
        if not isinstance(name, str) or not name:
            diagnostics.append(
                Diagnostic(
                    CODE_INVALID_SCHEMA, where, "'name' must be a non-empty string"
                )
            )
            continue
        if name in cases:
            diagnostics.append(
                Diagnostic(CODE_INVALID_SCHEMA, where, f"duplicate case name {name!r}")
            )
            continue
        cases[name] = BatchCase(
            name=name,
            request_name=request_name,
            scenario_name=scenario_name,
            request=requests[request_name],
            scenario=scenarios[scenario_name],
        )
    return tuple(case for _, case in sorted(cases.items()))


def _cross_product(
    requests: dict[str, Request], scenarios: dict[str, Scenario]
) -> tuple[BatchCase, ...]:
    """No `matrix:` means every request against every scenario."""
    return tuple(
        BatchCase(
            name=f"{request_name}+{scenario_name}",
            request_name=request_name,
            scenario_name=scenario_name,
            request=requests[request_name],
            scenario=scenarios[scenario_name],
        )
        for request_name in sorted(requests)
        for scenario_name in sorted(scenarios)
    )


def _named(
    value: Any,
    known: dict[str, Any],
    key: str,
    where: str,
    diagnostics: list[Diagnostic],
) -> str | None:
    if not isinstance(value, str) or not value:
        diagnostics.append(
            Diagnostic(
                CODE_INVALID_SCHEMA,
                where,
                f"'{key}' must name a {key} declared in the batch, "
                f"got {type_name(value)}",
            )
        )
        return None
    if value not in known:
        listed = ", ".join(sorted(known)) or "none"
        diagnostics.append(
            Diagnostic(
                CODE_INVALID_SCHEMA,
                where,
                f"{key} {value!r} is not declared in the batch; known: {listed}",
            )
        )
        return None
    return value


# --- rendering -------------------------------------------------------------


def format_batch_text(report: BatchReport) -> str:
    """Seed, one block per case, then one coverage line per route."""
    lines = [f"seed {report.seed}"]
    for case in report.cases:
        lines.append(f"case {case.name}: {case.result.format()}")
    for item in report.coverage:
        lines.append(
            f"coverage {item.route_id}: cases={item.cases} "
            f"selections={item.selections} "
            f"attempted=[{', '.join(item.attempted)}] "
            f"selected=[{', '.join(item.selected)}]"
        )
    missing = ", ".join(report.routes_without_selection) or "none"
    lines.append(f"routes without selection: {missing}")
    return "\n".join(lines) + "\n"


def format_batch_json(report: BatchReport, *, graph: str, batch: str) -> str:
    """A single JSON object, keys in a fixed order, no trailing newline."""
    payload = {
        "graph": graph,
        "batch": batch,
        "exit_code": batch_exit_code(report),
        **report.to_dict(),
    }
    return json.dumps(payload, indent=2, sort_keys=False)


def batch_exit_code(report: BatchReport) -> int:
    """0 when every case selected a node, 1 when any case failed."""
    return 0 if report.ok else 1
