# State and Data Layout

## `Core/state/` — small, hand-rolled persisted flags

Not a database — each file in `Core/state/` is a tiny, purpose-specific module that owns exactly one piece of durable state, using the same pattern throughout: read a JSON file into a module-level variable at import time, mutate in memory, write back with an **atomic write-then-replace** (`tmp = path.with_suffix(".json.tmp"); tmp.write_text(...); tmp.replace(path)`), so a crash mid-write can never leave a half-written, corrupt state file behind.

- **`lockdown_state.py`** — the single boolean behind FRED's kill-switch. `is_locked()` / `set_locked(bool)`. Persisted to `Core/data/lockdown_state.json` so a restart (including a full reboot) doesn't silently drop back to unlocked. **Fails open, not closed**: a missing or corrupt state file returns `False` (unlocked) rather than `True` — the reasoning stated directly in its header comment: "a state file that didn't survive a crash should never be the reason someone's shut out." `ToolRegistry.execute()` (`Core/tools/registry.py`) checks this before running any tool except the unlock tool itself; conversation still works while locked, only tool execution is blocked. Full detail on the lockdown *mechanism* (how it's engaged/disengaged conversationally) is in `04a_orchestrator_core.md`. Ships its own `if __name__ == "__main__":` self-check that round-trips a real set/load cycle against the actual state file, backing up and restoring its prior contents around the test — a lightweight regression check, not a `Core/tests/` suite entry.
- **`lockdown_log.py`** — an append-only audit trail of lock/unlock events, separate from the boolean state itself, persisted to `Core/data/lockdown_log.jsonl` (JSON-lines, one event per line — append-friendly, doesn't require rewriting the whole file per event, unlike the JSON-object files above).
- **`conversation_state.py`** — short-term/in-session conversation history used to build the message list passed to the LLM each turn (see `06_proactive_and_memory.md` for its exact interaction with `SHORT_TERM_MEMORY_LIMIT` and the FAISS long-term memory system — they are two different mechanisms: this is the literal recent-turns buffer, FAISS is semantic recall over everything ever said).

## `Core/data/` — gitignored runtime state (structure only; contents are personal, never read/quoted in this doc set)

Everything under `Core/data/` is created at runtime and excluded from git (`.gitignore`). This doc set does not read or summarize its contents — only the directory/file **shapes**, which are useful to know when rebuilding (what a fresh install needs to create, what format each piece is in).

```
Core/data/
  memory/                      long-term semantic memory (FAISS-backed) — see memory_manager.py
  indexes/
    default_user.faiss         the FAISS vector index for conversation memory (per-user file naming — DEFAULT_USERNAME)
    docs_chunks.json           FRED's own self-documentation index (DOCS_INDEX_PATH — see settings.py)
  logs/
    sessions/session_YYYY-MM-DD.jsonl   one JSONL file per calendar day, one line per logged turn/event — cited
                                          repeatedly throughout settings.py's own comments as the evidence trail
                                          for real bugs (e.g. "confirmed in session_2026-08-15.jsonl, 18:41")
    crash.log, sandbox_crash.log         unhandled-exception dumps
  memory_archive/               older/superseded memory snapshots (from-Core-cwd, from-root-cwd subfolders — a
                                 relic of a past cwd-inconsistency bug, kept for reference)
  wakeword_training/            training pipeline artifacts + a vendored piper-sample-generator dependency
                                 (synthetic sample generation for the "Hey FRED" wake-word model) — heavy,
                                 training-only, not needed at runtime (see 01_environment_and_setup.md)
  phrase_cache/                 cached pre-synthesized TTS audio for common phrases (see 03_voice_pipeline.md)

  reminders.sqlite              scheduler.py's persisted one-off/recurring reminders (survives restart —
                                 file-watches are explicitly in-memory only, do NOT persist across restart)
  proactive_state.json          PROACTIVE_STATE_PATH — dedup/last-fired bookkeeping for the proactive checks
  llm_status.json               LLM_STATUS_PATH — cross-process "what tier is resident in the main process right
                                 now" signal, read by the screen watcher's separate process (see 02, 08)
  screen_context.json           SCREEN_CONTEXT_PATH — the screen watcher's latest cached description + timestamp
  lockdown_state.json / lockdown_log.jsonl   see Core/state/ above — these are the actual on-disk files those
                                 modules read/write
  call_log_seen.json            phone_tools.py's watermark for "missed calls since FRED last checked"
  audio_device_prefs.json       remembered input/output device selection
  face_enrollment.json          presence.py's stored face embeddings (biometric — never read/quoted anywhere
                                 in this doc set; see 05_presence_and_sleep_mode.md for the *mechanism*, not
                                 the contents)
  face_reference.jpg, camera_capture.png   presence/enrollment-adjacent captured images
  file_index.db                 tools/file_index.py's filesystem index (SQLite) backing fast/fuzzy file search
  found_cache.json (naming approximate — see found_cache.py)   the (directory, query) -> resolved-path cache
                                 built to fix "file search is one deterministic pass" (see 07's resolved-bugs list)
  http_shortcuts_*.png / *.json  screenshots + exported config from setting up the HTTP Shortcuts Android
                                 integration (tools/http_shortcuts_setup.py) — setup-time artifacts, not
                                 read at runtime
```

**Rebuild note**: none of these files need to exist before first run — every module that owns one creates it lazily (`mkdir(parents=True, exist_ok=True)` + the atomic-write pattern above) the first time it has something to persist. `Core/config/settings.py` itself creates `DATA_DIR`, `MEMORY_DIR`, `INDEX_DIR`, `LOG_DIR` at *import* time (`Path.mkdir(exist_ok=True)` calls right in the module body), so a fresh checkout gets the base directory skeleton the instant any FRED entry point imports `config.settings` — before any model loads or any real logic runs.

## Two other data roots, deliberately kept outside `Core/data/` and outside git entirely

- **`VAULT_DIR`** (`Core/config/settings.py`) — Vatsal's personal memory vault, an entirely separate directory tree outside `Project_FRED`. Holds `persona.md`/`profile.md`/`rules.md`/`active-priorities.md` (read directly, hardcoded) plus arbitrary other `.md`/`.pdf` files (indexed for semantic retrieval — see `04a_orchestrator_core.md`). Its own `vectors/` subfolder holds the vault router's generated embedding index, deliberately stored *with* the vault rather than with this repo, so the index "travels with what it describes" and survives a fresh repo checkout without a full re-embed.
- **`MODELS_DIR`** (LM Studio's model folder) and **`LLAMACPP_BIN_DIR`** — see `01_environment_and_setup.md`. Neither is under `Project_FRED` at all.

This separation is a deliberate design pattern worth preserving in a rebuild: **anything that outlives one project checkout, or that must never touch git for privacy/size reasons, lives outside the repo tree entirely**, referenced only by absolute path from `Core/config/settings.py`. Runtime state that's cheap to regenerate and repo-scoped lives under `Core/data/`, gitignored but inside the tree.
