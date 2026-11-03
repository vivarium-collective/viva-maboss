"""MaBoSSProcess against the real MaBoSS engine (cmaboss). Nothing here mocks cmaboss.

The oracle for the switching tests is analytic, not package output. A node that switches on at rate k and never
switches off is on at time t with probability 1 - exp(-k t). MaBoSS reports the average over a time window [a, b],
which is 1 - (exp(-k a) - exp(-k b)) / (k (b - a)).
"""
from __future__ import annotations

import importlib.metadata
import math

import pytest
from process_bigraph import Composite, allocate_core
from process_bigraph.emitter import gather_emitter_results

from viva_maboss.processes import MaBoSSProcess

SWITCH = "Node A { logic = 1; rate_up = $k; rate_down = 0; }\n"
TOGGLE = (
    "Node A { logic = B; rate_up = @logic ? $k_up : 0; rate_down = @logic ? 0 : 1; }\n"
    "Node B { logic = !A; rate_up = @logic ? 1 : 0; rate_down = @logic ? 0 : 1; }\n"
)


def make(**config):
    return MaBoSSProcess(config=config, core=allocate_core())


def window_average_on(k: float, a: float, b: float) -> float:
    return 1.0 - (math.exp(-k * a) - math.exp(-k * b)) / (k * (b - a))


def apply(state, out):
    """Apply the Process's deltas the way map[float] and integer stores do."""
    return {
        **state,
        "node_state": {n: state["node_state"].get(n, 0.0) + d for n, d in out["node_state"].items()},
        "node_probabilities": {
            n: state["node_probabilities"].get(n, 0.0) + d for n, d in out["node_probabilities"].items()
        },
        "steps": state.get("steps", 0) + out["steps"],
    }


def advance(process, state, interval, count):
    history = []
    for _ in range(count):
        state = apply(state, process.update(state, interval))
        history.append(state)
    return history


# --- the engine against the analytic oracle -------------------------------------------------------------------------


@pytest.mark.parametrize("k", [0.5, 2.0])
def test_switching_probability_matches_the_analytic_window_average(k):
    process = make(network=SWITCH, parameters={"k": k}, sample_count=20000, windows_per_interval=20, seed=3)
    state = apply(process.initial_state(), process.update(process.initial_state(), 2.0))
    assert state["node_probabilities"]["A"] == pytest.approx(window_average_on(k, 1.9, 2.0), abs=0.01)


def test_the_reported_probability_is_the_window_average_not_the_instant():
    """Wide windows separate the two: over [1.5, 2.0] the window average is 0.62277 and the instant at 2.0 is 0.63212."""
    process = make(network=SWITCH, parameters={"k": 0.5}, sample_count=50000, windows_per_interval=4, seed=3)
    state = apply(process.initial_state(), process.update(process.initial_state(), 2.0))
    assert state["node_probabilities"]["A"] == pytest.approx(window_average_on(0.5, 1.5, 2.0), abs=0.003)
    assert abs(state["node_probabilities"]["A"] - (1.0 - math.exp(-1.0))) > 0.005


@pytest.mark.parametrize(("interval", "windows"), [(0.3, 3), (0.7, 7), (1.0, 3)])
def test_the_last_window_ends_at_the_interval_end(interval, windows):
    """Regression: an interval that was not a whole number of time_ticks read an earlier window."""
    k = 2.0
    process = make(network=SWITCH, parameters={"k": k}, sample_count=50000, windows_per_interval=windows, seed=4)
    state = apply(process.initial_state(), process.update(process.initial_state(), interval))
    expected = window_average_on(k, interval * (windows - 1) / windows, interval)
    assert state["node_probabilities"]["A"] == pytest.approx(expected, abs=0.01)


def test_coupling_across_intervals_tracks_the_exact_probability():
    """Single-trajectory replicates (sample_count=1), each carrying its sampled state across three intervals of 1.0.
    The fraction on at the end is P(on at t=3) = 1 - exp(-0.9) = 0.5934, up to a lag of half a window per interval
    (0.005 per interval at 100 windows). With one window per interval the lag is half the interval, which gives about
    1 - exp(-0.45) = 0.36, so this test separates the two."""
    k, replicates = 0.3, 2000
    on = 0
    for seed in range(replicates):
        process = make(network=SWITCH, parameters={"k": k}, sample_count=1, windows_per_interval=100, seed=seed)
        on += advance(process, process.initial_state(), 1.0, 3)[-1]["node_state"]["A"]
    assert on / replicates == pytest.approx(1.0 - math.exp(-k * 3.0), abs=0.035)


