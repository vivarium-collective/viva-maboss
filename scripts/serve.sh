#!/usr/bin/env bash
# Thin shim around the vivarium-workbench pip package.
#
# The dashboard runtime (server + templates + assets + lib helpers) was
# extracted out of viva-template into the standalone `vivarium-workbench`
# package. Workspaces now depend on it as a regular pip dep; this script
# is just a convenience wrapper so `bash scripts/serve.sh` keeps working.
set -euo pipefail

WS_ROOT="$(pwd)"
[ -f "$WS_ROOT/workspace.yaml" ] || { echo "ERROR: run from workspace root" >&2; exit 1; }

# Prefer the workspace venv (matches the viva-template scaffolding flow);
# fall back to a system-wide install if the venv has no vivarium-workbench.
if [ -x "$WS_ROOT/.venv/bin/vivarium-workbench" ]; then
    DASH="$WS_ROOT/.venv/bin/vivarium-workbench"
else
    DASH="$(command -v vivarium-workbench 2>/dev/null || true)"
fi

if [ -z "$DASH" ]; then
    echo "ERROR: vivarium-workbench is not installed." >&2
    echo "Install it into the workspace venv, e.g." >&2
    echo "    .venv/bin/pip install vivarium-workbench" >&2
    echo "or, for local dev:" >&2
    echo "    .venv/bin/pip install -e /path/to/vivarium-workbench" >&2
    exit 2
fi

exec "$DASH" serve --workspace "$WS_ROOT"
