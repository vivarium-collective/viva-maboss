"""Guard: a committed composite-state artifact must contain actual wiring.

The Composite Explorer renders a ``@composite_generator``'s wiring from the
default state the workbench resolves for it, preferring a committed artifact at
``reports/composite-state/<id>.json`` (written by a regeneration script run on a
host with the workspace's heavy build inputs, then force-added — ``reports/`` is
usually gitignored).

The failure this catches: ``resolve_composite`` returns a **200 payload with**
``state: null`` and ``wiring_status: "unavailable"`` when it cannot produce the
wiring. Serializing that payload writes a plausible-looking artifact whose only
content is the failure itself — and since the resolver reads that same file back
as its fallback, the Explorer then shows "default state for generator '<x>' is
not generated yet" forever, with a committed file present that makes the gap look
filled. An existence-only check passes; only a content check catches it.

Fully generic: it inspects whatever JSON is committed under
``reports/composite-state/``, imports nothing, and passes trivially for a
workspace that commits no artifacts at all.

Fix a failure by regenerating the artifact (build the composite for real), or by
deleting it — a missing artifact is honest and the live workbench builds the
generator on demand; a null one is a lie that silently disables the Explorer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _find_workspace_root() -> Path:
    """Walk up from cwd / this file looking for workspace.yaml."""
    for start in (Path.cwd(), Path(__file__).resolve().parent):
        node = start
        for _ in range(8):
            if (node / "workspace.yaml").is_file():
                return node
            if node.parent == node:
                break
            node = node.parent
    return Path.cwd()


def _usable_state(path: Path) -> bool:
    """True when the artifact carries a non-empty ``state`` mapping."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — an unreadable artifact is a broken one
        return False
    if not isinstance(data, dict):
        return False
    # Two shapes in the wild: the full resolve payload ({id, name, state, ...})
    # and the bare envelope written by ``regenerate_default_state``
    # ({state, _provenance}). Both put the wiring under "state".
    return isinstance(data.get("state"), dict) and bool(data["state"])


def test_no_committed_composite_state_artifact_is_empty():
    cstate = _find_workspace_root() / "reports" / "composite-state"
    if not cstate.is_dir():
        pytest.skip("workspace commits no composite-state artifacts")
    artifacts = sorted(cstate.glob("*.json"))
    if not artifacts:
        pytest.skip("no composite-state artifacts committed yet")
    broken = [p.name for p in artifacts if not _usable_state(p)]
    assert not broken, (
        "committed composite-state artifacts carry no usable `state` — these "
        f"render as 'not generated yet' in the Composite Explorer: {broken}. "
        "Regenerate them (build the composite on a host with its build inputs, "
        "e.g. the ParCa cache) and re-commit, or delete the artifact so the gap "
        "is visible instead of frozen into git."
    )
