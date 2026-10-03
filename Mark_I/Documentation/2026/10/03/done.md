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
