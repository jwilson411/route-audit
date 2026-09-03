"""Parse, check, and render. This is the whole public workflow.

`lint_path` reads a file off disk. That is the only I/O route-audit
performs: it validates architecture and does not send or proxy prompts.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from route_audit.checks import check_graph
from route_audit.diagnostics import CODE_PARSE_ERROR, Diagnostic, exit_code
from route_audit.parser import parse_document, parse_text


def lint_text(text: str, *, path: str = "<document>") -> list[Diagnostic]:
    graph, diagnostics = parse_text(text, path=path)
    if graph is None:
        return diagnostics
    return check_graph(graph)


def lint_document(data: object, *, path: str = "<document>") -> list[Diagnostic]:
    """Lint an already-loaded YAML/JSON document."""
    graph, diagnostics = parse_document(data, path=path)
    if graph is None:
        return diagnostics
    return check_graph(graph)


def lint_path(path: str | Path) -> list[Diagnostic]:
    """Lint a file. An unreadable file is a `parse_error`, not an exception."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [
            Diagnostic(CODE_PARSE_ERROR, str(path), f"cannot read file: {exc.strerror}")
        ]
    except UnicodeDecodeError:
        return [
            Diagnostic(CODE_PARSE_ERROR, str(path), "cannot read file: not valid UTF-8")
        ]
    return lint_text(text, path=str(path))


def format_text(diagnostics: Iterable[Diagnostic]) -> str:
    """One finding per line. Empty string when the graph is clean."""
    return "".join(f"{item.format()}\n" for item in diagnostics)


def format_json(diagnostics: Iterable[Diagnostic], *, path: str) -> str:
    """A single JSON object, keys in a fixed order, no trailing newline."""
    diagnostics = list(diagnostics)
    payload = {
        "file": path,
        "ok": not diagnostics,
        "exit_code": exit_code(diagnostics),
        "findings": [item.to_dict() for item in diagnostics],
    }
    return json.dumps(payload, indent=2, sort_keys=False)
