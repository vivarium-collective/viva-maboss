"""viva_maboss.cli.__main__ — the workspace's CLI entrypoint.

Common, generic command interface for viva-* workspaces: `run` builds and
runs any catalog composite (spec or generator) directly against
process-bigraph's own discovery + Composite APIs — the same primitives the
dashboard's composite resolver (`vivarium_workbench.lib.composite_resolve`)
is itself built on, minus that resolver's dashboard/cloud-dispatch layers,
which don't apply to a plain local CLI run. No server required. Add
workspace-specific sub-CLIs as sibling modules under cli/ and mount them on
`app` below.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

import typer
import yaml
from rich.console import Console

app = typer.Typer(name="maboss", help="maboss workspace CLI.")
console = Console()


@app.callback()
def _callback() -> None:
    """maboss workspace CLI.

    Keeps `run` an explicit subcommand (`maboss run ...`) even while it
    is the only command — Typer collapses a single `@app.command` into the
    bare top-level invocation unless a callback is registered. Add
    workspace-specific sub-CLIs as more `@app.command`s below, or mount
    sibling Typer apps here with `app.add_typer(...)`.
    """


def _find_workspace_root(start: "Path | None" = None) -> Path:
    """Walk up from `start` (default cwd) to the nearest workspace.yaml."""
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "workspace.yaml").is_file():
            return candidate
    raise typer.BadParameter(
        "no workspace.yaml found in this directory or any parent"
    )


def _package_path(workspace_root: Path) -> str:
    ws_data = yaml.safe_load(
        (workspace_root / "workspace.yaml").read_text(encoding="utf-8")
    ) or {}
    return ws_data.get("package_path") or "viva_maboss"


def _parse_override(raw: str) -> tuple[str, Any]:
    """Parse a `key=value` override; value is YAML-loaded so ints/floats/
    bools/lists parse naturally and plain strings still round-trip."""
    if "=" not in raw:
        raise typer.BadParameter(f"override must be key=value, got: {raw!r}")
    key, _, value = raw.partition("=")
    return key.strip(), yaml.safe_load(value)


def _resolve_composite_spec(workspace_root: Path, package_path: str, composite_id: str):
    """Resolve `composite_id` to a live process_bigraph CompositeSpec.

    Covers both composite conventions this ecosystem uses — a static
    `*.composite.yaml`/`.json` file under the workspace package, and a
    `@composite_spec`/`@composite_generator`-decorated Python generator —
    since both register into the same process_bigraph.composite_spec
    registry. `discover_specs` alone only reaches installed packages'
    top-level modules (an editable install of THIS workspace's own package
    is invisible to it — same caveat as build_core()'s own discovery, see
    core.py); when the id still misses, import the module the id names
    (a generator id is `<dotted.module>.<generator_name>`) so its decorator
    fires, then retry.
    """
    from process_bigraph.composite_spec import discover_specs, get as get_spec

    if str(workspace_root) not in sys.path:
        sys.path.insert(0, str(workspace_root))

    discover_specs(workspace=workspace_root / package_path)
    spec = get_spec(composite_id)
    if spec is None and "." in composite_id:
        module_name = composite_id.rsplit(".", 1)[0]
        try:
            importlib.import_module(module_name)
        except Exception:
            pass
        spec = get_spec(composite_id)
    if spec is None:
        raise typer.BadParameter(f"composite not found: {composite_id}")
    return spec


@app.command(name="run")
def run_composite(
    composite_id: str = typer.Argument(
        ..., help="Dotted composite reference, e.g. pkg.composites.my_model"
    ),
    steps: float = typer.Option(
        None, "--steps",
        help="Simulation duration in steps. Defaults to the composite's own default_n_steps, or 10.",
    ),
    emit: str = typer.Option(
        None, "--emit", help="Comma-separated '/'-joined store paths to print. Defaults to the full state."
    ),
    param: list[str] = typer.Option(
        [], "--param", help="Parameter override as key=value (repeatable)."
    ),
) -> None:
    """Build and run a catalog composite via process-bigraph directly.

    The generalized, built-in execution entrypoint: resolve `composite_id`
    to a CompositeSpec, then build+run it with the spec's own
    `to_composite()` (process-bigraph's single canonical builder for both
    spec-file and generator-decorated composites — it normalizes either
    return shape and installs the spec's declared emitters). No dashboard
    server involved.
    """
    workspace_root = _find_workspace_root()
    package_path = _package_path(workspace_root)
    core_module = importlib.import_module(f"{package_path}.core")
    core = core_module.build_core()

    overrides = dict(_parse_override(p) for p in param)
    spec = _resolve_composite_spec(workspace_root, package_path, composite_id)
    composite = spec.to_composite(overrides, core=core)
    duration = steps if steps is not None else (spec.default_n_steps or 10)
    composite.run(duration)

    if emit:
        for path in (p.strip() for p in emit.split(",") if p.strip()):
            value: Any = composite.state
            for part in path.split("/"):
                value = value[part]
            console.print(f"{path} = {value}")
    else:
        console.print(composite.state)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
