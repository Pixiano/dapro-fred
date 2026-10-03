# Phase 7 design: monthly and yearly consolidation (2026-10-04)

Status: DESIGN ONLY, not built. Decisions from Vatsal's answers.

## Decisions (Vatsal)
- **Schedule:** monthly on the 1st (night); yearly on Vatsal's birthday. The birthday (MM-DD) is personal data: stored only in the private memory database, never in the repo. **Open: Vatsal has not given the date yet.**
- **Life chapters timeline:** the monthly agent proposes named periods ("FRED v2 build, Sep-Oct 2026"), the yearly agent refines them. Chapters can be corrected by Vatsal (his version is locked, like personal-model items).
- **Yearly depth:** same model (gpt-oss-20b, the biggest local one that fits), highest reasoning effort, bigger budget, **plus a second critique pass** (read as "1+3": Vatsal wrote "1+4" but that question had three options; confirm).
- **Imported history:** not properly dated, so it is NOT replayed. It stays raw in the archive (`kind="historical"`). A backfill mode can be built later, only once dates can be established reliably. Until then every consolidation level ignores historical records, so undated imports cannot pollute live months. (This one protection is implemented now in `consolidate.py`.)

## In plain words
Weekly reviews look at days together. The monthly review looks at weeks together and answers one question: **what actually changed this month?** It compares how things stood at the start of the month with how they stand at the end, and explains the difference through the events, decisions and outcomes in between. It marks turning points and proposes or extends named chapters of life. The yearly review answers **how did Vatsal change this year?** It reads the twelve monthly reviews, finds the phases of the year, what stayed stable, what changed, decisions and their consequences, goals abandoned or revived, and recurring cycles. Because it is the hardest question, a second pass then argues against the first one's conclusions before anything is kept.

## One engine, three agents
The weekly loop (`weekly.py`) already handles: JSON action per turn, repair message, call cap, forced finish, fallback digest, idempotent runs, evidence rules. Monthly and yearly **reuse it** as subclasses with their own opening map, prompt and a few extra tools, rather than a second loop. All tools stay in the system prompt (no tool router, memory-only).

## Monthly agent
Opening map (code, no model): the month's weekly digests (daily logs for any week whose digest is missing), **state at start vs state at end** for every entity that changed (value before -> after), relationship changes, inference changes in the month (new, strengthened, weakened, rejected, with the log), decisions of the month with what followed, topic clusters of this month vs last month, open review items, personal-model items added or demoted, the chapter currently open.
Extra tools: `get_weekly_digests(from,to)`, `state_at(name, date)`, `compare_states(date1, date2)`, `list_decisions(from,to)`, `list_chapters()`, `upsert_chapter(...)`, `create_turning_point(...)`, plus all the weekly read tools.
Produces:
- Monthly digest sections (only where there is something to say): Month in brief, Turning points, Changed priorities, Project evolution, Relationship evolution, Behavioural changes, Emerging patterns, Weakened patterns, Unresolved questions.
- **Turning points** (`turning_points`: day, title, description, kind, period, supporting events). Rule in code: at least one real supporting event, dated inside or within a week of the period.
- **Chapters** (`chapters`: title, start_day, end_day or open, description, source model|user). Rules in code: one open chapter at a time (a new one closes the previous at the day before it starts), near-duplicate titles refused, user chapters locked.
- Inference updates under the same evidence rules as the weekly agent.
- After a successful monthly run, the scheduler calls the personal model's **full** update (Phase 6). A failing rebuild does not fail the monthly run.

## Yearly agent
Opening map: the 12 monthly digests (gaps flagged), all chapters and turning points of the year, personal-model items at the start vs end of the year (what was added, changed, demoted), inferences with long histories and their `support_weeks`, goal/project entities with their state history (started, abandoned, revived), month-to-month topic comparison for recurring cycles.
Extra tools: the monthly set, `get_monthly_digests(year)`, `model_diff(date1,date2)`, `refine_chapter(...)` (merge, rename, set an end), `list_turning_points(from,to)`.
Produces: a year-in-review digest (phases of the year, stable and changed traits, major decisions and consequences, relationships that changed substantially, recurring cycles, abandoned and revived goals, changes in self-concept), refined chapters, trait/value hypotheses as inferences (still `proposed`, only the personal-model review promotes them), and no change to anything Vatsal locked.
**Two passes.** Pass A (analyst) writes a draft and its inferences. Pass B (critic, fresh context) receives the draft and its cited evidence and must look for counter-evidence and alternative explanations for each major claim; it can weaken or reject inferences (`update_inference`), annotate claims, and call `revise_digest`. The final digest is the revised one. Both passes are logged separately.
Budget: highest reasoning effort, call cap about 3x the weekly one per pass. Digest and trait material stays user-pulled and traits never appear in digests (same as the weekly rule).

## New tables (schema_period.sql, when built)
`chapters`, `chapter_log`, `turning_points`, `turning_point_evidence`, `monthly_runs`/`yearly_runs` (idempotent per period), `period_digests(period, kind monthly|yearly, summary)`; the yearly anchor lives in a small `settings` table in the private DB.

## Failure cases
| Case | Handling |
|---|---|
| Little data in the month | One-line "quiet month" digest, no loop (same as quiet week) |
| A weekly digest is missing or incomplete | Map falls back to that week's daily logs and says so |
| Weeks straddling a month boundary | A week belongs to the month its last day falls in; stated in the map |
| A monthly run was skipped | The yearly map flags the gap and works from what exists; a late monthly run can still be made |
| Turning point without real evidence | Rejected in code, error returned to the model |
| Chapter overlaps or duplicates an existing one | New chapter closes the previous open one; near-duplicates refused; user chapters never touched |
| Critic disagrees with the analyst | The critic's weakening/rejection stands (it saw the counter-evidence); the digest is revised accordingly |
| Model never finishes or sends bad JSON | Same loop protections as weekly: repair, cap, fallback digest from the lower-level digests |
| Context too long for the local model | Opening map uses digests, never raw events; everything deeper is a tool call |
| Personal-model rebuild fails after monthly | Logged, monthly stays done, rebuild retried next run |
| Imported historical records | Excluded from every level until dates can be trusted |
| Yearly anchor date missing | Yearly run waits and flags it; never guesses |

## Sockets
`llm(messages)` injected; `run_monthly(mem, month, llm, index, model)`; `run_yearly(mem, anchor_day, llm, index, model)`; scheduler functions that check "is it the 1st / is it the anchor day" belong to integration.

## Open for build time
- The birthday date (MM-DD); confirmation of "1+3".
- Tier/threshold for what counts as a turning point (start: model proposes, code requires evidence, tune on real months).
- Call caps, map size caps, and the gpt-oss reasoning-effort setting per run.
- Spoken access ("how was last month?", "what was I doing last summer?") and temporal retrieval over chapters: integration.
