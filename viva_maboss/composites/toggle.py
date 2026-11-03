"""A two-node MaBoSS toggle switch: A is activated by B, B is repressed by A."""
from __future__ import annotations

from process_bigraph.composite_generator import composite_generator

TOGGLE_NETWORK = (
    "Node A { logic = B; rate_up = @logic ? $k_up : 0; rate_down = @logic ? 0 : 1; }\n"
    "Node B { logic = !A; rate_up = @logic ? 1 : 0; rate_down = @logic ? 0 : 1; }\n"
)


@composite_generator(
    name="maboss_toggle_switch",
    description="A two-node MaBoSS Boolean network (A activated by B, B repressed by A), advanced one interval at a "
    "time from one sampled joint state; emits each node's state and ensemble on-probability.",
    parameters={
        "k_up": {"type": "float", "default": 1.0, "description": "Rate at which A switches on when B is on"},
        "sample_count": {"type": "integer", "default": 1000, "description": "Stochastic trajectories per interval"},
        "seed": {"type": "integer", "default": 1, "description": "Base seed (MaBoSS and the joint-state sampler)"},
        "interval": {"type": "float", "default": 1.0, "description": "Coupling interval"},
    },
)
def toggle_switch(core=None, *, k_up=1.0, sample_count=1000, seed=1, interval=1.0):
    return {
        "boolean": {
            "_type": "process",
            "address": "local:MaBoSSProcess",
            "config": {
                "network": TOGGLE_NETWORK,
                "parameters": {"k_up": float(k_up)},
                "initial_state": {"A": 1},
                "sample_count": int(sample_count),
                "seed": int(seed),
            },
            "interval": float(interval),
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
