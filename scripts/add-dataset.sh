#!/usr/bin/env bash
# Stage 4: register a dataset in the workspace.
# Appends an entry to workspace.yaml.datasets — the canonical, schema-validated
# registry. (datasets/_index.yaml was retired in v0.1.5.)
set -euo pipefail

WS_ROOT="$(pwd)"
[ -f "$WS_ROOT/workspace.yaml" ] || { echo "ERROR: workspace.yaml not found; run from workspace root" >&2; exit 1; }

read -rp "dataset name: " DS_NAME
[[ "$DS_NAME" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "ERROR: name must match [A-Za-z0-9._-]+" >&2; exit 1; }

read -rp "source path (relative to workspace) OR url [path]: " DS_SOURCE
read -rp "is that a path or a url? [path/url]: " DS_KIND
DS_KIND="${DS_KIND:-path}"

DS_SHA=""
if [ "$DS_KIND" = "url" ]; then
  read -rp "sha256 of the file at <url> (required for url-based datasets): " DS_SHA
  [ -n "$DS_SHA" ] || { echo "ERROR: sha256 required for url-based datasets" >&2; exit 1; }
fi

read -rp "claims served (comma-separated, e.g. phase-1.dnaA-accumulation): " DS_CLAIMS

python3 -c "
import sys
from pathlib import Path
from vivarium_workbench.lib.workspace_yaml import load_workspace, save_workspace, WorkspaceValidationError
from vivarium_workbench.lib._root import set_workspace_root
set_workspace_root('$WS_ROOT')

ws_root = Path('$WS_ROOT')
ws_file = ws_root / 'workspace.yaml'
ws = load_workspace(ws_file)

entry = {'name': '$DS_NAME', 'claims': [c.strip() for c in '$DS_CLAIMS'.split(',') if c.strip()]}
if '$DS_KIND' == 'path':
    entry['path'] = '$DS_SOURCE'
else:
    entry['url'] = '$DS_SOURCE'
    entry['sha256'] = '$DS_SHA'

datasets = ws.setdefault('datasets', [])
for d in datasets:
    if d.get('name') == '$DS_NAME':
        sys.exit(f\"dataset '$DS_NAME' already registered in workspace.yaml.datasets; edit by hand to amend\")
datasets.append(entry)

try:
    save_workspace(ws_file, ws)
except WorkspaceValidationError as e:
    sys.exit(f'workspace.yaml validation failed after add: {e}')

print(f'appended workspace.yaml.datasets[\"$DS_NAME\"]')
"

python3 scripts/lint-workspace.py
echo "✓ dataset '$DS_NAME' registered. Next: scripts/add-reference.sh to record the source paper(s)."
