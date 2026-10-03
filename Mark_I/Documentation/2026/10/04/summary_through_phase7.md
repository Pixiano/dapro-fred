# Mark I memory: summary of everything built through Phase 7 (2026-10-04)

Scope: the memory system only, as a standalone set of parts in `Mark_I/Initial/memory/`. **No integration into FRED yet** (that is the last step). Every part is tested by its own self-check (`python <file>.py`, 12 files, all passing, about 2,900 lines of stdlib + numpy + FAISS). Every model-driven part takes an injected model function, so the real models plug in later.

## The idea in plain words
FRED keeps a **shoebox** (everything exactly as it happened), a **filing cabinet** (organised facts that change over time), a **librarian** (finds memories by meaning and by exact words), and a team of **editors** who read at different speeds: every night (the day), every week (patterns), every month (what changed), every year (how you changed). On top sits a **personal model**: a short list of things FRED has learned about you, used quietly and only when relevant. One rule runs through all of it: **nothing is overwritten or deleted.** When something changes, the old fact gets an end date and the new one starts, so FRED can answer "what is true now?" and "what was true in March?".

## The parts, from the bottom up

| # | Part | Files | Plain words | Technical |
|---|---|---|---|---|
| 1 | Raw archive | `archive.py` | The shoebox: every conversation, tool use and error, exactly as it happened, one folder per day. Can detect if anything is lost or altered. Imported old history is labelled and never mixed into live days. | Append-only JSONL per UTC day and stream, files by content hash, SQLite index, `verify()`, stream names validated |
| 2 | Memory database | `store.py`, `manager.py` | The filing cabinet: things (people, projects, goals, places) with details that change over time, notable events, and where each fact came from. One handle (`Memory(root)`) for everything; the data lives outside the code repo. | SQLite: entities, entity_states (history), events, sources, daily_logs; one lock; isolated hooks |
| 3 | Search | `semantic.py` | The librarian: finds memories by meaning and by exact words together (meaning alone misses names), hides shelved memories, and finds look-alikes of an event. | FAISS exact index as a rebuildable cache over SQLite vectors, per-model vectors, FTS5 keywords, rank fusion, `similar()` |
| 4 | Judged recall | `recall.py` | After searching, the model throws away the useless results. If nothing is left it searches wider with its own wording (the talker says "digging deeper"), and as a last resort asks you for a keyword. | Injected `llm`, one retry round, `say` callback, falls back to plain results on bad output |
| 5 | Graph and names | `graph.py` | A dated map of connections ("Priya works on FRED since January"). Handles spelling differences ("Priya"/"Preeya"), asks before assuming a nickname ("Do you mean Priya?"), tags every memory with who it is about, and can undo a wrong merge because nothing is deleted. | `relationships` with validity ranges, exclusive vs stacking relations, aliases, reversible merges, entity tags, `resolve()` as the one door for names |
| 6 | Nightly consolidation | `consolidate.py` | Each night a model reads the day and writes the diary entry (short for dull days), the key events, updated facts, new connections, and a "check later" list. A bad answer is retried the next night; running twice never duplicates. | One JSON call per day, idempotent run log, review queue, skips imported history |
| 7 | Hygiene | `hygiene.py` | Keeps memory from clogging: small talk and repeats stay in the shoebox only; old unimportant memories get shelved (not deleted) and come back if asked for; stale questions and shaky connections expire. | Write gate, cold marking, expiry; every number is a knob to tune with real use |
| 8 | Weekly analyst | `weekly.py` | Once a week a stronger model investigates the week like an analyst: forms a guess, searches for evidence for and against, keeps, weakens or drops it, rules on the "check later" pile, and writes a weekly digest you can ask for. Every guess is saved with the exact memories behind it. Guesses about personality are only hypotheses and never appear in the digest. | Tool loop with 14 memory-only tools written into the prompt (no tool router), code-enforced evidence rules, inference store, hard call cap, fallback digest |
| 9 | Personal model | `personal_model.py` | A short list of what FRED has learned about you. An item is added only once it has held up long enough (trivial: a week, most things: about four, claims about who you are: about eight). It is never recited; only items that match what you just said are quietly added to FRED's thinking. You can see it, correct it (your version is locked and beats any guess), or remove items. | Code gate no model can override, light weekly and full monthly update, demotion by code, automatic relevance-gated delivery |
| 10 | Monthly and yearly reviews | `period.py` | Monthly: what really changed, comparing how things stood at the start and end of the month, turning points, and a timeline of named chapters of your life. Yearly (on your birthday anchor): twelve small month passes, then one whole-year review, then a critic that argues against it before anything is kept. | Subclasses of the weekly loop; chapters on parallel tracks and turning points with code rules; the critic can also add recurrences it finds (at most 5, evidence from two months); resumable yearly run; anchor kept in the private DB only |
| 11 | Backups | `backup.py` | Back up all memory or one part; each part as an exact restorable copy and a readable Markdown file, plus a PDF, or a PNG picture for the relationship graph and the chapter timeline (formats chosen per part). Verified by hashes, never overwrites, refuses to save inside the code repo. | Native json/jsonl/sqlite + md + pdf (fpdf2), manifest with SHA-256, `.partial` then rename, CLI |

