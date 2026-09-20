# Daily devlog — instructions

## Why this exists

Three earlier attempts at keeping a work log each drifted into their own
mess: `DONE.md` (one file, grown to 300+ lines, no natural stopping point),
`handoff_*.md` (one-off files scattered loose at the repo root, created only
for sessions that felt "big enough"), and `fred-full-documentation_*/`
(a folder that got started once and abandoned). Work kept landing in
whichever of these felt convenient that day, which is the same as landing
nowhere consistent. This replaces all three going forward.

## The rule

**One file per calendar day: `devlog/YYYY-MM-DD.md`.** Whatever happened
that day — FRED code changes, DaPro Drive ops, anything — goes in that
day's file and nowhere else. Never split a day's work across multiple
files. Never invent a new file or folder for a "special" session; if it
happened that day, it goes in that day's file, full stop.

## Format

```
# YYYY-MM-DD

- **What changed** (file/component/system) — why, and the concrete outcome.
  Commit hash if there is one.
- **Next thing** — same shape.
```

No sub-headers needed — the date is already the filename. Keep entries
terse and concrete: what changed, why it mattered, what the actual result
was. Same density `DONE.md`'s entries already had — that part worked, only
the "one giant file forever" part didn't.

## When to write

As soon as something real is done — not batched from memory at the end of
the day. If a session spans midnight, split at the calendar date; each file
covers one date.

## Old files

`DONE.md`, `handoff_*.md`, and `fred-full-documentation_*/` are left as
historical record, not deleted, not backfilled here. This log starts from
its first entry in `devlog/`, not from the project's beginning.
