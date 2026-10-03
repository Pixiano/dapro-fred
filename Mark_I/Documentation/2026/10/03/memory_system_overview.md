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
- **Plain words:** a map of connections with dates: "Rina works on FRED, since January", "lives in Pune, since June". Some connections can only have one value at a time (where you live), others can have many (people you know). If a new fact contradicts the timeline, it is not forced in. It goes to the review list.
- **Technical:** `relationships` (subject, relation, object, validity range, confidence, source). `EXCLUSIVE` relations close the previous row when a new one arrives. `neighbors(entity, at=date)` answers "who was linked at that time", `history(a, b)` shows how a link changed.

### 6. Name matching (spelling the same person two ways) - in `graph.py`
- **Plain words:** speech-to-text may write "Rina" one day and "Rinah" the next. FRED treats clearly-the-same spellings as one person and remembers both spellings. If it is only maybe the same, it asks you instead of guessing, because merging two real people is worse than keeping one split. A mistaken merge can be undone because nothing is deleted.
- **Technical:** `resolve(kind, name)`: alias or exact match (case and spacing ignored) -> similarity >= 0.85 on raw and phonetic-ish forms (auto-alias) -> >= 0.65 (new entity plus a review question) -> new. Same kind only. `merge()` moves rows and marks the old entity merged.

### 7. Hybrid search and tidying (added later 2026-10-03)
- **Plain words:** search now uses two methods together: meaning (finds "began working on speech" for "started the voice project") and exact words (finds the name "Rina" even when meaning-search stumbles). To stop clutter, only meaningful things become events, repeats are skipped, and old unimportant things are shelved ("cold"): hidden from normal search but not deleted, and brought back if asked for again. Stale questions and shaky connections also expire.
- **Technical:** FAISS flat index (cache) over SQLite vectors + FTS5, reciprocal rank fusion; `hygiene.py` write gate, cold marking, review expiry, weak-link closing. Thresholds are knobs to tune with real use.

### 8. Judged recall (added later 2026-10-03)
- **Plain words:** after searching, the model reads the top 20 results and throws out the useless ones. If nothing useful is left, it searches again more widely using its own better wording and includes shelved memories. If that still fails, FRED asks you for a name, date or keyword instead of guessing. While it searches harder, the talker tells you ("digging deeper...") so you are not left in silence.
- **Technical:** `recall.py` `judged_search`: judge pool, one wider retry (rephrasings, cold included), then an `ask`. Only kept events are marked recalled. A bad model answer falls back to the plain top results.

## How a day flows through it (once integrated)
1. During the day, everything FRED sees and does is written to the **archive**, and important things become **events** (searchable by meaning).
2. At night, the **night editor** reads the day plus what is already known, and updates the diary, facts and connections. Anything doubtful goes to the **review list**.
3. Later, when asked a question, FRED can **search** by meaning, look up **current facts**, check **who connects to whom at a given time**, or read the **diary** for a day.

## What is not there yet
- Connection to the real FRED (feeding the archive, the real embedder, the real nightly model, the timer, recall tools, a spoken "X is Y" fix).
- Phase 5: weekly meta-agent that works through the review list and spots patterns across a week.
- Phase 6: personal model (what you are like, with evidence). Phase 7: monthly and yearly summaries. Phase 8: deep self-analysis.
- Smaller gaps: very long days are cut at 60k characters, no multi-hop connection search, no "undo merge" helper, nicknames and other-script names go through the review question, old archive days are never compressed.

## Safety rules built in
- Nothing is overwritten or deleted. Facts get end dates and merges are reversible.
- Every fact keeps its source.
- A bad AI answer or a bad single row never loses the rest of the day.
- Doubt goes to a review list, not into the memory as fact.
- Personal data lives outside the code repo.
