# Done 2026-10-05

## Phase 8: deep self-analysis
- `selfanalysis.py` + `schema_selfanalysis.sql`: read-only whole-memory agent (subclass of the year agent) that answers what it understands about Vatsal that he may not; four labelled claim kinds with evidence rules enforced in code; saved as a report (`latest_report`), on request and quarterly (`run_selfanalysis`, `run_selfanalysis_pending`); strong inference claims go straight into the personal model (`PersonalModel.add_analysis`, source `analysis`). Design: [phase8_self_analysis_design.md](phase8_self_analysis_design.md). Self-check: `python selfanalysis.py`.
- Not covered yet: backup module does not have a dedicated part for the self-analysis reports (they are in the sqlite snapshot); the report is not yet in the md/pdf parts.

## Decision log
- `decisionlog.py`: verbose, always-on log of every model prompt, reply, tool action, code override and model failure in the private database. Hooked into the tool loops (`Week._dlog`, `Week.call`), consolidation, recall judge, personal-model decisions and year notes. Self-check: `python decisionlog.py`. Details in the overview.
- Still parked: more specific memory tools (decide in the final phase), yearly alerts.

## Small gaps fixed, backfill mode
- **Long days:** `consolidate._chunks`: a day over `MAX_CHARS` goes to the model in parts; every part is validated before anything is written.
- **Multi-hop search:** `Graph.path(a, b, at, max_hops)`; analysts get `find_connection` (weekly, monthly, yearly, self-analysis).
- **Undo merge:** `Graph.merge` journals moved rows in `merge_moves`; `Graph.unmerge(entity)` restores them (state closures, aliases, tags, relationships, events). Merges made before this have no journal and refuse to undo.
- **Out-of-order facts:** `MemoryStore.set_state` slots an older fact into history instead of closing the current value.
- **Backfill mode:** `trust_dates` (Vatsal vouches for a date range, never automatic) + `backfill_pending` (oldest first; names only, no contradictions, appends to an existing live daily log, source kind `backfill`); live consolidation ignores backfilled events. Self-checks in `consolidate.py`, `graph.py`, `store.py`, `weekly.py`.
- **Left as they are:** other-script names and nicknames still ask (asking beats guessing); old archive days are never compressed (the archive is never edited).
