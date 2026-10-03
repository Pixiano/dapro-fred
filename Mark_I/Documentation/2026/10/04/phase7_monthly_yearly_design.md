# Phase 7 design: monthly and yearly consolidation (2026-10-04)

Status: DESIGN ONLY, not built. Decisions from Vatsal's answers.

## Decisions (Vatsal)
- **Schedule:** monthly on the 1st (night); yearly on Vatsal's birthday. The birthday anchor is personal data: given by Vatsal on 2026-10-04, stored only in the private memory database (a `settings` row, written at integration) and in Claude's private memory, never in the repo.
- **Life chapters timeline (parallel tracks, added 2026-10-04):** life runs side by side, so chapters live on **tracks** (one per big project, plus social life, school, health...). One chapter may be open per track, several tracks run at the same time, and a new chapter closes only the previous open one on its own track. The monthly agent keeps them side by side instead of forcing a month into one chapter. Original decision: the monthly agent proposes named periods ("FRED v2 build, Sep-Oct 2026"), the yearly agent refines them. Chapters can be corrected by Vatsal (his version is locked, like personal-model items).
- **Yearly depth:** same model (gpt-oss-20b, the biggest local one that fits), highest reasoning effort, bigger budget, **plus a second critique pass** (confirmed: Vatsal meant the first option merged with the critic pass).
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

## Yearly agent: 12 month runs, then one whole-year run (Vatsal's idea, 13+ runs)
Reading twelve monthly reviews at once is too much for a small local model, so the yearly review is a map-and-reduce:
1. **Twelve month passes (one run each, oldest first).** Each reads one monthly review plus that month's turning points, chapter changes and state/personal-model changes, and the *notes so far*. It writes a short **year note** for that month: what changed relative to the earlier months, what stayed stable, decisions and their consequences, goals started, dropped or revived, signs of a cycle. Notes are stored (`year_notes`), so the pass is resumable and a failed month is retried alone.
2. **The whole-year run (the 13th).** It gets the 12 year notes as its opening map (never raw events) and investigates with the tools to answer "how did Vatsal change this year?": phases of the year, stable and changed traits, major decisions and consequences, relationships that changed substantially, recurring cycles, abandoned and revived goals, changes in self-concept. It refines the chapters, records trait/value hypotheses as inferences (still `proposed`, only the personal-model review promotes them) and writes the digest.
**The critic can also add (2026-10-04).** Besides attacking the draft it hunts for what the analyst overlooked: something from earlier in the year (say March) that comes back later (say August) without the draft connecting the two. It gets the highest-importance events of each month as starting points and a `find_recurring` tool (similar events from other months), and may add what it finds as `recurrence` inferences: at least two supporting events from different calendar months (enforced in code), no trait hypotheses, at most 5 additions per run. Real recurrences are worked into the revised digest.
**Two passes.** Pass A A fresh context receives the draft digest and its cited evidence and must look for counter-evidence and alternative explanations for each major claim. It can weaken or reject inferences, annotate claims and `revise_digest`; its weakening or rejection stands. The final digest is the revised one.
Window: the run on the birthday anchor uses the 12 most recent *completed* monthly reviews (the month containing the anchor is not finished yet), plus that month's weekly digests so far as a "latest" input; about three weeks of overlap either way is accepted, it is not worth cutting months in half.
Map inputs for the whole-year run additionally include: all chapters and turning points of the year, personal-model items at the start vs end of the year, inferences with long histories (`support_weeks`), goal/project entities with their state history.
Extra tools: the monthly set, `get_year_notes()`, `get_monthly_digests(year)`, `model_diff(date1,date2)`, `refine_chapter(...)`, `list_turning_points(from,to)`.
Budget: highest reasoning effort for the whole-year and critic runs, call cap about 3x the weekly one each; the 12 month passes are small runs. Traits never appear in digests (same as the weekly rule).

## New tables (schema_period.sql, when built)
`chapters`, `chapter_log`, `turning_points`, `turning_point_evidence`, `year_notes(year, month, note)`, `monthly_runs`/`yearly_runs` (idempotent per period, per-month note runs resumable), `period_digests(period, kind monthly|yearly, summary)`; the yearly anchor lives in a small `settings` table in the private DB.

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
| One of the 12 month passes fails | That note is retried alone; the whole-year run starts only when all 12 notes exist, or flags the gap if a monthly review is missing |

## Sockets
`llm(messages)` injected; `run_monthly(mem, month, llm, index, model)`; `run_yearly(mem, anchor_day, llm, index, model)`; scheduler functions that check "is it the 1st / is it the anchor day" belong to integration.

## Open for build time
- Tier/threshold for what counts as a turning point (start: model proposes, code requires evidence, tune on real months).
- Call caps, map size caps, and the gpt-oss reasoning-effort setting per run.
- Spoken access ("how was last month?", "what was I doing last summer?") and temporal retrieval over chapters: integration.
