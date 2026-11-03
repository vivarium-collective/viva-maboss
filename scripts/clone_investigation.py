#!/usr/bin/env python3
"""Clone an investigation into a fresh planning state.

Copies an investigation directory and its constituent studies, renaming
them via a prefix swap, and strips simulation results (run logs, dbs,
charts, sims, tests) so the clone is the shape the dashboard would show
*before* any runs.

Designed as both a CLI and an importable library so the dashboard's
``POST /api/investigation-clone`` endpoint and any wrapper skill can call
``clone_investigation(...)`` directly.

Example
-------
    python scripts/clone_investigation.py \\
        --source dnaa-replication \\
        --target dnaa-replication-fresh \\
        --source-root /path/to/source/workspace \\
        --target-root /path/to/target/workspace

If ``--source-root`` / ``--target-root`` are omitted, both default to the
current working directory (single-workspace clone).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml


# Subdirectories/files inside a study directory that are simulation
# OUTPUT (not planning input). These are dropped during clone.
_STUDY_STRIP_DIR_NAMES = {
    "sims",
    "tests",
    "charts",
    "viz",
    "data",
    "__pycache__",
}
_STUDY_STRIP_FILE_NAMES = {
    "runs.db",
    "runs.db-shm",
    "runs.db-wal",
}
_STUDY_STRIP_FILE_GLOBS = (
    "run-*.html",
    "*.log",
)

# Inside an investigation subdirectory (biology-feedback-*, overnight-*,
# etc.) only these explicit planning docs are preserved. REPORT.md,
# probe_*.py, *.json result files, *.html, *.sh, sweep logs are dropped.
_INVEST_SUBDIR_KEEP_NAMES = {
    "PLAN.md",
    "ADDENDUM.md",
    "FRICTION.md",
    "PROGRESS.md",
}


def _today_iso() -> str:
    return date.today().isoformat()


def _remap_study_name(old: str, source_prefix: str, target_prefix: str) -> str:
    """Replace leading ``source_prefix-`` with ``target_prefix-``; otherwise return as-is."""
    if old.startswith(source_prefix + "-"):
        return target_prefix + "-" + old[len(source_prefix) + 1 :]
    return old


def _copy_investigation_dir(src_dir: Path, dst_dir: Path) -> None:
    """Copy investigation directory contents, keeping only planning material.

    Top level: keep ``investigation.yaml`` and any ``*.md`` (STATUS.md etc.).
    Subdirs:   keep only the four explicit planning docs in
               ``_INVEST_SUBDIR_KEEP_NAMES``. Empty subdirs are removed.
    """
    dst_dir.mkdir(parents=True, exist_ok=True)
    for entry in src_dir.iterdir():
        if entry.is_file():
            if entry.name == "investigation.yaml" or entry.suffix == ".md":
                shutil.copy2(entry, dst_dir / entry.name)
        elif entry.is_dir():
            sub_dst = dst_dir / entry.name
            sub_dst.mkdir(parents=True, exist_ok=True)
            for sub in entry.iterdir():
                if sub.is_file() and sub.name in _INVEST_SUBDIR_KEEP_NAMES:
                    shutil.copy2(sub, sub_dst / sub.name)
            if not any(sub_dst.iterdir()):
                sub_dst.rmdir()


def _copy_study_dir(src_dir: Path, dst_dir: Path) -> None:
    """Copy a study directory, stripping result subdirs and files."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    for entry in src_dir.iterdir():
        if entry.name in _STUDY_STRIP_DIR_NAMES or entry.name in _STUDY_STRIP_FILE_NAMES:
            continue
        if any(entry.match(g) for g in _STUDY_STRIP_FILE_GLOBS):
            continue
        if entry.is_file():
            shutil.copy2(entry, dst_dir / entry.name)
        elif entry.is_dir():
            shutil.copytree(entry, dst_dir / entry.name, dirs_exist_ok=True)


def _reset_investigation_yaml(
    src_path: Path,
    dst_path: Path,
    new_name: str,
    study_remap: dict[str, str],
) -> None:
    data = yaml.safe_load(src_path.read_text())
    data["name"] = new_name
    data["created"] = _today_iso()
    data["status"] = "planning"
    data["studies"] = [study_remap.get(s, s) for s in data.get("studies", [])]
    for crit in data.get("acceptance_criteria", []) or []:
        if isinstance(crit, dict) and crit.get("study") in study_remap:
            crit["study"] = study_remap[crit["study"]]
    dst_path.write_text(yaml.safe_dump(data, sort_keys=False, width=120))


