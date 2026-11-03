# notes/ — field notes, friction logs, walkthroughs

This directory is the canonical home for **human-readable and
agent-written field notes** about the work happening in this workspace.

## What lives here

- `notes/friction/<date>-<topic>.md` — friction logs from running studies
  (what slowed you down, what failed silently, what was confusing).
- `notes/walkthroughs/<date>-<topic>.md` — step-by-step accounts of how a
  particular flow actually played out, contrasted with how the docs say
  it should.
- `notes/agent-notes/<date>-<agent>.md` — transcripts or summaries from
  background agents (e.g., a long-running investigation Claude) that
  capture decisions and observations the agent made.
- `notes/decisions/<id>-<short>.md` — one-shot architecture or scoping
  decisions, recorded as ADR-style entries.

You can add subdirectories for your own categories — the convention is
soft.

## **Cleanup rule** — do not delete

> Files under `notes/` are FIELD RECORDS. They are not "planning docs"
> and they are not bloat. Cleanup sweeps (`chore(cleanup): …`,
> `chore(repo): trim …`) **MUST NOT** delete files in this directory.

This rule exists because the dashboard's feedback-cycle workflow
depends on these notes surviving long enough for a different session /
different agent to read them and act on them. Two prior cleanup commits
(`4a54c23`, `abc9655` in v2ecoli) deleted notes that were the entire
input to the next round of infrastructure improvements; recovering them
required git archaeology.

If a specific note is genuinely obsolete and must go, the deletion
should be in its own commit (not bundled with unrelated cleanup) with
a one-line justification per deleted file.

## Agents reading this file

When you encounter a `chore(cleanup)` PR proposal that would delete
files under `notes/`, refuse the deletion and ask the user to confirm
on a per-file basis with justification.
