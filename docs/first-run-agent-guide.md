# maboss + vivarium-workbench — first-run agent guide

> **You are a coding agent (Claude Code / Cursor / …). A first-time user handed
> you this file to get them onto the `maboss` + vivarium-workbench
> stack.** Your job: take them from zero to a *running* workbench with one of
> this workspace's composites open in the loom viewer, then orient them and
> offer next steps. Execute the **runbook** below gate-by-gate — run the
> commands yourself where the environment allows, verify each gate's success
> condition, and tell the user what you confirmed. After it's running, use the
> reference sections (authoring, contributing, troubleshooting) as their goals
> require.
>
> **Operating rules:**
> - Run project code through the workspace venv (`.venv/bin/…`) — **never** a
>   bare `python`.
> - Confirm each runbook gate before advancing; if one fails, consult
>   *Troubleshooting* (§8) first, don't improvise a workaround.
> - Ask before anything destructive (deleting `out/`, force-push, merging a PR).
> - Commands assume macOS/Linux.

---

## What these things are

- **This workspace (`maboss`)** — a
  [process-bigraph](https://github.com/vivarium-collective/process-bigraph)
  research workspace. Instead of one monolithic program, the model is a set of
  **typed, independently-wireable processes** you *compose* into whatever
  architecture a question needs. The Python package lives under
  `viva_maboss/` (`core.py` exposes `build_core()`); research state lives
  under `workspace/` — **composites** (wirings), **investigations** (a research
  question), and **studies** (the simulations that answer it).

- **[vivarium-workbench](https://github.com/vivarium-collective/vivarium-workbench)** —
  a local web UI over any process-bigraph workspace. Browse the process/type
  **registry**, **explore composites visually** (the embedded *bigraph-loom*
  graph viewer), view and run **studies**, and read **investigation reports**.
  Every action commits to a git branch in the workspace, so there's a full audit
  trail.

- **[viva-superpowers](https://github.com/vivarium-collective/viva-superpowers)** —
  a Claude Code plugin whose `/viva-*` skills drive the workbench's HTTP API so
  an agent can author and run the workspace conversationally (add composites,
  studies, investigations; run sims; regenerate reports). This is *the* intended
  way an agent adds content (§6).

---

## 0. Zero-install look (optional)

If the workspace has already published to GitHub Pages, the fastest orientation
is the read-only dashboard — no clone needed. The link is in the top of the
workspace `README.md` once the first publish has run (`Settings → Pages → Source
= gh-pages`). If it isn't published yet, skip to §1.

## 1. Clone

```bash
git clone <this repo's URL> maboss
cd maboss
```

**Gate:** `workspace.yaml` and `viva_maboss/core.py` exist.

## 2. Install (uv, into a workspace venv)

This workspace pins its dependencies with [`uv`](https://docs.astral.sh/uv/)
(`uv.lock`), and its `pyproject.toml` sources vivarium-workbench so the
`vivarium-workbench` CLI is available after sync.

```bash
uv sync                      # creates .venv/ and installs locked deps
uv pip install -e .          # editable install of this workspace's package
```

**Gate:** `.venv/bin/python -c "import viva_maboss; print('ok')"` prints
`ok`, and `.venv/bin/vivarium-workbench --help` runs.

## 3. Serve the workbench

```bash
bash scripts/serve.sh        # or: .venv/bin/vivarium-workbench serve --workspace .
```

It renders once, then serves on a free port and prints the URL.

**Gate:** the server prints a `http://127.0.0.1:<port>` URL and the page loads.
Tell the user the URL.

## 4. Open a composite in the viewer

In the running dashboard, go to **Composites** and open one of *this
workspace's* composites (the list is generated in the README's *Composites*
table and shown on the dashboard's Composites page). It renders as an
interactive **bigraph-loom** graph — nodes are processes/stores, edges are typed
port wirings.

**Gate:** a composite's wiring graph renders in the loom viewer. This is the
"it's running" milestone — confirm it to the user.

## 5. Orient

Point the user at the four things the dashboard exposes:

- **Registry** — the process and type definitions this workspace registers
  (from `viva_maboss/core.py`'s `build_core()`).
- **Composites** — the wirings; each opens in the loom viewer.
- **Investigations** — research questions under `workspace/investigations/<slug>/`,
  each with its studies and a published report.
- **Studies** — per-question simulations under `workspace/studies/<slug>/`, with
  their runs, gates, and outcomes.

## 6. Authoring (the intended path)

Add content through the `/viva-*` skills rather than hand-editing files — they
drive the workbench API and keep the workspace valid:

- `/viva-study <slug>` — start a study (spec with `phase: Design|Build|Simulate|Evaluate|Decide`).
- `/viva-investigation <slug>` — group related studies into an investigation
  (DAG via `inputs.from` / `pipeline_gate.prerequisites`).
- `/viva-expert <tool>` — wrap a simulator as a process-bigraph Process/Step.
- `/viva-viz` — generate a Visualization from a natural-language description.
- `/viva-report` — regenerate the workspace reports.

After adding composites or investigations, regenerate the README tables:

```bash
.venv/bin/vivarium-workbench gen-readme --workspace .
```

## 7. Contributing

Read **[AGENTS.md](../AGENTS.md)** before opening a PR — it defines the two PR
types (feature/fix merge candidates vs. living investigation branches), the
conventional-commit style, and the CI gate. In short: branch off `main`, keep
changes scoped, ensure CI is green (including `gen-readme --check`), open
ready-for-review, and never auto-merge.

## 8. Troubleshooting

- **`ImportError` for `viva_maboss` in the dashboard** — the editable
  install didn't run or ran into the wrong venv. Re-run `uv pip install -e .`
  and confirm `.venv/bin/python -c "import viva_maboss"`.
- **"Composite not found" / a composite fails to resolve** — the serving venv is
  missing a workspace dependency, or `build_core()` raised. Check the server log;
  re-run `uv sync`.
- **`vivarium-workbench: command not found`** — you're outside the venv. Use
  `.venv/bin/vivarium-workbench` or activate the venv.
- **Port already in use** — `serve.sh` picks a free port; if you pinned one,
  choose another with `--port`.
- **CI fails on `gen-readme --check`** — the README tables are stale. Run
  `vivarium-workbench gen-readme --workspace .` and commit the result.

When a UI detail here doesn't match the running app, trust the app and update
this file.
