"""`route-audit lint`, `route-audit simulate`, `route-audit audit`.

Reads local YAML files, prints stable output, exits with a documented
code. It never opens a socket.
"""

from __future__ import annotations

import argparse
import sys

from route_audit import __version__
from route_audit.audit import (
    audit_exit_code,
    format_audit_json,
    format_audit_text,
    run_audit,
)
from route_audit.batch import (
    batch_exit_code,
    format_batch_json,
    format_batch_text,
    load_batch_path,
    run_batch,
)
from route_audit.diagnostics import Diagnostic, exit_code
from route_audit.linter import format_json, format_text, lint_path
from route_audit.parser import parse_path
from route_audit.policy import check_region_vocabulary, load_policy_path
from route_audit.simulate import (
    Scenario,
    format_simulation_json,
    format_simulation_text,
    load_request_path,
    load_scenario_path,
    simulate,
    simulation_exit_code,
)

EPILOG = """\
exit codes:
  0  clean: no diagnostics
  1  diagnostics found
  2  usage error, unreadable file, or a document that could not be
     parsed or did not match the schema (parse_error, invalid_schema)

route-audit is a static linter. It validates architecture and does not send or proxy prompts.
It holds no credentials and does not check live availability.
"""

SIMULATE_EPILOG = """\
exit codes:
  0  a node was selected (every case selected, in batch mode)
  1  the walk ended in terminal failure (any case failed, in batch mode)
  2  usage error, unreadable file, or a document that could not be
     parsed or did not match the schema (parse_error, invalid_schema)

The simulation is offline and deterministic: the same graph, request,
and scenario always produce the same path. A request fixture declares
required capabilities, context tokens, and allowed providers - it must
not carry prompt text. route-audit validates architecture and does not
send or proxy prompts.
"""

AUDIT_EPILOG = """\
exit codes:
  0  no policy violation (a case the walk could not serve is a coverage
     hole, not a governance failure, and does not fail the audit)
  1  at least one policy violation
  2  usage error, unreadable file, or a document that could not be
     parsed or did not match the schema (parse_error, invalid_schema)

The audit is a layer on top of the simulation: simulate decides what the
graph would do, the policy decides whether that crossing was allowed.
Region tags are user-declared metadata, not a compliance certification -
route-audit performs no network geolocation. It validates architecture
and does not send or proxy prompts.
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

    sim = subparsers.add_parser(
        "simulate",
        help="Simulate a request through the fallback graph under an outage scenario",
        description=(
            "Walk a request through a routing graph offline: which nodes are "
            "attempted, why each is rejected, which one is selected."
        ),
        epilog=SIMULATE_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sim.add_argument("path", help="Path to the routing graph YAML file")
    source = sim.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--request",
        help="Path to a request fixture: capabilities, context tokens, providers",
    )
    source.add_argument(
        "--batch", help="Path to a seeded batch document to run as a matrix"
    )
    sim.add_argument(
        "--scenario",
        help="Path to an outage scenario marking nodes unavailable, rate limited, "
        "over context, or missing capabilities",
    )
    sim.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format (default: text, one stable line plus the attempted path)",
    )
    sim.add_argument(
        "--json",
        dest="json_flag",
        action="store_true",
        help="Shorthand for --format json",
    )
    sim.set_defaults(handler=_run_simulate, subparser=sim)

    audit = subparsers.add_parser(
        "audit",
        help="Audit every request class against every scenario under a policy",
        description=(
            "Simulate every declared request class under every scenario and "
            "report where a fallback crosses a boundary the policy forbids."
        ),
        epilog=AUDIT_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    audit.add_argument("path", help="Path to the routing graph YAML file")
    audit.add_argument(
        "--policy",
        required=True,
        help="Path to the policy document: providers, capabilities, hops, regions",
    )
    audit.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format (default: text, the smallest violating path first)",
    )
    audit.add_argument(
        "--json",
        dest="json_flag",
        action="store_true",
        help="Shorthand for --format json",
    )
    audit.set_defaults(handler=_run_audit)
    return parser


def _run_lint(args: argparse.Namespace) -> int:
    diagnostics = lint_path(args.path)
    if args.json_flag or args.format == "json":
        print(format_json(diagnostics, path=str(args.path)))
    else:
        sys.stdout.write(format_text(diagnostics))
    return exit_code(diagnostics)


def _run_simulate(args: argparse.Namespace) -> int:
    if args.scenario and not args.request:
        args.subparser.error("--scenario requires --request")
    json_mode = args.json_flag or args.format == "json"

    graph, diagnostics = parse_path(args.path)
    if graph is None:
        return _report(diagnostics, path=str(args.path), json_mode=json_mode)

    if args.batch:
        plan, diagnostics = load_batch_path(args.batch)
        if plan is None:
            return _report(diagnostics, path=str(args.batch), json_mode=json_mode)
        report = run_batch(graph, plan)
        if json_mode:
            print(format_batch_json(report, graph=str(args.path), batch=str(args.batch)))
        else:
            sys.stdout.write(format_batch_text(report))
        return batch_exit_code(report)

    request, diagnostics = load_request_path(args.request)
    failing = str(args.request)
    scenario = Scenario()
    if args.scenario:
        loaded, problems = load_scenario_path(args.scenario)
        if loaded is not None:
            scenario = loaded
        elif not diagnostics:
            failing = str(args.scenario)
        diagnostics = diagnostics + problems
    if request is None or diagnostics:
        return _report(diagnostics, path=failing, json_mode=json_mode)

    result = simulate(graph, request, scenario)
    if json_mode:
        print(
            format_simulation_json(
                result,
                graph=str(args.path),
                request=str(args.request),
                scenario=str(args.scenario) if args.scenario else None,
            )
        )
    else:
        sys.stdout.write(format_simulation_text(result))
    return simulation_exit_code(result)


def _run_audit(args: argparse.Namespace) -> int:
    json_mode = args.json_flag or args.format == "json"

    graph, diagnostics = parse_path(args.path)
    if graph is None:
        return _report(diagnostics, path=str(args.path), json_mode=json_mode)

    policy, diagnostics = load_policy_path(args.policy)
    if policy is None:
        return _report(diagnostics, path=str(args.policy), json_mode=json_mode)

    # Region tags on the graph are only checked against a declared
    # vocabulary, so this is the audit's business and never the linter's.
    diagnostics = check_region_vocabulary(graph, policy)
    if diagnostics:
        return _report(diagnostics, path=str(args.path), json_mode=json_mode)

    report = run_audit(graph, policy)
    if json_mode:
        print(format_audit_json(report, graph=str(args.path), policy=str(args.policy)))
    else:
        sys.stdout.write(format_audit_text(report))
    return audit_exit_code(report)


def _report(diagnostics: list[Diagnostic], *, path: str, json_mode: bool) -> int:
    """Render loader diagnostics the way `lint` renders findings."""
    if not diagnostics:  # a document that failed to load always says why
        return 2
    if json_mode:
        print(format_json(diagnostics, path=path))
    else:
        sys.stdout.write(format_text(diagnostics))
    return exit_code(diagnostics)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
