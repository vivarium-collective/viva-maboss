"""Guard: build_core() must register this workspace's own processes.

Processes defined INSIDE this workspace's editable-installed package are NOT
seen by ``process_bigraph.allocate_core()`` (an editable install is invisible to
``importlib.metadata.packages_distributions()``). If the explicit registration
in the workspace's ``core.build_core()`` is ever dropped, every composite that
addresses ``local:<ProcessName>`` breaks deep inside ``Composite`` construction
with a cryptic ``no link found at address`` error. This test makes that
regression fail fast, with a clear message, the moment a process is added.

It is fully generic — it discovers the workspace package at runtime (from
``workspace.yaml`` or the sole top-level ``viva_*`` (or legacy ``pbg_*``) package) and discovers that
package's own Process/Step subclasses. A brand-new scaffold with no process
classes yet passes trivially (it is skipped), so this never red-flags a fresh
workspace.
"""

from __future__ import annotations

import importlib
import pkgutil
import sys
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


# The workspace package (``viva_<name>/``) lives at the workspace root and is
# imported by virtue of the root being on sys.path — that is how the dashboard
# loads it (it chdirs to the root) and how a ``pip install -e .`` workspace
# exposes it. The root pyproject uses hatchling ``bypass-selection`` (the
# workspace is a research tree, not a distributable wheel), so the editable
# install does NOT put the package on ``site-packages``. Ensure the root is on
# ``sys.path`` so this test can import it under pytest, whose default import
# mode would otherwise only add ``tests/``.
_WS_ROOT = _find_workspace_root()
if str(_WS_ROOT) not in sys.path:
    sys.path.insert(0, str(_WS_ROOT))


def _discover_package_name() -> str:
    """Resolve the workspace package name.

    Prefer ``package_path`` from workspace.yaml; fall back to the sole top-level
    ``viva_*`` (or legacy ``pbg_*``) directory that looks like a Python package.
    """
    root = _find_workspace_root()
    wsyaml = root / "workspace.yaml"
    if wsyaml.is_file():
        try:
            import yaml

            data = yaml.safe_load(wsyaml.read_text()) or {}
            pkg = data.get("package_path")
            if pkg:
                return str(pkg)
        except Exception:
            pass
    # Fall back: a single top-level package directory with an __init__.py.
    candidates = [
        p.name
        for p in root.iterdir()
        if p.is_dir() and (p / "__init__.py").is_file() and not p.name.startswith(".")
    ]
    pkgs = [c for c in candidates if c.startswith("viva_") or c.startswith("pbg_")]
    if len(pkgs) == 1:
        return pkgs[0]
    if len(candidates) == 1:
        return candidates[0]
    pytest.skip(
        "could not uniquely resolve the workspace package "
        f"(workspace.yaml has no package_path; candidates={candidates})"
    )


def _own_process_classes(package_name: str):
    """Discover Process/Step subclasses DEFINED in the workspace package.

    Independent of core.py's own walk, so this test is a real cross-check.
    """
    from process_bigraph import Process, Step

    package = importlib.import_module(package_name)
    search_paths = getattr(package, "__path__", None)
    if search_paths is None:
        return {}
    found: dict[str, type] = {}
    for module_info in pkgutil.iter_modules(search_paths, package_name + "."):
        try:
            module = importlib.import_module(module_info.name)
        except Exception:
            continue
        for attr_name in dir(module):
            obj = getattr(module, attr_name, None)
            if not isinstance(obj, type):
                continue
            if not issubclass(obj, (Process, Step)) or obj is Process or obj is Step:
                continue
            if not getattr(obj, "__module__", "").startswith(package_name):
                continue
            found[obj.__name__] = obj
    return found


def test_build_core_succeeds():
    package_name = _discover_package_name()
    core_mod = importlib.import_module(f"{package_name}.core")
    core = core_mod.build_core()
    assert core is not None, "build_core() returned None"


def test_workspace_processes_registered():
    package_name = _discover_package_name()
    core_mod = importlib.import_module(f"{package_name}.core")
    core = core_mod.build_core()

    own = _own_process_classes(package_name)
    if not own:
        pytest.skip(
            f"{package_name} defines no Process/Step classes yet — "
            "nothing to register (fresh scaffold)."
        )

    missing = [name for name in own if name not in core.link_registry]
    assert not missing, (
        f"build_core() did not register workspace processes {missing}; "
        f"composites addressing `local:<name>` will fail to resolve with "
        f"'no link found at address'. Ensure {package_name}.core.build_core() "
        f"registers this package's own processes."
    )

    # The exact failure mode the bug produced: each must resolve by address.
    for name in own:
        assert core.link_registry.get(name) is not None, (
            f"local:{name} does not resolve in the core's link registry"
        )
