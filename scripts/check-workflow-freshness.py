#!/usr/bin/env python3
"""Guardrail against stale generated GitHub Actions workflows.

Workspaces are scaffolded from viva-template's ``template/.github/workflows/*``.
When those templates are fixed, existing workspaces keep running the OLD
generated workflows and their CI silently stays broken until someone
regenerates. This script CATCHES that drift.

The mechanism is a *provenance stamp* baked into every generated workflow — a
header comment block delimited by the markers::

    # >>> viva-template-provenance >>>
    ...
    # template: <source-filename-in-viva-template>
    # sha256: <canonical-hash-of-that-source>
    # <<< viva-template-provenance <<<

``sha256`` is the hash of the source template's *canonical content* — the file
with its own provenance block stripped out (so the human-readable comment text
in the block can change without churning the hash, and the hash covers only the
real workflow logic). Because the block carries no ``{{ }}`` placeholders, it
survives ``.j2`` rendering unchanged: the value baked in is always the hash of
the upstream source, even for ``workspace-ci.yml`` (rendered from
``workspace-ci.yml.j2``).

Three modes:

* ``--check-templates`` (run in the viva-template repo, wired into template-ci):
  assert every ``template/.github/workflows/*`` file's stamp matches its own
  canonical hash. This forces stamp maintenance — edit a template workflow and
  CI fails until you re-stamp. Mirrors the ``gen-readme --check`` staleness
  pattern. Fix with ``--update``.

* ``--update`` (run in viva-template): (re)write every stamp to the current
  canonical hash, inserting the block if missing. This is how you refresh after
  editing a template workflow.

* default / ``--check`` (run in a scaffolded workspace's CI): for every
  ``.github/workflows/*`` file carrying a stamp, fetch the CURRENT upstream
  template named by the stamp (from viva-template@<ref>, or a local
  ``--template-dir`` checkout) and fail if the upstream's canonical hash differs
  from the stamped hash — i.e. the workspace has drifted from the template it
  was generated from.

* ``--write`` (run in a scaffolded workspace): the SYNC / fix mode. Regenerate
  every STALE stamped workflow from the current upstream template, re-render it
  (deriving python-version from the workspace's requires-python) and re-stamp to
  the current hash. This is the centralized fix for the fan-out drift — run it
  instead of hand-copying files from viva-template.

Intentional forks: a workspace that must keep a workflow different from the
template (e.g. a per-repo rebrand) adds a ``# divergence: <reason>`` line inside
that file's provenance block. ``--check`` then reports it as intentionally
diverged instead of failing, and ``--write`` leaves it untouched.

Stdlib only; no third-party imports so it runs in a bare CI step.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

BEGIN_MARKER = "# >>> viva-template-provenance >>>"
END_MARKER = "# <<< viva-template-provenance <<<"

DEFAULT_REPO = "vivarium-collective/viva-template"
DEFAULT_REF = "main"
# Where the source workflow templates live inside viva-template.
UPSTREAM_WORKFLOWS_SUBPATH = "template/.github/workflows"

# The workflow files viva-template ships into a workspace, keyed by the source
# filename in viva-template. ``.j2`` files render to the extension-stripped name
# in the workspace but keep their stamp (which still names the ``.j2`` source).
TEMPLATE_WORKFLOWS = (
    "build-and-push.yml",
    "publish-dashboard.yml",
    "publish-reports.yml",
    "workspace-ci.yml.j2",
)

HUMAN_LINES = (
    "# This GitHub Actions workflow is GENERATED from viva-template. Do not edit",
    "# it by hand: your changes are lost the next time it is regenerated, and a",
    "# hand-edit is invisible to the drift guard.",
    "#",
    "# If viva-template's copy of this workflow changes, this file becomes STALE",
    "# and workspace CI flags it (scripts/check-workflow-freshness.py). Refresh by",
    "# re-scaffolding, or by copying the current source from",
    f"#   {DEFAULT_REPO}/{UPSTREAM_WORKFLOWS_SUBPATH}/<template>",
)


# ---------------------------------------------------------------------------
# Stamp parsing / canonical hashing
# ---------------------------------------------------------------------------

def strip_provenance_block(text: str) -> str:
    """Return ``text`` with its provenance block (markers inclusive) removed."""
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    in_block = False
    for line in lines:
        stripped = line.strip()
        if stripped == BEGIN_MARKER:
            in_block = True
            continue
        if stripped == END_MARKER:
            in_block = False
            continue
        if not in_block:
            out.append(line)
    return "".join(out)


def canonical_hash(text: str) -> str:
    """sha256 of the canonical (provenance-stripped, newline-normalized) content.

    Leading blank lines and a trailing-newline difference must not change the
    hash — otherwise inserting/removing the stamp block (which sits above the
    workflow body) would perturb it.
    """
    body = strip_provenance_block(text)
    body = body.lstrip("\n").rstrip() + "\n"
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def parse_stamp(text: str) -> dict | None:
    """Return ``{'template': ..., 'sha256': ...}`` from a file's stamp, or None."""
    lines = text.splitlines()
    in_block = False
    fields: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if stripped == BEGIN_MARKER:
            in_block = True
            continue
        if stripped == END_MARKER:
            break
        if in_block and stripped.startswith("#"):
            content = stripped[1:].strip()
            if ":" in content:
                key, _, value = content.partition(":")
                key = key.strip()
                if key in {"template", "sha256", "divergence"}:
                    fields[key] = value.strip()
    if "template" in fields and "sha256" in fields:
        return fields
    return None


