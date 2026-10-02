# Mark I memory: build order (Vatsal, 2026-10-02)

Spec: `Old/FRED_Memory_Overhaul.md` (predecessor doc, section 40 phases). Integration into FRED is LAST.

1. **Phase 1 Foundation: complete.** `store.py` (entities, events, state history, provenance, daily log, snapshot), `manager.py` (the single `Memory(root)` object). Projects/goals/tasks/preferences are entity kinds with state fields (spec section 9), not extra tables.
2. **Layer 1 Raw archive: built.** `archive.py`. Append-only JSONL per UTC day and stream, plus content-addressed blobs for files/artifacts. Index in SQLite, `verify()` detects loss/tamper.
3. **Phases 3-8, in order:** daily consolidation, temporal graph, weekly meta-agent, personal model, monthly/yearly, deep self-analysis.
4. **Phase 2 FRED integration: last.** Wires FRED's pipeline, event bus, capture/recall tools and the real embedder into the sockets below. `semantic.py` (event vectors + recall) is already built as a standalone piece and waits for this step.

## Sockets in place
- `Memory(root)`: only config; root lives outside the repo (personal data).
- `Archive.write(stream, data, session, ts, source_id, kind)`: every producer's single call. `on_write` hooks for extraction.
- `MemoryStore.add_source/add_event/set_state`, `on_event` hooks.
- `SemanticIndex(store, embed)`: injected embedder.
