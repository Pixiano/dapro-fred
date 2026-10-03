# 2026-10-04

- Phase 5 (weekly meta-agent) **designed**, not built: [phase5_weekly_agent_design.md](phase5_weekly_agent_design.md). Decisions from Vatsal: tool-using agent with memory-only tools, week map from embeddings, local GPU model, runs until done (with a hard safety cap), Sunday night, digest pulled by Vatsal, traits only as proposed hypotheses used silently.
- Rule saved: keep `memory_system_overview.md` updated after every memory change.
- Weekly model: not my choice, the master plan already names gpt-oss-20b (MXFP4, ~13-14 GB) for weekly and Qwen3.8-9B-Distill for nightly; VRAM does not fit beside FRED so it runs in a window with FRED's own models unloaded. Recorded in the design doc; to be verified at build time.

## Phase 5 built: `weekly.py` + `schema_weekly.sql` (2026-10-04)
- Weekly meta-agent as designed ([design](phase5_weekly_agent_design.md)): week map built by code (daily logs, FAISS topic clusters with the closest earlier match, state/graph changes, open review items, active inferences), then a tool loop with 14 memory-only tools; one JSON action per turn, repair message once, 3 bad replies end the run, 60-call safety cap then only `finish`, fallback digest from the daily logs.
- **Tools are written straight into the system prompt, no embedding tool router** (only about a dozen tools; the router exists for FRED's ~110 real tools). Embeddings still serve `search_memory`, `find_similar_events` and the week-map clusters.
- Code-enforced rules: an inference needs real supporting events; a trait_hypothesis needs 2+ events, confidence <= 0.5, can never become `supported` from the weekly run; duplicates of active inferences are refused; `support_weeks` counts distinct weeks (same week twice counts once); `finish` warns once if review items or active inferences were not reviewed.
- Review rulings: apparent/historical_change close the item; `real` stays open and is shielded from hygiene expiry; unknown stays open. Traits are kept out of the digest by prompt.
- `run_weekly` is idempotent per week (`weekly_runs`), a quiet week skips the loop, a failed run (e.g. model cannot load) keeps written inferences and retries. `run_weekly_pending` handles Sunday-ending weeks before today.
- `SemanticIndex.similar(event_id)` added. Self-check: `python weekly.py` (scripted fake model covers bad JSON, unknown entity, fake evidence, trait with one event, duplicate, finish warning, `real` ruling, idempotence, expiry shield, support_weeks, trait promotion block, runaway loop, quiet week).
- Not yet: the real gpt-oss-20b behind `llm` (integration; Vatsal tested the model separately and it works), the Sunday timer, VRAM load/unload window, summarising an overflowing loop context.
- **Added by Vatsal:** the consolidation models (nightly, weekly, monthly, ...) get only the memory tools; more specific and detailed memory tools may be added later. Marked in the final phase (integration) to decide, see [memory build order](../02/memory_build_order.md).

## Phase 6 built: `personal_model.py` + `schema_model.sql` ([design](phase6_personal_model_design.md))
- Items built from weekly inferences behind a code gate (tier weeks 1/4/8, >= 2 supporting events, counter-evidence <= 1/3 of support); model wording call in batches; light/full update modes; demotion by code; delivery by `relevant(text)` (automatic, no model call, nothing injected when nothing is close); `show/correct/add/remove` with locked user items. Self-check: `python personal_model.py`.
- `inferences` got a `tier` column; the weekly agent can raise it, never lower it.
- Not yet: pipeline wiring, spoken commands, monthly schedule (Phase 7).
- **Added by Vatsal:** the small models make many decisions, so build **very verbose, detailed logging of every model decision and command** (prompt in, raw reply, parsed action, tool call, result, what the code accepted or overrode). Parked for a later phase, see the build order. Already logged today: `inference_log`, `model_item_log`, `model_versions`, `weekly_runs`, `consolidation_runs`.

## Phase 7 designed (not built): [monthly + yearly consolidation](phase7_monthly_yearly_design.md)
- Decisions: monthly on the 1st, yearly on Vatsal's birthday (date not given yet, stored privately, never in the repo); life chapters (monthly proposes, yearly refines, user chapters locked); yearly = same model at highest effort with a bigger budget plus a second critique pass (read as "1+3", to confirm); imported history is undated so it stays raw, no replay, a backfill mode only once dates can be trusted.
- One engine: monthly and yearly reuse the weekly tool loop as subclasses with their own opening map, prompt and extra tools (state_at, compare_states, chapters, turning points, model_diff).
- Built now from this decision: `consolidate.py` skips `kind="historical"` archive records, so undated imports never leak into live days. Self-check updated.

- Phase 7 design updated: the yearly agent is a map-and-reduce (12 month passes writing year notes, then the whole-year run, then the critic pass) per Vatsal's idea; the effort choice was confirmed as highest effort plus the critic pass; the birthday anchor was given and is stored privately (not in this repo).
