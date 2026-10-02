# FRED — Overview

## What FRED is

FRED (Friendly, Responsive, Rational, Rakish Electronic Dude) is a personal, single-user, JARVIS-style voice assistant that runs continuously on one Windows desktop (log-on to shutdown). It is not a product — it is built and maintained for one person (Vatsal) and has direct, narrow integrations with his phone, his personal knowledge vault, his webcam, and his machine. It is not multi-tenant, has no auth system beyond a token-gated LAN endpoint for phone control, and nothing about its design should be read as intended for redistribution.

Repo root: `Project_FRED/`. Main codebase: `Project_FRED/Core/`.

This documentation set (`fred-full-documentation_2026-08-21/`) is written to let a fresh engineer/agent rebuild FRED from scratch, in one sitting, without prior familiarity with the codebase. It is organized as:

- `00_overview.md` — this file
- `01_environment_and_setup.md` — launchers, venv, model files, hardware assumptions, external deps
- `02_llm_and_model_tiers.md` — model tiers, `llm_client.py`, cloud cascade, `vision_server.py`
- `03_voice_pipeline.md` — wake word, STT, TTS, hold-to-talk, streaming, audio devices
- `04a_orchestrator_core.md` — tool-calling loop, intent routing, vault retrieval, scheduler, lockdown
- `04b_tool_inventory.md` — the full registered-tool catalog by category
- `05_presence_and_sleep_mode.md` — face-recognition presence detection + sleep mode (newest subsystem)
- `06_proactive_and_memory.md` — proactive nudges, FAISS memory, agenda/daily tasks
- `07_known_gaps_and_unfinished_work.md` — what is explicitly not built, broken, or deferred
- `08_ui_and_vision.md` — the native "pill" popup, the browser HUD, screen-watching

Read `00`, `01`, `02` first — everything else assumes you know how a turn is loaded and run.

## Core loop, at a glance

FRED has **two independent entry points** into the same orchestrator, not one unified runtime:

1. **CLI mode** — `Core/main.py`. Text loop by default; typing `voice` switches to a blocking voice loop using Vosk STT (`audio/stt.py`) and SAPI/pyttsx3 TTS (`audio/tts.py`). No wake word, no hotkey — it just listens turn by turn. Launched via `fred_cli.bat`.
2. **GUI mode** — `fred_popup.py` → `Core/ui/pill_app.py`. Hold-to-talk: hold **left Ctrl+Alt**, speak, release. A native Win32 layered "pill" window appears, shows state (IDLE/LISTENING/THINKING/SPEAKING), transcribes with faster-whisper (`audio/stt_whisper.py`), and speaks with Kokoro TTS (`audio/tts_kokoro.py`). A separately-trained "Hey FRED" wake-word model (`input/wakeword.py`) runs *alongside* hold-to-talk (not a replacement for it — hold-to-talk replaced an older always-on Vosk-based wake word entirely; the *new* trained wake-word model was added back in on 2026-08-09 as an additional trigger, not a revival of the old text-matching one). Launched via `FRED.bat` (brings up the HUD tray quietly in the background) or `FRED_POPUP.bat` (bare popup, no tray).

Both entry points construct a `Core/orchestrator/orchestrator.py::FREDOrchestrator` and call `.process(user_input)` (text in, reply text out). Everything downstream — intent classification, tool routing, tool execution, vault retrieval, memory, LLM calls — is entry-point-agnostic; CLI and GUI are just different front doors onto the identical orchestrator.

**Sleep mode is no longer just a state flag.** As of 2026-08-22, FRED's camera-driven presence detection (webcam face matching, `Core/input/presence.py`) drives a real sleep-mode state machine (`Core/orchestrator/sleep_mode.py`) with two real jobs riding its wake/sleep edges: **consolidation** (`Core/orchestrator/consolidation.py`, a propose-only day-summary + vault-gap recap bundled and spoken on wake) and a deep **reflection** pass (`Core/orchestrator/reflection.py`, a sleep-time reasoning job on a dedicated `"Reflect"` model tier that writes friend facts unattended to `people/*.md` and stages self-facts about Vatsal for review). See `05_presence_and_sleep_mode.md` for the full story — it's the most actively-changing subsystem in the codebase right now.

### A spoken turn, end to end (GUI mode)

