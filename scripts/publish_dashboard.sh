#!/usr/bin/env bash
# Build the read-only workbench snapshot — a self-contained static SPA bundle
# (every investigation + study + assets) that anyone can browse with no server.
# Built by `vivarium-workbench-publish`; the same build is used locally (to
# preview) and by .github/workflows/publish-dashboard.yml (to publish to
# gh-pages:workbench/).
#
# GENERIC: base-path + interactive-url are passed in (the CI workflow derives
# them from the GitHub repo), so this script is workspace-agnostic — it ships
# with every workspace scaffolded from the template, no per-repo edits.
#
# Usage:
#   scripts/publish_dashboard.sh [OUT_DIR] [BASE_PATH] [INTERACTIVE_URL]
#     OUT_DIR          default: reports/published/workbench
#     BASE_PATH        e.g. /<repo>/workbench for GitHub Pages' project subpath
#                      (omit for a root-served bundle)
#     INTERACTIVE_URL  link back to the live/interactive repo (optional)
#
# Preview locally (no base-path → served at the root):
#   scripts/publish_dashboard.sh /tmp/dash
#   python -m http.server -d /tmp/dash 8080   # -> http://localhost:8080/
#
# Needs `vivarium-workbench-publish` on PATH (run via the workspace .venv, or
# `uv run`).
set -euo pipefail

WS_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-$WS_ROOT/reports/published/workbench}"
BASE_PATH="${2:-}"
INTERACTIVE_URL="${3:-}"

rm -rf "$OUT"

args=(--workspace "$WS_ROOT" --out "$OUT")
[ -n "$BASE_PATH" ] && args+=(--base-path "$BASE_PATH")
[ -n "$INTERACTIVE_URL" ] && args+=(--interactive-url "$INTERACTIVE_URL")

# The workspace's own package must be importable for build_core() registration.
PYTHONPATH="$WS_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
  vivarium-workbench-publish "${args[@]}"

# Strip bigraph-loom source maps (~half the bundle) — a read-only viewer never
# needs them — and disable Jekyll so files starting with _ are served.
find "$OUT" -name '*.map' -delete
touch "$OUT/.nojekyll"

echo "built read-only workbench bundle at $OUT ($(du -sh "$OUT" | cut -f1))"
