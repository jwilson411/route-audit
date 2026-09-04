"""Text mode, JSON mode, and the documented exit codes."""

import json
from pathlib import Path

import pytest

from route_audit.cli import main

ROOT = Path(__file__).resolve().parents[1]


def run(capsys, argv) -> tuple[int, str, str]:
    code = main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def example(name: str) -> str:
    return str(ROOT / "examples" / name)


def test_clean_graph_exits_zero_and_prints_nothing(capsys, fixture_path) -> None:
    code, out, _ = run(capsys, ["lint", str(fixture_path("valid.yml"))])
    assert code == 0
    assert out == ""


def test_text_mode_one_finding_per_line(capsys, fixture_path) -> None:
    code, out, _ = run(capsys, ["lint", str(fixture_path("multi.yml"))])
    assert code == 1
    lines = out.splitlines()
    assert len(lines) == 5
    assert [line.split(" ", 1)[0] for line in lines] == [
        "cycle",
        "dangling_alias",
        "duplicate_priority",
        "no_terminal_model",
        "unreachable_node",
    ]
    assert lines[0] == (
        "cycle routes.chat: fallback edges form a cycle among nodes: backup, primary"
    )


def test_text_mode_is_byte_for_byte_stable(capsys, fixture_path) -> None:
    path = str(fixture_path("multi.yml"))
    _, first, _ = run(capsys, ["lint", path])
    _, second, _ = run(capsys, ["lint", path])
    assert first == second


def test_json_flag(capsys, fixture_path) -> None:
    path = str(fixture_path("cycle.yml"))
    code, out, _ = run(capsys, ["lint", path, "--json"])
    assert code == 1
    payload = json.loads(out)
    assert payload["file"] == path
    assert payload["ok"] is False
    assert payload["exit_code"] == 1
    assert [item["code"] for item in payload["findings"]] == ["cycle"]
    assert payload["findings"][0]["path"] == "routes.chat"
    assert "cycle" in payload["findings"][0]["message"]


def test_format_json_matches_json_flag(capsys, fixture_path) -> None:
    path = str(fixture_path("multi.yml"))
    _, flag_out, _ = run(capsys, ["lint", path, "--json"])
    _, format_out, _ = run(capsys, ["lint", path, "--format", "json"])
    assert flag_out == format_out


def test_json_mode_on_a_clean_graph(capsys, fixture_path) -> None:
    code, out, _ = run(capsys, ["lint", str(fixture_path("valid.yml")), "--json"])
    assert code == 0
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["findings"] == []


def test_parse_failure_exits_two(capsys, fixture_path) -> None:
    code, out, _ = run(capsys, ["lint", str(fixture_path("parse_error.yml"))])
    assert code == 2
    assert out.startswith("parse_error ")


def test_schema_failure_exits_two(capsys, fixture_path) -> None:
    code, out, _ = run(capsys, ["lint", str(fixture_path("invalid_schema.yml"))])
    assert code == 2
    assert all(line.startswith("invalid_schema ") for line in out.splitlines())


def test_unreadable_file_exits_two(capsys, tmp_path) -> None:
    code, out, _ = run(capsys, ["lint", str(tmp_path / "nope.yml")])
    assert code == 2
    assert "cannot read file" in out


def test_usage_error_exits_two(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["lint"])
    assert excinfo.value.code == 2


