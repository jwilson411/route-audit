"""The policy audit: judging the simulated path, not the graph.

A node the walk rejected is never a violation - that is the graph
working. A node the walk happily selected can be one, which is the whole
point: the fallback that is up, healthy, and capable is exactly the one a
policy exists to forbid.
"""

from route_audit import (
    CODE_INVALID_SCHEMA,
    VIOLATION_CAPABILITY_DOWNGRADE,
    VIOLATION_CONTEXT_DOWNGRADE,
    VIOLATION_MAX_HOPS_EXCEEDED,
    VIOLATION_PROVIDER_DENIED,
    VIOLATION_REGION_DENIED,
    VIOLATION_REGION_NOT_ALLOWED,
    FAILURE_EXHAUSTION,
    check_region_vocabulary,
    load_policy_document,
    parse_document,
    run_audit,
    simulate,
)

PRIMARY_DOWN = {"nodes": {"primary": {"unavailable": True}}}


def node(
    node_id: str,
    provider: str,
    *,
    capabilities=("chat", "tools"),
    context_limit=None,
    region=None,
) -> dict:
    declared = {
        "id": node_id,
        "provider": provider,
        "model": f"{provider}-model",
        "capabilities": list(capabilities),
    }
    if context_limit is not None:
        declared["context_limit"] = context_limit
    if region is not None:
        declared["region"] = region
    return declared


def graph_of(document: dict):
    graph, diagnostics = parse_document(document)
    assert diagnostics == []
    assert graph is not None
    return graph


def chain(*nodes: dict):
    """A one-route `chat` graph whose nodes fall back in declaration order."""
    ids = [item["id"] for item in nodes]
    return graph_of(
        {
            "routes": [
                {
                    "id": "chat",
                    "entry": ids[0],
                    "nodes": list(nodes),
                    "fallbacks": [
                        {"from": source, "to": target}
                        for source, target in zip(ids, ids[1:])
                    ],
                }
            ]
        }
    )


def policy_of(document: dict):
    policy, diagnostics = load_policy_document(document)
    assert diagnostics == []
    assert policy is not None
    return policy


def audit(graph_document, policy_document):
    return run_audit(graph_document, policy_of(policy_document))


def only_case(report):
    assert len(report.cases) == 1
    return report.cases[0]


# --- denied wins over allowed ------------------------------------------------


def test_a_denied_provider_beats_the_same_provider_being_allowed() -> None:
    graph = chain(
        node("primary", "openai", context_limit=128000),
        node("backup", "together", context_limit=128000),
    )
    policy = policy_of(
        {
            "allowed_providers": ["openai", "together"],
            "denied_providers": ["together"],
            "classes": {"tools": {"route": "chat", "required_capabilities": ["chat", "tools"]}},
            "scenarios": {"primary-down": PRIMARY_DOWN},
        }
    )
    report = run_audit(graph, policy)
    case = only_case(report)

    # The walk is happy: the backup is up, allowed by the request, and capable.
    selected = simulate(graph, policy.cases[0].request, policy.cases[0].scenario)
    assert selected.selected == "backup"
    assert selected.ok

    assert case.result.selected == "backup"
    assert case.codes == (VIOLATION_PROVIDER_DENIED,)
    assert not report.ok
    assert "provider 'together' is denied" in report.violations[0].message


def test_a_denied_region_beats_the_same_region_being_allowed() -> None:
    graph = chain(node("primary", "openai", region="apac"))
    report = audit(
        graph,
        {
            "allowed_regions": ["us", "apac"],
            "denied_regions": ["apac"],
            "classes": {"chat": {"route": "chat"}},
        },
    )
    case = only_case(report)
    assert case.result.selected == "primary"
    assert case.codes == (VIOLATION_REGION_DENIED,)
    assert "region 'apac' is denied" in report.violations[0].message


# --- downgrades, measured against the entry node ------------------------------


def test_a_capability_lost_on_the_way_is_a_downgrade_even_when_served() -> None:
    graph = chain(
        node("primary", "openai", capabilities=("chat", "tools", "json")),
        node("backup", "anthropic", capabilities=("chat", "tools")),
    )
    report = audit(
        graph,
        {
            "no_downgrade": {"capabilities": True},
            "classes": {"tools": {"route": "chat", "required_capabilities": ["chat", "tools"]}},
            "scenarios": {"primary-down": PRIMARY_DOWN},
        },
    )
    case = only_case(report)
    assert case.result.selected == "backup"  # the request itself is satisfied
    assert case.codes == (VIOLATION_CAPABILITY_DOWNGRADE,)
    assert "json" in report.violations[0].message