# --- inputs, stores and seeds ---------------------------------------------------------------------------------------


def test_parameters_input_overrides_the_config_rate():
    process = make(network=SWITCH, parameters={"k": 0.5}, sample_count=20000, windows_per_interval=20, seed=3)
    initial = process.initial_state()
    state = apply(initial, process.update({**initial, "parameters": {"k": 4.0}}, 2.0))
    assert state["node_probabilities"]["A"] == pytest.approx(window_average_on(4.0, 1.9, 2.0), abs=0.01)


def test_outputs_are_deltas_against_the_stores_whatever_they_held():
    """Regression: deltas were taken against the Process's own memory, so a store seeded differently (A at 1.0 here,
    with the config starting A at 0) drifted past 1."""
    process = make(network=SWITCH, parameters={"k": 0.5}, sample_count=2000, windows_per_interval=20, seed=3)
    state = {"node_state": {"A": 0.7}, "node_probabilities": {"A": 1.0}, "parameters": {}, "steps": 0}
    for state in advance(process, state, 1.0, 4):
        assert 0.0 <= state["node_probabilities"]["A"] <= 1.0 + 1e-9
        assert state["node_state"]["A"] in (0.0, 1.0)


def test_same_seed_reproduces_and_another_seed_differs():
    def run(seed):
        process = make(network=TOGGLE, parameters={"k_up": 1.0}, initial_state={"A": 1}, sample_count=500, seed=seed)
        return advance(process, process.initial_state(), 1.0, 6)

    assert run(1) == run(1)
    assert run(1) != run(2)


def test_consecutive_seeds_do_not_share_streams():
    """Regression: seed + interval index made seed=1 at interval 1 identical to seed=2 at interval 0."""
    def interval_result(seed, step):
        process = make(network=TOGGLE, parameters={"k_up": 1.0}, initial_state={"A": 1}, sample_count=500, seed=seed)
        return process.update({**process.initial_state(), "steps": step}, 1.0)

    assert interval_result(1, 1) != interval_result(2, 0)


def test_a_resumed_run_continues_the_same_streams():
    """Regression: the interval index lived on the instance, so a run resumed with a new instance diverged."""
    config = dict(network=TOGGLE, parameters={"k_up": 1.0}, initial_state={"A": 1}, sample_count=500, seed=9)
    whole = advance(make(**config), make(**config).initial_state(), 1.0, 6)
    first = advance(make(**config), make(**config).initial_state(), 1.0, 3)
    resumed = advance(make(**config), first[-1], 1.0, 3)
    assert first + resumed == whole


def test_an_absorbing_state_stays_on_once_sampled_on():
    process = make(network=SWITCH, parameters={"k": 3.0}, sample_count=500, seed=11)
    states = [s["node_state"]["A"] for s in advance(process, process.initial_state(), 1.0, 10)]
    first_on = states.index(1.0)
    assert all(v == 1.0 for v in states[first_on:])


def test_the_interval_starts_from_the_written_node_state():
    """A node_state written by a sibling (here A on) is where the interval starts: the absorbing node stays on."""
    process = make(network=SWITCH, parameters={"k": 0.5}, sample_count=500, seed=2)
    state = {"node_state": {"A": 1.0}, "node_probabilities": {"A": 0.0}, "parameters": {}, "steps": 0}
    state = apply(state, process.update(state, 1.0))
    assert state["node_state"]["A"] == 1.0
    assert state["node_probabilities"]["A"] == pytest.approx(1.0)


def test_a_network_beyond_64_nodes_runs():
    """cmaboss's default build caps at 64 nodes; the Process picks the smallest build that fits."""
    network = "".join(f"Node N{i} {{ logic = 1; rate_up = 1; rate_down = 0; }}\n" for i in range(65))
    process = make(network=network, sample_count=10, seed=1)
    out = process.update(process.initial_state(), 1.0)
    assert len(out["node_probabilities"]) == 65