def render_stamp(template_name: str, sha256: str) -> str:
    """Build the provenance block text (without a trailing blank line)."""
    body = [BEGIN_MARKER, *HUMAN_LINES,
            f"# template: {template_name}",
            f"# sha256: {sha256}",
            END_MARKER]
    return "\n".join(body) + "\n"


def apply_stamp(text: str, template_name: str) -> str:
    """Return ``text`` with a fresh provenance block for its canonical hash.

    Any existing block is removed first; the new block is prepended so the file
    starts with its provenance. The hash is computed over the stripped body, so
    re-stamping is idempotent.
    """
    stripped = strip_provenance_block(text).lstrip("\n")
    sha256 = canonical_hash(text)
    stamp = render_stamp(template_name, sha256)
    return stamp + "\n" + stripped


# ---------------------------------------------------------------------------
# Upstream template retrieval (for workspace drift check)
# ---------------------------------------------------------------------------

def fetch_upstream_template(template_name: str, repo: str, ref: str,
                            template_dir: Path | None) -> str | None:
    """Return the current upstream source text for ``template_name``.

    From a local ``template_dir`` checkout if given (no network), else from
    GitHub raw at ``repo@ref``. Returns None if it cannot be retrieved.
    """
    if template_dir is not None:
        candidate = template_dir / template_name
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
        return None
    url = (f"https://raw.githubusercontent.com/{repo}/{ref}/"
           f"{UPSTREAM_WORKFLOWS_SUBPATH}/{template_name}")
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 (fixed https host)
            return resp.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

def _iter_workflow_files(workflows_dir: Path):
    for path in sorted(workflows_dir.iterdir()):
        if path.is_file() and (path.suffix in {".yml", ".yaml"}
                               or path.name.endswith((".yml.j2", ".yaml.j2"))):
            yield path


def mode_check_templates(workflows_dir: Path) -> int:
    """viva-template self-check: every template stamp matches its own content."""
    stale: list[str] = []
    unstamped: list[str] = []
    checked = 0
    for path in _iter_workflow_files(workflows_dir):
        text = path.read_text(encoding="utf-8")
        stamp = parse_stamp(text)
        if stamp is None:
            unstamped.append(path.name)
            continue
        checked += 1
        expected = canonical_hash(text)
        if stamp["sha256"] != expected:
            stale.append(f"{path.name}: stamp {stamp['sha256'][:12]} "
                         f"!= actual {expected[:12]}")
        if stamp["template"] != path.name:
            stale.append(f"{path.name}: stamp template={stamp['template']} "
                         f"does not match filename")

    print(f"checked {checked} stamped template workflow(s) in {workflows_dir}")
    if unstamped:
        print("\nUNSTAMPED template workflows (add a provenance stamp with "
              "--update):", file=sys.stderr)
        for name in unstamped:
            print(f"  - {name}", file=sys.stderr)
    if stale:
        print("\nSTALE stamps (regenerate with: python "
              "scripts/check-workflow-freshness.py --update):", file=sys.stderr)
        for msg in stale:
            print(f"  - {msg}", file=sys.stderr)
        return 1
    if unstamped:
        return 1
    print("template workflow provenance: OK")
    return 0


def mode_update(workflows_dir: Path) -> int:
    """Rewrite every template workflow's stamp to its current canonical hash."""
    changed = 0
    for path in _iter_workflow_files(workflows_dir):
        text = path.read_text(encoding="utf-8")
        new_text = apply_stamp(text, path.name)
        if new_text != text:
            path.write_text(new_text, encoding="utf-8")
            print(f"stamped {path.name} -> {canonical_hash(text)[:12]}")
            changed += 1
        else:
            print(f"  ok  {path.name} (already current)")
    print(f"updated {changed} workflow stamp(s)")
    return 0


