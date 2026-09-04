"""The policy document: what loads, what fails closed, and the case matrix.

A policy that cannot be understood is never an empty policy that passes,
so every malformed document here has to come back as a diagnostic and a
`None` policy.
"""

from route_audit import (
    CODE_INVALID_SCHEMA,
    CODE_PARSE_ERROR,
    load_policy_document,
    load_policy_path,
    load_policy_text,
)

MINIMAL = {"classes": {"chat": {"route": "chat"}}}


def codes(diagnostics) -> list[str]:
    return [item.code for item in diagnostics]


def messages(diagnostics) -> str:
    return "\n".join(item.message for item in diagnostics)


# --- what loads --------------------------------------------------------------


def test_a_minimal_policy_loads_with_the_implicit_none_scenario() -> None:
    policy, diagnostics = load_policy_document(MINIMAL)
    assert diagnostics == []
    assert policy is not None
    assert policy.class_names == ("chat",)
    assert [case.name for case in policy.cases] == ["chat+none"]
    assert policy.cases[0].request.route == "chat"


def test_named_scenarios_replace_the_implicit_one() -> None:
    policy, diagnostics = load_policy_document(
        {
            "classes": {"chat": {"route": "chat"}},
            "scenarios": {"primary-down": {"nodes": {"primary": {"unavailable": True}}}},
        }
    )
    assert diagnostics == []
    assert policy is not None
    assert policy.scenario_names == ("primary-down",)
    assert [case.name for case in policy.cases] == ["chat+primary-down"]


def test_a_policy_without_a_matrix_is_the_cross_product() -> None:
    policy, diagnostics = load_policy_document(
        {
            "classes": {"chat": {"route": "chat"}, "embed": {"route": "embed"}},
            "scenarios": {"healthy": {}, "down": {"nodes": {"primary": {"unavailable": True}}}},
        }
    )
    assert diagnostics == []
    assert policy is not None
    assert len(policy.cases) == 4
    assert [case.name for case in policy.cases] == [
        "chat+down",
        "chat+healthy",
        "embed+down",
        "embed+healthy",
    ]


def test_a_matrix_runs_only_the_pairs_it_names() -> None:
    policy, diagnostics = load_policy_document(
        {
            "classes": {"chat": {"route": "chat"}, "embed": {"route": "embed"}},
            "scenarios": {"healthy": {}, "down": {"nodes": {"primary": {"unavailable": True}}}},
            "matrix": [{"class": "chat", "scenario": "down"}],
        }
    )
    assert diagnostics == []
    assert policy is not None
    assert len(policy.cases) == 1
    assert policy.cases[0].name == "chat+down"


def test_load_policy_path_reads_the_example(tmp_path) -> None:
    path = tmp_path / "policy.yml"
    path.write_text("classes:\n  chat:\n    route: chat\n", encoding="utf-8")
    policy, diagnostics = load_policy_path(path)
    assert diagnostics == []
    assert policy is not None
    assert policy.class_names == ("chat",)


# --- what fails closed -------------------------------------------------------


def test_malformed_yaml_is_a_parse_error() -> None:
    policy, diagnostics = load_policy_text("classes: [\n", path="inline")
    assert policy is None
    assert codes(diagnostics) == [CODE_PARSE_ERROR]


def test_a_list_is_not_a_policy() -> None:
    policy, diagnostics = load_policy_document(["classes"])
    assert policy is None
    assert codes(diagnostics) == [CODE_INVALID_SCHEMA]
    assert "must be a mapping, got list" in messages(diagnostics)


def test_a_string_is_not_a_policy() -> None:
    policy, diagnostics = load_policy_text("just a string\n", path="inline")
    assert policy is None
    assert codes(diagnostics) == [CODE_INVALID_SCHEMA]
    assert "must be a mapping, got string" in messages(diagnostics)


def test_negative_max_fallback_hops_is_a_schema_error() -> None:
    policy, diagnostics = load_policy_document({**MINIMAL, "max_fallback_hops": -1})
    assert policy is None
    assert codes(diagnostics) == [CODE_INVALID_SCHEMA]
    assert "'max_fallback_hops' must not be negative" in messages(diagnostics)


def test_a_region_outside_the_declared_vocabulary_is_a_schema_error() -> None:
    policy, diagnostics = load_policy_document(
        {**MINIMAL, "known_region_tags": ["us"], "allowed_regions": ["eu"]}
    )
    assert policy is None
    assert codes(diagnostics) == [CODE_INVALID_SCHEMA]
    assert "'eu'" in messages(diagnostics)
    assert "known_region_tags" in messages(diagnostics)


def test_a_duplicate_key_is_a_schema_error_not_last_wins() -> None:
    document = """
denied_providers: [together]
denied_providers: [openai]
classes:
  chat:
    route: chat
"""
    policy, diagnostics = load_policy_text(document, path="inline")
    assert policy is None
    assert codes(diagnostics) == [CODE_INVALID_SCHEMA]
    assert "duplicate key 'denied_providers'" in messages(diagnostics)


def test_a_matrix_naming_an_undeclared_class_is_a_schema_error() -> None:
    policy, diagnostics = load_policy_document(
        {**MINIMAL, "matrix": [{"class": "missing"}]}
    )
    assert policy is None
    assert codes(diagnostics) == [CODE_INVALID_SCHEMA]
    assert "class 'missing' is not declared in the policy" in messages(diagnostics)


def test_a_policy_with_no_classes_never_passes() -> None:
    for document in ({}, {"classes": {}}, {"allowed_providers": ["openai"]}):
        policy, diagnostics = load_policy_document(document)
        assert policy is None
        assert codes(diagnostics) == [CODE_INVALID_SCHEMA]
        assert (
            "'classes' must declare at least one request class"
            in messages(diagnostics)
        )


def test_a_class_carrying_prompt_text_is_rejected() -> None:
    policy, diagnostics = load_policy_document(
        {"classes": {"chat": {"route": "chat", "prompt": "hi"}}}
    )
    assert policy is None
    assert CODE_INVALID_SCHEMA in codes(diagnostics)
    assert "must not carry prompt text" in messages(diagnostics)
