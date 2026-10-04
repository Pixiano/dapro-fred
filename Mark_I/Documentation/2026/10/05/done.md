# Done 2026-10-05

## Phase 8: deep self-analysis
- `selfanalysis.py` + `schema_selfanalysis.sql`: read-only whole-memory agent (subclass of the year agent) that answers what it understands about Vatsal that he may not; four labelled claim kinds with evidence rules enforced in code; saved as a report (`latest_report`), on request and quarterly (`run_selfanalysis`, `run_selfanalysis_pending`); strong inference claims go straight into the personal model (`PersonalModel.add_analysis`, source `analysis`). Design: [phase8_self_analysis_design.md](phase8_self_analysis_design.md). Self-check: `python selfanalysis.py`.
- Not covered yet: backup module does not have a dedicated part for the self-analysis reports (they are in the sqlite snapshot); the report is not yet in the md/pdf parts.

## Decision log
- `decisionlog.py`: verbose, always-on log of every model prompt, reply, tool action, code override and model failure in the private database. Hooked into the tool loops (`Week._dlog`, `Week.call`), consolidation, recall judge, personal-model decisions and year notes. Self-check: `python decisionlog.py`. Details in the overview.
- Still parked: more specific memory tools (decide in the final phase).

## Small gaps fixed, backfill mode
- **Long days:** `consolidate._chunks`: a day over `MAX_CHARS` goes to the model in parts; every part is validated before anything is written.
- **Multi-hop search:** `Graph.path(a, b, at, max_hops)`; analysts get `find_connection` (weekly, monthly, yearly, self-analysis).
- **Undo merge:** `Graph.merge` journals moved rows in `merge_moves`; `Graph.unmerge(entity)` restores them (state closures, aliases, tags, relationships, events). Merges made before this have no journal and refuse to undo.
- **Out-of-order facts:** `MemoryStore.set_state` slots an older fact into history instead of closing the current value.
- **Backfill mode:** `trust_dates` (Vatsal vouches for a date range, never automatic) + `backfill_pending` (oldest first; names only, no contradictions, appends to an existing live daily log, source kind `backfill`); live consolidation ignores backfilled events. Self-checks in `consolidate.py`, `graph.py`, `store.py`, `weekly.py`.
- **Left as they are:** other-script names and nicknames still ask (asking beats guessing); old archive days are never compressed (the archive is never edited).

## Backups: encryption, restore, schedule
- `backup.py`: `password=` seals a backup into one `.fredbak` (AES-256-GCM chunks, scrypt key; tamper, truncation and wrong password detected); `verify_backup(path, password)`; `restore(src, dest_root, password)` rebuilds a memory folder from a full backup (manifest verified first, refuses a non-empty destination, `.partial` then rename, integrity check, reports archive files the backup lacked); `run_backup_pending(mem, out_dir, every_days, password)` is the timer entry point (full backup, no PDFs, only when the newest is older than every_days; nothing is deleted automatically); CLI `restore | verify | scheduled | --encrypt` with the password from `FRED_BACKUP_PASSWORD` or a prompt. Self-check in `backup.py` (round trip from folder and from sealed file, wrong password, bit flip, truncation, chunk boundaries, schedule).
- Left out on purpose: restoring a single part into a live memory, automatic pruning of old backups, key files/keyrings (the integration step decides where the password lives).

## Backups: restore one part, cleanup, default folder
- `restore_part(mem, src, part, password, overwrite)`, `prune_backups(out_dir, keep)`, `run_backup_pending(..., keep=8)` prunes after a successful backup, `DEFAULT_BACKUP_DIR = ~/FRED_Backups`, CLI `restore-part` and `prune`. Plain backups stay the default; encryption is opt-in per backup and the password is never stored by FRED (given through the environment or a prompt). Self-checks in `backup.py`.

## Yearly alerts
- `alerts.py` (personal alerts in the private database; public days from `calendar_india.json`), `gen_calendar.py` (builds the calendar for 2026-2035), self-check `python alerts.py`. Vatsal's own list is kept in a private file outside the repo and loaded with `load_seed` at integration; unsure dates are stored as unsure and asked about once; FRED's own suggestions need his yes (`confirm`/`reject`).
- Web-checked festival dates (2026-2030) fill gaps in the holidays package; Dhanteras/Bhai Dooj dropped (sources disagreed).
- January recompute of movable days only: `recompute_year`, `run_alerts_pending(mem, fetch, today)` (tables `alert_calendar`, `alert_refreshes`); self-check in `alerts.py`.
- FRED's own birthday: a script date scan of `Old/` found the earliest FRED file on 2025-09-18 (another early file 19 Sep); settled by Vatsal as 16 Sep 2025 (first local model download; only the 16th is used).

## Curation tools and proposal queue
- 14 new tools for the memory agents (`memtools.py`: ten read-only quality tools in every agent, four proposal tools for the critic and the new maintenance agent) and the proposal engine with tiering (`maintenance.py`): sure, undoable merges apply themselves and are reported; everything else waits for Vatsal (asked in conversation or via `python maintenance.py review`). Unanswered proposals never expire, rejected ones stay on file and never return (`history`), they are in the backup's review part (md/pdf, restorable), nothing is deleted. Self-check: `python maintenance.py`. History of the example name was also scrubbed from Mark I commits and force-pushed on request (2026-10-05).

## Review hardening
- A larger model (Opus) reviewed all the memory code adversarially (27 findings, 23 reproduced with probe scripts). Fixed: 1 critical, 6 high, 12 medium and several low ones, each with a regression assertion in the module self-checks; the list and what was left alone is in the overview ("Review hardening"). Re-running the review's own probe scripts now shows the intended refusals.

## Synthetic year and oracle run
- An Opus agent wrote 120 days of invented chat with a 16-trap answer key (`Mark_I/Initial/memory/testdata/synthetic_year/`). `python run_synthetic.py` replays it through the pipeline with an oracle model and scores the code-decided traps: all pass. It exposed that one-letter-apart names (Pranay/Pranav) were still auto-merged; fixed in `graph._variant`. The local-model runs (daily, weekly, monthly, yearly, self-analysis on this data) are not done yet and wait for Vatsal's go.