def test_a_smaller_context_window_on_the_way_is_a_downgrade() -> None:
    graph = chain(
        node("primary", "openai", context_limit=128000),
        node("backup", "anthropic", context_limit=8000),
    )
    report = audit(
        graph,
        {
            "no_downgrade": {"context_limit": True},
            "classes": {"chat": {"route": "chat"}},
            "scenarios": {"primary-down": PRIMARY_DOWN},
        },
    )
    case = only_case(report)
    assert case.result.selected == "backup"
    assert case.codes == (VIOLATION_CONTEXT_DOWNGRADE,)
    assert "8000" in report.violations[0].message


# --- hops --------------------------------------------------------------------


def hops_report(cap: int):
    graph = chain(node("primary", "openai"), node("backup", "anthropic"))
    return audit(
        graph,
        {
            "max_fallback_hops": cap,
            "classes": {"chat": {"route": "chat"}},
            "scenarios": {"primary-down": PRIMARY_DOWN},
        },
    )


def test_one_hop_over_a_cap_of_zero_is_a_violation() -> None:
    report = hops_report(0)
    case = only_case(report)
    assert case.result.selected == "backup"
    assert case.hops == 1
    assert case.codes == (VIOLATION_MAX_HOPS_EXCEEDED,)


def test_one_hop_under_a_cap_of_one_is_not_a_violation() -> None:
    report = hops_report(1)
    case = only_case(report)
    assert case.hops == 1
    assert case.codes == ()
    assert report.ok


# --- regions -----------------------------------------------------------------


def test_a_node_with_no_region_fails_closed_when_regions_are_constrained() -> None:
    graph = chain(node("primary", "openai"))
    report = audit(
        graph,
        {"allowed_regions": ["us"], "classes": {"chat": {"route": "chat"}}},
    )
    case = only_case(report)
    assert case.codes == (VIOLATION_REGION_NOT_ALLOWED,)
    assert "declares no region" in report.violations[0].message


def test_a_region_tag_outside_the_declared_vocabulary_is_a_schema_error() -> None:
    graph = chain(node("primary", "openai", region="apac"))
    policy = policy_of(
        {"known_region_tags": ["us"], "classes": {"chat": {"route": "chat"}}}
    )
    diagnostics = check_region_vocabulary(graph, policy)
    assert [item.code for item in diagnostics] == [CODE_INVALID_SCHEMA]
    assert diagnostics[0].path == "routes.chat.nodes.primary.region"
    assert "'apac' is not in known_region_tags: us" in diagnostics[0].message


def test_no_declared_vocabulary_means_no_region_schema_check() -> None:
    graph = chain(node("primary", "openai", region="apac"))
    policy = policy_of({"classes": {"chat": {"route": "chat"}}})
    assert check_region_vocabulary(graph, policy) == []


# --- the smallest violating path ---------------------------------------------


def test_the_smallest_violating_path_wins() -> None:
    graph = graph_of(
        {
            "routes": [
                {
                    "id": "chat",
                    "entry": "primary",
                    "nodes": [
                        node("primary", "openai", capabilities=("chat", "tools")),
                        node("backup", "together", capabilities=("chat", "json")),
                    ],
                    "fallbacks": [{"from": "primary", "to": "backup"}],
                },
                {
                    "id": "solo",
                    "nodes": [node("only", "together", capabilities=("chat", "json"))],
                },
            ]
        }
    )
    report = audit(
        graph,
        {
            "denied_providers": ["together"],
            "classes": {
                "long": {"route": "chat", "required_capabilities": ["json"]},
                "short": {"route": "solo", "required_capabilities": ["json"]},
            },
        },
    )
    assert len(report.cases) == 2
    assert not report.ok
    smallest = report.smallest
    assert smallest is not None
    assert smallest.class_name == "short"
    assert smallest.path == ("only",)
    assert [case.path for case in report.cases if case.class_name == "long"] == [
        ("primary", "backup")
    ]


# --- an unservable request is a coverage hole, not a governance failure --------


def test_exhaustion_alone_does_not_fail_the_audit() -> None:
    graph = chain(node("primary", "openai"), node("backup", "anthropic"))
    report = audit(
        graph,
        {
            "denied_providers": ["together"],
            "classes": {"chat": {"route": "chat"}},
            "scenarios": {
                "all-down": {
                    "nodes": {
                        "primary": {"unavailable": True},
                        "backup": {"unavailable": True},
                    }
                }
            },
        },
    )
    case = only_case(report)
    assert case.result.selected is None
    assert case.result.failure == FAILURE_EXHAUSTION
    assert case.violations == ()
    assert report.ok
    assert report.smallest is None
