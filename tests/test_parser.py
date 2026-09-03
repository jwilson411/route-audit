"""Parse and schema failures are diagnostics, not exceptions."""

from route_audit import (
    CODE_INVALID_SCHEMA,
    CODE_PARSE_ERROR,
    exit_code,
    lint_path,
    lint_text,
    parse_text,
)


def test_parse_error(fixture_path) -> None:
    diagnostics = lint_path(fixture_path("parse_error.yml"))
    assert [item.code for item in diagnostics] == [CODE_PARSE_ERROR]
    assert exit_code(diagnostics) == 2


def test_invalid_schema_reports_every_problem(fixture_path) -> None:
    diagnostics = lint_path(fixture_path("invalid_schema.yml"))
    assert {item.code for item in diagnostics} == {CODE_INVALID_SCHEMA}
    messages = [item.message for item in diagnostics]
    assert any("'priority' must be an integer" in message for message in messages)
    assert any("'model' must be a non-empty string" in message for message in messages)
    assert exit_code(diagnostics) == 2


def test_missing_routes_key() -> None:
    diagnostics = lint_text("version: 1\n", path="inline")
    assert [item.code for item in diagnostics] == [CODE_INVALID_SCHEMA]
    assert diagnostics[0].message == "missing required key 'routes'"


def test_empty_document() -> None:
    diagnostics = lint_text("", path="inline")
    assert [item.code for item in diagnostics] == [CODE_INVALID_SCHEMA]
    assert diagnostics[0].message == "document is empty"


def test_document_must_be_a_mapping() -> None:
    diagnostics = lint_text("- one\n- two\n", path="inline")
    assert [item.code for item in diagnostics] == [CODE_INVALID_SCHEMA]
    assert "must be a mapping" in diagnostics[0].message


def test_duplicate_alias_is_a_schema_error() -> None:
    document = """
routes:
  - id: chat
    alias: shared
    nodes:
      - id: primary
        alias: shared
        provider: openai
        model: gpt-4o
        terminal: true
"""
    diagnostics = lint_text(document, path="inline")
    assert [item.code for item in diagnostics] == [CODE_INVALID_SCHEMA]
    assert "duplicate alias 'shared'" in diagnostics[0].message


def test_unreadable_file_is_a_parse_error(tmp_path) -> None:
    diagnostics = lint_path(tmp_path / "missing.yml")
    assert [item.code for item in diagnostics] == [CODE_PARSE_ERROR]
    assert "cannot read file" in diagnostics[0].message
    assert exit_code(diagnostics) == 2


def test_unknown_keys_are_ignored() -> None:
    document = """
version: 1
metadata:
  owner: platform
routes:
  - id: chat
    tags: [experimental]
    nodes:
      - id: primary
        provider: openai
        model: gpt-4o
        terminal: true
        cost_per_token: 0.00001
"""
    assert lint_text(document, path="inline") == []


def test_parse_builds_the_graph(fixture_path) -> None:
    graph, diagnostics = parse_text(
        fixture_path("valid.yml").read_text(encoding="utf-8"), path="valid.yml"
    )
    assert diagnostics == []
    assert graph is not None
    assert [route.id for route in graph.routes] == ["chat", "embed"]
    chat = graph.route("chat")
    assert chat.node_ids == ("primary", "backup", "local")
    assert chat.node("primary").capabilities == ("chat", "tools")
    assert chat.node("primary").context_limit == 128000
    assert chat.priority == 10
    assert {alias.name for alias in graph.aliases} == {
        "default-chat",
        "chat-backup",
        "fast",
        "primary-chat",
    }
