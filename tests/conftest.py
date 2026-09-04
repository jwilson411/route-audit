import json
import re
from pathlib import Path
from typing import Any, Iterator

import pytest

from route_audit import PROMPT_KEYS

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
GOLDENS = Path(__file__).resolve().parent / "goldens"

#: Strings a report must never carry. route-audit holds no credentials,
#: so any of these appearing in output would have to have come out of a
#: file it was asked to describe.
CREDENTIAL_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "credential",
    "password",
    "secret",
    "token",
)

#: SARIF 2.1.0 spells a message `{"message": {"text": ...}}`. That `text`
#: is the format's own property name, not a prompt payload key.
ALLOWED_KEYS = {("message", "text")}


@pytest.fixture
def fixture_path():
    def _path(name: str) -> Path:
        return FIXTURES / name

    return _path


@pytest.fixture
def golden():
    """The frozen bytes of a committed golden file."""

    def _read(name: str) -> str:
        return (GOLDENS / name).read_text(encoding="utf-8")

    return _read


@pytest.fixture
def at_repo_root(monkeypatch):
    """Run a command on paths relative to the repo root.

    Golden output records the file names it was given, so the test has to
    give it the same names every machine would.
    """
    monkeypatch.chdir(ROOT)


@pytest.fixture
def assert_no_payload():
    """A report carries configuration metadata, never content or secrets."""

    def _check(text: str) -> None:
        lowered = text.lower()
        for marker in CREDENTIAL_MARKERS:
            assert marker not in lowered, f"credential-looking string {marker!r}"
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            for key in PROMPT_KEYS:
                assert not re.search(rf"\b{key}\b", lowered), f"prompt key {key!r}"
            return
        for parent, key in _keys(payload):
            if (parent, key) in ALLOWED_KEYS:
                continue
            assert key not in PROMPT_KEYS, f"prompt key {key!r}"

    return _check


def _keys(node: Any, parent: str | None = None) -> Iterator[tuple[str | None, str]]:
    """Every mapping key in a document, with the key it hangs under."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield parent, key
            yield from _keys(value, key)
    elif isinstance(node, list):
        for item in node:
            yield from _keys(item, parent)