def mode_check_workspace(workflows_dir: Path, repo: str, ref: str,
                         template_dir: Path | None) -> int:
    """Workspace drift check: stamped hash vs CURRENT upstream template hash."""
    drifted: list[str] = []
    diverged: list[str] = []
    unresolved: list[str] = []
    checked = 0
    stamped_any = False
    for path in _iter_workflow_files(workflows_dir):
        text = path.read_text(encoding="utf-8")
        stamp = parse_stamp(text)
        if stamp is None:
            # A workflow with no stamp is not managed by this guard (e.g. a
            # repo-specific workflow the workspace added). Skip it silently.
            continue
        stamped_any = True
        if stamp.get("divergence"):
            # Intentionally forked from the template (e.g. a workspace-specific
            # rebrand). Reported, never failed, never auto-synced.
            diverged.append(f"{path.name} (diverged: {stamp['divergence']})")
            continue
        template_name = stamp["template"]
        upstream = fetch_upstream_template(template_name, repo, ref, template_dir)
        if upstream is None:
            unresolved.append(f"{path.name} (template {template_name})")
            continue
        checked += 1
        upstream_hash = canonical_hash(upstream)
        if stamp["sha256"] != upstream_hash:
            drifted.append(
                f"{path.name}: generated from {template_name}@"
                f"{stamp['sha256'][:12]} but {repo}@{ref} is now "
                f"{upstream_hash[:12]}")

    source = f"{template_dir}" if template_dir else f"{repo}@{ref}"
    print(f"checked {checked} stamped workflow(s) against {source}")

    if diverged:
        print("\nIntentionally diverged workflows (declared; not checked for drift):")
        for item in diverged:
            print(f"  - {item}")

    if unresolved:
        # Could not reach upstream — warn but do NOT fail (network flake or an
        # older pin). Mirrors the gen-readme step's availability guard.
        print("\nWARNING: could not retrieve upstream template for:",
              file=sys.stderr)
        for item in unresolved:
            print(f"  - {item}", file=sys.stderr)
        print("Skipping drift check for those (treated as non-fatal).",
              file=sys.stderr)

    if drifted:
        print("\nSTALE generated workflows — they no longer match "
              f"{repo}@{ref}:", file=sys.stderr)
        for msg in drifted:
            print(f"  - {msg}", file=sys.stderr)
        print("\nFix them in place with:\n"
              "  python scripts/check-workflow-freshness.py --write\n"
              "(regenerates each stale workflow from the current template and re-stamps; "
              "mark an intentional fork with a '# divergence: <reason>' line in its stamp "
              "to keep it and silence this guard for that file.)", file=sys.stderr)
        return 1

    if not stamped_any:
        print("no viva-template-stamped workflows found — nothing to check")
        return 0
    print("generated workflows are fresh relative to viva-template")
    return 0


# ---------------------------------------------------------------------------
# Sync mode (the centralized fix): regenerate stale workflows from the template
# ---------------------------------------------------------------------------

def _output_name(template_name: str) -> str:
    """Workspace filename for a template source (``*.yml.j2`` -> ``*.yml``)."""
    return template_name[:-3] if template_name.endswith(".j2") else template_name


def _derive_python_version(workflows_dir: Path) -> str:
    """CI python-version from the workspace's requires-python (mirrors template-init.sh:
    the first ``3.N`` in requires-python; N>=12 -> 3.12, else 3.11)."""
    pyproject = workflows_dir.parent.parent / "pyproject.toml"
    if pyproject.is_file():
        for line in pyproject.read_text(encoding="utf-8").splitlines():
            if re.match(r"\s*requires-python\s*=", line):
                m = re.search(r"3\.(\d+)", line)
                return "3.12" if (m and int(m.group(1)) >= 12) else "3.11"
    return "3.11"


def _render_workflow(body: str, workflows_dir: Path) -> str:
    """Render a template workflow body for this workspace. Workflow templates are
    substitution-light; the only historically per-workspace variable is ``python_version``
    (now usually hard-set upstream). Mirrors template-init.sh's substitution for forward-compat."""
    pv = _derive_python_version(workflows_dir)
    return body.replace("{{ python_version }}", pv).replace("{{python_version}}", pv)


