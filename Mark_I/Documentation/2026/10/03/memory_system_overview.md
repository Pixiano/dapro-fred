# Mark I memory system: how it works as a whole (2026-10-03)

Written as if everything were connected to FRED. Today the parts exist and pass their own tests, but nothing in FRED feeds them yet (that is the last step, "integration"). Each part has a plain-words version first, then the technical one.

## The big idea, in plain words
FRED should remember your life the way a good assistant keeps a diary and a filing cabinet.
- **Diary:** every day, write down what happened.
- **Filing cabinet:** keep facts organised, who you know, what you work on, where things stand.
- **Shoebox:** keep the original, untouched record of everything, so nothing is ever lost.
- **Librarian:** find the right memory when asked, even if you word the question differently.
- **Night editor:** every night, read the day, update the diary and the cabinet, and note anything that looks wrong or unclear.

A rule runs through all of it: **nothing is overwritten or deleted.** When something changes ("I moved from Delhi to Pune"), the old fact is marked "true until then" and the new one starts. So FRED can answer both "where do I live?" and "where did I live in March?".

## Parts

### 1. Raw archive (the shoebox) - `archive.py`
- **Plain words:** a record of everything exactly as it happened: conversations, tool use, what was heard, errors. Each day gets its own folder. It only ever adds to the record. It can check itself and tell you if anything went missing or was altered.
- **Technical:** append-only JSONL files per UTC day and stream, plus files stored by content hash (identical files are kept once). An index in SQLite lets `verify()` compare line counts. Stream names are validated, which blocks path-traversal tricks. Historical imports are labelled so they are never mistaken for live data.

### 2. Memory database (the filing cabinet) - `store.py`, `manager.py`
- **Plain words:** the organised facts. Things (people, projects, goals, places) have changing details. Events are notable things that happened. Every entry remembers its source, so you can ask "how do you know this?". `Memory(root)` is the one handle FRED holds to all of it, and the data lives outside the code repo because it is personal.
- **Technical:** SQLite with `entities`, `entity_states` (full history with `valid_from/valid_to`), `events`, `sources` (provenance) and `daily_logs`. One shared connection behind one lock, so several threads can write safely. A broken add-on hook can never lose a saved event.

### 3. Smart search (the librarian) - `semantic.py`
- **Plain words:** finds memories by meaning, not exact words. Asking "when did I start the voice project?" can find a note that says "began working on FRED's speech".
- **Technical:** each event gets a vector from an embedder (injected, not chosen yet), stored per model so swapping models never mixes incompatible vectors. Recall is brute-force cosine similarity in numpy, fine up to about 100k events. `sync()` backfills anything missed, so the index cannot drift from the events.

### 4. Daily consolidation (the night editor) - `consolidate.py`
- **Plain words:** each night, an AI reads the whole day and produces: a short diary entry (short on boring days, longer on busy ones), key events, updates to project facts, new relationships, and two "check later" lists: things that seem to contradict what was known, and questions it could not answer. If the AI gives a bad answer, that day is retried the next night. Running it twice never duplicates anything.
- **Technical:** one LLM call per day returns JSON; results are written with a `consolidation` source. `consolidation_runs` makes it idempotent and records failures; `review_queue` holds contradictions and open questions for the weekly agent. The model is injected (`llm(prompt)->str`). The scheduler is just `run_pending()` on a timer.

### 5. Relationship graph (who and what connects) - `graph.py`
- **Plain words:** a map of connections with dates: "Priya works on FRED, since January", "lives in Pune, since June". Some connections can only have one value at a time (where you live), others can have many (people you know). If a new fact contradicts the timeline, it is not forced in. It goes to the review list.
- **Technical:** `relationships` (subject, relation, object, validity range, confidence, source). `EXCLUSIVE` relations close the previous row when a new one arrives. `neighbors(entity, at=date)` answers "who was linked at that time", `history(a, b)` shows how a link changed.

### 6. Name matching (spelling the same person two ways) - in `graph.py`
- **Plain words:** speech-to-text may write "Priya" one day and "Preeya" the next. FRED treats clearly-the-same spellings as one person and remembers both spellings. If it is only maybe the same, it asks you instead of guessing, because merging two real people is worse than keeping one split. A mistaken merge can be undone because nothing is deleted.
- **Technical:** `resolve(kind, name)`: alias or exact match (case and spacing ignored) -> similarity >= 0.85 on raw and phonetic-ish forms (auto-alias) -> >= 0.65 (new entity plus a review question) -> new. Same kind only. `merge()` moves rows and marks the old entity merged.

