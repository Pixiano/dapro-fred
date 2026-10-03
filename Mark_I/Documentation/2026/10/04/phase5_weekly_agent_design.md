# Phase 5 design: the weekly meta-agent (2026-10-04)

Status: DESIGN ONLY, not built. Decisions below come from Vatsal's answers on 2026-10-04.

## Decisions (Vatsal)
- **Tool-using agent, many calls, memory tools only.** It starts from a map of the week built from the week's embeddings.
- **Focus:** projects, routines/habits, decisions -> outcomes, relationships.
- **Traits, values, motivations:** the weekly agent may write them only as `proposed` hypotheses with evidence; promotion needs several weeks of recurrence (decided by Claude, Vatsal was undecided). They are used silently: FRED never recites them or asks about them; the personal model shapes how it answers.
- **Model:** local, on GPU (so: strict, simple tool protocol; the GPU-only rule applies: if it does not fit, flag and defer, never kill processes).
- **Budget:** until the agent decides it is done, plus a hard safety cap so a loop cannot run forever.
- **Schedule:** Sunday night, after that day's nightly consolidation.
- **Surfacing:** a weekly digest Vatsal pulls. Real contradictions wait in a review folder. No nudges.

## In plain words
Once a week, a stronger model reads the week like an analyst. It first gets a one-page map of the week (what topics came up, what changed, what is still open). Then it investigates: it forms a guess ("Vatsal works on FRED mostly at night"), searches memory for evidence **and** for evidence against it, then keeps, weakens or drops the guess. When it is satisfied it writes a short digest. It also rules on the "check later" items piling up in the review list. A guess about personality is kept only as a hypothesis, and only grows into something FRED relies on after it keeps showing up week after week.

## What it receives first: the week map (built by code, no model)
1. **Daily logs** for the 7 days.
2. **Topic clusters:** the week's event vectors are grouped (FAISS k-means, k about 5-12 by event count). Each cluster gets its 3 most central events plus count and importance, so the model sees "what the week was about" without reading every event.
3. **Closest past weeks:** each cluster is compared to earlier weeks' clusters (nearest vectors), so "this looks like the week of Sept 8" is visible up front.
4. **State and graph changes** that week (entity_states and relationships opened or closed).
5. **Open review items** (contradictions, questions, with ids).
6. **Active inferences** (id, statement, status, confidence), so it updates instead of duplicating.
Capped to fit a local model's context (see Local-model notes).

## Tools (memory only; the model cannot touch anything else)
Read:
- `search_memory(query, k)` hybrid vector + keyword search, cold included on request.
- `get_daily_logs(from, to)`
- `get_entity(name)` current state, history, aliases (resolved through the name matcher).
- `get_events_about(name, from, to)` tagged events.
- `get_relationship_history(a, b)`
- `compare_periods(from1, to1, from2, to2)` counts by tag and type, state changes in each, side by side.
- `find_similar_events(event_id, k)`
- `list_inferences(status)` / `get_inference(id)`
Write:
- `create_inference(statement, category, confidence, supporting_event_ids, counter_event_ids, alternatives)`
- `update_inference(id, status, confidence, add_support, add_counter, note)`
- `rule_review_item(id, ruling, note)` ruling = apparent | historical_change | real | unknown
- `finish(digest)` ends the run.

## The loop (how the harness drives a local model)
- Each turn the model answers with **one JSON action** `{"tool": ..., "args": {...}}`. The harness runs it and sends back the result, truncated to a fixed size.
- Bad JSON or an unknown tool: one repair message ("reply with valid JSON for one of these tools"), then that turn is skipped. Three skipped turns in a row end the run.
- Hard safety cap (about 60 calls) and a total-token cap. On either, the harness says "write your digest now" and only `finish` is allowed.
- The run's write calls are each saved immediately (idempotent), so a crash loses at most the digest, and the next Sunday or a manual rerun continues from the stored inferences.
- Because it is a loop, there is one `weekly_runs` row per week (status: running, done, failed) and the run does not start twice.

## Prompt outline (the system prompt the model gets)
1. **Role:** "You are FRED's weekly analyst. You see one week of Vatsal's life through memory. Your job is to find what is visible only when days are looked at together."
2. **Method (required):** for each conclusion: state it as a hypothesis; search for supporting evidence; search for counter-evidence; decide keep / weaken / reject; only then write it. Prefer updating an existing inference to creating a new one.
3. **Focus:** projects and their trajectory (moving, stalled, abandoned), routines and habits (compare to prior weeks with `compare_periods`), decisions and what happened afterwards, relationships (who appears more or less, changes).
4. **Evidence rules:** observed facts are not inferences. Every inference must cite supporting event ids; none, no inference. Always list alternative explanations. Confidence is how well the evidence supports it, not how interesting it is. One week of data is thin: say so.
5. **Traits, values, motivations:** allowed only with `category = "trait_hypothesis"`, status `proposed`, at most confidence 0.5. Never state them to the user. Never create one from a single event.
6. **Contradictions:** classify each review item as apparent (different circumstances), historical_change (he genuinely changed), real (two current beliefs cannot both be true) or unknown. Do not resolve a real one yourself.
7. **Don't invent:** if memory has nothing, say "no pattern" rather than reaching. A quiet week gets a short digest.
8. **Finish:** call `finish` with the digest (format below). Do not call `finish` before you have checked the open review items and the active inferences.

