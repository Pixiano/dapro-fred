# Documentation System — Mark 2

*Adopted 2026-09-27 (Vatsal's call). Supersedes Mark 1 (2026-08-28),
summarised at the bottom.*

## The rule

**One folder per calendar day: `docs/YYYY/MM/DD/`.** Everything produced
or learned that day goes in that folder — work log, decisions,
research, benchmark results, session notes, memory. As many files as the
day needs; never a second folder for the same day.

```
docs/
└── 2026/
    └── 09/
        └── 27/
            ├── done.md              <- what changed today (always present)
            ├── benchmark_baseline.md
            └── research_....md
```

- `done.md` is the one file every day folder has: terse bullets, **what**
  changed, **why** in one line, commit hash if there is one.
- Other files are named by topic, no date in the name (the folder is the
  date).
- A day with nothing done gets no folder.

## What stays outside the day folders

- **`TODO.md`** (root) — forward-looking, pending/deferred work.
- **Living planning docs** (`plan_*.md`, `roadmap_*.md` already at the
  root) — edited in place as plans evolve. New plans written on a given
  day go in that day's folder.
- **Reference docs** (`README.md`, `SETUP.md`, `PHONE.md`) — the project
  as it is now.

## Legacy

- `DONE.md` — the Mark 1 log, frozen 2026-09-27. Its 2026-09-27 entry
  moved to `docs/2026/09/27/done.md`.
- The older one-off files at the root (`handoff_*.md`, `session_*.md`,
  `dapro-drive-*`) stay as historical record.

## Mark 1 (superseded)

One `DONE.md` at the root with one `## YYYY-MM-DD` section per day, no
one-off topic files. Replaced because a single growing file doesn't give
each day room for more than a log (research, results, notes).