### 7. Hybrid search and tidying (added later 2026-10-03)
- **Plain words:** search now uses two methods together: meaning (finds "began working on speech" for "started the voice project") and exact words (finds the name "Priya" even when meaning-search stumbles). To stop clutter, only meaningful things become events, repeats are skipped, and old unimportant things are shelved ("cold"): hidden from normal search but not deleted, and brought back if asked for again. Stale questions and shaky connections also expire.
- **Technical:** FAISS flat index (cache) over SQLite vectors + FTS5, reciprocal rank fusion; `hygiene.py` write gate, cold marking, review expiry, weak-link closing. Thresholds are knobs to tune with real use.

### 8. Judged recall (added later 2026-10-03)
- **Plain words:** after searching, the model reads the top 20 results and throws out the useless ones. If nothing useful is left, it searches again more widely using its own better wording and includes shelved memories. If that still fails, FRED asks you for a name, date or keyword instead of guessing. While it searches harder, the talker tells you ("digging deeper...") so you are not left in silence.
- **Technical:** `recall.py` `judged_search`: judge pool, one wider retry (rephrasings, cold included), then an `ask`. Only kept events are marked recalled. A bad model answer falls back to the plain top results.

### 9. Tags and nicknames (added later 2026-10-03)
- **Plain words:** every memory is tagged with who or what it is about. So asking "What's Pri's favourite colour?" works even though the note never says "Su": FRED pulls everything tagged with Priya. If you use a nickname FRED does not know yet, it asks "Do you mean Priya?" and remembers your yes. It never assumes, and if two names fit, it doesn't guess.
- **Technical:** `event_entities` tags written by the nightly run, `Graph.find_in_text` (alias/exact = found, unique capitalised prefix = ask), `events_about`, `confirm_alias`; `judged_search(graph=...)` merges tagged events and saved facts into the candidates.

## How a day flows through it (once integrated)
1. During the day, everything FRED sees and does is written to the **archive**, and important things become **events** (searchable by meaning).
2. At night, the **night editor** reads the day plus what is already known, and updates the diary, facts and connections. Anything doubtful goes to the **review list**.
3. Later, when asked a question, FRED can **search** by meaning, look up **current facts**, check **who connects to whom at a given time**, or read the **diary** for a day.

### 10. The weekly analyst (Phase 5, built 2026-10-04) - `weekly.py`
- **Plain words:** once a week a stronger model reads the week like an analyst. It first gets a one-page map of the week (what topics came up, what changed, what is still open, and which earlier week this resembles). Then it investigates by searching memory for evidence for and against its own guesses, and keeps, weakens or drops each guess. Each guess is saved with the exact memories that back it, so FRED can always say why it believes something. It also rules on the "check later" pile: differences that are only circumstance or a real change of mind are closed, genuine contradictions are kept for you. At the end it writes a short weekly digest that you can ask for. Guesses about personality (traits, values, motivations) are only kept as hypotheses: they need at least two memories behind them, are capped at modest confidence, never reach "supported" from a single week, and never appear in the digest. They grow into something FRED relies on only after showing up week after week, and FRED never recites them or asks you about them: they quietly shape how it answers.
- **Technical:** tool loop, one JSON action per turn, 14 memory-only tools written directly into the system prompt (no embedding tool router, since there are only a dozen), week map from FAISS clusters, `inferences` + evidence + alternatives + change log, `support_weeks` gate, hard call cap with a fallback digest, idempotent per week, runs on the local gpt-oss-20b through an injected `llm(messages)`. Design: `2026/10/04/phase5_weekly_agent_design.md`.
- **Still open:** more specific tools for the consolidation models are to be decided in the final phase.

