#!/usr/bin/env python
"""Export every investigation's self-contained HTML report.

The report is built SERVER-SIDE by the vivarium-workbench route
``GET /api/investigation-report/<slug>`` — a single, deterministic, data-only
HTML document (every panel read from ``investigation.yaml`` / ``study.yaml`` /
loop-trajectory JSON; interactive figures inlined). This script serves the
dashboard, fetches that route for each investigation, validates the result, and
writes ``<out>/investigations/<slug>.html``.

(Historical note: reports used to be generated CLIENT-SIDE by driving the SPA's
``_generateInvestigationReport()`` button in headless Chromium. That function
was removed when generation moved server-side, so the old Playwright path now
times out — this script fetches the route directly instead. No browser needed.)

Flow:
  1. discover investigations under workspace/investigations/*/investigation.yaml
  2. serve the dashboard (`vivarium-workbench serve`) unless --url points at a
     running one
  3. for each investigation: GET /api/investigation-report/<slug> → write
     <out>/investigations/<slug>.html
  4. fail loudly if a report is implausibly small or missing its figure embeds

Usage:
  .venv/bin/python scripts/publish_investigation_reports.py \
      --workspace . --out reports/published
  # reuse an already-running dashboard:
  .venv/bin/python scripts/publish_investigation_reports.py --url http://localhost:52243

Exit code is non-zero if any investigation fails to produce a valid report, so
CI can gate on it.
"""
from __future__ import annotations

import argparse
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

# A report with figures must embed them; a report that lost its embeds has none.
# We treat "claims figures but embeds none" as a hard failure rather than
# silently publishing a stripped report.
MIN_REPORT_BYTES = 20_000


def discover_investigations(ws_root: Path) -> list[str]:
    inv_root = ws_root / "workspace" / "investigations"
    if not inv_root.is_dir():
        inv_root = ws_root / "investigations"  # flat-layout fallback
    if not inv_root.is_dir():
        return []  # workspace has no investigations dir yet — not an error
    return sorted(
        d.name for d in inv_root.iterdir()
        if d.is_dir() and (d / "investigation.yaml").is_file()
    )


def _load_investigation_yaml(ws_root: Path, slug: str) -> dict:
    """Read an investigation's investigation.yaml (nested or flat layout)."""
    for base in (ws_root / "workspace" / "investigations", ws_root / "investigations"):
        p = base / slug / "investigation.yaml"
        if p.is_file():
            return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return {}


def build_index_fragment(ws_root: Path, slugs: list[str]) -> str:
    """Build the ``<div class="invest">`` blocks for the gh-pages landing page.

    Generated from each investigation.yaml (title, status, a short description,
    study list) so the root gallery lists EVERY investigation that has a
    published report — no hand-curation. Injected between the
    ``auto-investigations`` markers in ``index.html`` by the publish workflow.
    """
    import html as _h

    blocks: list[str] = []
    for slug in slugs:
        spec = _load_investigation_yaml(ws_root, slug)
        title = str(spec.get("title") or slug)
        status_raw = str(spec.get("status") or "").strip()
        status_label = status_raw.replace("_", " ") or "—"
        status_class = status_raw or "in_progress"

        # Description: prefer executive.what_is_this, else the question; collapse
        # whitespace and truncate so the gallery card stays compact.
        execu = spec.get("executive") if isinstance(spec.get("executive"), dict) else {}
        desc = str(execu.get("what_is_this") or spec.get("question") or "").strip()
        desc = " ".join(desc.split())
        if len(desc) > 300:
            desc = desc[:297].rstrip() + "…"

        studies = [s.get("name") if isinstance(s, dict) else s
                   for s in (spec.get("studies") or [])]
        studies = [str(s) for s in studies if s]
        meta = f"{len(studies)} stud{'y' if len(studies) == 1 else 'ies'}"
        if 0 < len(studies) <= 4:
            meta += " · " + " · ".join(studies)

        blocks.append(
            '<div class="invest">\n'
            f'  <h3><a href="investigations/{_h.escape(slug)}.html">{_h.escape(title)}</a>\n'
            f'      <span class="pill {_h.escape(status_class)}">{_h.escape(status_label)}</span></h3>\n'
            f'  <p>{_h.escape(desc)}</p>\n'
            f'  <p class="meta">{_h.escape(meta)}</p>\n'
            '</div>'
        )
    return "\n\n".join(blocks)


