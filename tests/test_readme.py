"""The README makes claims. These check the load-bearing ones."""

from pathlib import Path

from route_audit import ALL_CODES, lint_path

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
