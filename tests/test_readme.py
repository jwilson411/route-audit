"""The README makes claims. These check the load-bearing ones."""

from pathlib import Path

import pytest

from route_audit import (
    ALL_CODES,
    ALL_FAILURES,
    ALL_REASONS,
    ALL_VIOLATIONS,
    PROMPT_KEYS,
    lint_path,
)
from route_audit.cli import main

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")


def test_readme_states_the_no_proxy_claim() -> None:
    assert "validates architecture and does not send or proxy prompts" in README


def test_readme_documents_the_cli_invocation() -> None:
    assert "route-audit lint routes.yml" in README


def test_readme_documents_every_diagnostic_code() -> None:
    for code in ALL_CODES:
        assert f"`{code}`" in README


def test_readme_documents_exit_codes_and_make_test() -> None:
    for fragment in ("Exit codes", "`0`", "`1`", "`2`", "make test", "MIT"):
        assert fragment in README


def test_readme_documents_the_schema_vocabulary() -> None:
    for fragment in (
        "routes",
        "provider",
        "model",
        "alias",
        "fallbacks",
        "capabilities",
        "context_limit",
        "priority",
        "terminal",
    ):
        assert fragment in README


def test_readme_documents_the_simulate_invocation() -> None:
    assert "route-audit simulate routes.yml --request request.yml" in README
    assert "--scenario" in README
    assert "--batch" in README


def test_readme_says_a_request_carries_no_prompt_text() -> None:
    assert "never its content" in README
    for key in PROMPT_KEYS:
        assert f"`{key}`" in README


def test_readme_documents_the_request_and_scenario_vocabulary() -> None:
    for fragment in (
        "required_capabilities",
        "context_tokens",
        "allowed_providers",
        "unavailable",
        "rate_limited",
        "over_context",
        "missing_capabilities",
    ):
        assert f"`{fragment}`" in README


def test_readme_documents_every_reason_and_failure() -> None:
    for name in ALL_REASONS + ALL_FAILURES:
        assert f"`{name}`" in README


def test_readme_documents_the_batch_seed_and_coverage() -> None:
    for fragment in (
        "`seed`",
        "byte-identical",
        "`coverage.routes`",
        "`coverage.routes_without_selection`",
    ):
        assert fragment in README


def _imported_root_module(line: str) -> str | None:
    """The top-level module an `import x` / `from x import y` line names."""
    parts = line.split()
    if len(parts) < 2 or parts[0] not in ("import", "from"):
        return None
    return parts[1].lstrip(".").split(".", 1)[0]


def test_no_vendor_gateway_sdk_imports() -> None:
    banned = {"openai", "anthropic", "litellm", "agno", "httpx", "requests", "urllib"}
    for source in (ROOT / "src" / "route_audit").rglob("*.py"):
        text = source.read_text(encoding="utf-8")
        for line in text.splitlines():
            module = _imported_root_module(line.strip())
            assert module not in banned, f"{source.name}: {line.strip()}"


def test_the_readme_example_graph_lints_clean() -> None:
    assert lint_path(ROOT / "examples" / "routes.yml") == []


def _transcript(command: str) -> str:
    """The output the README shows under `$ <command>`, up to the fence."""
    _, _, rest = README.partition(f"$ {command}\n")
    assert rest, f"README shows no transcript for {command!r}"
    body, _, _ = rest.partition("```")
    return body


@pytest.mark.parametrize(
    "command",
    [
        "route-audit simulate examples/routes.yml --request examples/request.yml"
        " --scenario examples/outage.yml",
        "route-audit simulate examples/routes.yml --batch examples/batch.yml",
    ],
)
def test_the_readme_simulate_transcripts_are_real(capsys, command) -> None:
    argv = [str(ROOT / part) if "/" in part else part for part in command.split()[1:]]
    assert main(argv) == 0
    printed = capsys.readouterr().out
    assert printed == _transcript(command).replace(str(ROOT) + "/", "")
