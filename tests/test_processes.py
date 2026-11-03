"""MaBoSSProcess against the real MaBoSS engine (cmaboss). Nothing here mocks cmaboss.

The oracle for the switching tests is analytic, not package output. A node that switches on at rate k and never
switches off is on at time t with probability 1 - exp(-k t). MaBoSS reports the average over its last time window
[a, b], which is 1 - (exp(-k a) - exp(-k b)) / (k (b - a)).
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


def advance(process, state, interval, steps):
    """Apply the Process's deltas the way a map[float] store does, interval by interval."""
    history = []
    for _ in range(steps):
        out = process.update(state, interval)
        state = {
            **state,
            "node_state": {n: state["node_state"][n] + d for n, d in out["node_state"].items()},
            "node_probabilities": {n: state["node_probabilities"][n] + d for n, d in out["node_probabilities"].items()},
        }
        history.append((dict(state["node_state"]), dict(state["node_probabilities"])))
    return history


@pytest.mark.parametrize("k", [0.5, 2.0])
def test_switching_probability_matches_the_analytic_window_average(k):
    process = make(network=SWITCH, parameters={"k": k}, sample_count=20000, time_tick=0.1, seed=3)
    out = process.update(process.initial_state(), 2.0)
    assert out["node_probabilities"]["A"] == pytest.approx(window_average_on(k, 1.9, 2.0), abs=0.01)


def test_parameters_input_overrides_the_config_rate():
    process = make(network=SWITCH, parameters={"k": 0.5}, sample_count=20000, time_tick=0.1, seed=3)
    out = process.update({**process.initial_state(), "parameters": {"k": 4.0}}, 2.0)
    assert out["node_probabilities"]["A"] == pytest.approx(window_average_on(4.0, 1.9, 2.0), abs=0.01)


def test_same_seed_reproduces_and_another_seed_differs():
    def run(seed):
        process = make(network=TOGGLE, parameters={"k_up": 1.0}, initial_state={"A": 1}, sample_count=500,
                       time_tick=0.1, seed=seed)
        return advance(process, process.initial_state(), 1.0, 6)

    assert run(1) == run(1)
    assert run(1) != run(2)


def test_carried_state_is_a_joint_state_of_zeros_and_ones():
    process = make(network=TOGGLE, parameters={"k_up": 1.0}, initial_state={"A": 1}, sample_count=500, seed=5)
    for node_state, probabilities in advance(process, process.initial_state(), 1.0, 8):
        assert set(node_state) == {"A", "B"}
        assert all(v in (0.0, 1.0) for v in node_state.values())
        assert all(0.0 <= p <= 1.0 for p in probabilities.values())


def test_an_absorbing_state_stays_on_once_sampled_on():
    process = make(network=SWITCH, parameters={"k": 3.0}, sample_count=500, time_tick=0.1, seed=11)
    states = [node_state["A"] for node_state, _ in advance(process, process.initial_state(), 1.0, 10)]
    first_on = states.index(1.0)
    assert all(v == 1.0 for v in states[first_on:])


def test_the_interval_starts_from_the_written_node_state():
    """A node_state written by a sibling (here A on) is where the interval starts: the absorbing node stays on."""
    process = make(network=SWITCH, parameters={"k": 0.5}, sample_count=500, time_tick=0.1, seed=2)
    out = process.update({"node_state": {"A": 1.0}, "parameters": {}}, 1.0)
    assert out["node_state"]["A"] == 0.0  # no change: it started on and stayed on
    assert out["node_probabilities"]["A"] == pytest.approx(1.0)  # from the initial 0.0 to 1.0


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"network": ""}, "needs a network"),
        ({"network": SWITCH, "parameters": {"k": 1.0}, "initial_state": {"Z": 1}}, "not in the network"),
        ({"network": SWITCH, "parameters": {"k bad": 1.0}}, "invalid MaBoSS parameter name"),
        # -1, not 0: process-bigraph 1.9.1 treats a zero config value as unset and fills in the default.
        ({"network": SWITCH, "parameters": {"k": 1.0}, "sample_count": -1}, "sample_count"),
    ],
)
def test_invalid_config_is_refused(config, message):
    with pytest.raises(ValueError, match=message):
        make(**config)


def test_a_time_tick_longer_than_the_interval_is_refused():
    process = make(network=SWITCH, parameters={"k": 1.0}, time_tick=0.5)
    with pytest.raises(ValueError, match="exceeds the interval"):
        process.update(process.initial_state(), 0.25)


def _toggle_document(address):
    return {
        "state": {
            "boolean": {
                "_type": "process",
                "address": address,
                "config": {"network": TOGGLE, "parameters": {"k_up": 1.0}, "initial_state": {"A": 1},
                           "sample_count": 200, "seed": 7},
                "interval": 1.0,
                "inputs": {"node_state": ["node_state"], "parameters": ["parameters"]},
                "outputs": {"node_state": ["node_state"], "node_probabilities": ["node_probabilities"]},
            },
            "node_state": {"A": 1.0, "B": 0.0},
            "node_probabilities": {"A": 1.0, "B": 0.0},
            "parameters": {},
            "emitter": {
                "_type": "step",
                "address": "local:RAMEmitter",
                "config": {"emit": {"node_state": "map[float]", "node_probabilities": "map[float]"}},
                "inputs": {"node_state": ["node_state"], "node_probabilities": ["node_probabilities"]},
            },
        }
    }


def _run_and_check(document, core):
    composite = Composite(document, core=core)
    composite.run(3.0)
    rows = gather_emitter_results(composite)[("emitter",)]
    assert len(rows) >= 3
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