def test_help_documents_flags_and_exit_codes(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["lint", "--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "--json" in out
    assert "--format" in out
    assert "exit codes:" in out
    assert "does not send or proxy prompts" in out


# --- audit ------------------------------------------------------------------


def test_audit_of_a_satisfied_policy_exits_zero(capsys) -> None:
    code, out, _ = run(
        capsys,
        ["audit", example("routes.yml"), "--policy", example("policy-clean.yml")],
    )
    assert code == 0
    assert out == "ok\n"


def test_audit_of_a_violated_policy_exits_one(capsys) -> None:
    code, out, _ = run(
        capsys,
        ["audit", example("audit-routes.yml"), "--policy", example("policy.yml")],
    )
    assert code == 1
    assert out.startswith("smallest ")


def test_audit_json_reports_the_smallest_path_and_every_violation(capsys) -> None:
    code, out, _ = run(
        capsys,
        [
            "audit",
            example("audit-routes.yml"),
            "--policy",
            example("policy.yml"),
            "--json",
        ],
    )
    assert code == 1
    payload = json.loads(out)
    assert payload["ok"] is False
    assert payload["exit_code"] == 1
    assert payload["smallest"]["path"] == ["primary", "backup", "local"]
    assert "provider_denied" in {item["code"] for item in payload["violations"]}
    assert payload["cases"]


def test_audit_without_a_policy_is_a_usage_error(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["audit", example("routes.yml")])
    assert excinfo.value.code == 2


def test_audit_with_an_unreadable_policy_exits_two(capsys, tmp_path) -> None:
    code, out, _ = run(
        capsys,
        ["audit", example("routes.yml"), "--policy", str(tmp_path / "nope.yml")],
    )
    assert code == 2
    assert "cannot read file" in out


# --- report -----------------------------------------------------------------


def test_report_defaults_to_markdown(capsys) -> None:
    code, out, _ = run(
        capsys,
        ["report", example("routes.yml"), "--policy", example("policy-clean.yml")],
    )
    assert code == 0
    assert out.startswith("# Coverage matrix\n")
    assert "## Untested edges" in out
    assert out.endswith("\n")


def test_report_of_a_violated_policy_exits_one(capsys) -> None:
    code, out, _ = run(
        capsys,
        ["report", example("audit-routes.yml"), "--policy", example("policy.yml")],
    )
    assert code == 1
    assert "| tools | two-down |" in out


def test_report_json_flag_matches_format_json(capsys) -> None:
    argv = ["report", example("routes.yml"), "--policy", example("policy-clean.yml")]
    _, flag_out, _ = run(capsys, [*argv, "--json"])
    _, format_out, _ = run(capsys, [*argv, "--format", "json"])
    assert flag_out == format_out
    payload = json.loads(flag_out)
    assert payload["exit_code"] == 0
    assert payload["routes_without_selection"] == ["embed"]
    assert [row["class"] for row in payload["matrix"]] == ["tools"]


def test_report_without_a_policy_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["report", example("routes.yml")])
    assert excinfo.value.code == 2


def test_report_with_an_unreadable_policy_exits_two(capsys, tmp_path) -> None:
    code, out, _ = run(
        capsys,
        ["report", example("routes.yml"), "--policy", str(tmp_path / "nope.yml")],
    )
    assert code == 2
    assert "cannot read file" in out


def test_report_of_an_unparseable_graph_exits_two(capsys, fixture_path) -> None:
    code, out, _ = run(
        capsys,
        [
            "report",
            str(fixture_path("parse_error.yml")),
            "--policy",
            example("policy-clean.yml"),
        ],
    )
    assert code == 2
    assert out.startswith("parse_error ")


# --- sarif ------------------------------------------------------------------


def test_lint_sarif_exits_like_lint(capsys, fixture_path) -> None:
    code, out, _ = run(
        capsys, ["lint", str(fixture_path("cycle.yml")), "--format", "sarif"]
    )
    assert code == 1
    payload = json.loads(out)
    assert payload["version"] == "2.1.0"
    assert [item["ruleId"] for item in payload["runs"][0]["results"]] == ["cycle"]


def test_lint_sarif_of_a_clean_graph_exits_zero(capsys, fixture_path) -> None:
    code, out, _ = run(
        capsys, ["lint", str(fixture_path("valid.yml")), "--format", "sarif"]
    )
    assert code == 0
    assert json.loads(out)["runs"][0]["results"] == []


def test_lint_sarif_reports_a_fatal_document(capsys, fixture_path) -> None:
    code, out, _ = run(
        capsys, ["lint", str(fixture_path("parse_error.yml")), "--format", "sarif"]
    )
    assert code == 2
    assert [item["ruleId"] for item in json.loads(out)["runs"][0]["results"]] == [
        "parse_error"
    ]


def test_audit_sarif_exits_like_audit(capsys) -> None:
    code, out, _ = run(
        capsys,
        [
            "audit",
            example("audit-routes.yml"),
            "--policy",
            example("policy.yml"),
            "--format",
            "sarif",
        ],
    )
    assert code == 1
    codes = {item["ruleId"] for item in json.loads(out)["runs"][0]["results"]}
    assert "provider_denied" in codes


def test_audit_sarif_of_a_satisfied_policy_exits_zero(capsys) -> None:
    code, out, _ = run(
        capsys,
        [
            "audit",
            example("routes.yml"),
            "--policy",
            example("policy-clean.yml"),
            "--format",
            "sarif",
        ],
    )
    assert code == 0
    assert json.loads(out)["runs"][0]["results"] == []


def test_json_flag_still_wins_over_format(capsys, fixture_path) -> None:
    path = str(fixture_path("cycle.yml"))
    _, out, _ = run(capsys, ["lint", path, "--format", "sarif", "--json"])
    assert json.loads(out)["file"] == path


def test_report_help_documents_the_matrix_and_exit_codes(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["report", "--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "--policy" in out
    assert "--format" in out
    assert "exit codes:" in out
    assert "does not send or proxy prompts" in out
