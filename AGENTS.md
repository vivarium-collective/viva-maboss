# AGENTS.md

Guidance for AI coding assistants (and humans following the same flow)
working in this workspace. Read this before opening a PR.

Workspaces built from [viva-template] inherit this file as the seed; extend
it as the workspace grows (see v2ecoli's AGENTS.md for a fully-fleshed
example covering process-bigraph composition, biological domain rules,
etc.). The conventions below are **framework-wide** — they apply to every
viva-superpowers workspace.

[viva-template]: https://github.com/vivarium-collective/viva-template

## What this workspace is

`maboss` is a viva-superpowers workspace. The Python package
lives under `viva_maboss/`; investigations live under
`workspace/investigations/<slug>/`; per-study YAML + runs live under
`workspace/studies/<slug>/`. See [`README.md`](README.md) and
[`NEXT_STEPS.md`](NEXT_STEPS.md) for the workspace's purpose + onboarding
flow.

## PR conventions

Two distinct PR types live in any viva-superpowers workspace, and they
look different on GitHub:

### Feature / fix PRs (merge candidates)

Standard `gh pr create`. Conventional-commit-style title (`feat(...)`,
`fix(...)`, `chore(...)`, `docs(...)`). Opens **ready-for-review** (not
draft). Targets `main`. CI must pass before merge. Live examples: most
PRs on `main`'s log.

These ship infrastructure: composites, processes, listeners, visualizations,
schema bumps, documentation. They are the unit of change that lands on
`main`.

### Investigation PRs (living integration branches)

A long-running branch hosting an **investigation** (a collection of
related studies under a shared research question). **NOT a merge target**
— the branch lives indefinitely; infrastructure carved out of it ships
via separate feature PRs against `main`.

Two conventions:

1. **Title prefix `investigation:`** — e.g.
   `investigation: <slug> — <one-line research question>`. Visually
   distinguishes investigation branches from feature PRs in the
   GitHub PR list.

2. **Draft state** — open with `gh pr create --draft …` (or, when
   creating from the vivarium-workbench GitHub tab, the `draft=True`
   default applies automatically). Draft signals "don't merge me" to
   both reviewers and to GitHub's auto-merge / branch-policy machinery.

The PR body should call out:

- That this branch is NOT a merge target.
- Companion **feature PRs** (already merged) that ship the infrastructure
  this investigation depends on, vs. **content** that stays on this
  branch (study YAMLs, expert PDFs, rendered reports, references).
- The investigation's primary research question + headline figures.
- The current "verdict" (see the v4 narrative spine's `executive.verdict`
  + `report.verdict` fields on the constituent studies).

### When in doubt: feature first, investigation second

If a change is reusable across investigations — a new Process class, a
schema field, a dashboard endpoint, a script other workspaces could
adopt — it belongs in a **feature PR** against `main`, NOT on an
investigation branch. The investigation branch then consumes the merged
infrastructure rather than carrying it.

## Close-investigation workflow

When an investigation is done (research question answered, narrative
written, report rendered), run:

    /viva-investigation close <slug>

This stamps the investigation YAML with `status: closed`, populates
`contributors[]` (derived from `git log` + agent sessions), copies the
rendered HTML report into `workspace/investigations/<slug>/report.html` (git-
tracked artifact), commits on the branch, and opens a PR. **Never auto-
merges.** Reviewer clicks merge in the GitHub UI.

See `/viva-investigation close --help` for details.
