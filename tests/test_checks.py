"""One test per diagnostic class, driven off tests/fixtures/."""

from route_audit import (
    CODE_CYCLE,
    CODE_DANGLING_ALIAS,
    CODE_DUPLICATE_PRIORITY,
    CODE_NO_TERMINAL_MODEL,
    CODE_UNREACHABLE_NODE,
    lint_path,
    lint_text,
)


def codes(diagnostics) -> list[str]:
    return [item.code for item in diagnostics]


def test_valid_graph_is_clean(fixture_path) -> None:
    assert lint_path(fixture_path("valid.yml")) == []


def test_cycle(fixture_path) -> None:
    diagnostics = lint_path(fixture_path("cycle.yml"))
    assert codes(diagnostics) == [CODE_CYCLE]
    assert diagnostics[0].path == "routes.chat"
    assert diagnostics[0].message.endswith("backup, primary, spare")


def test_self_loop_is_a_cycle() -> None:
    document = """
routes:
  - id: chat
    nodes:
      - id: primary
        provider: openai
        model: gpt-4o
        terminal: true
    fallbacks:
      - from: primary
        to: primary
"""
    diagnostics = lint_text(document, path="inline")
    assert codes(diagnostics) == [CODE_CYCLE]
    assert "primary" in diagnostics[0].message


def test_dangling_alias(fixture_path) -> None:
    diagnostics = lint_path(fixture_path("dangling_alias.yml"))
    assert codes(diagnostics) == [CODE_DANGLING_ALIAS]
    assert diagnostics[0].path == "aliases.fast"
    assert "chat.does-not-exist" in diagnostics[0].message


def test_dangling_fallback_endpoint(fixture_path) -> None:
    diagnostics = lint_path(fixture_path("dangling_edge.yml"))
    assert codes(diagnostics) == [CODE_DANGLING_ALIAS, CODE_UNREACHABLE_NODE]
    assert diagnostics[0].path == "routes.chat.fallbacks[0]"
    assert "retired-node" in diagnostics[0].message


def test_dangling_entry() -> None:
    document = """
routes:
  - id: chat
    entry: ghost
    nodes:
      - id: primary
        provider: openai
        model: gpt-4o
        terminal: true
"""
    diagnostics = lint_text(document, path="inline")
    assert codes(diagnostics) == [CODE_DANGLING_ALIAS]
    assert diagnostics[0].path == "routes.chat.entry"


def test_unreachable_node(fixture_path) -> None:
    diagnostics = lint_path(fixture_path("unreachable_node.yml"))
    assert codes(diagnostics) == [CODE_UNREACHABLE_NODE]
    assert diagnostics[0].path == "routes.chat.nodes.orphan"


def test_duplicate_priority(fixture_path) -> None:
    diagnostics = lint_path(fixture_path("duplicate_priority.yml"))
    assert codes(diagnostics) == [CODE_DUPLICATE_PRIORITY]
    assert diagnostics[0].message == "priority 10 is shared by routes: chat, tools"


def test_routes_without_priority_do_not_collide() -> None:
    document = """
routes:
  - id: chat
    nodes:
      - id: a
        provider: openai
        model: gpt-4o
        terminal: true
  - id: embed
    nodes:
      - id: b
        provider: openai
        model: text-embedding-3-large
        terminal: true
"""
    assert lint_text(document, path="inline") == []


def test_no_terminal_model(fixture_path) -> None:
    diagnostics = lint_path(fixture_path("no_terminal_model.yml"))
    assert codes(diagnostics) == [CODE_NO_TERMINAL_MODEL]
    assert diagnostics[0].path == "routes.chat"


def test_node_without_outgoing_fallbacks_terminates_implicitly() -> None:
    document = """
routes:
  - id: chat
    nodes:
      - id: primary
        provider: openai
        model: gpt-4o
      - id: backup
        provider: anthropic
        model: claude-sonnet-4
    fallbacks:
      - from: primary
        to: backup
"""
    assert lint_text(document, path="inline") == []


def test_aliases_resolve_to_routes_and_nodes() -> None:
    document = """
routes:
  - id: chat
    alias: default-chat
    nodes:
      - id: primary
        alias: hot
        provider: openai
        model: gpt-4o
        terminal: true
      - id: backup
        provider: anthropic
        model: claude-sonnet-4
        terminal: true
    fallbacks:
      - from: hot
        to: backup
aliases:
  cheap: backup
  entrypoint: default-chat
"""
    assert lint_text(document, path="inline") == []


def test_diagnostics_are_stable_and_sorted(fixture_path) -> None:
    path = fixture_path("multi.yml")
    first = lint_path(path)
    second = lint_path(path)
    assert first == second
    assert first == sorted(first)
    assert codes(first) == [
        "cycle",
        "dangling_alias",
        "duplicate_priority",
        "no_terminal_model",
        "unreachable_node",
    ]