## Inference store (new table `inferences`, plus link tables)
`id, statement, category (project | routine | decision | relationship | trait_hypothesis), confidence 0-1, status (proposed | supported | weakened | rejected | outdated), created_at, valid_from, valid_to, last_reviewed_week, support_weeks` and `inference_evidence(inference_id, event_id, role = support | counter)` and `inference_alternatives(inference_id, text)`.
- `support_weeks` counts distinct weeks in which the pattern was re-confirmed. It is the number that gates trait promotion in Phases 6-7 (rule of thumb: at least 4 weeks and no strong counter-evidence).
- Code enforces: an inference needs at least one supporting event that exists; trait_hypothesis cannot be `supported` from here; confidence is clamped; status changes are logged (nothing is overwritten, same as the rest of memory).

## Review-item rulings
- apparent / historical_change: closed with the ruling recorded; a historical change also creates a state change if an entity field is named.
- real: stays open in the review folder for Vatsal (it never gets expired by hygiene while it is flagged real).
- unknown: stays open one more week, then hygiene expires it as usual.

## The digest (what Vatsal pulls)
Short, plain prose, sections only if there is something to say: **The week in brief**, **Projects**, **Routines**, **Decisions and how they went**, **People**, **Changed my mind about** (inferences weakened or rejected), **Please check** (real contradictions). Traits and hypotheses about personality never appear in the digest. Length follows the week like the daily logs (quiet week: a few lines). Stored in `weekly_digests(week, summary)`.

## Failure cases
| Case | Handling |
|---|---|
| Model returns bad JSON / unknown tool | One repair message, then skip the turn; 3 skips in a row end the run, digest marked incomplete |
| Never calls finish | Safety cap forces a final `finish` turn; if that fails, a plain digest is built from the week's daily logs |
| Inference with no valid evidence | Rejected in code, error returned to the model so it can fix it |
| Model creates a duplicate of an active inference | Detected by statement similarity; the call returns the existing id and asks it to update instead |
| Trait claimed as supported or with high confidence | Clamped to proposed, 0.5 |
| Week with almost no data | Skip the loop, write a one-line digest ("quiet week") |
| Run crashes mid-way | Writes are already saved; `weekly_runs` marks it failed; rerun resumes without duplicating |
| GPU does not have room for the model | Flag it, defer the run, never kill other processes |
| Context too long for the local model | Week map and tool results are capped; clusters carry the summary instead of raw events |
| Prompt injection inside memory text (a note saying "ignore your rules") | Tool results are data; the harness only executes the fixed tool list and ignores instructions inside results |

## Which local model (from the project plan, not my own pick)
`Old/MARK2_MASTER_PLAN.md` (section 4.5) already lists the weekly model: **gpt-oss-20b**, GGUF MXFP4 (12.1 GB file) or Q4_K_M (11.6 GB), about 13-14 GB of VRAM (estimate), with low/medium/high reasoning effort. An unchosen alternative there is a 10-14B dense Q4 (about 8-9 GB). The nightly model in the same plan is Qwen3.8-9B-Distill (Q4_K_M/Q5_K_M, about 6-8 GB), which is what Phase 3 consolidation will run on.
- **VRAM problem:** 13-14 GB does not fit beside FRED's always-resident models (budget about 12-13 GB). The plan's answer: load dynamically only in the nightly window and skip if Vatsal is present. So the weekly run unloads FRED's own models first (never kills anything else) and defers if it still does not fit.
- **Not yet verified:** gpt-oss-20b's tool-call reliability through llama.cpp on this machine, and its real VRAM. Both are tested at build time before trusting it; if it fails, the 10-14B dense alternative is next.

## Local-model notes
- A local model is the weak link for multi-step tool use. So: one action per turn, small flat JSON, short tool results, an example of each tool call in the prompt, temperature low.
- Plan to compare it against a cloud model on a few weeks of real data before trusting its inferences (Vatsal can switch the socket later).

## Sockets (same style as the rest)
- `llm(messages) -> str` injected (chat style, since this is a multi-turn loop).
- `run_weekly(mem, week_start, llm, index, model)`; the scheduler is `run_weekly_pending` on a timer (integration).
- Reads only through the existing layers: `MemoryStore`, `Graph`, `SemanticIndex`; writes only through the tools above.

## Open for build time
- Cluster count and sizes, truncation sizes, safety cap numbers (tuned on real weeks).
- Exact support_weeks promotion threshold (Phase 6-7).
- Whether the digest is also spoken by the talker model on request ("how was my week?").
