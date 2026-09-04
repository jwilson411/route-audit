"""SARIF 2.1.0 for lint diagnostics and for policy violations."""

import json

import pytest

from route_audit import (
    ALL_CODES,
    ALL_VIOLATIONS,
    SARIF_SCHEMA,
    SARIF_VERSION,
    __version__,
    format_audit_sarif,
    format_lint_sarif,
    lint_path,
    load_policy_path,
    parse_path,
    run_audit,
)
from route_audit.cli import main

from conftest import ROOT

GRAPH = "examples/audit-routes.yml"
POLICY = "examples/policy.yml"


def lint_sarif(name: str) -> str:
    path = f"tests/fixtures/{name}"
    return format_lint_sarif(lint_path(ROOT / path), path=path)


def audit_sarif() -> str:
    graph, diagnostics = parse_path(ROOT / GRAPH)
    assert diagnostics == []
    policy, diagnostics = load_policy_path(ROOT / POLICY)
    assert diagnostics == []
    return format_audit_sarif(run_audit(graph, policy), graph=GRAPH, policy=POLICY)


def run(capsys, argv) -> tuple[int, str]:
    code = main(argv)
    return code, capsys.readouterr().out


def results(document: str) -> list[dict]:
    return json.loads(document)["runs"][0]["results"]


def logical(result: dict) -> list[str]:
    location = result["locations"][0]
    return [item["fullyQualifiedName"] for item in location["logicalLocations"]]


# --- the document -----------------------------------------------------------


@pytest.mark.parametrize("document", [lint_sarif("cycle.yml"), audit_sarif()])
def test_the_envelope_is_sarif_2_1_0(document) -> None:
    payload = json.loads(document)
    assert payload["$schema"] == SARIF_SCHEMA
    assert payload["$schema"] == "https://json.schemastore.org/sarif-2.1.0.json"
    assert payload["version"] == SARIF_VERSION == "2.1.0"
    assert len(payload["runs"]) == 1


@pytest.mark.parametrize("document", [lint_sarif("cycle.yml"), audit_sarif()])
def test_the_tool_driver_names_route_audit_and_its_version(document) -> None:
    driver = json.loads(document)["runs"][0]["tool"]["driver"]
    assert driver["name"] == "route-audit"
    assert driver["version"] == __version__


def test_a_clean_file_produces_a_run_with_no_results() -> None:
    document = lint_sarif("valid.yml")
    payload = json.loads(document)
    assert payload["runs"][0]["results"] == []
    assert payload["runs"][0]["tool"]["driver"]["rules"] == []


# --- rule ids ---------------------------------------------------------------


def test_lint_rule_ids_are_the_existing_diagnostic_codes() -> None:
    for name in ("cycle.yml", "dangling_alias.yml", "multi.yml"):
        for result in results(lint_sarif(name)):
            assert result["ruleId"] in ALL_CODES


def test_audit_rule_ids_are_the_existing_violation_codes() -> None:
    for result in results(audit_sarif()):
        assert result["ruleId"] in ALL_VIOLATIONS


def test_every_rule_used_is_declared_on_the_driver() -> None:
    for document in (lint_sarif("multi.yml"), audit_sarif()):
        payload = json.loads(document)
        declared = [rule["id"] for rule in payload["runs"][0]["tool"]["driver"]["rules"]]
        assert declared == sorted(set(declared))
        assert set(declared) == {
            result["ruleId"] for result in payload["runs"][0]["results"]
        }


# --- locations --------------------------------------------------------------


def test_a_lint_result_locates_itself_by_yaml_path() -> None:
    [result] = results(lint_sarif("cycle.yml"))
    assert logical(result) == ["routes.chat"]
    assert result["locations"][0]["physicalLocation"]["artifactLocation"] == {
        "uri": "tests/fixtures/cycle.yml"
    }
    assert result["locations"][0]["logicalLocations"][0]["name"] == "chat"


def test_a_dangling_alias_locates_itself_in_the_aliases_block() -> None:
    [result] = results(lint_sarif("dangling_alias.yml"))
    assert result["ruleId"] == "dangling_alias"
    assert logical(result) == ["aliases.fast"]


def test_an_audit_result_names_the_node_the_class_and_the_scenario() -> None:
    result = next(
        item for item in results(audit_sarif()) if item["ruleId"] == "provider_denied"
    )
    assert logical(result) == [
        "routes.chat.nodes.local",
        "classes.eu-chat",
        "scenarios.two-down",
    ]
    assert result["locations"][0]["physicalLocation"]["artifactLocation"] == {
        "uri": GRAPH
    }


def test_the_audit_run_records_both_documents_as_artifacts() -> None:
    artifacts = json.loads(audit_sarif())["runs"][0]["artifacts"]
    assert [item["location"]["uri"] for item in artifacts] == [GRAPH, POLICY]


# --- messages, level, ordering ----------------------------------------------


def test_every_finding_is_an_error() -> None:
    for document in (lint_sarif("multi.yml"), audit_sarif()):
        for result in results(document):
            assert result["level"] == "error"


def test_message_text_is_the_message_the_text_renderer_prints() -> None:
    document = lint_sarif("cycle.yml")
    [diagnostic] = lint_path(ROOT / "tests/fixtures/cycle.yml")
    assert results(document)[0]["message"]["text"] == diagnostic.message


def test_results_are_sorted_by_rule_location_and_message() -> None:
    for document in (lint_sarif("multi.yml"), audit_sarif()):
        keys = [
            (result["ruleId"], logical(result)[0], result["message"]["text"])
            for result in results(document)
        ]
        assert keys == sorted(keys)


def test_rendering_is_byte_stable() -> None:
    assert lint_sarif("multi.yml") == lint_sarif("multi.yml")
    assert audit_sarif() == audit_sarif()


# --- goldens ----------------------------------------------------------------


@pytest.mark.parametrize(
    "name,golden_name",
    [
        ("cycle.yml", "lint-cycle.sarif.json"),
        ("dangling_alias.yml", "lint-dangling-alias.sarif.json"),
    ],
)
def test_lint_sarif_matches_the_golden(golden, name, golden_name) -> None:
    assert lint_sarif(name) + "\n" == golden(golden_name)


def test_audit_sarif_matches_the_golden(golden) -> None:
    assert audit_sarif() + "\n" == golden("audit-policy.sarif.json")


@pytest.mark.usefixtures("at_repo_root")
def test_cli_lint_sarif_matches_the_golden(capsys, golden) -> None:
    code, out = run(capsys, ["lint", "tests/fixtures/cycle.yml", "--format", "sarif"])
    assert code == 1
    assert out == golden("lint-cycle.sarif.json")


@pytest.mark.usefixtures("at_repo_root")
def test_cli_audit_sarif_matches_the_golden(capsys, golden) -> None:
    code, out = run(
        capsys, ["audit", GRAPH, "--policy", POLICY, "--format", "sarif"]
    )
    assert code == 1
    assert out == golden("audit-policy.sarif.json")


# --- the no-payload claim ---------------------------------------------------


def test_no_prompt_text_or_credentials_travel_in_sarif(assert_no_payload) -> None:
    for document in (
        lint_sarif("cycle.yml"),
        lint_sarif("multi.yml"),
        audit_sarif(),
    ):
        assert_no_payload(document)
