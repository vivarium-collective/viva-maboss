# Next steps for maboss

This workspace IS the model. You build it incrementally through phases.
Most work happens in the **dashboard** — a side-rail of tabs, each with
the buttons you need. Skills (Claude Code) are the alternative for
code-writing tasks. The set of tabs evolves as the dashboard grows;
see what's live at the URL printed by `scripts/serve.sh`.

```
bash scripts/serve.sh   # opens browser at http://localhost:<port>
```

## 0 — One-time setup

- [ ] Create the venv: `uv venv .venv`
- [ ] Activate it: `source .venv/bin/activate`
- [ ] Install workspace deps: `uv pip install -e ".[dev]"`
- [ ] Lint: `python3 scripts/lint-workspace.py` should print `workspace lint: OK`
- [ ] Commit + (eventually) push: `git init && git add -A && git commit -m "feat: workspace bootstrap"`

> **If `uv pip install` fails with "vivarium-workbench was not found in
> the package registry":** vivarium-workbench isn't on PyPI yet. The
> template's init script ALWAYS pins it to its public git source in
> `pyproject.toml` (never a committed local path — that would break CI,
> Docker, and collaborators who lack the sibling checkout):
>
> ```toml
> [tool.uv.sources]
> vivarium-workbench = { git = "https://github.com/vivarium-collective/vivarium-workbench.git", branch = "main" }
> ```
>
> If that block is missing, add it and re-run `uv pip install -e ".[dev]"`.
> For local dev against a sibling checkout, override with an editable install
> into your venv (no committed local path required):
>
> ```bash
> uv pip install -e ../vivarium-workbench
> ```

## The dashboard tabs

> The dashboard's tab set evolves as the platform grows. The list below
> reflects the rail at the time this template was rendered; the source
> of truth is the live UI you opened at `http://localhost:<port>/`.

| Rail label | Hash route | What it's for |
|---|---|---|
| **Workspace inputs** | `#workspace-inputs` | External resources — datasets, references (PDFs auto-extract metadata), expert docs. Simulation modules live in the Registry tab. |
| **Registry** | `#registry` | Browse the curated catalog (`scripts/_catalog/modules.json`), install viva-* modules, and view live `build_core()` introspection of discovered Processes/Steps/Types. |
| **Composites** | `#simulation-setup` | Browse and explore composites available in the workspace. Each composite packages a runnable simulation state with parameters you can configure. |
| **Investigations** | `#investigations` | Declarative research recipes — pick a composite, declare simulations (single / sweep / seeds), name observables, choose visualizations. Run, save, compare. |
| **Visualizations** | `#visualizations` | Charts rendered from observable trajectories. Configure a registered Visualization class with specific settings, or describe a chart in natural language and let `/viva-viz` generate it. |
| **GitHub Branches** | `#branches` | All `stage/*` branches in the workspace with one-click merge / PR / diff actions. Branches accumulate as the dashboard creates them on actions (Add observable, Install module, …); use this tab to land them on main. |