# --- refusals -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"network": ""}, "needs a network"),
        ({"network": SWITCH, "parameters": {"k": 1.0}, "initial_state": {"Z": 1}}, "not in the network"),
        ({"network": SWITCH, "parameters": {"k bad": 1.0}}, "invalid MaBoSS parameter name"),
        ({"network": SWITCH, "parameters": {"k": -1.0}}, "non-negative"),
        ({"network": SWITCH}, "needs a default"),
        # -1, not 0: process-bigraph 1.9.1 treats a zero config value as unset and fills in the default.
        ({"network": SWITCH, "parameters": {"k": 1.0}, "sample_count": -1}, "sample_count"),
        ({"network": SWITCH, "parameters": {"k": 1.0}, "windows_per_interval": -1}, "windows_per_interval"),
    ],
)
def test_invalid_config_is_refused(config, message):
    with pytest.raises(ValueError, match=message):
        make(**config)


@pytest.mark.parametrize("rate", [-1.0, -0.001, float("nan"), float("inf")])
def test_an_invalid_input_rate_is_refused_before_the_engine(rate):
    """Regression: a negative rate reached MaBoSS, which killed the process (SIGBUS), uncatchable."""
    process = make(network=SWITCH, parameters={"k": 1.0}, sample_count=10)
    with pytest.raises(ValueError, match="non-negative"):
        process.update({**process.initial_state(), "parameters": {"k": rate}}, 1.0)


# --- composites -----------------------------------------------------------------------------------------------------


def _toggle_document(address):
    return {
        "state": {
            "boolean": {
                "_type": "process",
                "address": address,
                "config": {"network": TOGGLE, "parameters": {"k_up": 1.0}, "initial_state": {"A": 1},
                           "sample_count": 200, "seed": 7},
                "interval": 1.0,
                "inputs": {"node_state": ["node_state"], "node_probabilities": ["node_probabilities"],
                           "parameters": ["parameters"], "steps": ["steps"]},
                "outputs": {"node_state": ["node_state"], "node_probabilities": ["node_probabilities"],
                            "steps": ["steps"]},
            },
            "node_state": {"A": 1.0, "B": 0.0},
            "node_probabilities": {"A": 1.0, "B": 0.0},
            "parameters": {},
            "steps": 0,
            "emitter": {
                "_type": "step",
                "address": "local:RAMEmitter",
                "config": {"emit": {"node_state": "map[float]", "node_probabilities": "map[float]",
                                    "steps": "integer"}},
                "inputs": {"node_state": ["node_state"], "node_probabilities": ["node_probabilities"],
                           "steps": ["steps"]},
            },
        }
    }


def _run_and_check(document, core):
    composite = Composite(document, core=core)
    composite.run(3.0)
    rows = gather_emitter_results(composite)[("emitter",)]
    assert len(rows) >= 3
    assert rows[-1]["steps"] == 3
    for row in rows:
        assert set(row["node_state"]) == {"A", "B"}
        assert all(v in (0.0, 1.0) for v in row["node_state"].values())
        assert all(-1e-9 <= p <= 1.0 + 1e-9 for p in row["node_probabilities"].values())


def _discoverable() -> bool:
    """process-bigraph resolves local: addresses from installed distributions; an editable hatchling install is not
    mapped back to its package, so it is not discoverable (see viva_maboss/core.py)."""
    return bool(importlib.metadata.packages_distributions().get("viva_maboss"))


@pytest.mark.skipif(not _discoverable(), reason="needs a regular (non-editable) install, as compose-api installs it")
def test_runs_in_a_composite_on_a_bare_core_by_module_address():
    """The core compose-api's runner builds (allocate_core, no build_core) and the address form its registry needs."""
    _run_and_check(_toggle_document("local:viva_maboss.processes.MaBoSSProcess"), allocate_core())


def test_runs_in_a_composite_on_the_workspace_core():
    """The workspace's build_core() registers its own classes, so this runs under an editable install too."""
    from viva_maboss.core import build_core

    _run_and_check(_toggle_document("local:MaBoSSProcess"), build_core())


def test_toggle_switch_generator_is_discoverable_and_runs():
    from process_bigraph.composite_generator import discover_generators

    from viva_maboss.composites import toggle_switch
    from viva_maboss.core import build_core

    assert any(gid.endswith(".maboss_toggle_switch") for gid in discover_generators())
    core = build_core()
    composite = Composite({"state": toggle_switch(core, sample_count=200, seed=3)}, core=core)
    composite.run(3.0)
    rows = gather_emitter_results(composite)[("emitter",)]
    assert len(rows) >= 3
