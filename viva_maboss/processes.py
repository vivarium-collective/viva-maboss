"""MaBoSS (stochastic Boolean network simulation) as a process-bigraph Process.

Each interval drives the real MaBoSS engine (``cmaboss``): it writes the network (``.bnd``) and a configuration (``.cfg``)
for that interval, runs an ensemble of ``sample_count`` stochastic trajectories, and reads back:

- ``node_probabilities``: the ensemble probability that each node is on, over the interval's last time window;
- ``node_state``: ONE joint state, sampled from the ensemble's joint-state distribution over that last window.

Coupling between intervals. MaBoSS can start a run from a per-node initial distribution (``<node>.istate``), but not
from a joint distribution, so carrying the ensemble's joint distribution forward is not possible. Instead the Process
carries one sampled joint state and starts the next interval from exactly that state (each node's ``istate`` set to 0
or 1). Over many trajectories this is exact in distribution. The sample is drawn from the distribution averaged over
the interval's last ``time_tick`` window (MaBoSS reports window averages, not instants), so a smaller ``time_tick``
gives a carried state closer to the end of the interval.

Reproducibility. MaBoSS's ``seed_pseudorandom`` and the sampler's generator are both derived from ``seed`` and the
interval's index, so a run is reproducible, and successive intervals do not reuse one stream.

Parameters. ``$name`` parameters in the network are set in each interval's ``.cfg``, from ``config['parameters']``
overridden by the ``parameters`` input, so an upstream process (a kinetic layer, for example) can drive rates.
``cmaboss.MaBoSSSim.update_parameters`` was not used: on cmaboss 1.0.0b34 it accepted a parameter change and the
simulation result did not change.
"""
from __future__ import annotations

import math
import re
import tempfile
from pathlib import Path

import numpy as np
from process_bigraph import Process

_PARAMETER_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_NO_NODE_ON = "<nil>"
_JOINT_SEPARATOR = " -- "


def _on_nodes(label: str) -> set[str]:
    """The nodes that are on in a MaBoSS joint-state label: '<nil>' is none, 'A -- B' is A and B."""
    return set() if label == _NO_NODE_ON else set(label.split(_JOINT_SEPARATOR))


