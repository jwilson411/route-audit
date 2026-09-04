"""Policy audit: judge the simulated path, not the graph.

`simulate` stays the source of truth for what the graph would do. The
audit runs every declared request class against every scenario, takes
the path the walk produced, and asks one question the walk never asks:
was the organization willing to end up there?

That distinction matters in both directions. A node the walk rejects is
not a violation - it is the graph working. A node the walk happily
selects can still be a violation: a backup that is up, healthy, and
capable is exactly the fallback a policy exists to forbid.

A walk that selects nothing is recorded as a case outcome, not a
governance failure: an unservable request is a coverage hole in the
graph, and `simulate` already reports it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from route_audit.checks import entry_node
from route_audit.model import Node, RouteGraph
from route_audit.policy import (
    VIOLATION_CAPABILITY_DOWNGRADE,
    VIOLATION_CAPABILITY_REQUIRED,
    VIOLATION_CONTEXT_DOWNGRADE,
    VIOLATION_MAX_HOPS_EXCEEDED,
    VIOLATION_PROVIDER_DENIED,
    VIOLATION_PROVIDER_NOT_ALLOWED,
    VIOLATION_REGION_DENIED,
    VIOLATION_REGION_NOT_ALLOWED,
    Policy,
    PolicyCase,
)
from route_audit.resolve import Resolver
from route_audit.simulate import (
    FAILURE_EXHAUSTION,
    HEALTHY,
    NodeFault,
    SimulationResult,
    resolve_faults,
    simulate,
)


@dataclass(frozen=True, slots=True)
class Violation:
    """One boundary one case crossed, on one path."""

    code: str
    class_name: str
    scenario_name: str
    route_id: str
    path: tuple[str, ...]
    node: str | None
    message: str

    @property
    def sort_key(self) -> tuple[str, str, str, str, str]:
        """`(code, class, scenario, path, message)`, so output is stable."""
        return (
            self.code,
            self.class_name,
            self.scenario_name,
            " -> ".join(self.path),
            self.message,
        )

    @property
    def location(self) -> str:
        return self.route_id if self.node is None else f"{self.route_id}.{self.node}"

    def format(self) -> str:
        """One line of stable text output."""
        return (
            f"{self.code} classes.{self.class_name} scenarios.{self.scenario_name} "
            f"{self.location}: {self.message}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "class": self.class_name,
            "scenario": self.scenario_name,
            "path": list(self.path),
            "node": self.node,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class AuditCase:
    """One class against one scenario: what the walk did, and what it cost."""

    class_name: str
    scenario_name: str
    result: SimulationResult
    path: tuple[str, ...] = ()
    violations: tuple[Violation, ...] = ()

    @property
    def name(self) -> str:
        return f"{self.class_name}+{self.scenario_name}"

    @property
    def ok(self) -> bool:
        """True when the policy allows this path, selection or not."""
        return not self.violations

    @property
    def hops(self) -> int:
        """Rejected nodes before the outcome. Selecting the entry is 0 hops."""
        return len(self.result.attempted)

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(sorted({item.code for item in self.violations}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "class": self.class_name,
            "scenario": self.scenario_name,
            **self.result.to_dict(),
            "path": list(self.path),
            "hops": self.hops,
            "codes": list(self.codes),
        }

    def to_smallest_dict(self) -> dict[str, Any]:
        return {
            "class": self.class_name,
            "scenario": self.scenario_name,
            "route": self.result.route_id,
            "path": list(self.path),
            "selected": self.result.selected,
            "hops": self.hops,
            "codes": list(self.codes),
        }


@dataclass(frozen=True, slots=True)
class AuditReport:
    cases: tuple[AuditCase, ...] = ()

    @property
    def violations(self) -> tuple[Violation, ...]:
        """Every violation in every case, in one stable order."""
        return tuple(
            sorted(
                (item for case in self.cases for item in case.violations),
                key=lambda item: item.sort_key,
            )
        )

    @property
    def ok(self) -> bool:
        return not self.violations

    @property
    def smallest(self) -> AuditCase | None:
        """The violating case with the fewest nodes on its path."""
        violating = [case for case in self.cases if case.violations]
        if not violating:
            return None
        return min(
            violating,
            key=lambda case: (len(case.path), case.class_name, case.scenario_name),
        )

    def to_dict(self) -> dict[str, Any]:
        smallest = self.smallest
        return {
            "ok": self.ok,
            "smallest": None if smallest is None else smallest.to_smallest_dict(),
            "cases": [case.to_dict() for case in self.cases],
            "violations": [item.to_dict() for item in self.violations],
        }


def run_audit(graph: RouteGraph, policy: Policy) -> AuditReport:
    """Simulate every declared case, then judge each path against the policy."""
    resolver = Resolver(graph)
    return AuditReport(
        tuple(
            _judge(graph, resolver, policy, case, simulate(graph, case.request, case.scenario))
            for case in policy.cases
        )
    )


def _judge(
    graph: RouteGraph,
    resolver: Resolver,
    policy: Policy,
    case: PolicyCase,
    result: SimulationResult,
) -> AuditCase:
    route = graph.route(result.route_id)
    entry = None if route is None else entry_node(route, resolver)[0]
    path = _path(entry, result)

    violations: list[Violation] = []

    def add(code: str, node: str | None, message: str) -> None:
        violations.append(
            Violation(
                code=code,
                class_name=case.class_name,
                scenario_name=case.scenario_name,
                route_id=result.route_id,
                path=path,
                node=node,
                message=message,
            )
        )

    if route is not None and result.selected is not None:
        selected = route.node(result.selected)
        if selected is not None:
            fault = resolve_faults(route, resolver, case.scenario)[0].get(
                selected.id, HEALTHY
            )
            _judge_provider(policy, selected, add)
            _judge_capabilities(policy, selected, fault, add)
            _judge_region(policy, selected, add)
            entry_declaration = None if entry is None else route.node(entry)
            _judge_downgrade(policy, entry_declaration, selected, fault, add)

    _judge_hops(policy, result, add)

    return AuditCase(
        class_name=case.class_name,
        scenario_name=case.scenario_name,
        result=result,
        path=path,
        violations=tuple(sorted(violations, key=lambda item: item.sort_key)),
    )


def _judge_provider(policy: Policy, selected: Node, add: Any) -> None:
    """Denied wins: a provider in both lists is still denied."""
    if selected.provider in policy.denied_providers:
        add(
            VIOLATION_PROVIDER_DENIED,
            selected.id,
            f"provider {selected.provider!r} is denied",
        )
    elif policy.allowed_providers and selected.provider not in policy.allowed_providers:
        add(
            VIOLATION_PROVIDER_NOT_ALLOWED,
            selected.id,
            f"provider {selected.provider!r} is not in allowed_providers: "
            f"{', '.join(policy.allowed_providers)}",
        )


def _judge_capabilities(
    policy: Policy, selected: Node, fault: NodeFault, add: Any
) -> None:
    """Policy capabilities hold on the selected node, on top of the class."""
    missing = sorted(set(policy.required_capabilities) - _available(selected, fault))
    if missing:
        add(
            VIOLATION_CAPABILITY_REQUIRED,
            selected.id,
            f"selected node is missing required capabilities: {', '.join(missing)}",
        )


def _judge_region(policy: Policy, selected: Node, add: Any) -> None:
    """Denied wins, and a missing tag fails closed when regions are constrained."""
    if selected.region is not None and selected.region in policy.denied_regions:
        add(
            VIOLATION_REGION_DENIED,
            selected.id,
            f"region {selected.region!r} is denied",
        )
        return
    if not policy.allowed_regions:
        return
    listed = ", ".join(policy.allowed_regions)
    if selected.region is None:
        add(
            VIOLATION_REGION_NOT_ALLOWED,
            selected.id,
            f"selected node declares no region and allowed_regions is set: {listed}",
        )
    elif selected.region not in policy.allowed_regions:
        add(
            VIOLATION_REGION_NOT_ALLOWED,
            selected.id,
            f"region {selected.region!r} is not in allowed_regions: {listed}",
        )


def _judge_downgrade(
    policy: Policy, entry: Node | None, selected: Node, fault: NodeFault, add: Any
) -> None:
    """The fallback measured against the route's entry node, not the request."""
    if entry is None or not policy.no_downgrade.enabled:
        return
    if policy.no_downgrade.capabilities:
        lost = sorted(set(entry.capabilities) - _available(selected, fault))
        if lost:
            add(
                VIOLATION_CAPABILITY_DOWNGRADE,
                selected.id,
                f"capabilities lost against entry {entry.id!r}: {', '.join(lost)}",
            )
    if policy.no_downgrade.context_limit and entry.context_limit is not None:
        if selected.context_limit is None:
            add(
                VIOLATION_CONTEXT_DOWNGRADE,
                selected.id,
                f"selected node declares no context_limit but entry {entry.id!r} "
                f"declares {entry.context_limit}",
            )
        elif selected.context_limit < entry.context_limit:
            add(
                VIOLATION_CONTEXT_DOWNGRADE,
                selected.id,
                f"context_limit {selected.context_limit} is below entry "
                f"{entry.id!r} context_limit {entry.context_limit}",
            )


