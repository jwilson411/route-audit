"""Static routing-graph parser and linter.

route-audit validates architecture and does not send or proxy prompts.
"""

from route_audit.checks import check_graph
from route_audit.diagnostics import (
    ALL_CODES,
    CODE_CYCLE,
    CODE_DANGLING_ALIAS,
    CODE_DUPLICATE_PRIORITY,
    CODE_INVALID_SCHEMA,
    CODE_NO_TERMINAL_MODEL,
    CODE_PARSE_ERROR,
    CODE_UNREACHABLE_NODE,
    FATAL_CODES,
    Diagnostic,
    exit_code,
    sort_diagnostics,
)
from route_audit.linter import (
    format_json,
    format_text,
    lint_document,
    lint_path,
    lint_text,
)
from route_audit.model import Alias, FallbackEdge, Node, Route, RouteGraph
from route_audit.parser import parse_document, parse_text
from route_audit.resolve import Resolver, Target

__version__ = "0.1.0"

__all__ = [
    "ALL_CODES",
    "Alias",
    "CODE_CYCLE",
    "CODE_DANGLING_ALIAS",
    "CODE_DUPLICATE_PRIORITY",
    "CODE_INVALID_SCHEMA",
    "CODE_NO_TERMINAL_MODEL",
    "CODE_PARSE_ERROR",
    "CODE_UNREACHABLE_NODE",
    "Diagnostic",
    "FATAL_CODES",
    "FallbackEdge",
    "Node",
    "Resolver",
    "Route",
    "RouteGraph",
    "Target",
    "__version__",
    "check_graph",
    "exit_code",
    "format_json",
    "format_text",
    "lint_document",
    "lint_path",
    "lint_text",
    "parse_document",
    "parse_text",
    "sort_diagnostics",
]
