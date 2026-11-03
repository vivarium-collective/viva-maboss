# Datasets

Datasets are registered as entries in `workspace.yaml.datasets` (the
schema-validated, lint-checked registry). Each entry maps a dataset name to:
- `path` (relative to the workspace) for in-tree files, OR
- `url` + `sha256` for files fetched from elsewhere
- `claims` — list of model claim IDs this dataset serves

Add a dataset with:

    bash scripts/add-dataset.sh

…or with the `viva-superpowers` plugin's `/viva-data <model>` skill.

Storage rules:

- Files <10MB are committed to git directly under a per-dataset subdir
  (e.g. `datasets/<name>/raw.csv`). Reference them with `path: datasets/<name>/`.
- Larger files use `url:` + `sha256:` pointers; `scripts/lint-workspace.py`
  checks that every URL-backed dataset declares a sha256 (it doesn't fetch).

Per-dataset subdirs (e.g. `datasets/bremer-1996/`) hold raw inputs +
preprocessing scripts + processed outputs. The convention is:

    datasets/
    ├── README.md          (this file)
    └── <dataset-name>/
        ├── raw/...        (original sources, never edited)
        ├── prep.py        (preprocessing — produces processed/)
        └── processed/...  (model-ready outputs)
