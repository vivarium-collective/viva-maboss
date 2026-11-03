"""MaBoSS (stochastic Boolean network simulation) as a process-bigraph Process.

Each interval drives the real MaBoSS engine (``cmaboss``): it writes the network (``.bnd``) and a configuration (``.cfg``)
for that interval, runs an ensemble of ``sample_count`` stochastic trajectories, and reads back:

- ``node_probabilities``: the ensemble probability that each node is on, over the interval's last time window;
- ``node_state``: ONE joint state, sampled from the ensemble's joint-state distribution over that last window.

Coupling between intervals. MaBoSS starts a run from per-node initial distributions (``<node>.istate``), not from a
joint distribution, so the ensemble's joint distribution cannot be carried forward. The Process carries one sampled
joint state and starts the next interval from exactly that state (each node's ``istate`` set to 0 or 1). MaBoSS reports
averages over time windows, not instants, so the sample comes from the last window, and the carried state lags the
interval's end by about half a window, ``interval / (2 * windows_per_interval)``. That is an approximation, small by
default (100 windows per interval, a lag of 0.5% of the interval); raise ``windows_per_interval`` to tighten it.

Stores. Outputs are deltas taken against the values the input stores hold, so ``map[float]`` stores end at the new
values whatever they held before: a store seeded differently, a sibling's write, or a restart from a saved state.

Reproducibility. Each interval's MaBoSS seed and sampler come from ``numpy.random.SeedSequence([seed, step])``, where
``step`` is the interval's index, kept in the ``steps`` store. Distinct seeds never share streams, and a run resumed
from a saved state continues the same streams.

Parameters. The network's ``$name`` parameters are written into each interval's ``.cfg``, from ``config['parameters']``
overridden by the ``parameters`` input, so an upstream process (a kinetic layer, for example) can drive rates. Every
``$name`` needs a default in ``config['parameters']``. Rates must be finite and non-negative: MaBoSS kills the process
on a negative rate (SIGBUS on cmaboss 1.0.0b34), so they are refused first. ``cmaboss.MaBoSSSim.update_parameters`` is
not used: on cmaboss 1.0.0b34 it accepted a change and the result did not change.

Network size. cmaboss is built in variants by maximum node count (64, 128, 256, 512 and 1024); the smallest that fits
the network is used.
"""
from __future__ import annotations

import importlib
import math
import re
import tempfile
from pathlib import Path

import numpy as np
from process_bigraph import Process

