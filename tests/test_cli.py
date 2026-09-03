"""Text mode, JSON mode, and the documented exit codes."""

import json

import pytest

from route_audit.cli import main


def run(capsys, argv) -> tuple[int, str, str]:
    code = main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


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