def _reset_study_yaml(
    src_path: Path,
    dst_path: Path,
    new_name: str,
    study_remap: dict[str, str],
) -> None:
    data = yaml.safe_load(src_path.read_text())
    data["name"] = new_name
    data["created"] = _today_iso()
    data["status"] = "planning"
    data.pop("last_run", None)
    if "phase" in data:
        data["phase"] = "Design"

    pg = data.get("pipeline_gate")
    if isinstance(pg, dict):
        pg["prerequisites"] = [study_remap.get(s, s) for s in pg.get("prerequisites", []) or []]
        pg["enables"] = [study_remap.get(s, s) for s in pg.get("enables", []) or []]
        pg["gate_status"] = "blocked"
        pg["gate_status_summary"] = (
            "Not yet started — investigation cloned in fresh planning state."
        )

    for parent in data.get("parent_studies", []) or []:
        if isinstance(parent, dict) and parent.get("study") in study_remap:
            parent["study"] = study_remap[parent["study"]]

    for sim in data.get("simulation_set", []) or []:
        if isinstance(sim, dict):
            sim["status"] = "gated"

    data.pop("runs", None)
    data.pop("findings", None)
    data.pop("conclusion", None)

    verdicts = data.get("conclusion_verdicts")
    if isinstance(verdicts, dict):
        for cat in verdicts.values():
            if isinstance(cat, dict):
                cat["result"] = "PENDING"
                cat["basis"] = "No runs yet — investigation cloned in fresh planning state."
                cat.pop("contributing_tests", None)

    for ed in data.get("expert_decisions_needed", []) or []:
        if isinstance(ed, dict):
            ed["status"] = "open"

    tests = data.get("tests")
    if isinstance(tests, dict):
        tests["last_results"] = None

    dst_path.write_text(yaml.safe_dump(data, sort_keys=False, width=120))


@dataclass
class CloneSummary:
    source: str
    target: str
    studies_remapped: dict[str, str] = field(default_factory=dict)
    paths_written: list[str] = field(default_factory=list)


def clone_investigation(
    source_name: str,
    target_name: str,
    source_root: Path,
    target_root: Path,
    source_prefix: str | None = None,
    target_prefix: str | None = None,
) -> CloneSummary:
    """Clone ``source_name`` to ``target_name`` as a fresh planning copy.

    Parameters
    ----------
    source_name, target_name:
        Investigation names (directory names under ``investigations/``).
    source_root, target_root:
        Workspace roots. May be the same path.
    source_prefix, target_prefix:
        Prefix to swap on study names. Defaults to the first dash-segment
        of the corresponding investigation name. Studies whose names do
        not start with ``source_prefix-`` are treated as external
        references and left untouched.
    """
    src_invest = source_root / "investigations" / source_name
    dst_invest = target_root / "investigations" / target_name

    if not src_invest.is_dir():
        raise FileNotFoundError(f"source investigation not found: {src_invest}")
    if dst_invest.exists():
        raise FileExistsError(f"target investigation already exists: {dst_invest}")

    invest_yaml = yaml.safe_load((src_invest / "investigation.yaml").read_text())

    sp = source_prefix or source_name.split("-")[0]
    tp = target_prefix or target_name.split("-")[0]

    listed_studies = list(invest_yaml.get("studies") or [])
    study_remap: dict[str, str] = {
        s: _remap_study_name(s, sp, tp) for s in listed_studies if s.startswith(sp + "-")
    }

    summary = CloneSummary(
        source=source_name,
        target=target_name,
        studies_remapped=study_remap,
    )

    _copy_investigation_dir(src_invest, dst_invest)
    _reset_investigation_yaml(
        src_invest / "investigation.yaml",
        dst_invest / "investigation.yaml",
        new_name=target_name,
        study_remap=study_remap,
    )
    summary.paths_written.append(str(dst_invest))

    for old_study, new_study in study_remap.items():
        src_study_dir = source_root / "studies" / old_study
        dst_study_dir = target_root / "studies" / new_study
        if not src_study_dir.is_dir():
            print(f"warning: source study missing, skipping: {src_study_dir}", file=sys.stderr)
            continue
        if dst_study_dir.exists():
            raise FileExistsError(f"target study already exists: {dst_study_dir}")
        _copy_study_dir(src_study_dir, dst_study_dir)
        study_yaml_dst = dst_study_dir / "study.yaml"
        if study_yaml_dst.exists():
            _reset_study_yaml(
                src_study_dir / "study.yaml",
                study_yaml_dst,
                new_name=new_study,
                study_remap=study_remap,
            )
        summary.paths_written.append(str(dst_study_dir))

    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--source", required=True, help="Source investigation name")
    parser.add_argument("--target", required=True, help="Target investigation name")
    parser.add_argument(
        "--source-root", type=Path, default=Path.cwd(),
        help="Workspace root containing the source (default: cwd)",
    )
    parser.add_argument(
        "--target-root", type=Path, default=Path.cwd(),
        help="Workspace root for the target (default: cwd)",
    )
    parser.add_argument(
        "--source-prefix", default=None,
        help="Study-name prefix to strip (default: first dash-segment of --source)",
    )
    parser.add_argument(
        "--target-prefix", default=None,
        help="Study-name prefix to apply (default: first dash-segment of --target)",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON summary on stdout")
    args = parser.parse_args(argv)

    try:
        summary = clone_investigation(
            source_name=args.source,
            target_name=args.target,
            source_root=args.source_root.resolve(),
            target_root=args.target_root.resolve(),
            source_prefix=args.source_prefix,
            target_prefix=args.target_prefix,
        )
    except (FileNotFoundError, FileExistsError) as exc:
        print(f"clone failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({
            "source": summary.source,
            "target": summary.target,
            "studies_remapped": summary.studies_remapped,
            "paths_written": summary.paths_written,
        }, indent=2))
    else:
        print(f"cloned investigation {summary.source!r} -> {summary.target!r}")
        for old, new in summary.studies_remapped.items():
            print(f"  study  {old}  ->  {new}")
        print(f"  {len(summary.paths_written)} paths written under {args.target_root}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
