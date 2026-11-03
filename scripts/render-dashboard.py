#!/usr/bin/env python3
"""Render the workspace dashboard.

After the dashboard runtime was extracted to ``vivarium-workbench``, this
script just wraps :func:`vivarium_workbench.lib.report.render_dashboard`.
Kept for backward compatibility with older `bash` callers and CI.
"""
from __future__ import annotations
import sys
from pathlib import Path


def _count_embedded(ws_root: Path) -> dict:
    """Walk the workspace and count what the rendered SPA covers.

    Surfaced in the script's post-render summary (mem3dg-readdy friction #7
    — "render-dashboard.py output is opaque"). Counts are derived from
    on-disk artifacts, not parsed from the rendered HTML, so they stay
    correct even if the SPA shape changes.
    """
    import yaml as _yaml

    counts = {
        "studies": 0,
        "investigations": 0,
        "composites": 0,
        "visualizations": 0,
        "expert_docs": 0,
        "bib_keys": 0,
    }

    # Studies + per-study visualization counts.
    viz_total = 0
    for study_yaml in (ws_root / "studies").glob("*/study.yaml"):
        counts["studies"] += 1
        try:
            data = _yaml.safe_load(study_yaml.read_text()) or {}
            viz_total += len(data.get("visualizations") or [])
        except Exception:
            pass

    # Investigations.
    counts["investigations"] = sum(1 for _ in
        (ws_root / "investigations").glob("*/investigation.yaml"))

    # Composites — discovered the same way the dashboard does: any
    # *.composite.yaml / *.composite.json under the workspace package
    # directories. Walks the workspace package + any imports/<name>/ trees.
    for pattern in ("**/*.composite.yaml", "**/*.composite.json"):
        for path in ws_root.glob(pattern):
            # Skip venv / build artifacts so we don't double-count vendored
            # composites that happen to ship inside .venv/site-packages.
            if any(part in (".venv", "build", "__pycache__")
                   for part in path.parts):
                continue
            counts["composites"] += 1

    # Workspace-level visualizations + accumulated study viz.
    try:
        ws_data = _yaml.safe_load((ws_root / "workspace.yaml").read_text()) or {}
        counts["visualizations"] = (
            len(ws_data.get("visualizations") or []) + viz_total
        )
        counts["expert_docs"] = len(ws_data.get("expert_docs") or [])
    except Exception:
        pass

    # Bib keys (rough — counts @entries{key,...} in papers.bib).
    bib = ws_root / "references" / "papers.bib"
    if bib.is_file():
        try:
            import re as _re
            counts["bib_keys"] = len(
                _re.findall(r"@\w+\{([A-Za-z0-9_:-]+),", bib.read_text())
            )
        except Exception:
            pass

    return counts


def main() -> int:
    try:
        from vivarium_workbench.lib.report import render_dashboard
    except ImportError:
        print("ERROR: vivarium-workbench is not installed.", file=sys.stderr)
        print("Install it into the workspace venv:", file=sys.stderr)
        print("    .venv/bin/pip install vivarium-workbench", file=sys.stderr)
        return 2
    ws_root = Path.cwd()
    if not (ws_root / "workspace.yaml").is_file():
        print(f"ERROR: not a workspace (no workspace.yaml): {ws_root}", file=sys.stderr)
        return 1
    # Make the workspace's own package importable for build_core().
    ws_str = str(ws_root)
    if ws_str not in sys.path:
        sys.path.insert(0, ws_str)
    from vivarium_workbench.lib._root import set_workspace_root
    set_workspace_root(ws_root)
    out = render_dashboard(ws_root, write_all=True)
    print(f"rendered {out}")

    # Summary of what got embedded vs. what's fetched client-side.
    # mem3dg-readdy friction #7: bare "rendered <path>" was opaque after
    # 20 minutes of YAML wiring — the user couldn't tell whether the SPA
    # picked up the studies they'd seeded. The counts here are derived
    # from on-disk artifacts; the rendered SPA fetches runs / sweep
    # results / per-study test outcomes client-side via /api.
    try:
        c = _count_embedded(ws_root)
        print(
            f"  embedded: {c['studies']} studies, "
            f"{c['investigations']} investigations, "
            f"{c['composites']} composites, "
            f"{c['visualizations']} visualizations"
        )
        if c["expert_docs"] or c["bib_keys"]:
            print(
                f"  references: {c['expert_docs']} expert_docs, "
                f"{c['bib_keys']} bib keys"
            )
        print("  (runs, sweep results, and test outcomes are fetched "
              "client-side via /api/* — refresh the dashboard to see new ones)")
    except Exception as e:  # noqa: BLE001 — summary is advisory; never fail render
        print(f"  (summary unavailable: {type(e).__name__}: {e})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