_PARAMETER_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_NODE_LINE = re.compile(r"^\s*node\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE | re.MULTILINE)
_NO_NODE_ON = "<nil>"
_JOINT_SEPARATOR = " -- "
# cmaboss's builds, by the most nodes each supports
_ENGINES = ((64, "cmaboss"), (128, "cmaboss_128n"), (256, "cmaboss_256n"), (512, "cmaboss_512n"),
            (1024, "cmaboss_1024n"))


def _on_nodes(label: str) -> set[str]:
    """The nodes that are on in a MaBoSS joint-state label: '<nil>' is none, 'A -- B' is A and B."""
    return set() if label == _NO_NODE_ON else set(label.split(_JOINT_SEPARATOR))


def _engine_for(node_count: int):
    for capacity, module in _ENGINES:
        if node_count <= capacity:
            return importlib.import_module(module)
    raise ValueError(f"the network has {node_count} nodes; cmaboss supports at most {_ENGINES[-1][0]}")


class MaBoSSProcess(Process):
    """A MaBoSS Boolean network, advanced one interval at a time from one carried joint state.

    Config:
        network: the network in MaBoSS ``.bnd`` syntax.
        parameters: defaults for the network's ``$name`` parameters (names without ``$``); every one needs a default.
        initial_state: each node's starting value, 0 or 1 (nodes not listed start at 0).
        sample_count: stochastic trajectories per interval.
        windows_per_interval: MaBoSS time windows per interval; the carried state lags by half of one.
        seed: the base seed for MaBoSS and the joint-state sampler.

    Inputs:
        node_state: the joint state the next interval starts from (a value >= 0.5 is on). A sibling may write it.
        node_probabilities: the current store values, so the outputs are deltas against them.
        parameters: overrides for the network's ``$name`` parameters, so an upstream process can drive rates.
        steps: the number of intervals run so far (the seed's interval index).

    Outputs (deltas against the input stores, so ``map[float]`` stores end at the new values):
        node_state: the sampled joint state, 0.0 or 1.0 per node.
        node_probabilities: the ensemble probability that each node is on, over the interval's last window.
        steps: 1 per interval.
    """

    config_schema = {
        "network": "string",
        "parameters": {"_type": "map[float]", "_default": {}},
        "initial_state": {"_type": "map[float]", "_default": {}},
        "sample_count": {"_type": "integer", "_default": 1000},
        "windows_per_interval": {"_type": "integer", "_default": 100},
        "seed": {"_type": "integer", "_default": 1},
    }

    def initialize(self, config):
        network = config.get("network") or ""
        if not network.strip():
            raise ValueError("MaBoSSProcess needs a network (MaBoSS .bnd text) in config['network']")
        if int(config.get("sample_count") or 0) < 1:
            raise ValueError("MaBoSSProcess needs sample_count >= 1")
        if int(config.get("windows_per_interval") or 0) < 1:
            raise ValueError("MaBoSSProcess needs windows_per_interval >= 1")
        self._parameters = self._checked_parameters(config.get("parameters") or {})
        self._workdir = tempfile.TemporaryDirectory(prefix="viva-maboss-")
        self._bnd = Path(self._workdir.name) / "network.bnd"
        self._bnd.write_text(network)
        self._engine = _engine_for(len(set(_NODE_LINE.findall(network))))
        self._nodes = self._read_nodes()
        unknown = set(config.get("initial_state") or {}) - set(self._nodes)
        if unknown:
            raise ValueError(f"initial_state names nodes not in the network: {sorted(unknown)}")

    def inputs(self):
        return {
            "node_state": "map[float]",
            "node_probabilities": "map[float]",
            "parameters": "map[float]",
            "steps": "integer",
        }

    def outputs(self):
        return {"node_state": "map[float]", "node_probabilities": "map[float]", "steps": "integer"}

    def initial_state(self):
        state = {node: self._initial_value(node) for node in self._nodes}
        return {"node_state": state, "node_probabilities": dict(state), "steps": 0}

    def update(self, state, interval):
        interval = float(interval)
        stored_state = state.get("node_state") or {}
        stored_probabilities = state.get("node_probabilities") or {}
        start = {node: 1.0 if float(stored_state.get(node, 0.0)) >= 0.5 else 0.0 for node in self._nodes}
        parameters = {**self._parameters, **self._checked_parameters(state.get("parameters") or {})}

        step = int(state.get("steps") or 0)
        engine_seed, sampler_seed = np.random.SeedSequence([int(self.config["seed"]), step]).generate_state(2)
        # MaBoSS mishandles seed_pseudorandom >= 2**31 (measured on cmaboss 1.0.0b34: single trajectories with such
        # seeds switched on 0.64 of the time where the exact value is 0.26), so the engine seed is kept to 31 bits.
        result = self._run(start, parameters, interval, int(engine_seed) & 0x7FFFFFFF)

        node_probs, _, node_labels, _ = result.get_last_nodes_probtraj()
        probabilities = dict.fromkeys(self._nodes, 0.0)
        probabilities.update({str(node): float(p) for node, p in zip(node_labels, node_probs[0])})

        joint_probs, _, joint_labels, _ = result.get_last_probtraj()
        weights = np.clip(np.asarray(joint_probs[0], dtype=float), 0.0, None)
        if not weights.sum() > 0.0:
            raise RuntimeError("MaBoSS returned an empty joint-state distribution for the interval")
        chosen = np.random.default_rng(int(sampler_seed)).choice(len(joint_labels), p=weights / weights.sum())
        on = _on_nodes(str(joint_labels[chosen]))

        return {
            "node_state": {
                node: (1.0 if node in on else 0.0) - float(stored_state.get(node, 0.0)) for node in self._nodes
            },
            "node_probabilities": {
                node: probabilities[node] - float(stored_probabilities.get(node, 0.0)) for node in self._nodes
            },
            "steps": 1,
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
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"MaBoSS parameter {name!r} must be finite and non-negative, got {value}")
            checked[bare] = value
        return checked

    def _read_nodes(self) -> list[str]:
        """The network's nodes, as MaBoSS parses them (a minimal config, so only the network is read)."""
        cfg = Path(self._workdir.name) / "nodes.cfg"
        lines = [f"${name} = {value!r};" for name, value in self._parameters.items()]
        cfg.write_text("\n".join([*lines, "max_time = 1;", "sample_count = 1;", ""]))
        try:
            simulation = self._engine.MaBoSSSim(str(self._bnd), str(cfg))
        except Exception as exc:
            raise ValueError(
                f"MaBoSS could not read the network: {exc}. Every $parameter needs a default in config['parameters']."
            ) from exc
        return sorted(str(node) for node in simulation.get_nodes())

    def _run(self, start, parameters, interval, seed):
        time_tick = interval / int(self.config["windows_per_interval"])
        lines = [f"${name} = {value!r};" for name, value in parameters.items()]
        lines += [
            f"max_time = {interval!r};",
            f"time_tick = {time_tick!r};",
            f"sample_count = {int(self.config['sample_count'])};",
            f"seed_pseudorandom = {seed};",
        ]
        lines += [f"{node}.istate = {int(start[node])};" for node in self._nodes]
        cfg = Path(self._workdir.name) / "interval.cfg"
        cfg.write_text("\n".join([*lines, ""]))
        return self._engine.MaBoSSSim(str(self._bnd), str(cfg)).run()
