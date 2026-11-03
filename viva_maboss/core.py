"""build_core() — the workspace core that composites and tests run against.

`process_bigraph.allocate_core()` auto-discovers processes/types from *installed
distributions* that depend on bigraph-schema (it scans
``importlib.metadata.packages_distributions()``). That works for sibling
``viva-*`` wrapper packages installed as regular wheels, but it does NOT see this
workspace's own package when it is installed *editable* (``pip install -e .`` —
the way CI and local dev install it): an editable install records only a
``.pth`` shim in its ``RECORD``, so ``packages_distributions()`` never maps this
package back to its distribution and the discovery scan skips it entirely. The
result is that any Process/Step defined IN this package is missing from the
core's link registry, and a composite addressing ``local:<ProcessName>`` fails
at build with::

    Exception: no link found at address: {'protocol': 'local', 'data': '<ProcessName>'}

So we register this workspace's own Process/Step classes explicitly here. The
registration is idempotent (a class already provided by auto-discovery — e.g. in
a non-editable / Docker install — is left untouched), so ``build_core()`` is
correct regardless of how the workspace was installed. A fresh scaffold with no
process classes yet is a clean no-op.
"""
from __future__ import annotations

import importlib
import pkgutil

from process_bigraph import Process, Step, allocate_core


def _iter_own_process_classes():
    """Yield (name, cls) for each Process/Step subclass defined in THIS package.

    Walks only this package's own top-level modules (resolved from ``__package__``
    so no name is hard-coded). Defensive: an import error in any single module is
    swallowed so one broken module never breaks ``build_core()``. A brand-new
    package with no process classes simply yields nothing.
    """
    package_name = __package__
    if not package_name:
        return
    try:
        package = importlib.import_module(package_name)
    except Exception:
        return
    search_paths = getattr(package, "__path__", None)
    if search_paths is None:
        return
    seen = set()
    for module_info in pkgutil.iter_modules(search_paths, package_name + "."):
        try:
            module = importlib.import_module(module_info.name)
        except Exception:
            # A module that fails to import (e.g. an optional heavy dep missing)
            # must not take down the whole core build.
            continue
        for attr_name in dir(module):
            obj = getattr(module, attr_name, None)
            if not isinstance(obj, type):
                continue
            if not issubclass(obj, (Process, Step)):
                continue
            if obj is Process or obj is Step:
                continue
            # Only register classes actually DEFINED in this package, not ones
            # imported into a module from elsewhere (e.g. the base classes).
            if not getattr(obj, "__module__", "").startswith(package_name):
                continue
            if obj.__name__ in seen:
                continue
            seen.add(obj.__name__)
            yield obj.__name__, obj


def register_workspace_processes(core):
    """Register this workspace's own Process/Step classes into ``core``.

    Idempotent: a name already present in ``core.link_registry`` (e.g. provided
    by auto-discovery in a non-editable install) is left untouched.
    """
    for name, cls in _iter_own_process_classes():
        if name not in core.link_registry:
            core.register_link(name, cls)
    return core


def _chain_installed_module_cores(core):
    """Chain each catalog-installed module's own core registration into ``core``.

    A module added via the Catalog tab (e.g. viva-munk, spatio-flux) registers
    its custom TYPES (``set_float``, ``pymunk_agent`` …) and composite generators
    in its own ``<pkg>.core.build_core`` / ``<pkg>.register_types``. The dashboard
    builds composites against THIS ``build_core``, so unless we call the installed
    modules' registration here, their composites fail to realize with e.g.
    ``unable to parse type "map[set_float]"``. Reading ``workspace.yaml`` imports
    means we don't have to hardcode a linked-package list — a newly installed
    module is chained automatically. Best-effort: a module with no core, or one
    that fails to import, is skipped.
    """
    try:
        import yaml
    except Exception:
        return core
    from pathlib import Path
    here = Path(__file__).resolve()
    for ws_file in (here.parent.parent / "workspace.yaml",
                    Path(__import__("os").environ.get("WORKSPACE_DIR", "") or ".") / "workspace.yaml",
                    Path.cwd() / "workspace.yaml"):
        if ws_file.is_file():
            break
    else:
        return core
    try:
        imports = (yaml.safe_load(ws_file.read_text(encoding="utf-8")) or {}).get("imports") or {}
    except Exception:
        return core
    if not isinstance(imports, dict):
        return core
    for name, spec in imports.items():
        spec = spec if isinstance(spec, dict) else {}
        pkg = (spec.get("package") or str(name)).replace("-", "_")
        # Prefer ``<pkg>.core.build_core(core)``; fall back to ``<pkg>.build_core``
        # / ``<pkg>.register_types``. Each is a ``(core) -> core | None`` call.
        for modname, fn_name in ((pkg + ".core", "build_core"),
                                 (pkg, "build_core"),
                                 (pkg, "register_types")):
            try:
                fn = getattr(importlib.import_module(modname), fn_name, None)
                if callable(fn):
                    core = fn(core) or core
                    break
            except Exception:
                continue
    return core


def build_core(core=None):
    """Return a process-bigraph core with this workspace's processes registered.

    This is the canonical core for the workspace: composites that address
    ``local:<ProcessName>`` (and the test suite) must build their ``Composite``
    against a core returned from here, not a bare ``allocate_core()``.

    Catalog-installed modules declared in ``workspace.yaml`` imports are chained
    in too (:func:`_chain_installed_module_cores`), so their custom types and
    composites are available without hardcoding a linked-package list.
    """
    if core is None:
        core = allocate_core()
    register_workspace_processes(core)
    _chain_installed_module_cores(core)
    return core
