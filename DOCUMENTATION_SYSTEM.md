# Documentation System — Mark 1

*"Mark" = version of this system itself. This is the first formalized
version, written 2026-08-28. If it ever gets overhauled, that becomes
Mark 2, and this file gets superseded, not silently edited into
something unrecognizable.*

## The problem this fixes

Before this: work got logged into whatever file felt closest at the
time — `handoff_2026-08-17.md`, `session_2026-08-01_changes.md`,
`dapro-drive-session-report_2026-08-15.md`, ad-hoc one-off files, plus
`DONE.md` itself growing **six separate `## 2026-08-28` sections in one
day** because each work batch just prepended its own header instead of
writing into the day's existing one. No single place to look, no
consistent granularity.

## The rule

**One file. One dated section per calendar day. Nothing else.**

- File: `DONE.md`, repo root. This is now the ONLY place completed work
  gets logged going forward.
- One `## YYYY-MM-DD` heading per calendar day — never more than one
  for the same date. The first thing logged on a given day creates the
  heading; everything else logged that same day is a new bullet under
  the SAME heading, not a new one.
- Newest day at the top of the file (unchanged from before).
- Each bullet: **what** changed, **why** in one line, commit hash if
  there is one. Terse — this is a log, not a transcript. Detailed
  reasoning belongs in the code's own comments and commit messages,
  not duplicated here.
- No new `handoff_*.md`, `session_*.md`, `*_report_*.md`, or similar
  one-off topic files at the repo root. If something feels like it
  needs its own file, it almost always just needs a bullet in today's
  `DONE.md` section instead.

## What stays separate (not part of this system)

- **`TODO.md`** — forward-looking (pending/deferred work), not a log of
  what happened. Keep it distinct; don't merge the two.
- **Planning docs** (`roadmap_*.md`, `plan_*.md`) — living documents
  that get edited in place as a plan evolves, not dated log entries.
  `DONE.md` can reference them, but doesn't replace them.
- **`README.md` / `SETUP.md` / `PHONE.md`** — reference docs describing
  the project as it currently is, not a history of how it got there.

## Legacy files

Everything already at the repo root from before this system
(`handoff_2026-08-17.md`, `handoff_2026-08-22.md`,
`session_2026-08-01_changes.md`, `session_2026-08-12_wakeword_changes.md`,
`dapro-drive-friend-sharing-setup_2026-08-15.md`,
`dapro-drive-session-report_2026-08-15.md`) is left as-is — historical
record, not deleted, not migrated. Nothing new of that shape gets
created from here on; new entries go into `DONE.md` instead.

## Backfill note

`DONE.md`'s pre-existing entries (2026-08-27 and earlier same-day
batches on 2026-08-28) were written before this system existed and
follow the old per-batch-header habit — left as historical record
rather than rewritten. The six fragmented 2026-08-28 sections were
consolidated into one as part of adopting this system (see `DONE.md`'s
own top entry). Everything from 2026-08-28 onward follows the one-
section-per-day rule for real.
