# Module catalog

The **available-modules registry** (the dashboard's "what can I install" list)
is no longer vendored here. It ships as the single source of truth in
`viva-superpowers` (`pbg_superpowers/catalog/modules.json`) and the dashboard
reads it via `pbg_superpowers.catalog.load_registry`.

- **`overlay.json`** — optional, per-workspace. A JSON list of extra module
  entries (same shape as the canonical registry) that should appear in *this*
  workspace's registry only. Empty by default. An entry with a new `name`
  appends to the canonical list; a matching `name` overrides it.

To curate the ecosystem-wide registry, edit/regenerate it in viva-superpowers
(`python3 -m pbg_superpowers.catalog.sync_catalog`) and release.
