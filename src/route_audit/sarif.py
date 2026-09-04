"""SARIF 2.1.0 for structural diagnostics and policy violations.

SARIF is the format code scanners already speak, so this is the shape
route-audit takes when it runs inside somebody else's pipeline. Nothing
new is decided here: rule ids are the existing codes from
`diagnostics.ALL_CODES` and `policy.ALL_VIOLATIONS`, and message text is
the same text the plain renderers print.

route-audit has no line numbers to give - a diagnostic locates itself by
YAML path, not by offset - so a result carries the input file as its
physical location and the path as a logical one: `routes.chat`,
`classes.tools`, `scenarios.two-down`. A SARIF document holds
configuration metadata only. No file bodies, no environment, no
credentials, no prompt text.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

import route_audit
from route_audit.audit import AuditReport
from route_audit.diagnostics import Diagnostic

#: The schema every consumer validates a 2.1.0 document against.
SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
SARIF_VERSION = "2.1.0"

TOOL_NAME = "route-audit"
TOOL_URI = "https://github.com/jwilson411/route-audit"

#: A finding is a finding. route-audit has no warning tier.
LEVEL_ERROR = "error"


def format_lint_sarif(diagnostics: Iterable[Diagnostic], *, path: str) -> str:
    """Structural diagnostics as SARIF. Rule ids are the lint codes."""
    results = [
        _result(item.code, item.message, uri=path, logical=(item.path,))
        for item in diagnostics
    ]
    return _document(results)


def format_audit_sarif(report: AuditReport, *, graph: str, policy: str) -> str:
    """Policy violations as SARIF. Rule ids are the violation codes.

    The physical location is the graph, because the violation is about a
    path the graph takes. The class and the scenario that reached it ride
    along as further logical locations, naming their own paths in the
    policy document.
    """
    results = [
        _result(
            item.code,
            item.message,
            uri=graph,
            logical=(
                _node_path(item.route_id, item.node),
                f"classes.{item.class_name}",
                f"scenarios.{item.scenario_name}",
            ),
        )
        for item in report.violations
    ]
    # `policy` is recorded as an artifact so a reader knows which document
    # the class and scenario paths belong to. Only the name travels.
    return _document(results, artifacts=(graph, policy))


def _node_path(route_id: str, node: str | None) -> str:
    return f"routes.{route_id}" if node is None else f"routes.{route_id}.nodes.{node}"


def _result(
    rule_id: str, message: str, *, uri: str, logical: tuple[str, ...]
) -> dict[str, Any]:
    return {
        "ruleId": rule_id,
        "level": LEVEL_ERROR,
        "message": {"text": message},
        "locations": [
            {
                "physicalLocation": {"artifactLocation": {"uri": uri}},
                "logicalLocations": [
                    {
                        "name": name.rsplit(".", 1)[-1],
                        "fullyQualifiedName": name,
                        "kind": "member",
                    }
                    for name in logical
                ],
            }
        ],
    }


def _sort_key(result: dict[str, Any]) -> tuple[str, str, str, tuple[str, ...]]:
    """`(ruleId, fullyQualifiedName, message)`, then the remaining paths."""
    names = _logical_names(result)
    return (result["ruleId"], names[0], result["message"]["text"], names[1:])


def _logical_names(result: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        item["fullyQualifiedName"]
        for item in result["locations"][0]["logicalLocations"]
    )


def _document(
    results: list[dict[str, Any]], *, artifacts: tuple[str, ...] = ()
) -> str:
    """One run, rules for every code present, results in a stable order."""
    ordered = sorted(results, key=_sort_key)
    rules = [
        {"id": rule_id, "name": rule_id}
        for rule_id in sorted({result["ruleId"] for result in ordered})
    ]
    run: dict[str, Any] = {
        "tool": {
            "driver": {
                "name": TOOL_NAME,
                # Read late: `route_audit` is still importing this module.
                "version": route_audit.__version__,
                "informationUri": TOOL_URI,
                "rules": rules,
            }
        },
        "results": ordered,
    }
    if artifacts:
        run["artifacts"] = [
            {"location": {"uri": uri}} for uri in sorted(set(artifacts))
        ]
    return json.dumps(
        {"$schema": SARIF_SCHEMA, "version": SARIF_VERSION, "runs": [run]},
        indent=2,
        sort_keys=False,
    )
