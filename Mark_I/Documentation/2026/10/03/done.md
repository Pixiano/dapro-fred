# 2026-10-03

- Polish pass, Phase 3 (daily consolidation) and Phase 4 (temporal graph + name resolution) were built today; their notes are in [2026/10/02/done.md](../02/done.md) (written there before this day's folder existed).
- New: [memory system overview](memory_system_overview.md), the whole memory system explained in plain words and technically.
- All six self-checks pass (`python store.py|archive.py|semantic.py|manager.py|graph.py|consolidate.py` in `Mark_I/Initial/memory/`). Pushed to `origin/main`.
- Next: Phase 5 weekly meta-agent. Integration into FRED stays last.

## Search upgrade + memory hygiene
- **Search:** `semantic.py` now uses a FAISS flat (exact) index as a rebuildable cache over the vectors in SQLite (SQLite stays the truth, so no drift), plus FTS5 keyword search, merged into `search()` by reciprocal rank fusion. Names and exact terms that weak local embedders miss are caught by the keyword side. Cold events are hidden; results are marked recalled.
- **Hygiene** (`hygiene.py`, `schema_hygiene.sql`, self-check `python hygiene.py`): write gate in `consolidate.py` (importance < 0.3 or a same-day near-duplicate stays in the archive only); `run_hygiene()` marks old, unimportant, not-recently-recalled events cold (60 days, importance < 0.7), expires open review items after 30 days, closes weak links (confidence < 0.4) after 30 days. Nothing is deleted, a recalled cold event turns warm.
- All thresholds are knobs to tune after a week or more of real use; the real test is living with FRED.
- Not yet: reranking step (LLM or small reranker, integration), cooling of one-off entities, scheduling `run_hygiene` after `run_pending`, an IVF/HNSW index beyond ~1M events.
- Answered: the best store for conversation memory is not one type. It is the archive (truth) + vectors (meaning) + keywords (names) + graph/states (structured facts), fused, which is what this is.

## Judged recall (`recall.py`)
- `judged_search(index, query, llm)`: take the top 20 candidates, the model keeps the useful ones (junk dropped, and only kept events are marked recalled). If none are useful it retries once, wider: the model's own rephrasings, cold events included, pool of 40. If still nothing, it returns a question asking the user for a name, date or keyword instead of guessing. Bad model output falls back to the plain top results. Self-check: `python recall.py`.
- `SemanticIndex.search(touch=False)` added so candidates are not marked warm before they are judged.
- Not yet: the real model behind `llm` (integration); more than one retry round.
- `judged_search(..., say=callback)`: the talker model's voice. It is told "Nothing obvious yet, digging deeper..." before the wider retry (not when the first pass works). A failing callback is ignored. At integration the talker also speaks the final "ask" question from the result.

## Entity tags + nickname handling ("Whats Su's fav colour?")
- Problem: the question never says "Rina", so text search misses the memory. Fix is tags, not better text matching.
- `event_entities` table (an event can be tagged with several entities). The nightly model now labels each event with who it is about (`about`), and returns `aliases` only when the user explicitly said a nickname; a mere suspicion goes to `open_questions`.
- Question time (`judged_search(..., graph=g)`): names are found by alias or exact name; everything tagged with them is added to the candidates (plus their saved facts, returned as `facts`). A capitalised word that starts exactly one known name ("Su" -> Rina) is never used silently: the result carries `ask` ("Do you mean Rina when you say 'Su'?") and `maybe`; on yes the caller runs `graph.confirm_alias(word, id)` and the nickname works from then on. Ambiguous prefixes (two entities) are not guessed.
- `merge()` also moves tags. Self-checks: `python graph.py|recall.py|consolidate.py`.
- Not yet: the talker speaking the question and calling confirm on the user's yes (integration); tagging old events retroactively (only nightly runs tag).
