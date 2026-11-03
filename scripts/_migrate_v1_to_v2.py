#!/usr/bin/env python3
"""Migrate workspace.yaml from schema v1 to v2.

v1 had per-model nesting (models[<name>].phases/observables/visualizations).
v2 lifts the first model's content to the top level; the workspace IS the model.

Usage:
    python3 scripts/_migrate_v1_to_v2.py [--dry-run]

Prints the diff (always) and writes in place (unless --dry-run).
"""
from __future__ import annotations

import copy
import difflib
import re
import sys
from pathlib import Path

import yaml


WS_ROOT = Path(__file__).resolve().parents[1]
WS_FILE = WS_ROOT / "workspace.yaml"


def _safe_slug(name: str) -> str:
    """Convert a model name to a Python-safe package slug."""
    return re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_")


def migrate_v1_to_v2(original: dict) -> dict:
    """Return a new v2 workspace dict migrated from v1 *original*."""
    if original.get("schema_version") == 2:
        return copy.deepcopy(original)

    ws = copy.deepcopy(original)

    # Pull the first model's data.
    models: dict = ws.pop("models", {}) or {}
    first_model_name: str | None = next(iter(models), None)
    first_model: dict = models.get(first_model_name, {}) if first_model_name else {}

    # Bump schema version.
    ws["schema_version"] = 2

    # Set package_path from first model slug.
    if first_model_name:
        ws["package_path"] = f"viva_{_safe_slug(first_model_name)}"

    # Lift pbg_processes.
    ws["pbg_processes"] = first_model.get("pbg_processes", []) or []

    # Lift phases, observables, visualizations.
    ws["phases"] = first_model.get("phases", []) or []
    ws["observables"] = first_model.get("observables", []) or []
    ws["visualizations"] = first_model.get("visualizations", []) or []

    # Preserve insertion order: put new top-level fields after plugin_version/stages.
    # yaml.safe_dump sorts by key unless sort_keys=False; we reconstruct order.
    ordered: dict = {}
    for key in ("schema_version", "name", "created", "plugin_version",
                "package_path", "pbg_processes", "stages",
                "phases", "observables", "visualizations",
                "imports", "datasets", "expert_docs",
                "references_pdfs", "references_bib", "server"):
        if key in ws:
            ordered[key] = ws[key]
    # Any remaining keys not in the canonical order.
    for key, val in ws.items():
        if key not in ordered:
            ordered[key] = val

    return ordered


def _dump(ws: dict) -> str:
    return yaml.dump(ws, sort_keys=False, allow_unicode=True, default_flow_style=False)


def main() -> None:
    dry_run = "--dry-run" in sys.argv

    if not WS_FILE.exists():
        sys.exit(f"workspace.yaml not found at {WS_FILE}")

    original_text = WS_FILE.read_text()
    original = yaml.safe_load(original_text)

    if original.get("schema_version") == 2:
        print("workspace.yaml is already schema v2 — nothing to do.")
        return
    if original.get("schema_version") not in (1, None):
        sys.exit(f"Unexpected schema_version: {original.get('schema_version')} — cannot migrate.")

    migrated = migrate_v1_to_v2(original)
    new_text = _dump(migrated)

    diff = list(difflib.unified_diff(
        original_text.splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        fromfile="workspace.yaml (v1)",
        tofile="workspace.yaml (v2)",
    ))

    if diff:
        print("".join(diff))
    else:
        print("(no changes)")

    if not dry_run:
        WS_FILE.write_text(new_text)
        print(f"\nMigrated {WS_FILE} to schema v2.")
    else:
        print("\n[dry-run] No changes written.")


if __name__ == "__main__":
    main()
