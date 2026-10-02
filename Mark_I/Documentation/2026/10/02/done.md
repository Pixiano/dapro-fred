# 2026-10-02 - Mark I begins

- Naming: Mark I (= v2), then Mark II..Mark X. Build in `Mark_I/Initial/`, extract to `Mark_I/` when complete. Current FRED untouched.
- Method: per step, ship only the affected components, each with documented sockets; undecidable ones are decided when reached.
- Docs system set up (this folder). Rules saved to Claude memory (`mark-naming-and-layout`, `mark-build-method`, `mark-docs-system`).
- **Memory Phase 1** (`Initial/memory/`): `schema.sql` (sources, entities, entity_states, events, daily_logs) and `store.py` (stdlib sqlite3, self-check passes: `python store.py`).
  - Sockets: DB path is a parameter; `add_source` provenance on every write; `on_event` hooks (Phase 2 plugs FAISS/memory candidates in); later tables arrive as new schema files.
  - Not yet: relationships, inferences, contradictions, consolidation_runs, archive_index, embeddings (Phases 2-8).
- Pending: git repo overhaul (`Old/` holds everything, Mark I is main); needs Vatsal's confirmation before it runs.

## Phase 2 started: semantic recall
- Checked v1.2: `Core/memory/memory_manager.py` is jsonl + FAISS with `store(role, content)` / `retrieve_relevant(query, top_k)`; its `_generate_embedding(text, is_query)` is also borrowed by `orchestrator`, `tool_router` and `vault_router`. Its FAISS file and jsonl could drift apart.
- Shipped `Initial/memory/semantic.py` + `schema_embeddings.sql`: one vector per event in SQLite, numpy cosine recall, `sync()` backfill so the index cannot drift. Self-check: `python semantic.py`.
- Sockets: injected `embed(text, is_query)` (same signature as v1.2's), `attach()` onto `on_event`.
- Not yet: the real embedder wiring, capture/recall brain tools, project state, tool events, the router borrowing `_generate_embedding` (decide when we reach the embedder swap / tool schemas).
- Git overhaul still waiting for explicit confirmation ("go" was ambiguous between it and Phase 2; I did Phase 2).