class MaBoSSProcess(Process):
    """A MaBoSS Boolean network, advanced one interval at a time from one carried joint state.

    Config:
        network: the network in MaBoSS ``.bnd`` syntax.
        parameters: values for the network's ``$name`` parameters (names without ``$``).
        initial_state: each node's starting value, 0 or 1 (nodes not listed start at 0).
        sample_count: stochastic trajectories per interval.
        time_tick: MaBoSS's time window; it must not exceed the interval.
        seed: the base seed for MaBoSS and the joint-state sampler.

    Inputs:
        node_state: the joint state the next interval starts from (a value >= 0.5 is on). A sibling process may write
            it, for example to force a switch.
        parameters: overrides for the network's ``$name`` parameters, so an upstream process can drive rates.

    Outputs (deltas against the previous values, so ``map[float]`` stores accumulate to the new values):
        node_state: the sampled joint state, 0.0 or 1.0 per node.
        node_probabilities: the ensemble probability that each node is on, over the interval's last window.
    """

    config_schema = {
        "network": "string",
        "parameters": {"_type": "map[float]", "_default": {}},
        "initial_state": {"_type": "map[float]", "_default": {}},
        "sample_count": {"_type": "integer", "_default": 1000},
        "time_tick": {"_type": "float", "_default": 0.1},
        "seed": {"_type": "integer", "_default": 1},
    }

    def initialize(self, config):
        network = config.get("network") or ""
        if not network.strip():
            raise ValueError("MaBoSSProcess needs a network (MaBoSS .bnd text) in config['network']")
        if int(config.get("sample_count") or 0) < 1:
            raise ValueError("MaBoSSProcess needs sample_count >= 1")
        if not float(config.get("time_tick") or 0.0) > 0.0:
            raise ValueError("MaBoSSProcess needs time_tick > 0")
        self._parameters = self._checked_parameters(config.get("parameters") or {})
        self._workdir = tempfile.TemporaryDirectory(prefix="viva-maboss-")
        self._bnd = Path(self._workdir.name) / "network.bnd"
        self._bnd.write_text(network)
        self._nodes = self._read_nodes()
        unknown = set(config.get("initial_state") or {}) - set(self._nodes)
        if unknown:
            raise ValueError(f"initial_state names nodes not in the network: {sorted(unknown)}")
        self._intervals = 0
        self._probabilities = {node: self._initial_value(node) for node in self._nodes}

    def inputs(self):
        return {"node_state": "map[float]", "parameters": "map[float]"}

    def outputs(self):
        return {"node_state": "map[float]", "node_probabilities": "map[float]"}

    def initial_state(self):
        state = {node: self._initial_value(node) for node in self._nodes}
        return {"node_state": state, "node_probabilities": dict(state)}

    def update(self, state, interval):
        interval = float(interval)
        time_tick = float(self.config["time_tick"])
        if time_tick > interval:
            raise ValueError(f"time_tick ({time_tick}) exceeds the interval ({interval})")
        current = {node: 1.0 if float((state.get("node_state") or {}).get(node, 0.0)) >= 0.5 else 0.0
                   for node in self._nodes}
        parameters = {**self._parameters, **self._checked_parameters(state.get("parameters") or {})}

        seed = int(self.config["seed"]) + self._intervals
        self._intervals += 1
        result = self._run(current, parameters, interval, time_tick, seed)

        node_probs, _, node_labels, _ = result.get_last_nodes_probtraj()
        probabilities = dict.fromkeys(self._nodes, 0.0)
        probabilities.update({str(node): float(p) for node, p in zip(node_labels, node_probs[0])})

        joint_probs, _, joint_labels, _ = result.get_last_probtraj()
        weights = np.clip(np.asarray(joint_probs[0], dtype=float), 0.0, None)
        if not weights.sum() > 0.0:
            raise RuntimeError("MaBoSS returned an empty joint-state distribution for the interval")
        chosen = np.random.default_rng(seed).choice(len(joint_labels), p=weights / weights.sum())
        on = _on_nodes(str(joint_labels[chosen]))
        sampled = {node: 1.0 if node in on else 0.0 for node in self._nodes}

        probability_deltas = {node: probabilities[node] - self._probabilities[node] for node in self._nodes}
        self._probabilities = probabilities
        return {
            "node_state": {node: sampled[node] - current[node] for node in self._nodes},
            "node_probabilities": probability_deltas,
        }

    def _initial_value(self, node: str) -> float:
        return 1.0 if float((self.config.get("initial_state") or {}).get(node, 0.0)) >= 0.5 else 0.0

    @staticmethod
    def _checked_parameters(parameters) -> dict[str, float]:
        checked = {}
        for name, value in parameters.items():
            bare = str(name).removeprefix("$")
            if not _PARAMETER_NAME.match(bare):
                raise ValueError(f"invalid MaBoSS parameter name: {name!r}")
            value = float(value)
            if not math.isfinite(value):
                raise ValueError(f"MaBoSS parameter {name!r} is not finite: {value}")
            checked[bare] = value
        return checked

    def _read_nodes(self) -> list[str]:
        """The network's nodes, as MaBoSS parses them (a minimal config, so only the network is read)."""
        import cmaboss

        cfg = Path(self._workdir.name) / "nodes.cfg"
        lines = [f"${name} = {value!r};" for name, value in self._parameters.items()]
        cfg.write_text("\n".join([*lines, "max_time = 1;", "sample_count = 1;", ""]))
        return sorted(str(node) for node in cmaboss.MaBoSSSim(str(self._bnd), str(cfg)).get_nodes())

    def _run(self, current, parameters, interval, time_tick, seed):
        import cmaboss

        lines = [f"${name} = {value!r};" for name, value in parameters.items()]
        lines += [
            f"max_time = {interval!r};",
            f"time_tick = {time_tick!r};",
            f"sample_count = {int(self.config['sample_count'])};",
            f"seed_pseudorandom = {seed};",
        ]
        lines += [f"{node}.istate = {int(current[node])};" for node in self._nodes]
        cfg = Path(self._workdir.name) / "interval.cfg"
        cfg.write_text("\n".join([*lines, ""]))
        return cmaboss.MaBoSSSim(str(self._bnd), str(cfg)).run()