def _judge_hops(policy: Policy, result: SimulationResult, add: Any) -> None:
    """Hops are counted when the walk ran its course, not when it never started."""
    cap = policy.max_fallback_hops
    if cap is None:
        return
    if result.selected is None and result.failure != FAILURE_EXHAUSTION:
        return
    hops = len(result.attempted)
    if hops > cap:
        add(
            VIOLATION_MAX_HOPS_EXCEEDED,
            result.selected,
            f"fallback took {_hops(hops)}, more than max_fallback_hops {cap}",
        )


def _available(node: Node, fault: NodeFault) -> set[str]:
    """Declared capabilities, minus whatever the scenario took away."""
    return set(node.capabilities) - set(fault.missing_capabilities)


def _path(entry: str | None, result: SimulationResult) -> tuple[str, ...]:
    """`[entry, ...attempted, selected]`, without repeating the entry."""
    nodes = list(result.attempted_nodes)
    if result.selected is not None:
        nodes.append(result.selected)
    if entry is not None and (not nodes or nodes[0] != entry):
        nodes.insert(0, entry)
    return tuple(nodes)


def _hops(count: int) -> str:
    return f"{count} hop" + ("" if count == 1 else "s")


# --- rendering -------------------------------------------------------------


def format_audit_text(report: AuditReport) -> str:
    """`ok`, or the smallest violating path followed by every violation."""
    smallest = report.smallest
    if smallest is None:
        return "ok\n"
    rendered = " -> ".join(
        f"{smallest.result.route_id}.{node}" for node in smallest.path
    )
    lines = [f"smallest {smallest.name}: {rendered} ({_hops(smallest.hops)})"]
    lines.extend(item.format() for item in report.violations)
    return "\n".join(lines) + "\n"


def format_audit_json(report: AuditReport, *, graph: str, policy: str) -> str:
    """A single JSON object, keys in a fixed order, no trailing newline."""
    payload = report.to_dict()
    return json.dumps(
        {
            "graph": graph,
            "policy": policy,
            "ok": payload["ok"],
            "exit_code": audit_exit_code(report),
            "smallest": payload["smallest"],
            "cases": payload["cases"],
            "violations": payload["violations"],
        },
        indent=2,
        sort_keys=False,
    )


def audit_exit_code(report: AuditReport) -> int:
    """0 when no case violated the policy, 1 when one did."""
    return 0 if report.ok else 1
