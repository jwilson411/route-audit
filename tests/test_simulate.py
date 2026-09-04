"""Offline simulation: outage fallback, capability and context rejection,
exhaustion, replay stability, and the batch matrix.

Every case here is decided from three inert documents. Nothing opens a
socket, and no request fixture carries prompt text.
"""

import json

import pytest

from route_audit.batch import (
    format_batch_json,
    load_batch_document,
    load_batch_path,
    run_batch,
)
from route_audit.cli import main
from route_audit.parser import parse_path
from route_audit.simulate import (
    FAILURE_EXHAUSTION,
    REASON_CAPABILITY_MISMATCH,
    REASON_CONTEXT_OVERFLOW,
    REASON_RATE_LIMITED,
    REASON_UNAVAILABLE,
    Scenario,
    format_simulation_json,
    load_request_path,
    load_scenario_path,
    simulate,
)


@pytest.fixture
def graph(fixture_path):
    parsed, diagnostics = parse_path(fixture_path("valid.yml"))
    assert diagnostics == []
    assert parsed is not None
    return parsed


@pytest.fixture
def request_of(fixture_path):
    def _load(name: str):
        loaded, diagnostics = load_request_path(fixture_path(f"simulate/{name}"))
        assert diagnostics == []
        assert loaded is not None
        return loaded

    return _load


@pytest.fixture
def scenario_of(fixture_path):
    def _load(name: str):
        loaded, diagnostics = load_scenario_path(fixture_path(f"simulate/{name}"))
        assert diagnostics == []
        assert loaded is not None
        return loaded

    return _load


def run(capsys, argv) -> tuple[int, str, str]:
    code = main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def reasons(result) -> list[tuple[str, str]]:
    return [(item.node_id, item.reason) for item in result.attempted]


# --- the three ways a node is passed over -----------------------------------


def test_outage_falls_through_to_the_backup(graph, request_of, scenario_of) -> None:
    result = simulate(
        graph, request_of("request_tools.yml"), scenario_of("scenario_primary_down.yml")
    )
    assert result.selected == "backup"
    assert result.failure is None
    assert result.ok
    assert reasons(result) == [("primary", REASON_UNAVAILABLE)]


def test_capability_mismatch_falls_through_to_the_backup(
    graph, request_of, scenario_of
) -> None:
    result = simulate(
        graph, request_of("request_tools.yml"), scenario_of("scenario_no_tools.yml")
    )
    assert result.selected == "backup"
    assert reasons(result) == [("primary", REASON_CAPABILITY_MISMATCH)]
    assert "tools" in result.attempted[0].detail


def test_context_overflow_is_decided_from_the_declared_limits(
    graph, request_of
) -> None:
    result = simulate(graph, request_of("request_large.yml"), Scenario())
    assert result.selected == "backup"  # 150000 fits the backup's 200000
    assert reasons(result) == [("primary", REASON_CONTEXT_OVERFLOW)]
    assert "128000" in result.attempted[0].detail


def test_rate_limited_is_distinct_from_unavailable(
    graph, request_of, scenario_of
) -> None:
    limited = simulate(
        graph, request_of("request_tools.yml"), scenario_of("scenario_rate_limited.yml")
    )
    down = simulate(
        graph, request_of("request_tools.yml"), scenario_of("scenario_primary_down.yml")
    )
    assert reasons(limited) == [("primary", REASON_RATE_LIMITED)]
    assert reasons(down) == [("primary", REASON_UNAVAILABLE)]
    assert REASON_RATE_LIMITED != REASON_UNAVAILABLE
    assert limited.selected == down.selected == "backup"


def test_every_node_down_is_exhaustion(graph, request_of, scenario_of) -> None:
    result = simulate(
        graph, request_of("request_chat.yml"), scenario_of("scenario_all_down.yml")
    )
    assert result.selected is None
    assert not result.ok
    assert result.failure == FAILURE_EXHAUSTION
    assert result.attempted_nodes == ("primary", "backup", "local")
    assert reasons(result) == [
        ("primary", REASON_UNAVAILABLE),
        ("backup", REASON_RATE_LIMITED),  # reached by its alias, chat-backup
        ("local", REASON_CONTEXT_OVERFLOW),
    ]


