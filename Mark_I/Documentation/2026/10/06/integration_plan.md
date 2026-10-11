# Memory freeze and FRED integration plan (2026-10-06)

## Freeze
The Mark I memory pipeline is frozen as of 2026-10-06 (commit `6eb1b54`): no new features, only fixes for bugs the integration finds. State: all self-checks pass; precedents, ask queue and two-project recall built and tested on invented chats (gpt-oss low, Qwen3-8B, Gemma); the nightly/weekly/monthly roles are chosen (see `2026/10/05/done.md`). Not part of the freeze: the daily news digest (Core, `news`, designed 2026-10-11), real-history dry run (ask Vatsal first), known review gaps M1, H4, M3, M4, L3, L4, L6, L8.

## Integration (FRED current code stays untouched until each step is agreed; one step at a time, tested before the next)
Every item below is a socket the memory side already exposes. What FRED's side looks like has to be read from FRED's own code first (not done yet): do that before step 1.

| # | Step | Socket on the memory side | Needs from FRED |
|---|---|---|---|
| 1 | Feed the archive | `Archive.write(stream, data, session, ts)` | each producer (conversation, tool use, ASR, errors) calls it |
| 2 | Models | one adapter over the playground (`http://localhost:8090/v1`): `llm(prompt)` and `llm(messages)`; embedder `local/qwen3-embedding-0.6b-q8_0` (1024-d; changed from the 4B on 2026-10-07) | the playground started by FRED's launcher; GPU load window for the nightly/weekly jobs |
| 3 | Timers | `run_pending` (nightly), weekly/monthly/yearly runners, `run_maintenance_pending`, `run_backup_pending`, `run_alerts_pending`, `run_selfanalysis_pending` | one scheduler that respects "FRED stays idle while a job holds the GPU" |
| 4 | Recall in the turn | `judged_search(graph=...)`, `PersonalModel.relevant(text)` | the talker says "digging deeper" through `say`; groups/`follow_up` from two-project answers spoken |
| 5 | Spoken commands | `PersonalModel.show/correct/remove`, `restore`, backup, `Graph.correct` ("X is Y") | intent routing |
| 6 | Doubts at conversation end | `askqueue.ask(mem, topic, turns, decider)`, `answer(..., recorder)`, `report`, `undo`, `settle` | the "conversation is ending" signal; FRED speaks the question and offers a pill popup with a text box or the HUD; a reply goes to `answer` |
| 7 | Precedents wiring | `precedents.recorder`, `decide` with gpt-oss at low reasoning (fallback Qwen3-8B) | schema-forced JSON for the three call types (see `prec_e2e.py` pattern in the 2026-10-06 notes) |
| 8 | Digest and alerts | weekly digest footer, `alerts.due` | where FRED says them (morning, on request) |

Open decisions for Vatsal at integration: where the memory data folder lives, when the nightly window runs, what the pill popup looks like, how much the talker may interrupt.