def study_figure_count(ws_root: Path, slug: str) -> int:
    """How many figure HTMLs the investigation's studies reference on disk.

    Used only to decide whether a generated report SHOULD contain embeds, so we
    can flag a silently-stripped report. Counts committed figure files under
    reports/figures/<study>/ for each study in the investigation.
    """
    for base in (ws_root / "workspace" / "investigations", ws_root / "investigations"):
        inv_yaml = base / slug / "investigation.yaml"
        if inv_yaml.is_file():
            break
    else:
        return 0
    spec = yaml.safe_load(inv_yaml.read_text(encoding="utf-8")) or {}
    studies = [s.get("name") if isinstance(s, dict) else s
               for s in (spec.get("studies") or [])]
    fig_root = ws_root / "reports" / "figures"
    n = 0
    for st in filter(None, studies):
        d = fig_root / st
        if d.is_dir():
            n += len(list(d.glob("*.html")))
    return n


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_healthy(url: str, timeout: float = 60.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def _dashboard_binary(ws_root: Path) -> str:
    """Resolve the vivarium-workbench CLI: workspace .venv first, then PATH."""
    local = ws_root / ".venv" / "bin" / "vivarium-workbench"
    if local.exists():
        return str(local)
    found = shutil.which("vivarium-workbench")
    if found:
        return found
    raise RuntimeError(
        "vivarium-workbench CLI not found (looked in .venv/bin and PATH); "
        "install it with `uv sync` or `uv pip install vivarium-workbench`"
    )


def serve_dashboard(ws_root: Path, port: int) -> subprocess.Popen:
    cmd = [_dashboard_binary(ws_root), "serve",
           "--workspace", str(ws_root), "--port", str(port)]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if not _wait_healthy(f"http://127.0.0.1:{port}/", timeout=90):
        out = proc.stdout.read().decode(errors="replace")[-2000:] if proc.stdout else ""
        proc.terminate()
        raise RuntimeError(f"dashboard did not become healthy on :{port}\n{out}")
    return proc


def export_report(base_url: str, slug: str, out_path: Path,
                  expect_figures: bool) -> tuple[bool, str]:
    """Fetch one investigation's server-rendered report and write it. (ok, msg).

    ``GET /api/investigation-report/<slug>`` returns the full self-contained HTML
    document. We validate size + figure embeds BEFORE writing ``out_path`` so a
    failed/stripped report leaves no file — the gh-pages copy step then preserves
    the last-good published copy instead of clobbering it.
    """
    url = f"{base_url.rstrip('/')}/api/investigation-report/{urllib.parse.quote(slug)}"
    try:
        with urllib.request.urlopen(url, timeout=180) as r:
            status = r.status
            body = r.read()
    except urllib.error.HTTPError as e:
        detail = (e.read()[:200].decode(errors="replace")) if hasattr(e, "read") else ""
        return False, f"HTTP {e.code}{(': ' + detail) if detail else ''}"
    except Exception as e:  # noqa: BLE001
        return False, f"fetch failed: {e}"
    if status != 200:
        return False, f"HTTP {status}"
    html = body.decode("utf-8", errors="replace")
    size = len(body)
    if "<html" not in html.lower():
        # 404s / errors come back as JSON, not an HTML document.
        return False, f"not an HTML report ({size} B) — slug missing?"
    embeds = html.count("<iframe") + html.count("srcdoc") + html.count("data:image")
    if size < MIN_REPORT_BYTES:
        return False, f"report too small ({size} B < {MIN_REPORT_BYTES}); not published"
    if expect_figures and embeds == 0:
        return False, (f"{size} B but ZERO figure embeds while studies reference "
                       f"figures — report stripped; not published (kept last-good)")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(body)
    return True, f"{size:,} B, {embeds} embed-markers"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workspace", default=".", help="workspace root (default: .)")
    ap.add_argument("--out", default="reports/published",
                    help="output dir; reports written to <out>/investigations/<slug>.html")
    ap.add_argument("--url", default=None,
                    help="use an already-running dashboard at this URL instead of spawning one")
    ap.add_argument("--port", type=int, default=0, help="port to serve on (default: auto)")
    ap.add_argument("--only", default=None,
                    help="comma-separated investigation slugs to publish (default: all)")
    args = ap.parse_args()

    ws_root = Path(args.workspace).resolve()
    out_dir = Path(args.out).resolve()
    slugs = discover_investigations(ws_root)
    if args.only:
        want = {s.strip() for s in args.only.split(",")}
        slugs = [s for s in slugs if s in want]
    if not slugs:
        # A workspace with no investigations is a valid (early) state, not a
        # failure — exit cleanly so this non-gating deploy job stays green
        # instead of reddening (and emailing) on every push.
        print("no investigations found; nothing to publish", file=sys.stderr)
        return 0
    print(f"investigations: {', '.join(slugs)}")

    proc = None
    base_url = args.url
    try:
        if base_url is None:
            port = args.port or _free_port()
            print(f"serving dashboard on :{port} …")
            proc = serve_dashboard(ws_root, port)
            base_url = f"http://127.0.0.1:{port}"
        print(f"using dashboard at {base_url}")

        results: dict[str, tuple[bool, str]] = {}
        for slug in slugs:
            out_path = out_dir / "investigations" / f"{slug}.html"
            expect_figures = study_figure_count(ws_root, slug) > 0
            try:
                ok, msg = export_report(base_url, slug, out_path, expect_figures)
            except Exception as e:  # noqa: BLE001 — report per-slug, keep going
                ok, msg = False, f"exception: {e}"
            results[slug] = (ok, msg)
            print(f"  {'✓' if ok else '✗'} {slug}: {msg}")
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

    # Regenerate the landing-page investigation list from ALL discovered
    # investigations (not just this run's --only subset), so the gh-pages root
    # gallery always lists every investigation with a published report.
    all_slugs = discover_investigations(ws_root)
    fragment = build_index_fragment(ws_root, all_slugs)
    index_fragment_path = out_dir / "investigations_index.html"
    index_fragment_path.parent.mkdir(parents=True, exist_ok=True)
    index_fragment_path.write_text(fragment + "\n", encoding="utf-8")
    print(f"wrote landing-page fragment ({len(all_slugs)} investigations) to "
          f"{index_fragment_path}")

    failed = [s for s, (ok, _) in results.items() if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} reports published to "
          f"{out_dir / 'investigations'}")
    if failed:
        print(f"FAILED: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