## How a day, week, month and year flow (once connected)
1. During the day everything goes into the **archive**; meaningful things become **events**, tagged with who they are about and searchable by meaning and keywords.
2. Each **night** the day is turned into a diary entry, facts and connections; doubts go to the review list.
3. Each **week** the analyst finds patterns, writes inferences with evidence, rules on doubts, and refreshes the personal model lightly.
4. Each **month** the review compares start and end, marks turning points and chapters, and triggers the full personal-model rebuild.
5. Each **year** (birthday anchor) the twelve months become twelve notes, a whole-year review, and a critic pass.
6. When you ask something, FRED searches, checks facts and the graph, and quietly mixes in personal-model items that match.

## Safety and honesty rules built in
- Nothing is deleted. Facts get end dates, merges are reversible, shelved memories can come back.
- Every conclusion keeps its evidence; no evidence, no conclusion.
- Code, not the model, has the last word on promotion, evidence and caps. A small model's bad output is skipped, never fatal, and never loses the rest of the day.
- Personality-type guesses are hypotheses, need weeks of repeated evidence, are never stated in digests, and never asked about. Your corrections are locked.
- Doubt goes to a review list, not into memory as fact. Real contradictions are never auto-resolved or auto-expired.
- Personal data (including the yearly anchor) lives only in the private memory database, never in the repo.

## What is deliberately NOT built (all to be decided or done at integration or later)
- **Integration into FRED:** nothing writes to the archive yet; the real embedder, the real models (gpt-oss-20b weekly, Qwen3.8-9B nightly), the timers (nightly, Sunday, 1st of month, anchor day), the VRAM load/unload window, `relevant()` in the turn pipeline, spoken commands ("show me what you know", "that's wrong", "X is Y"), the talker speaking the "digging deeper" line and questions.
- **Verbose decision logging** of every model prompt, reply, action and what the code overruled (Vatsal's request, integration phase at the latest).
- **More specific detailed memory tools** for the consolidation models (decide in the final phase).
- **Permanent yearly alerts** for friends, family and occasions (later phase, always confirmed with Vatsal before saving).
- **Imported history** stays raw until dates can be trusted (backfill mode later).
- Smaller gaps: day chunking past 60k characters, summarising an overflowing loop context, multi-hop graph queries, an "undo merge" helper, cooling one-off entities, retention of very old archive days, reranker model.

## Rough edges to settle at integration
- **Two model call shapes:** nightly, judged recall and the personal model take `llm(prompt)`; the weekly, monthly and yearly tool loops take `llm(messages)`. One small adapter covers both.
- **Tuning numbers are guesses** (module constants): importance gate 0.3, cold after 60 days, duplicate 0.9, name auto-link 0.85 / ask 0.65, relevance cut-off 0.45, tier weeks 1/4/8, call caps. They only get tuned by living with FRED for a week or more.
- **Monthly and yearly are subclasses of the weekly loop**, so a change there is felt everywhere (intended, one engine).

## Next
Phase 8, deep self-analysis (spec section 40), built 2026-10-05: see ../05/. After that, integration.
