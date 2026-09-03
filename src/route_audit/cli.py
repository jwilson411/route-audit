"""`route-audit lint routes.yml`.

Reads one YAML file, prints stable diagnostics, exits with a documented
code. It never opens a socket.
"""

from __future__ import annotations

import argparse
import sys

from route_audit import __version__
from route_audit.diagnostics import exit_code
from route_audit.linter import format_json, format_text, lint_path

EPILOG = """\
exit codes:
  0  clean: no diagnostics
  1  diagnostics found
  2  usage error, unreadable file, or a document that could not be
     parsed or did not match the schema (parse_error, invalid_schema)

route-audit is a static linter. It validates architecture and does not send or proxy prompts.
It holds no credentials and does not check live availability.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="route-audit",
        description="Static routing-graph parser and linter for LLM route configs.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"route-audit {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    lint = subparsers.add_parser(
        "lint",
        help="Lint a routing graph YAML file",
        description="Lint a routing graph YAML file and report diagnostics.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    lint.add_argument("path", help="Path to the routing graph YAML file")
    lint.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format (default: text, one finding per line)",
    )
    lint.add_argument(
        "--json",
        dest="json_flag",
        action="store_true",
        help="Shorthand for --format json",
    )
    lint.set_defaults(handler=_run_lint)
    return parser


def _run_lint(args: argparse.Namespace) -> int:
    diagnostics = lint_path(args.path)
    if args.json_flag or args.format == "json":
        print(format_json(diagnostics, path=str(args.path)))
    else:
        sys.stdout.write(format_text(diagnostics))
    return exit_code(diagnostics)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