# --- replay -----------------------------------------------------------------


def test_the_same_walk_replays_identically(graph, request_of, scenario_of) -> None:
    request = request_of("request_tools.yml")
    scenario = scenario_of("scenario_primary_down.yml")
    first = simulate(graph, request, scenario)
    second = simulate(graph, request, scenario)
    assert first == second
    assert first.to_dict() == second.to_dict()
    assert format_simulation_json(
        first, graph="valid.yml", request="request_tools.yml"
    ) == format_simulation_json(
        second, graph="valid.yml", request="request_tools.yml"
    )


def test_a_seeded_batch_replays_identically(graph, fixture_path) -> None:
    plan, diagnostics = load_batch_path(fixture_path("simulate/batch.yml"))
    assert diagnostics == []
    assert plan is not None
    first = run_batch(graph, plan)
    second = run_batch(graph, plan)
    assert first.to_dict() == second.to_dict()
    assert format_batch_json(
        first, graph="valid.yml", batch="batch.yml"
    ) == format_batch_json(second, graph="valid.yml", batch="batch.yml")
    assert first.seed == plan.seed == 20260904


def test_a_batch_without_a_matrix_is_the_cross_product() -> None:
    plan, diagnostics = load_batch_document(
        {
            "seed": 7,
            "requests": {"chat": {"route": "chat"}, "embed": {"route": "embed"}},
            "scenarios": {"down": {"nodes": {"primary": {"unavailable": True}}}},
        }
    )
    assert diagnostics == []
    assert plan is not None
    assert [case.name for case in plan.cases] == [
        "chat+down",
        "chat+none",
        "embed+down",
        "embed+none",
    ]


# --- a request describes shape, not payload ---------------------------------


def test_a_request_carrying_prompt_text_is_rejected(fixture_path) -> None:
    loaded, diagnostics = load_request_path(fixture_path("simulate/request_prompt.yml"))
    assert loaded is None
    assert [item.code for item in diagnostics] == ["invalid_schema"]
    assert "must not carry prompt text" in diagnostics[0].message


def test_cli_rejects_a_request_carrying_prompt_text(capsys, fixture_path) -> None:
    code, out, _ = run(
        capsys,
        [
            "simulate",
            str(fixture_path("valid.yml")),
            "--request",
            str(fixture_path("simulate/request_prompt.yml")),
        ],
    )
    assert code == 2
    assert out.startswith("invalid_schema ")


# --- CLI --------------------------------------------------------------------


def test_cli_simulate_prints_the_selected_node(capsys, fixture_path) -> None:
    code, out, _ = run(
        capsys,
        [
            "simulate",
            str(fixture_path("valid.yml")),
            "--request",
            str(fixture_path("simulate/request_tools.yml")),
            "--scenario",
            str(fixture_path("simulate/scenario_primary_down.yml")),
        ],
    )
    assert code == 0
    assert "selected chat.backup" in out
    assert "rejected chat.primary: unavailable" in out


def test_cli_batch_json_reports_seed_and_coverage(capsys, fixture_path) -> None:
    argv = [
        "simulate",
        str(fixture_path("valid.yml")),
        "--batch",
        str(fixture_path("simulate/batch.yml")),
        "--json",
    ]
    code, out, _ = run(capsys, argv)
    assert code == 1  # one case in the matrix takes the whole chat route down
    payload = json.loads(out)
    assert payload["seed"] == 20260904
    routes = {item["route"]: item for item in payload["coverage"]["routes"]}
    assert set(routes) == {"chat", "embed"}
    assert routes["chat"]["cases"] == 4
    assert routes["chat"]["selections"] == 3
    assert routes["chat"]["attempted"] == ["backup", "local", "primary"]
    assert routes["embed"]["selected"] == ["only"]
    assert payload["coverage"]["routes_without_selection"] == []

    _, again, _ = run(capsys, argv)
    assert out == again