1. User holds the hotkey (`input/hotkey.py`, a low-level Windows keyboard hook). `ModelLifecycle.preload()` (`Core/utils/model_lifecycle.py`) fires immediately and asynchronously — it starts warming Whisper (and the LLM, only if currently offline — see rationale below) *while* the user is still talking, not after.
2. Recording starts instantly (needs no model). Key release ends the utterance — Whisper gets a clean, pre-segmented recording for free, which is the whole reason hold-to-talk beats a wake-word+VAD design for transcription quality.
3. `audio/stt_whisper.py` transcribes with faster-whisper `large-v3-turbo`, beam size 5, primed with a command-word prompt (see `03_voice_pipeline.md`).
4. The transcript goes to `FREDOrchestrator.process()`. Intent classification (`orchestrator/intent.py`) decides chat vs. tools *before* the model ever sees a tool menu — this exists because a small model shown ~80 tool definitions on a plain "hello" turn has been observed to hallucinate a tool call (documented incident: `open_website` fired on "Hello Fred, how are you doing?").
5. For a chat-only turn, `llm/llm_client.py::generate_stream()` streams tokens back — speech can start on the first finished sentence instead of waiting for the whole reply (this is why streaming is chat-only: a tool's result can't be narrated before the tool has run).
6. For a tool-needing turn, the tool-call loop in `orchestrator.py` runs (see `04a_orchestrator_core.md`): category/cue-word routing narrows ~80 tools down to a handful, the model picks and calls one, destructive tools pause for a spoken confirmation, and the loop continues until the model produces a final reply.
7. `audio/tts_kokoro.py` speaks the reply. A short silence preroll/postroll (`TTS_PREROLL_SEC`/`TTS_POSTROLL_SEC`) is written around the real audio to work around Bluetooth output ramp-up/ramp-down clipping the first/last words.
8. Pressing the hotkey again while FRED is speaking interrupts playback (see `03_voice_pipeline.md` and the cancellation mechanics documented in `llm_client.generate_stream()`).

### Architecture at a glance

```
                      ┌─────────────────────────┐
   hotkey / CLI  ───► │  FREDOrchestrator        │
   text input         │  (orchestrator.py)       │
                      │   ├─ intent.py  (chat vs tools, category routing)
                      │   ├─ tool_router.py (semantic rescue router)
                      │   ├─ vault_router.py (personal-vault retrieval)
                      │   ├─ scheduler.py (reminders/timers/file-watches)
                      │   ├─ sleep_mode.py / proactive_checks.py
                      │   └─ tools/*.py  (~80 registered tools)
                      └────────────┬─────────────┘
                                   │
                      ┌────────────▼─────────────┐
                      │  llm/llm_client.py        │
                      │  cloud cascade first       │
                      │  (Cerebras gpt-oss-120b)   │
                      │  → local llama.cpp tiers   │
                      │    (Standard/Backup/Deep/  │
                      │     Extreme, see 02)       │
                      └────────────┬─────────────┘
                                   │
        ┌──────────────┬──────────┴──────────┬───────────────┐
        ▼              ▼                      ▼               ▼
  audio/stt*.py   audio/tts*.py      memory/memory_manager  vision/*
  (Vosk / Whisper) (SAPI / Kokoro)   (FAISS + embeddings)   (screen watcher,
                                                              vision_server.py)
```

The HUD (`hud/`, a loopback-only browser page) and the phone LAN endpoint (`Core/web/phone_api.py`) are separate front ends that also ultimately call into the same orchestrator/tool surface — see `08_ui_and_vision.md` and `04b_tool_inventory.md`.

## Design philosophy worth internalizing before rebuilding

- **Everything fails open, quietly, to a local fallback.** Cloud LLM calls fall back to local models; cloud vision falls back to local vision; a missing phone falls back to "no-op, not an error." The codebase treats "third party is down" as an expected, routine condition, not an exception path.
- **VRAM is the scarcest resource and is treated as such.** One card (RTX 5060 Ti, 16310 MiB), a documented history of hard access-violation crashes from exhausting it. Only one LLM tier is ever resident at a time (see `02`); idle models unload on a timer; the background screen watcher runs in a **separate OS process** specifically so it can coordinate (via a small status file) rather than collide with the main conversation model.
- **Comments in this codebase are load-bearing.** Very unusually for a codebase, most non-trivial constants and design choices are documented with real measured numbers, dates, and the specific bug or incident that justified them. Preserve that discipline in this doc set and in any rebuild — a rebuild that reproduces the code without the reasoning will silently reintroduce the same bugs (empty TTS on unterminated reasoning blocks, tool-call JSON getting spoken aloud, VRAM collisions, Bluetooth ramp clipping, etc. — all real, all previously shipped, all fixed by a specific commented change).
- **Vatsal's explicit calls override "safer" defaults, and the code says so.** E.g. `SENSITIVE_LOCAL_ONLY = False` — the vault's own rules.md says never send personal data to a hosted model, but this flag was turned off on Vatsal's direct instruction, with the enforcement machinery left fully wired so it's a one-line revert. Treat comments like this as authoritative statements of current intent, not as bugs to "fix" back to the stricter behavior.
- **Small local models need real routing to use ~80 tools correctly.** This produced concrete engineering (the intent gate, cue-word category router, semantic rescue router) — described in `04a_orchestrator_core.md`.

## Where to find ground truth, not assumptions

- Model tiers/paths/thresholds: `Core/config/settings.py` — read it directly, it is extremely well commented and is cited throughout this doc set. Do not trust any tier name/model mapping stated elsewhere (including README.md, which drifts) without checking this file.
- The real current tool list: `Core/tools/registry.py`.
- The real current orchestrator control flow: `Core/orchestrator/orchestrator.py`.
- What's actually built vs. planned: cross-reference `07_known_gaps_and_unfinished_work.md` against the repo-root planning docs (`fred-presence-sleep-mode-plan_2026-08-18.md`, `MVP Plan (v1.0 - v1.1).txt`, handoff files) — those docs describe *intent*, the code is *truth*.
