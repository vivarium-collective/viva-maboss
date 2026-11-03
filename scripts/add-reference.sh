#!/usr/bin/env bash
# Stage 5: add a BibTeX reference + claim mapping.
# Prompts for a BibTeX entry, validates the key is unique, writes to references/papers.bib.
# Optionally maps the key to one or more claim IDs in references/claims.yaml.
set -euo pipefail

WS_ROOT="$(pwd)"
[ -f "$WS_ROOT/workspace.yaml" ] || { echo "ERROR: workspace.yaml not found; run from workspace root" >&2; exit 1; }

echo "Paste the BibTeX entry (end with a single line containing only EOF):"
BIB_TMP="$(mktemp)"
while IFS= read -r line; do
  [ "$line" = "EOF" ] && break
  echo "$line" >> "$BIB_TMP"
done

# Extract the citation key
BIB_KEY="$(python3 -c "
import re, sys
text = open('$BIB_TMP').read()
m = re.search(r'@\w+\{([A-Za-z0-9_:-]+),', text)
if not m: sys.exit('ERROR: could not extract citation key from BibTeX entry')
print(m.group(1))
")"
echo "extracted key: $BIB_KEY"

python3 -c "
import re, sys
from pathlib import Path
ws_root = Path('$WS_ROOT')
bib_path = ws_root / 'references' / 'papers.bib'
existing = bib_path.read_text() if bib_path.exists() else ''
existing_keys = set(re.findall(r'@\w+\{([A-Za-z0-9_:-]+),', existing))
if '$BIB_KEY' in existing_keys:
    sys.exit(f\"ERROR: BibTeX key '$BIB_KEY' already in papers.bib; edit by hand or change the key\")
new_text = open('$BIB_TMP').read().rstrip() + '\n'
with bib_path.open('a') as f:
    f.write('\n' if existing and not existing.endswith('\n') else '')
    f.write(new_text)
print(f'appended {bib_path}')
"

rm -f "$BIB_TMP"

read -rp "map this key to claims? (comma-separated claim IDs, blank to skip): " CLAIMS
if [ -n "$CLAIMS" ]; then
  python3 -c "
import yaml
from pathlib import Path
ws_root = Path('$WS_ROOT')
yp = ws_root / 'references' / 'claims.yaml'
data = yaml.safe_load(yp.read_text()) or {'claims': {}}
for c in [s.strip() for s in '$CLAIMS'.split(',') if s.strip()]:
    existing = data['claims'].get(c)
    if existing is None:
        data['claims'][c] = '$BIB_KEY'
    elif isinstance(existing, list):
        if '$BIB_KEY' not in existing:
            existing.append('$BIB_KEY')
    else:
        data['claims'][c] = [existing, '$BIB_KEY'] if existing != '$BIB_KEY' else existing
yp.write_text(yaml.safe_dump(data, sort_keys=False))
print(f'updated {yp}')
"
fi

python3 scripts/lint-workspace.py
echo "✓ reference '$BIB_KEY' added."
