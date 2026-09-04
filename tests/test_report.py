"""The coverage matrix, its holes, and its golden output."""

import json

import pytest

from route_audit import (
    build_report,
    format_report_json,
    format_report_markdown,
    load_policy_path,
    parse_path,
    report_exit_code,
    run_audit,
)
from route_audit.cli import main
from route_audit.report import COLUMNS

from conftest import ROOT

AUDIT_PAIR = ("examples/audit-routes.yml", "examples/policy.yml")
CLEAN_PAIR = ("examples/routes.yml", "examples/policy-clean.yml")


def build(pair: tuple[str, str]):
    graph_path, policy_path = pair
    graph, diagnostics = parse_path(ROOT / graph_path)
    assert diagnostics == []
    policy, diagnostics = load_policy_path(ROOT / policy_path)
    assert diagnostics == []
    audit = run_audit(graph, policy)
    report = build_report(
        graph, audit, graph_path=graph_path, policy_path=policy_path
    )
    return audit, report


def run(capsys, argv) -> tuple[int, str]:
    code = main(argv)
    return code, capsys.readouterr().out


# --- the matrix -------------------------------------------------------------


def test_a_row_per_declared_case_in_a_stable_order() -> None:
    audit, report = build(AUDIT_PAIR)
    assert len(report.rows) == len(audit.cases)
    assert [(row.class_name, row.scenario_name) for row in report.rows] == sorted(
        (case.class_name, case.scenario_name) for case in audit.cases
    )


def test_a_row_carries_the_documented_fields() -> None:
    _, report = build(AUDIT_PAIR)
    row = next(
        item
        for item in report.rows
        if (item.class_name, item.scenario_name) == ("tools", "two-down")
    )
    assert row.to_dict() == {
        "class": "tools",
        "scenario": "two-down",
        "route": "chat",
        "ok": False,
        "selected": "local",
        "failure": None,
        "hops": 2,
        "violations": [
            "capability_downgrade",
            "context_downgrade",
            "max_hops_exceeded",
            "provider_denied",
            "region_denied",
        ],
        "attempted": ["primary", "backup"],
    }
    assert set(row.to_dict()) == set(COLUMNS)


def test_an_unservable_case_records_its_failure_and_is_not_a_violation() -> None:
    _, report = build(AUDIT_PAIR)
    row = next(
        item
        for item in report.rows
        if (item.class_name, item.scenario_name) == ("structured", "two-down")
    )
    assert row.selected is None
    assert row.failure == "exhaustion"
    assert row.attempted == ("primary", "backup", "local")


def test_the_healthy_scenario_keeps_its_name() -> None:
    _, report = build(CLEAN_PAIR)
    assert [row.scenario_name for row in report.rows] == ["healthy"]


# --- coverage holes ---------------------------------------------------------


def test_an_edge_no_case_walked_is_untested() -> None:
    _, report = build(CLEAN_PAIR)
    assert [item.to_dict() for item in report.untested_edges] == [
        {"route": "chat", "from": "primary", "to": "backup"}
    ]


def test_a_node_no_case_reached_is_untested() -> None:
    _, report = build(CLEAN_PAIR)
    assert [item.to_dict() for item in report.untested_nodes] == [
        {"route": "chat", "node": "backup"},
        {"route": "embed", "node": "only"},
    ]


def test_a_route_nothing_came_out_of_is_reported() -> None:
    _, report = build(CLEAN_PAIR)
    assert report.routes_without_selection == ("embed",)


def test_a_matrix_that_walks_every_edge_leaves_no_holes() -> None:
    _, report = build(AUDIT_PAIR)
    assert report.untested_edges == ()
    assert report.untested_nodes == ()
    assert report.routes_without_selection == ()


# --- rendering --------------------------------------------------------------


@pytest.mark.parametrize(
    "pair,name",
    [(AUDIT_PAIR, "report-audit.md"), (CLEAN_PAIR, "report-clean.md")],
)
def test_markdown_matches_the_golden(golden, pair, name) -> None:
    _, report = build(pair)
    assert format_report_markdown(report) == golden(name)


@pytest.mark.parametrize(
    "pair,name",
    [(AUDIT_PAIR, "report-audit.json"), (CLEAN_PAIR, "report-clean.json")],
)
def test_json_matches_the_golden(golden, pair, name) -> None:
    _, report = build(pair)
    assert format_report_json(report) + "\n" == golden(name)


def test_markdown_keeps_every_section_even_when_empty(golden) -> None:
    body = golden("report-audit.md")
    for title in ("Untested edges", "Untested nodes", "Routes without selection"):
        assert f"## {title}\n\nnone\n" in body


def test_markdown_column_order_is_fixed() -> None:
    _, report = build(AUDIT_PAIR)
    header = format_report_markdown(report).splitlines()[5]
    assert header == "| " + " | ".join(COLUMNS) + " |"


def test_rendering_is_byte_stable() -> None:
    _, first = build(AUDIT_PAIR)
    _, second = build(AUDIT_PAIR)
    assert format_report_markdown(first) == format_report_markdown(second)
    assert format_report_json(first) == format_report_json(second)


def test_no_prompt_text_or_credentials_travel_in_a_report(assert_no_payload) -> None:
    for pair in (AUDIT_PAIR, CLEAN_PAIR):
        _, report = build(pair)
        assert_no_payload(format_report_markdown(report))
        assert_no_payload(format_report_json(report))


def test_the_report_names_its_inputs_but_never_copies_them() -> None:
    _, report = build(AUDIT_PAIR)
    rendered = format_report_json(report)
    payload = json.loads(rendered)
    assert payload["graph"] == "examples/audit-routes.yml"
    assert payload["policy"] == "examples/policy.yml"
    body = (ROOT / "examples" / "policy.yml").read_text(encoding="utf-8")
    for line in body.splitlines():
        if line.strip():
            assert line.strip() not in rendered


# --- exit codes and the CLI -------------------------------------------------


def test_exit_code_follows_the_audit() -> None:
    assert report_exit_code(build(AUDIT_PAIR)[0]) == 1
    assert report_exit_code(build(CLEAN_PAIR)[0]) == 0


def test_a_coverage_hole_alone_does_not_fail_the_report() -> None:
    audit, report = build(CLEAN_PAIR)
    assert report.untested_edges
    assert report_exit_code(audit) == 0


@pytest.mark.usefixtures("at_repo_root")
def test_cli_markdown_matches_the_golden(capsys, golden) -> None:
    code, out = run(capsys, ["report", *_argv(AUDIT_PAIR)])
    assert code == 1
    assert out == golden("report-audit.md")


@pytest.mark.usefixtures("at_repo_root")
def test_cli_json_matches_the_golden(capsys, golden) -> None:
    code, out = run(capsys, ["report", *_argv(AUDIT_PAIR), "--format", "json"])
    assert code == 1
    assert out == golden("report-audit.json")


def _argv(pair: tuple[str, str]) -> list[str]:
    graph_path, policy_path = pair
    return [graph_path, "--policy", policy_path]
