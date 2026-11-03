#!/usr/bin/env python3
"""Validate workspace.yaml + cross-references. Exit non-zero on failure."""
from __future__ import annotations
import hashlib
import importlib
import json
import re
import subprocess
import sys
from pathlib import Path
from collections import Counter

import yaml
from jsonschema import Draft7Validator, FormatChecker, ValidationError


def _try_get_registry(ws_root: Path, ws_data: dict) -> set | None:
    """Try to introspect build_core() and return a set of registered process names.

    Returns None if introspection fails (caller should warn, not fail).
    """
    package_name = ws_data.get("package_path") or ("viva_" + ws_data.get("name", "").replace("-", "_"))
    try:
        py = sys.executable
        script = f"""
import json, sys
try:
    from {package_name}.core import build_core
    core = build_core()
    try:
        names = sorted(core.process_registry.list())
    except Exception:
        try:
            names = sorted(core.process_registry.registry.keys())
        except Exception:
            names = []
    print(json.dumps(names))
except Exception as e:
    print(json.dumps([]))
"""
        result = subprocess.run(
            [py, "-c", script],
            cwd=ws_root, capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        names = json.loads(result.stdout.strip().split("\n")[-1])
        if isinstance(names, list):
            return set(names)
        return None
    except Exception:
        return None


def _validate_simulation_processes(ws_data: dict, registry: set | None) -> list[str]:
    """Return error strings for simulation process refs not in registry.

    If registry is None (introspection failed), returns empty list (warning only).
    """
    if registry is None:
        return []
    errors = []
    for sim in ws_data.get("simulations", []) or []:
        if not isinstance(sim, dict):
            continue
        for proc in sim.get("processes", []) or []:
            if proc not in registry:
                errors.append(
                    f"simulation '{sim.get('name', '?')}' references missing process '{proc}'"
                )
    return errors


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


WS_ROOT = Path(__file__).resolve().parents[1]
WS_FILE = WS_ROOT / "workspace.yaml"

# Canonical flat layout — keep in sync with the WorkspacePaths resolver
# (vivarium_workbench.lib.workspace_paths.LAYOUT_DEFAULTS). Directory locations
# come from the optional `layout:` map in workspace.yaml; unset keys default to
# the flat name, so a workspace with no `layout:` block is unchanged.
_LAYOUT_DEFAULTS = {
    "studies": "studies", "investigations": "investigations",
    "composites": "composites", "references": "references",
    "datasets": "datasets", "notes": "notes", "experiments": "experiments",
    "reports": "reports", "pbg": ".pbg", "scripts": "scripts",
    "tests": "tests", "docs": "docs",
}


def _load_layout() -> dict:
    cfg = {}
    if WS_FILE.exists():
        try:
            cfg = yaml.safe_load(WS_FILE.read_text()) or {}
        except Exception:
            cfg = {}
    layout = dict(_LAYOUT_DEFAULTS)
    for k, v in (cfg.get("layout") or {}).items():
        if k in _LAYOUT_DEFAULTS and isinstance(v, str) and v:
            layout[k] = v
    return layout


_LAYOUT = _load_layout()


def _dir(name: str) -> Path:
    """Absolute path to a workspace directory, honoring workspace.yaml `layout:`."""
    return WS_ROOT / _LAYOUT[name]


def _schema(name: str) -> dict:
    p = _dir("pbg") / "schemas" / name
    if not p.exists():
        sys.exit(f"missing schema at {p}; was workspace scaffolded?")
    return json.loads(p.read_text())


def _fail(msg: str) -> None:
    print(f"LINT FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    if not WS_FILE.exists():
        _fail(f"{WS_FILE} not found — run inside a workspace")
    ws = yaml.safe_load(WS_FILE.read_text())

    # Schema version guard: fail clearly for unsupported (e.g. v1) workspaces.
    # v2 and v3 are both accepted (v3 adds default_baseline); see
    # .pbg/schemas/workspace.schema.json (schema_version enum).
    schema_ver = ws.get("schema_version")
    if schema_ver not in (2, 3):
        _fail(
            f"workspace.yaml is schema v{schema_ver}; "
            "only schema v2 and v3 are supported. "
            "Run `python3 scripts/_migrate_v1_to_v2.py` to migrate a v1 workspace."
        )

    Draft7Validator(_schema("workspace.schema.json"), format_checker=FormatChecker()).validate(ws)

    # Datasets
    for d in ws.get("datasets", []):
        path, url, sha = d.get("path"), d.get("url"), d.get("sha256")
        if path is not None:
            full = WS_ROOT / path
            if not full.exists():
                _fail(f"dataset '{d['name']}' path missing: {path}")
            elif sha:
                actual = _sha256(full)
                if actual != sha:
                    _fail(f"dataset '{d['name']}' sha256 mismatch (recorded={sha[:16]}…, actual={actual[:16]}…)")
        elif url is not None:
            if not sha:
                _fail(f"dataset '{d['name']}' has url but no sha256")
        else:
            _fail(f"dataset '{d['name']}' has neither path nor url")

    # References cross-ref
    refs_yaml = _dir("references") / "claims.yaml"
    bib = _dir("references") / "papers.bib"
    if refs_yaml.exists() and bib.exists():
        claims = (yaml.safe_load(refs_yaml.read_text()) or {}).get("claims", {}) or {}
        bib_text = bib.read_text()
        bib_keys = set(re.findall(r"@\w+\{([A-Za-z0-9_:-]+),", bib_text))
        for claim, key in claims.items():
            if isinstance(key, list):
                for k in key:
                    if k not in bib_keys:
                        _fail(f"claim '{claim}' references missing bib key '{k}'")
            elif isinstance(key, str):
                if key not in bib_keys:
                    _fail(f"claim '{claim}' references missing bib key '{key}'")

    # Expert docs path existence + optional sha256 verification
    for doc in ws.get("expert_docs", []) or []:
        doc_path = doc.get("path", "")
        if doc_path:
            full = WS_ROOT / doc_path
            if not full.exists():
                _fail(
                    f"expert_docs entry '{doc.get('name', '?')}' path missing: {doc_path} "
                    f"(expected at {full})"
                )
            sha = doc.get("sha256")
            if sha:
                actual = _sha256(full)
                if actual != sha:
                    _fail(f"expert_docs '{doc.get('name', '?')}' sha256 mismatch (recorded={sha[:16]}…, actual={actual[:16]}…)")

    # References PDFs: always verify sha256 + bib_key cross-reference
    bib = _dir("references") / "papers.bib"
    bib_keys: set = set()
    if bib.exists():
        bib_text = bib.read_text()
        bib_keys = set(re.findall(r"@\w+\{([A-Za-z0-9_:-]+),", bib_text))
    for pdf_entry in ws.get("references_pdfs", []) or []:
        bib_key = pdf_entry.get("bib_key", "")
        pdf_path = pdf_entry.get("path", "")
        sha = pdf_entry.get("sha256", "")
        if bib_key and bib_keys and bib_key not in bib_keys:
            _fail(f"references_pdfs entry '{bib_key}' has no matching entry in papers.bib")
        if pdf_path:
            full = WS_ROOT / pdf_path
            if not full.exists():
                _fail(f"references_pdfs '{bib_key}' path missing: {pdf_path}")
            elif sha:
                actual = _sha256(full)
                if actual != sha:
                    _fail(f"references_pdfs '{bib_key}' sha256 mismatch (recorded={sha[:16]}…, actual={actual[:16]}…)")

    # Collect registered simulation names.
    registered_sim_names: set = set()
    for sim in ws.get("simulations", []) or []:
        if not isinstance(sim, dict):
            continue
        sim_name = sim.get("name", "?")
        registered_sim_names.add(sim_name)
        # t_end > t_start
        t_start = sim.get("t_start")
        t_end = sim.get("t_end")
        if t_start is not None and t_end is not None:
            if float(t_end) <= float(t_start):
                _fail(f"simulation '{sim_name}' has t_end ({t_end}) <= t_start ({t_start})")

    # Visualization simulation references.
    for viz in ws.get("visualizations", []) or []:
        if not isinstance(viz, dict):
            continue
        viz_name = viz.get("name", "?")
        # simulation reference
        viz_sim = viz.get("simulation")
        if viz_sim and registered_sim_names and viz_sim not in registered_sim_names:
            _fail(f"visualization '{viz_name}' references missing simulation '{viz_sim}'")

    # Simulation process references (best-effort: warn if registry unavailable).
    has_process_refs = any(
        isinstance(s, dict) and s.get("processes")
        for s in ws.get("simulations", []) or []
    )
    if has_process_refs:
        registry = _try_get_registry(WS_ROOT, ws)
        if registry is None:
            print(
                "LINT WARNING: Could not introspect build_core() — "
                "simulation process references not validated.",
                file=sys.stderr,
            )
        else:
            for err in _validate_simulation_processes(ws, registry):
                _fail(err)

    # ---- Summary -------------------------------------------------------
    # Count expert_docs
    expert_docs = ws.get("expert_docs", []) or []
    n_expert = len(expert_docs)
    expert_names = [d.get("name", "?") for d in expert_docs if isinstance(d, dict)]

    # Count bib keys
    bib_file = _dir("references") / "papers.bib"
    bib_keys_found: set = set()
    if bib_file.exists():
        bib_keys_found = set(re.findall(r"@\w+\{([A-Za-z0-9_:-]+),", bib_file.read_text()))
    n_bib = len(bib_keys_found)

    # Count claims
    claims_file = _dir("references") / "claims.yaml"
    n_claims = 0
    if claims_file.exists():
        try:
            claims_data = yaml.safe_load(claims_file.read_text()) or {}
            if isinstance(claims_data, dict):
                n_claims = len(claims_data.get("claims", claims_data) or {})
            elif isinstance(claims_data, list):
                n_claims = len(claims_data)
        except Exception:
            # Fall back to line count
            n_claims = sum(1 for ln in claims_file.read_text().splitlines() if ln.strip() and not ln.startswith("#"))

    # Enumerate studies
    study_schema_path = _dir("pbg") / "schemas" / "study.schema.json"
    study_schema: dict | None = None
    if study_schema_path.exists():
        try:
            study_schema = json.loads(study_schema_path.read_text())
        except Exception:
            pass

    # Try to import vivarium_workbench for dashboard-equivalent validation.
    _dashboard_load_spec = None
    _dashboard_spec_error = None
    _dashboard_importable = False
    try:
        from vivarium_workbench.lib.investigations import (  # type: ignore[import]
            load_spec as _dashboard_load_spec,
            InvestigationSpecError as _dashboard_spec_error,
        )
        _dashboard_importable = True
    except ImportError:
        pass  # warn once in summary; don't hard-fail

    study_names: list[str] = []
    study_statuses: list[str] = []
    study_warnings: list[str] = []
    # F-friction #6: keep a separate count of studies that fail dashboard
    # load so the summary line can swap ✓ for ✗ when any fail. Without this,
    # the summary said `dashboard ✓` while WARN lines underneath said
    # `fails dashboard load`. Headline + body must agree.
    n_studies_failed_dashboard = 0

    for study_yaml_path in sorted((_dir("studies")).glob("*/study.yaml")):
        study_dir = study_yaml_path.parent.name
        try:
            study_data = yaml.safe_load(study_yaml_path.read_text()) or {}
            s_name = study_data.get("name", study_dir)
            s_status = study_data.get("status", "unknown")
            study_names.append(s_name)
            study_statuses.append(s_status)

            # Validate against JSON schema if available
            if study_schema is not None:
                try:
                    from jsonschema import Draft202012Validator
                    validator = Draft202012Validator(study_schema)
                    errs = sorted(validator.iter_errors(study_data), key=lambda e: list(e.path))
                    for err in errs:
                        path_str = ".".join(str(p) for p in err.path) if err.path else "(root)"
                        study_warnings.append(f"  WARN study {s_name}: [{path_str}] {err.message}")
                except ImportError:
                    # jsonschema draft 2020-12 validator not available; fall back silently
                    try:
                        Draft7Validator(study_schema, format_checker=FormatChecker()).validate(study_data)
                    except Exception as ve:
                        study_warnings.append(f"  WARN study {s_name}: {ve.message}")
                except Exception as ve:
                    study_warnings.append(f"  WARN study {s_name}: {ve}")

            # Dashboard-equivalent validation: run the same migration + validation
            # path that the running dashboard uses (catches v3→v4 field collisions
            # and other issues that JSON Schema alone cannot detect).
            if _dashboard_load_spec is not None:
                try:
                    _dashboard_load_spec(study_yaml_path)
                except _dashboard_spec_error as e:
                    study_warnings.append(
                        f"  WARN study {s_name} fails dashboard load: {e}"
                    )
                    n_studies_failed_dashboard += 1
        except Exception as exc:
            study_warnings.append(f"  WARN could not parse {study_yaml_path}: {exc}")

    n_studies = len(study_names)
    status_counts = Counter(study_statuses)
    status_summary = ", ".join(f"{v} {k}" for k, v in sorted(status_counts.items()))

    # Enumerate investigations (added 2026-05-18 per mem3dg-readdy friction §8 —
    # "Lint summary doesn't mention investigations". Investigations are lighter
    # than studies — typically just name, title, studies[] — but they're the
    # first artifact a workspace promotion lands, so the lint summary should
    # confirm they exist + parse.)
    inv_schema_path = _dir("pbg") / "schemas" / "investigation.schema.json"
    inv_schema: dict | None = None
    if inv_schema_path.exists():
        try:
            inv_schema = json.loads(inv_schema_path.read_text())
        except Exception:
            pass

    inv_names: list[str] = []
    inv_warnings: list[str] = []
    # Cross-reference target — the slugs we'll check investigation references
    # against. Computed once from the studies directory rather than per-iter.
    on_disk_study_slugs = {p.parent.name for p in
                            (_dir("studies")).glob("*/study.yaml")}

    for inv_yaml_path in sorted((_dir("investigations")).glob("*/investigation.yaml")):
        inv_dir = inv_yaml_path.parent.name
        try:
            inv_data = yaml.safe_load(inv_yaml_path.read_text()) or {}
            i_name = inv_data.get("name", inv_dir)
            inv_names.append(i_name)
            if inv_schema is not None:
                try:
                    from jsonschema import Draft202012Validator
                    validator = Draft202012Validator(inv_schema)
                    errs = sorted(validator.iter_errors(inv_data),
                                  key=lambda e: list(e.path))
                    for err in errs:
                        path_str = ".".join(str(p) for p in err.path) if err.path else "(root)"
                        inv_warnings.append(
                            f"  WARN investigation {i_name}: [{path_str}] {err.message}"
                        )
                except ImportError:
                    pass
                except Exception as ve:
                    inv_warnings.append(f"  WARN investigation {i_name}: {ve}")

            # Cross-reference check (mem3dg-readdy friction #18 — "Investigations
            # don't validate that their study slugs exist"). Without this, an
            # investigation seeded with `studies: [fixed-boundary, ...]` against
            # an empty studies/ dir passes lint and 404s in the dashboard.
            referenced_slugs: set[str] = set()
            for slug in (inv_data.get("studies") or []):
                if isinstance(slug, str):
                    referenced_slugs.add(slug)
            for ac in (inv_data.get("acceptance_criteria") or []):
                if isinstance(ac, dict) and isinstance(ac.get("study"), str):
                    referenced_slugs.add(ac["study"])
            for slug in sorted(referenced_slugs):
                if slug not in on_disk_study_slugs:
                    inv_warnings.append(
                        f"  WARN investigation {i_name}: references study "
                        f"{slug!r} which has no studies/{slug}/study.yaml on "
                        "disk. Seed it with `/viva-study new` or remove the "
                        "reference."
                    )
        except Exception as exc:
            inv_warnings.append(f"  WARN could not parse {inv_yaml_path}: {exc}")
    n_investigations = len(inv_names)
    inv_names_preview = ", ".join(inv_names[:3])
    if n_investigations > 3:
        inv_names_preview += f", ... (+{n_investigations - 3} more)"

    # Count runs (by checking for runs.db files)
    n_active_runs = 0
    n_completed_runs = 0
    for runs_db in (_dir("studies")).glob("*/runs.db"):
        try:
            import sqlite3
            conn = sqlite3.connect(str(runs_db))
            try:
                # vivarium-workbench writes the per-study run metadata to the
                # `runs_meta` table (see composite_runs.py), not `runs` — the
                # old name made this always report "0 completed runs" (#84).
                cur = conn.execute("SELECT status FROM runs_meta")
                for (row_status,) in cur.fetchall():
                    if row_status in ("running", "pending"):
                        n_active_runs += 1
                    elif row_status == "completed":
                        n_completed_runs += 1
            except Exception:
                pass
            finally:
                conn.close()
        except Exception:
            pass

    # v2ecoli friction #2: `_type: 'any'` is not a registered bigraph-schema
    # type; it slips through inline python composite builds but explodes
    # inside the subprocess runner with "schema is not found". Surface
    # any literal use as an advisory warning — the fix is to switch to
    # `'node'` (the registered alias for "untyped node").
    any_type_warnings: list[str] = []
    pkg_path = ws.get("package_path")
    if pkg_path:
        pkg_dir = WS_ROOT / pkg_path
        if pkg_dir.exists():
            _any_pat = re.compile(r"""['"]_type['"]\s*:\s*['"]any['"]""")
            for py_file in pkg_dir.rglob("*.py"):
                try:
                    text = py_file.read_text()
                except OSError:
                    continue
                for lineno, line in enumerate(text.splitlines(), 1):
                    if _any_pat.search(line):
                        any_type_warnings.append(
                            f"WARN {py_file.relative_to(WS_ROOT)}:{lineno}: "
                            f"'_type': 'any' — use 'node' (registered alias); "
                            f"'any' raises 'schema is not found' inside the "
                            f"dashboard's subprocess runner. (v2ecoli friction #2)"
                        )

    # Composite hygiene: a "composite" that wraps a SINGLE process with no extra
    # wiring and no parameters is really a config preset of one process, not a
    # composition. Parameterize a config family into ONE @composite_generator
    # (parameters=...), or reference the process + config from the study.
    composite_warnings: list[str] = []
    _cpkg = ws.get("package_path") or ("viva_" + ws.get("name", "").replace("-", "_"))
    try:
        import importlib as _il
        _il.import_module(_cpkg)  # fire @composite_generator decorators
        from process_bigraph.composite_generator import (
            _REGISTRY as _CG, build_generator as _bg)
        for _gid, _entry in list(_CG.items()):
            if not _gid.startswith(_cpkg + "."):
                continue  # foreign generator
            # core_extensions hygiene: a generator that registers custom types or
            # processes must declare them via core_extensions=[...], or the
            # Composites drill-in — which realizes the generator in a BARE core —
            # fails with "cannot resolve type map[...]". We can't tell from here
            # whether a given generator *needs* extensions, so this is a WARN.
            if not (getattr(_entry, "core_extensions", None) or []):
                composite_warnings.append(
                    f"  WARN composite {_gid}: declares no core_extensions — if it "
                    f"registers custom types/processes, the Composites drill-in "
                    f"(bare core) fails with 'cannot resolve type map[...]'. Add "
                    f"core_extensions=[register_types, register_processes] to the "
                    f"@composite_generator.")
            if getattr(_entry, "parameters", None):
                continue  # a parameterized family — skip the single-process check
            try:
                _doc = _bg(_entry)
            except Exception:
                continue
            _procs = [k for k, v in _doc.items()
                      if isinstance(v, dict) and v.get("_type") == "process"
                      and "emitter" not in str(v.get("address", "")).lower()]
            if len(_procs) == 1:
                composite_warnings.append(
                    f"  WARN composite {_gid}: wraps a single process ({_procs[0]}) "
                    f"with no parameters — a config preset, not a composition. "
                    f"Parameterize a config family into ONE "
                    f"@composite_generator(parameters=...), or reference the process "
                    f"+ config from the study.")
    except Exception:
        pass  # process-bigraph too old / no generators — skip silently

    # Print summary
    ws_name = ws.get("name", "?")
    ws_pkg = ws.get("package_path", "")
    study_names_preview = ", ".join(study_names[:3])
    if n_studies > 3:
        study_names_preview += f", ... (+{n_studies - 3} more)"

    # Build study validation label. F-friction #6: when any study fails
    # dashboard load, the summary must say ✗ — the WARN lines underneath
    # already say "fails dashboard load", and the headline contradicting
    # them is exactly the bug.
    schema_check = "schema ✓" if study_schema is not None else "schema skipped"
    if not _dashboard_importable:
        dashboard_check = "dashboard skipped — install vivarium-workbench"
    elif n_studies_failed_dashboard > 0:
        dashboard_check = f"dashboard ✗ ({n_studies_failed_dashboard} failing)"
    else:
        dashboard_check = "dashboard ✓"

    print("workspace lint: OK")
    print(f"  workspace: {ws_name}  (package: {ws_pkg})")
    print(f"  {n_expert} expert_docs, {n_bib} bib keys, {n_claims} claims")
    if n_investigations == 0:
        print("  0 investigations")
    else:
        print(f"  {n_investigations} investigations: {inv_names_preview}")
    if n_studies == 0:
        print("  0 studies")
    else:
        print(
            f"  {n_studies} studies: {study_names_preview}  "
            f"(status: {status_summary})  "
            f"(validated: {schema_check}, {dashboard_check})"
        )
    print(f"  {n_active_runs} active runs, {n_completed_runs} completed runs")

    # Surface investigation validation warnings (one line per error). These
    # don't fail the lint — they're advisory; the dashboard does strict
    # validation when an investigation is loaded.
    for w in inv_warnings:
        print(w, file=sys.stderr)

    if not _dashboard_importable and n_studies > 0:
        print(
            "  (vivarium-workbench not importable; dashboard-equivalent validation skipped)",
            file=sys.stderr,
        )

    for w in study_warnings:
        print(w, file=sys.stderr)
    for w in any_type_warnings:
        print(w, file=sys.stderr)
    for w in composite_warnings:
        print(w, file=sys.stderr)


if __name__ == "__main__":
    main()
