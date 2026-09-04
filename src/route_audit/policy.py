"""The organization policy document: what a fallback is allowed to cost.

A fallback graph can be internally coherent, lint clean, and still walk
a request across a boundary the organization never agreed to - onto a
provider nobody approved, into a region tag nobody expected, down two
more hops than the design allows, or onto a node that quietly dropped
`tools` or `json` on the way. A policy names those boundaries so the
audit can decide them from the documents alone.

Region tags are user-declared metadata, not a compliance certification.
route-audit checks the tag written on a node against the vocabulary the
policy declares. It performs no network geolocation and knows nothing
about where a provider actually runs.

Every failure here fails closed: a policy that cannot be understood is
`parse_error` or `invalid_schema`, never an empty policy that passes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from route_audit.batch import NO_SCENARIO
from route_audit.diagnostics import (
    CODE_INVALID_SCHEMA,
    CODE_PARSE_ERROR,
    Diagnostic,
    sort_diagnostics,
)
from route_audit.model import RouteGraph
from route_audit.parser import (
    optional_bool,
    optional_int,
    optional_str_list,
    read_text_path,
    type_name,
    yaml_message,
)
from route_audit.simulate import (
    Request,
    Scenario,
    load_request_document,
    load_scenario_document,
)

#: A selected node crossed a boundary the policy forbids.
VIOLATION_CAPABILITY_DOWNGRADE = "capability_downgrade"
VIOLATION_CAPABILITY_REQUIRED = "capability_required"
VIOLATION_CONTEXT_DOWNGRADE = "context_downgrade"
VIOLATION_MAX_HOPS_EXCEEDED = "max_hops_exceeded"
VIOLATION_PROVIDER_DENIED = "provider_denied"
VIOLATION_PROVIDER_NOT_ALLOWED = "provider_not_allowed"
VIOLATION_REGION_DENIED = "region_denied"
VIOLATION_REGION_NOT_ALLOWED = "region_not_allowed"

#: Every violation code the audit can emit, sorted. Distinct from the lint
#: codes in `diagnostics.ALL_CODES`: a violation is a governance finding
#: about a path, not a schema finding about a document.
ALL_VIOLATIONS = (
    VIOLATION_CAPABILITY_DOWNGRADE,
    VIOLATION_CAPABILITY_REQUIRED,
    VIOLATION_CONTEXT_DOWNGRADE,
    VIOLATION_MAX_HOPS_EXCEEDED,
    VIOLATION_PROVIDER_DENIED,
    VIOLATION_PROVIDER_NOT_ALLOWED,
    VIOLATION_REGION_DENIED,
    VIOLATION_REGION_NOT_ALLOWED,
)


@dataclass(frozen=True, slots=True)
class NoDowngrade:
    """Which properties of the entry node a fallback must preserve."""

    capabilities: bool = False
    context_limit: bool = False

    @property
    def enabled(self) -> bool:
        return self.capabilities or self.context_limit


@dataclass(frozen=True, slots=True)
class PolicyCase:
    """One declared request class against one scenario."""

    class_name: str
    scenario_name: str
    request: Request
    scenario: Scenario

    @property
    def name(self) -> str:
        return f"{self.class_name}+{self.scenario_name}"


@dataclass(frozen=True, slots=True)
class Policy:
    """A validated policy document. `cases` is already in run order.

    Empty `allowed_*` means "any". Denied always wins over allowed.
    """

    allowed_providers: tuple[str, ...] = ()
    denied_providers: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    max_fallback_hops: int | None = None
    known_region_tags: tuple[str, ...] = ()
    allowed_regions: tuple[str, ...] = ()
    denied_regions: tuple[str, ...] = ()
    no_downgrade: NoDowngrade = NoDowngrade()
    classes: tuple[tuple[str, Request], ...] = ()
    scenarios: tuple[tuple[str, Scenario], ...] = ()
    cases: tuple[PolicyCase, ...] = ()

    @property
    def class_names(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self.classes)

    @property
    def scenario_names(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self.scenarios)

    @property
    def constrains_regions(self) -> bool:
        return bool(self.allowed_regions or self.denied_regions)


PolicyResult = tuple[Policy | None, list[Diagnostic]]


# --- loading ---------------------------------------------------------------


class _DuplicateKey(Exception):
    """A document that declares one name twice. Ambiguous, so fatal."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that refuses to silently keep the last of two same keys.

    PyYAML's default is last-wins, which would let a second
    `denied_providers:` erase the first one without a word.
    """

    def construct_mapping(self, node: Any, deep: bool = False) -> dict:
        seen: set[str] = set()
        for key_node, _ in node.value:
            if key_node.tag != "tag:yaml.org,2002:str":
                continue  # merge keys and non-string keys are handled below
            if key_node.value in seen:
                raise _DuplicateKey(key_node.value)
            seen.add(key_node.value)
        return super().construct_mapping(node, deep=deep)


def load_policy_text(text: str, *, path: str = "<policy>") -> PolicyResult:
    """Load policy YAML source. Duplicate keys are an `invalid_schema`."""
    try:
        data = yaml.load(text, Loader=_UniqueKeyLoader)
    except _DuplicateKey as exc:
        return None, [
            Diagnostic(
                CODE_INVALID_SCHEMA,
                path,
                f"duplicate key {exc.key!r}: a policy must declare each name once",
            )
        ]
    except yaml.YAMLError as exc:
        return None, [Diagnostic(CODE_PARSE_ERROR, path, yaml_message(exc))]
    return load_policy_document(data, path=path)


def load_policy_path(path: str | Path) -> PolicyResult:
    """Read and validate a policy from disk. Unreadable is a `parse_error`."""
    text, diagnostics = read_text_path(path)
    if text is None:
        return None, diagnostics
    return load_policy_text(text, path=str(path))


def load_policy_document(data: Any, *, path: str = "<policy>") -> PolicyResult:
    """Validate an already-loaded policy document."""
    diagnostics: list[Diagnostic] = []
    if not isinstance(data, dict):
        return None, [
            Diagnostic(
                CODE_INVALID_SCHEMA,
                path,
                f"policy must be a mapping, got {type_name(data)}",
            )
        ]

    allowed_providers = optional_str_list(
        data.get("allowed_providers"), path, "allowed_providers", diagnostics
    )
    denied_providers = optional_str_list(
        data.get("denied_providers"), path, "denied_providers", diagnostics
    )
    required_capabilities = optional_str_list(
        data.get("required_capabilities"), path, "required_capabilities", diagnostics
    )
    known_region_tags = optional_str_list(
        data.get("known_region_tags"), path, "known_region_tags", diagnostics
    )
    allowed_regions = optional_str_list(
        data.get("allowed_regions"), path, "allowed_regions", diagnostics
    )
    denied_regions = optional_str_list(
        data.get("denied_regions"), path, "denied_regions", diagnostics
    )
    _check_vocabulary(known_region_tags, allowed_regions, "allowed_regions", diagnostics)
    _check_vocabulary(known_region_tags, denied_regions, "denied_regions", diagnostics)

    max_hops = optional_int(
        data.get("max_fallback_hops"), path, "max_fallback_hops", diagnostics
    )
    if max_hops is not None and max_hops < 0:
        diagnostics.append(
            Diagnostic(
                CODE_INVALID_SCHEMA, path, "'max_fallback_hops' must not be negative"
            )
        )

    no_downgrade = _load_no_downgrade(data.get("no_downgrade"), diagnostics)
    classes = _load_named(
        data.get("classes"), "classes", load_request_document, diagnostics
    )
    scenarios = _load_named(
        data.get("scenarios"), "scenarios", load_scenario_document, diagnostics
    )
    cases = _load_matrix(data.get("matrix"), classes, scenarios, diagnostics)
    if not classes:  # nothing to run: an empty policy must never pass
        diagnostics.append(
            Diagnostic(
                CODE_INVALID_SCHEMA,
                "classes",
                "'classes' must declare at least one request class",
            )
        )

    if diagnostics:
        return None, sort_diagnostics(diagnostics)
    return (
        Policy(
            allowed_providers=allowed_providers,
            denied_providers=denied_providers,
            required_capabilities=required_capabilities,
            max_fallback_hops=max_hops,
            known_region_tags=known_region_tags,
            allowed_regions=allowed_regions,
            denied_regions=denied_regions,
            no_downgrade=no_downgrade,
            classes=tuple(sorted(classes.items())),
            scenarios=tuple(sorted(scenarios.items())),
            cases=cases,
        ),
        [],
    )


def check_region_vocabulary(graph: RouteGraph, policy: Policy) -> list[Diagnostic]:
    """Region tags on graph nodes, against the policy's declared vocabulary.

    Only the audit runs this: `lint` has no policy, so it keeps ignoring
    an unknown key the way it ignores every other one. With
    `known_region_tags` set, a tag outside it is `invalid_schema` rather
    than a region constraint quietly matching nothing.
    """
    if not policy.known_region_tags:
        return []
    known = ", ".join(policy.known_region_tags)
    diagnostics = [
        Diagnostic(
            CODE_INVALID_SCHEMA,
            f"{route.path}.nodes.{node.id}.region",
            f"region tag {node.region!r} is not in known_region_tags: {known}",
        )
        for route in graph.routes
        for node in route.nodes
        if node.region is not None and node.region not in policy.known_region_tags
    ]
    return sort_diagnostics(diagnostics)


def _check_vocabulary(
    known: tuple[str, ...],
    tags: tuple[str, ...],
    where: str,
    diagnostics: list[Diagnostic],
) -> None:
    if not known:
        return
    listed = ", ".join(known)
    for tag in tags:
        if tag not in known:
            diagnostics.append(
                Diagnostic(
                    CODE_INVALID_SCHEMA,
                    where,
                    f"region tag {tag!r} is not in known_region_tags: {listed}",
                )
            )


def _load_no_downgrade(raw: Any, diagnostics: list[Diagnostic]) -> NoDowngrade:
    if raw is None:
        return NoDowngrade()
    if not isinstance(raw, dict):
        diagnostics.append(
            Diagnostic(
                CODE_INVALID_SCHEMA,
                "no_downgrade",
                f"'no_downgrade' must be a mapping, got {type_name(raw)}",
            )
        )
        return NoDowngrade()
    return NoDowngrade(
        capabilities=bool(
            optional_bool(
                raw.get("capabilities"), "no_downgrade", "capabilities", diagnostics
            )
        ),
        context_limit=bool(
            optional_bool(
                raw.get("context_limit"), "no_downgrade", "context_limit", diagnostics
            )
        ),
    )


def _load_named(
    raw: Any,
    key: str,
    loader: Any,
    diagnostics: list[Diagnostic],
) -> dict[str, Any]:
    """Load a `name: document` block through one of the simulate loaders."""
    loaded: dict[str, Any] = {}
    if raw is None:
        return loaded
    if not isinstance(raw, dict):
        diagnostics.append(
            Diagnostic(
                CODE_INVALID_SCHEMA,
                key,
                f"'{key}' must be a mapping of name to document, got {type_name(raw)}",
            )
        )
        return loaded
    for name, document in raw.items():
        if not isinstance(name, str) or not name:
            diagnostics.append(
                Diagnostic(
                    CODE_INVALID_SCHEMA,
                    key,
                    f"{key[:-1]} name must be a non-empty string",
                )
            )
            continue
        value, problems = loader(document, path=f"{key}.{name}")
        diagnostics.extend(problems)
        if value is not None:
            loaded[name] = value
    return loaded


def _load_matrix(
    raw: Any,
    classes: dict[str, Request],
    scenarios: dict[str, Scenario],
    diagnostics: list[Diagnostic],
) -> tuple[PolicyCase, ...]:
    """The class x scenario pairs to audit. No `matrix:` means all of them."""
    declared = scenarios or {NO_SCENARIO: Scenario()}
    if raw is None:
        return _cross_product(classes, declared)

    known_scenarios = dict(declared)
    known_scenarios.setdefault(NO_SCENARIO, Scenario())
    if not isinstance(raw, list) or not raw:
        diagnostics.append(
            Diagnostic(
                CODE_INVALID_SCHEMA,
                "matrix",
                "'matrix' must be a non-empty list of cases",
            )
        )
        return ()

    cases: dict[str, PolicyCase] = {}
    for position, entry in enumerate(raw):
        where = f"matrix[{position}]"
        if not isinstance(entry, dict):
            diagnostics.append(
                Diagnostic(
                    CODE_INVALID_SCHEMA,
                    where,
                    f"case must be a mapping, got {type_name(entry)}",
                )
            )
            continue
        class_name = _named(entry.get("class"), classes, "class", where, diagnostics)
        scenario_name = (
            NO_SCENARIO
            if entry.get("scenario") is None
            else _named(
                entry.get("scenario"), known_scenarios, "scenario", where, diagnostics
            )
        )
        if class_name is None or scenario_name is None:
            continue
        case = PolicyCase(
            class_name=class_name,
            scenario_name=scenario_name,
            request=classes[class_name],
            scenario=known_scenarios[scenario_name],
        )
        if case.name in cases:
            diagnostics.append(
                Diagnostic(
                    CODE_INVALID_SCHEMA, where, f"duplicate case {case.name!r}"
                )
            )
            continue
        cases[case.name] = case
    return tuple(case for _, case in sorted(cases.items()))


def _cross_product(
    classes: dict[str, Request], scenarios: dict[str, Scenario]
) -> tuple[PolicyCase, ...]:
    return tuple(
        PolicyCase(
            class_name=class_name,
            scenario_name=scenario_name,
            request=classes[class_name],
            scenario=scenarios[scenario_name],
        )
        for class_name in sorted(classes)
        for scenario_name in sorted(scenarios)
    )


def _named(
    value: Any,
    known: dict[str, Any],
    key: str,
    where: str,
    diagnostics: list[Diagnostic],
) -> str | None:
    if not isinstance(value, str) or not value:
        diagnostics.append(
            Diagnostic(
                CODE_INVALID_SCHEMA,
                where,
                f"'{key}' must name a {key} declared in the policy, "
                f"got {type_name(value)}",
            )
        )
        return None
    if value not in known:
        listed = ", ".join(sorted(known)) or "none"
        diagnostics.append(
            Diagnostic(
                CODE_INVALID_SCHEMA,
                where,
                f"{key} {value!r} is not declared in the policy; known: {listed}",
            )
        )
        return None
    return value