(Note: the **Composites** tab still routes under `#simulation-setup` for
backwards compatibility — the rail label was renamed but the URL fragment
wasn't. Use the table above when bookmarking focused panels.)

> **Composite vs. process — a modeling convention.** A composite *composes* — it
> wires two or more processes together (or adds emitters / structure). A "composite"
> that wraps a **single process** with a hardcoded config is really a *config preset
> of one process*, not a composition. For a family of configs of the same process
> (e.g. the same engine at different parameters), register **ONE** parameterized
> composite and pass params from each study:
>
> ```python
> @composite_generator(name="recruitment",
>     parameters={"cue_rate": {...}, "chemo_lambda": {...}})
> def recruitment(core=None, cue_rate=10, chemo_lambda=14):
>     return composite_document(cue_rate, chemo_lambda)
> # studies pass params -> one composite, not one-per-config
> ```
>
> `scripts/lint-workspace.py` warns on single-process composites that carry no
> parameters, so this stays visible as the workspace grows.

## 1 — Workspace inputs (any time)

Inputs aren't a sequential stage — load them whenever they're useful.

- **Datasets** — experimental data the model validates against. Drag-drop a file.
- **References** — paper PDFs. Drop the file; pypdf extracts metadata; bibtex auto-generates.
- **Expert docs** — lab notes, curated reviews, working drafts. Drag-drop a PDF.

Simulation modules (Python packages that contribute Processes/Steps) are
installed from the **Registry** tab, not here.

Each `+ Add` lands on a `stage/*` branch you can merge from the GitHub Branches tab.

## 2 — Registry

Two coordinated views on one tab:

- **Available modules** — the curated `viva-*` catalog in `scripts/_catalog/modules.json`, regenerated from the `vivarium-collective` GitHub org by `scripts/_catalog/sync-catalog.py`. Click **Install** to `git submodule add` into `external/<name>/`, `pip install -e` it into the workspace venv, and append it to `pyproject.toml` deps + `workspace.yaml.imports`.
- **Discovered processes / types** — live introspection of `build_core()`. Every Process/Step/Emitter/Visualization/Type that `allocate_core()` finds via [bigraph-schema's discovery convention][discovery]. Click **Refresh registry** after an Install to see new entries.

Empty Discovered view usually means the venv isn't built (`uv pip install -e ".[dev]"`) or the import isn't installed.

## 3 — Composites

Composite documents — process-bigraph state trees — live here. Pick one, inspect its wiring (process tree, store paths, emitters), and run it from the **Composite Explorer** sub-page to confirm it produces sensible output before wiring it into an investigation. Composites you write directly into `viva_maboss/composites/` show up automatically.

## 4 — Studies and Investigations

A **study** (`studies/<slug>/study.yaml`) is one research question, with a
`phase:` of `Design | Build | Simulate | Evaluate | Decide`. Use `/viva-study
<slug>` from the [viva-superpowers] plugin to scaffold one. Decide-phase
studies can record `followup_proposals[]`; seed a child study from any
proposal with `/viva-study seed-from-followup <parent>/<proposal_id>` (the
child gets a `seeded_from:` provenance stamp).

### The study.yaml narrative spine (schema v4)

A complete study has 14 sections, grouped into three layers. Fill them in
roughly top-to-bottom — sections marked **★** are the ones most worth
authoring first because the dashboard renders them at the top of the report
and a reviewer can land on a study without reading the YAML.

**Executive layer** (renders at the top of the study page)
1. `runtime:` — per-study execution overrides (subprocess_timeout_s, default_emitter, max_generations, post_run_scripts). Skip if you're using workspace defaults.
2. **★ `report:`** — `{title, verdict, confidence, evidence_quality, objective, conclusion, main_insight, caveat, key_metrics: [...]}`. The exec summary a reviewer reads first.
3. **★ `study_card:`** — `{goal, mechanism, why_before_next, expected_result, main_expert_question}`. One paragraph each. The dashboard one-pager.

**Framing layer** (what + why)
4. **★ `question:`** + `assumptions:` — the hypothesis, plus the literature facts the study assumes (each with `cites` + `verified_in_v2ecoli` flag).
5. **★ `conditions:`** — `{baseline, variants, model_settings: [...], expert_inputs: [...]}`. `model_settings` is the tunable-parameter catalog (each with default/current/range/cites). v3 specs put baseline/variants at top level; v4 groups them under `conditions:`.
6. `enforced_params:` — parameter values the study REQUIRES be applied. The framework verifies after each run and flags drift.

**Validation layer** (what would falsify it)
7. **★ `tests:`** (a.k.a. `behavior_tests:` for v3 compat) — `[{name, classification, measure: {path, kind, window}, pass_if: {op, low/high}, status, cites}]`. Each test ties a measurable outcome to a literature-grounded threshold.
8. **★ `readouts:`** — `[{name, store_path, units, status, blocked_by_requirements}]`. The observables the tests read from. `store_path` is the exact emission path in the composite output.
9. `biological_summary:` — multi-paragraph plain-English mechanism prose. The textbook write-up.
10. `literature_anchors:` — `[{expectation, model_observable, source, status_in_workspace}]`. Pairs each literature claim with the model observable that would falsify or confirm it.

**Implementation + decisions layer**
11. `model_change:` — declarative inventory: `{base_model, new_processes, new_state_variables, new_parameters, new_listeners, modified_processes}`. What code changes.
12. `implementation_requirements:` — `[{id, kind, title, effort, status, steps, unblocks}]`. The TODO list.
13. `design_pivot_required:` — named open decision points with alternatives + `requested_response`. Surfaces the choices an expert can weigh in on.
14. **★ `conclusion_verdicts:`** — three-track verdict: `{regression_compatibility, biological_validation, explanatory_gain}` each `{result, basis}`. Lets a study be "PASS on regression but MIXED on biology" instead of forcing one boolean.

### The investigation.yaml narrative spine (schema v2)

An **investigation** (`investigations/<slug>/investigation.yaml`) groups
related studies and orders them as a DAG. A complete investigation pairs the
study list with a narrative spine that mirrors the study spine at a level up.
Use `/viva-investigation <slug>` to scaffold.

- `executive:` — `{what_is_this, verdict, verdict_status, decisions_needed: [...]}`. The headline panel.
- `scientific_argument:` — `{main_claim, evidence_for, evidence_against, key_figures, caveats}`. The chain of reasoning.
- `lead:` — 3-4 sentence intro (first thing a reader sees).
- `biological_story:` — multi-paragraph mechanism prose.
- `at_a_glance:` — `[{study, role: '<one-line role>'}]`. What each member study contributes.
- `how_to_read:` — evaluator tips.
- `glossary:` — `[{term, definition}]` for investigation-local terms.
- `guidelines:` — literature anchors, parameter catalog, calibration targets every member study respects.
- `studies:` — ordered slug list. The DAG topology is derived from each study's `pipeline_gate.prerequisites`.
- `acceptance_criteria:` — `[{study, behavior}]` referencing `behavior_tests[].name` on member studies.

You do NOT have to fill every section before running. Start with `report` +
`study_card` + `question` + `conditions` + `tests` + `readouts` (the ★
sections). Add the rest as the study matures.

The legacy per-investigation "run a composite + observables + visualizations"
flow still lives in the dashboard's Investigations tab — pick a composite,
declare simulations (single / sweep / seeds), name observables, run, save,
compare.

## 5 — Visualizations

Charts rendered from observable trajectories. Two creation paths:

- **Configure a registered class** — pick from the Visualization classes discovered in the Registry (subclasses of `pbg_superpowers.visualization.Visualization` in any installed viva-* package), give it settings.
- **Generate from natural language** — describe what you want; the dashboard writes a request file and prompts you to run `/viva-viz <name>`, which scaffolds a new Visualization function into `viva_maboss/visualizations/` and commits it on a stage branch.

## 6 — GitHub Branches

Every dashboard action (install a module, add an observable, generate a viz, …) creates a `stage/*` branch and commits the change there. This tab is the landing UI:

- **Copy gh** — `gh pr create` command for that branch.
- **Copy git merge** — local merge command, no PR.
- **Show diff** — preview without leaving the tab.

When all your in-flight stage branches are merged, the rail badge clears.

### Container image (GHCR)

Each push triggers `.github/workflows/build-and-push.yml`, which builds your workspace `Dockerfile` and publishes `ghcr.io/<owner>/<repo>:<tag>` (tags: `<branch>`, `pr-N`, `sha-<short>`, `latest` on default). Images are multi-arch (`linux/amd64` + `linux/arm64`), so Apple Silicon contributors get native pulls.

**Anonymous `docker pull` — visibility depends on who owns the repo:**

- **Org-owned repos** (e.g. `vivarium-collective/<workspace>`): the workflow's `Sync GHCR package visibility` step auto-syncs the package visibility to match the source repo on every push. If your repo is public, `docker pull ghcr.io/<owner>/<repo>:<tag>` works anonymously immediately. Nothing else to do.

- **User-owned repos** (e.g. `<your-username>/<workspace>`): GitHub's REST API does **not** allow the workflow's default `GITHUB_TOKEN` to flip user-owned-package visibility (it requires a PAT with `write:packages` scope). The very first push will publish the package as **private** and emit a `::notice::` in the workflow log explaining this. Two ways to make anonymous pulls work:
  1. **One-time UI link** (recommended, no PAT needed). On `github.com`, open your profile's **Packages** tab → click the package → **Package settings** → **Manage Actions access** → connect this repository → under **Danger Zone** → **Change package visibility** set to `public`. After that one-time link, every future push inherits visibility from the repo automatically.
  2. **`GHCR_PAT` secret** (full automation). Create a Personal Access Token (classic) with `write:packages` scope, add it as a repo secret named `GHCR_PAT`. The workflow auto-detects the secret and uses it instead of `GITHUB_TOKEN` for the visibility step.

If you intentionally want the image private even though the repo is public, leave `GHCR_PAT` unset and skip the one-time UI link.

## Two paths to the same workspace

- **Browser dashboard** is the primary UI. Pure-CLI workflows are supported via `python3 scripts/lint-workspace.py` + direct YAML edits, but the buttons handle branch-creation and PR setup for you.
- **Claude Code + [viva-superpowers]** adds skills that pair with the dashboard: `/viva-study` and `/viva-investigation` (scaffold a study or investigation), `/viva-viz` (generate a visualization), `/viva-expert` (wrap a simulator; pass `--lightweight` for in-workspace, or omit for a sibling repo + composite form via `<name> <tools…>`), `/viva-report` (regenerate per-model reports). The dashboard and the skills share the same `workspace.yaml`.

## Config files to know

- `workspace.yaml` — canonical state (observables, simulations, visualizations, imports, datasets, …)
- `studies/<slug>/study.yaml` — one research question (8 canonical sections, `phase:` lifecycle)
- `investigations/<slug>/investigation.yaml` — DAG of studies via `pipeline_gate.prerequisites`
- `references/{papers.bib, claims.yaml}` — bibliography + claim → paper mapping
- `.pbg/schemas/{workspace,study,investigation}.schema.json` — JSON schemas (lint enforces them)

## Focused dashboard panels

Open just one panel for a targeted interaction:

| URL | What |
|---|---|
| `http://localhost:<port>/?focus=workspace-inputs` | Datasets / references / expert docs |
| `http://localhost:<port>/?focus=registry` | Module catalog + installed modules |
| `http://localhost:<port>/?focus=simulation-setup` | Composites browser (legacy route name) |
| `http://localhost:<port>/?focus=investigations` | Investigation specs + runs |
| `http://localhost:<port>/?focus=visualizations` | Visualization lifecycle |
| `http://localhost:<port>/?focus=branches` | GitHub Branches lander |
| `http://localhost:<port>/?focus=composite-explore&id=<composite>` | A single composite's explorer |

Skills in Claude Code can `open <url>` to surface a focused interaction without dropping users into the full rail.

## Run on sms-api (remote HPC)

Use this path to run a composite on a remote sms-api instance (e.g. AWS
GovCloud via `vivarium-workbench`).  Because the container runner installs
the workspace from git, **all processes must live in the committed
`viva_maboss` package and the HEAD must be pushed.**

1. Commit everything and push the branch:
   ```bash
   git add -A && git commit -m "chore: ready for remote run"
   git push origin HEAD
   ```
2. Launch from the dashboard CLI:
   ```bash
   vivarium-workbench run-remote <composite-id>
   ```
   This will:
   - Verify the working tree is clean and HEAD is on the remote.
   - Export the composite to a portable `.pbg` file (full import-path
     addresses: `local:!viva_maboss.<Module>.<ClassName>`).
   - Submit to sms-api with `extra_pip_deps=["git+<origin>@<sha>"]` so
     the Singularity container installs `viva_maboss` from the
     exact committed revision.
   - Poll until the job completes, then download `results.zip` containing
     `final_state.json` and any emitter output.

> **Tip:** The dashboard `SmsApiClient` reads the sms-api URL and credentials
> from the workspace's `workspace.yaml` (or the `SMS_API_URL` env var).
> Point `vivarium-workbench serve` at a live sms-api endpoint before running.

## Publishing (read-only, hosted)

This workspace ships with auto-publish CI. On every merge to `main`:

- **`publish-dashboard.yml`** builds the static read-only dashboard SPA and pushes it to the `gh-pages` branch at `dashboard/`.
- **`publish-reports.yml`** renders each investigation's self-contained HTML report (headless Playwright driving the SPA) and pushes them to `gh-pages` at `investigations/<slug>.html`, plus a landing index.

Both derive the repo name / links from the GitHub repo automatically — nothing to edit. They are deploy jobs, not PR gates: a failure never blocks a merge.

**One-time setup:** after the first publish run creates the `gh-pages` branch, go to **Settings → Pages → Source = "Deploy from a branch" → `gh-pages` / `(root)`**. The dashboard is then served at `https://<user>.github.io/maboss/dashboard/` and reports at `…/maboss/investigations/<slug>.html`.

Preview the published bundle locally (no server, no base-path):

```bash
scripts/publish_dashboard.sh /tmp/dash && python -m http.server -d /tmp/dash 8080
```

## When stuck

1. `python3 scripts/lint-workspace.py` — most workspace-shape issues surface here.
2. Look at the **Next step** banner at the top of the dashboard — it points at the right tab.
3. File an issue at <https://github.com/vivarium-collective/viva-superpowers/issues>.

[discovery]: https://github.com/vivarium-collective/viva-superpowers/blob/main/docs/conventions/discovery.md
[viva-superpowers]: https://github.com/vivarium-collective/viva-superpowers