### 11. The personal model (Phase 6, built 2026-10-04) - `personal_model.py`
- **Plain words:** a short list of things FRED has learned about you: priorities, how you work, habits, preferences, patterns in your decisions. Each item is backed by evidence and is only added once it has held up long enough. Small and obvious things take a week, most things about four weeks, and big claims about who you are (traits, values, motivations) take about eight. If later evidence turns against an item, it is shelved. FRED does not recite this list or put it in every answer. Only when what you say is close to an item does it quietly get added to what FRED considers for that reply. If you ask to see it, FRED shows it, and if you say something is wrong, your version replaces it, is locked, and the guess behind it is dropped for good. It is refreshed lightly every week and rebuilt more thoroughly every month.
- **Technical:** `model_items` (+ log, versions, embeddings), a code gate (tier weeks 1/4/8, 2+ supporting events, counter-evidence at most a third of support) that no model decision can bypass, batched JSON wording call, light/full update modes, demotion by code, `relevant(text)` via embeddings (cut-off 0.45, top 3, nothing when nothing is close), `show/correct/add/remove`. Design: `2026/10/04/phase6_personal_model_design.md`.

### 12. Monthly and yearly reviews (Phase 7, built 2026-10-04) - `period.py`
- **Plain words:** on the 1st of each month FRED reviews the month and asks what really changed: it compares how things stood at the start with how they stand at the end, marks turning points, and keeps a timeline of named chapters of your life ("FRED v2 build, Sep-Oct 2026"). Life runs in parallel, so chapters sit on separate tracks (a big project, social life, school...) and several can be open at the same time. Your own chapters are locked. On your yearly anchor day it looks at the whole year in stages: a short note for each of the twelve months, then one review of the whole year (phases of the year, what stayed the same, what changed, goals dropped or revived), then a critic that argues against that review, weakens anything the evidence does not support, and also looks for what the review missed, for example something from March that comes back in August without anyone connecting the two (it may add a few such findings, with evidence from at least two different months). Old imported history is not mixed in, because its dates cannot be trusted.
- **Technical:** `Month`/`Year`/`Critic` subclass the weekly tool loop; tables for chapters (one open at a time), turning points (need real events), year notes, period runs and digests; resumable yearly run; the anchor sits in the private DB settings. Design: `2026/10/04/phase7_monthly_yearly_design.md`. A full summary of everything through Phase 7 is in `2026/10/04/summary_through_phase7.md`.

### 13. Backups (added 2026-10-04) - `backup.py`
- **Plain words:** you can back up all of FRED's memory, or just one part (the raw archive, the connection map, the diary, the personal model, and so on). Each part comes out as an exact copy that can be restored plus a readable Markdown version, and then the format that suits it: a PDF that opens anywhere for most parts, but a picture (PNG) for the relationship graph (people, projects and places as a node-and-arrow drawing, ended links dashed) and for the life chapters (a timeline with one row per track). You can choose the formats per part. A backup lands in its own dated folder and is never written over, a checker confirms nothing in it changed, and it refuses to be saved inside the code repository because it holds personal data. Backups are plain files, so keep them somewhere private.
- **Technical:** `backup(mem, out_dir, parts, since, until, pdf, blobs)`; native `.json` / byte-identical archive `.jsonl` + blobs / SQLite online-backup snapshot; `.md` complete; `.pdf` via fpdf2, capped; `manifest.json` with SHA-256 per file and `verify_backup`; `.partial` folder renamed on completion; CLI included.

## What is not there yet
- Connection to the real FRED (feeding the archive, the real embedder, the real nightly model, the timer, recall tools, a spoken "X is Y" fix).
- Phase 8: deep self-analysis.
- Smaller gaps: very long days are cut at 60k characters, no multi-hop connection search, no "undo merge" helper, nicknames and other-script names go through the review question, old archive days are never compressed.

## Planned: decision logging
The small models make many decisions, so a very detailed record of every model prompt, reply, action and what the code overruled is planned (integration phase at the latest). Today, changes to inferences, items and runs are already logged.

## Planned: yearly alerts
Recurring dates for friends, family and occasions (birthdays, anniversaries) that FRED reminds you of every year. FRED will ask before saving a date it picked up in conversation. Planned for a later phase; see the build order.

## Safety rules built in
- Nothing is overwritten or deleted. Facts get end dates and merges are reversible.
- Every fact keeps its source.
- A bad AI answer or a bad single row never loses the rest of the day.
- Doubt goes to a review list, not into the memory as fact.
- Personal data lives outside the code repo.
