#!/usr/bin/env bash
# Guard against local laptop paths leaking into committed config.
#
# A recurring failure mode in pbg workspaces: a local editable install such as
#   vivarium-workbench = { path = "/Users/<you>/code/vivarium-workbench", ... }
# gets committed to pyproject.toml. uv then synthesizes a `file:///Users/...`
# URL that exists only on the author's laptop, so CI (and any fresh clone)
# fails with "Distribution not found at: file:///Users/...".
#
# This script greps the DEPENDENCY / CONFIG files for absolute local paths and
# fails fast with a clear message. It scans ONLY the files where a local path
# actually breaks a fresh clone — the dependency and build config. It does NOT
# scan docs, notebooks, generated reports/HTML, or generated run artifacts: a
# `/Users/...` string in a markdown note, a notebook's captured stderr, or a
# generated report is harmless (it is not a dependency source) and scanning
# them only produces false-positive CI failures. The reproducibility guard
# lives where reproducibility is decided: the config files below.
set -euo pipefail

# Run from the repo root so `git grep` sees the whole tracked tree regardless
# of the caller's working directory.
cd "$(git rev-parse --show-toplevel)"

# Patterns that should never appear in a committed dependency/config file.
# Assembled at runtime so this script does not match itself when it scans.
patterns=(
  "file:""///"        # local file:// URLs (uv expands path= sources to these)
  "/Users""/"         # macOS home dirs
  "/home""/"          # linux home dirs (laptop checkouts)
)

# Only the dependency/build config is scanned — the files where a leaked local
# path actually breaks `uv sync` / a fresh clone. (Everything else — docs,
# notebooks, generated reports, sim artifacts — may legitimately mention a path.)
include_pathspecs=(
  "pyproject.toml" "**/pyproject.toml"
  "uv.toml" "**/uv.toml"
  "uv.lock" "**/uv.lock"
  "requirements*.txt" "**/requirements*.txt"
  "setup.py" "setup.cfg"
  "Pipfile" "poetry.lock"
)

found=0
for pat in "${patterns[@]}"; do
  if git grep -n -- "$pat" -- "${include_pathspecs[@]}" 2>/dev/null; then
    found=1
  fi
done

if [ "$found" -ne 0 ]; then
  echo ""
  echo "ERROR: a local absolute path leaked into a dependency/config file (see above)."
  echo "       Use a git source / tool.uv.sources, never a committed local path."
  echo "       Replace local 'path = \"/Users/...\"' deps with a git source, e.g.:"
  echo "         vivarium-workbench = { git = \"https://github.com/vivarium-collective/vivarium-workbench.git\", branch = \"main\" }"
  echo "       For local development use an editable install in your venv instead:"
  echo "         uv pip install -e ../vivarium-workbench"
  exit 1
fi

echo "OK: no local absolute paths in dependency/config files."