def mode_sync_workspace(workflows_dir: Path, repo: str, ref: str,
                        template_dir: Path | None, write: bool) -> int:
    """Regenerate STALE stamped workflows from the current upstream template and re-stamp
    to the current hash. Intentionally-diverged workflows (a ``# template-divergence:`` line
    in the stamp) are left untouched. This is the centralized fix for the fan-out drift:
    run it in any workspace instead of hand-copying files."""
    synced: list[str] = []
    skipped_div: list[str] = []
    unresolved: list[str] = []
    up_to_date = 0
    for path in _iter_workflow_files(workflows_dir):
        text = path.read_text(encoding="utf-8")
        stamp = parse_stamp(text)
        if stamp is None:
            continue
        if stamp.get("divergence"):
            skipped_div.append(f"{path.name} (diverged: {stamp['divergence']})")
            continue
        template_name = stamp["template"]
        upstream = fetch_upstream_template(template_name, repo, ref, template_dir)
        if upstream is None:
            unresolved.append(f"{path.name} (template {template_name})")
            continue
        upstream_hash = canonical_hash(upstream)
        if stamp["sha256"] == upstream_hash:
            up_to_date += 1
            continue
        out_path = workflows_dir / _output_name(template_name)
        body = _render_workflow(strip_provenance_block(upstream).lstrip("\n"), workflows_dir)
        new_text = render_stamp(template_name, upstream_hash) + "\n" + body
        if write:
            out_path.write_text(new_text, encoding="utf-8")
        synced.append(f"{out_path.name} <- {template_name}@{upstream_hash[:12]}")

    verb = "synced" if write else "WOULD sync"
    extra = f"; {len(skipped_div)} intentionally diverged" if skipped_div else ""
    print(f"{verb} {len(synced)} stale workflow(s); {up_to_date} already fresh{extra}")
    for s in synced:
        print(f"  {verb}: {s}")
    for d in skipped_div:
        print(f"  kept (diverged): {d}")
    if unresolved:
        print("\nWARNING: could not retrieve upstream template for:", file=sys.stderr)
        for item in unresolved:
            print(f"  - {item}", file=sys.stderr)
        return 1 if write else 0
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _default_workflows_dir(check_templates: bool, update: bool) -> Path:
    """Pick a sensible default workflows dir for the mode + cwd."""
    template_dir = Path(UPSTREAM_WORKFLOWS_SUBPATH)
    workspace_dir = Path(".github/workflows")
    if check_templates or update:
        if template_dir.is_dir():
            return template_dir
        return workspace_dir
    # Workspace mode: prefer the workspace's own workflows, but fall back to the
    # template layout when run from a viva-template checkout.
    if workspace_dir.is_dir():
        return workspace_dir
    return template_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check generated GitHub Actions workflows for staleness "
                    "relative to viva-template.")
    parser.add_argument("--check-templates", action="store_true",
                        help="viva-template self-check: assert each template "
                             "workflow's stamp matches its own content.")
    parser.add_argument("--update", action="store_true",
                        help="(re)write template workflow provenance stamps.")
    parser.add_argument("--check", action="store_true",
                        help="workspace drift check (default when no mode given).")
    parser.add_argument("--write", action="store_true",
                        help="SYNC: regenerate stale workspace workflows from the current "
                             "upstream template and re-stamp (the centralized fix). Skips "
                             "workflows marked '# template-divergence:'.")
    parser.add_argument("--workflows-dir", type=Path, default=None,
                        help="directory of workflow files (auto-detected).")
    parser.add_argument("--repo", default=DEFAULT_REPO,
                        help=f"upstream repo (default: {DEFAULT_REPO}).")
    parser.add_argument("--ref", default=DEFAULT_REF,
                        help=f"upstream ref for the drift check (default: {DEFAULT_REF}).")
    parser.add_argument("--template-dir", type=Path, default=None,
                        help="compare against a local viva-template workflows "
                             "checkout instead of fetching from GitHub.")
    args = parser.parse_args(argv)

    workflows_dir = args.workflows_dir or _default_workflows_dir(
        args.check_templates, args.update)
    if not workflows_dir.is_dir():
        print(f"ERROR: workflows dir not found: {workflows_dir}", file=sys.stderr)
        return 2

    if args.update:
        return mode_update(workflows_dir)
    if args.check_templates:
        return mode_check_templates(workflows_dir)
    if args.write:
        return mode_sync_workspace(workflows_dir, args.repo, args.ref,
                                   args.template_dir, write=True)
    return mode_check_workspace(workflows_dir, args.repo, args.ref,
                                args.template_dir)


if __name__ == "__main__":
    raise SystemExit(main())
