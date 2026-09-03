"""Stable diagnostic records.

A diagnostic is a `(code, path, message)` triple. Codes are part of the
public contract: the same document always produces the same codes, the
same paths, the same message text, and the same order. Tools that grep
or diff route-audit output can rely on that.

Sort order is `(code, path, message)`, which `order=True` on the
dataclass gives us for free.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

CODE_PARSE_ERROR = "parse_error"
CODE_INVALID_SCHEMA = "invalid_schema"
CODE_CYCLE = "cycle"
CODE_DANGLING_ALIAS = "dangling_alias"
CODE_UNREACHABLE_NODE = "unreachable_node"
CODE_DUPLICATE_PRIORITY = "duplicate_priority"
CODE_NO_TERMINAL_MODEL = "no_terminal_model"

#: Codes that stop analysis: the document could not be turned into a graph.
FATAL_CODES = frozenset({CODE_PARSE_ERROR, CODE_INVALID_SCHEMA})

#: Every code route-audit can emit, in sorted order.
ALL_CODES = (
    CODE_CYCLE,
    CODE_DANGLING_ALIAS,
    CODE_DUPLICATE_PRIORITY,
    CODE_INVALID_SCHEMA,
    CODE_NO_TERMINAL_MODEL,
    CODE_PARSE_ERROR,
    CODE_UNREACHABLE_NODE,
)


@dataclass(frozen=True, slots=True, order=True)
class Diagnostic:
    """One finding. `path` locates it in the document, e.g. `routes.chat`."""

    code: str
    path: str
    message: str

    def format(self) -> str:
        """One line of stable text output."""
        return f"{self.code} {self.path}: {self.message}"

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


def sort_diagnostics(items: Iterable[Diagnostic]) -> list[Diagnostic]:
    """Canonical ordering. Duplicates are collapsed."""
    return sorted(set(items))


def is_fatal(items: Iterable[Diagnostic]) -> bool:
    return any(item.code in FATAL_CODES for item in items)


def exit_code(items: Iterable[Diagnostic]) -> int:
    """0 clean, 1 diagnostics found, 2 the document could not be analysed."""
    items = list(items)
    if is_fatal(items):
        return 2
    return 1 if items else 0
