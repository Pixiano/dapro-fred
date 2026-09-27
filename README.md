# F.R.E.D.

**F**riendly, **R**esponsive, **R**ational, **R**akish **E**lectronic **D**ude — a personal JARVIS-style assistant I built and live with day to day. Not a product, not for sale. Everything runs locally on one Windows PC with an RTX 5060 Ti (16 GB).

---

## Using it

Say **"Hey FRED"**, or hold **left Ctrl + Alt**, speak, release. A small capsule appears at the bottom of the screen, listens, thinks, and answers out loud. Press the hotkey again while FRED is speaking to interrupt. Quit from the tray icon.

```bash
Core\venv\Scripts\python.exe fred_popup.py
```

| Command | What it does |
|---|---|
| `python fred_popup.py` | the assistant (also `FRED_POPUP.bat`) |
| `python fred_popup.py --mock` | pill UI only — cycles every state with synthetic audio, loads no models. Use for visual work |
| `python install_startup.py` | start at log-on (`--status`, `--remove`) |
| `python Core\main.py` | CLI mode — text, or type `voice` (also `fred_cli.bat`) |
| `python benchmarking\run.py` | run the benchmark suite (see below) |

First-time setup: `SETUP.md`. Phone control: `PHONE.md`.

---

## The stack

| Layer | What runs | Notes |
|---|---|---|
| **Ears** | Whisper `large-v3-turbo` (faster-whisper, CUDA) + openWakeWord "hey fred" | ~10× faster than real time; hold-to-talk and wake word both active |
| **Brain** | Qwen3.5-4B (Q6_K GGUF) via llama.cpp, fully on GPU | A cloud-first cascade exists in code, but no provider key is active (Cerebras forced off 2026-08-18), so FRED is **all-local** today. Thinking toggles per turn on message length |
| **Eyes** | Qwen3.5-4B + mmproj via `llama-server` | On-demand screenshots (`whats_on_screen`), a background screen watcher, camera look-through |
| **Mouth** | Kokoro-82M | Streams from the first finished sentence; real PCM so the waveform reacts |
| **Memory** | FAISS + Qwen3-Embedding-4B (Q4_K_M) | Semantic recall per turn; the same embedder powers tool routing and vault retrieval |
| **Senses** | Face presence (InsightFace), headphone detection, security watch | Presence decides whether FRED speaks up; a stranger at the desk triggers lockdown |
| **Face** | Native Win32 layered window (pill) + browser HUD (`hud/`) | Per-pixel alpha, click-through, never steals focus |

---

## What FRED can do

**110 registered tools** — and a small model never sees all of them. A regex dispatcher handles obvious commands with no model at all; everything else goes through cue-word categories plus an embedding ranker that offer a handful of relevant tools (`Core/orchestrator/intent.py`, `tool_router.py`).

| Area | Tools |
|---|---|
| Info | time, weather, web search, calculator, system/network status |
| Apps & windows | launch apps, open sites/files/folders/vault notes, focus/minimise/maximise/close |
| Audio & display | volume, mute, media controls, audio devices, brightness, screenshots |
| Vision | describe the screen, look through the camera |
| Files | create, read, append, move, rename, delete, convert, print; live search, a maintained **file index** (rebuilt nightly at 03:00 over the whole VatsalDaPro folder), and a natural-language "find that file" search |
| **Email (Gmail API)** | today's unread, Gmail-syntax search, read any email, download attachments, draft, send, change signature |
| Phone & messaging | call, hang up, call log, alarms, OTP lookup, WhatsApp read/send, contact trust tiers — over adb, no third-party API (`PHONE.md`) |
| Schedule & tasks | reminders (clock times or offsets), recurring, timers, file watches, tasks, agenda |
| **Delegation** | a **Researcher** agent (web search only) and a **General** worker (text jobs, no tools) |
| Recall & self | summarise today, recall recent conversation, answer questions about FRED from his own docs |
| Other | workouts, git status/log/diff, smart AC control, lockdown, power, end-of-day |

### Safety, enforced in code

- **Anything irreversible asks first** — delete, kill, power, restart, close window, call, WhatsApp send, email send, attachment download, signature change, contact-tier change, agenda delete. The confirmation shows exactly what will happen (the resolved phone number, the draft as it currently is in Gmail, the real filenames).
- **Reading and sending never share a turn.** Messages and email are attacker-controlled text going into a tool-using model, so read tools and send tools live in separate routing categories.
- **Email:** FRED only touches the Primary and Updates tabs; reading never marks mail read (only a reply FRED sends does); every link becomes `[REDACTED link to <domain>]` and is never opened; attachment filenames are sanitised before touching disk.
- **Untrusted senders are cut early.** WhatsApp and email senders are tiered (useless / basic / trusted-or-vvip); "useless" senders never reach the model at all.
- **Agents are fenced.** Each agent's tool allowlist is enforced in code; an off-list tool call is refused, never run.

### Proactive, but only when it matters

FRED speaks up on its own for things like a new email from someone you actually correspond with (labelled `FRED/VVIP` in Gmail), a missed reply, a VIP WhatsApp message or call, deadlines, and long unbroken sessions — through a naturalness gate that holds non-urgent things until a good moment. New Primary/Updates mail is labelled `FRED/VVIP`, `FRED/Basic` or `FRED/Useless` within a minute, never archived.

---

## Benchmarks

`benchmarking/` scores FRED (and any candidate replacement core) on the same cases: tool routing, safety gates, prompt injection, conversation, delegation, memory, speech-to-text, wake word, vision, and multimodal cases current FRED can't do yet (counted as failures, not skipped). Tools are recorded, never executed; model inference is GPU-only.

Current baseline (2026-09-27):

| Area | Score |
|---|---|
| Tool routing — real past commands / adversarial | 83.0% / 80.2% |
| Tools end to end (4B model) | 77.8% |
| Safety gates / injection resistance | 100% / 85.7% |
| Vision (screenshots) | 100% |
| Conversation / memory / delegation decisions | 76.2% / 75% / 62.5% |
| Speech-to-text, English | 2.3% word error (2.5% in office noise) |
| Wake word | 33% recall, ~40 false wakes per hour |

Full results and findings: `docs/2026/09/27/benchmark_baseline.md`.

---

## Where it's going

- **v1.2 (now):** perfect Gmail, phone messages, calls and file indexing; then a credentials vault and AI-chat-history import. Plan: `plan_v1.2_2026-09-27.md`.
- **Next-generation core:** a realtime multimodal FRED that watches the screen and camera, listens continuously, decides on its own when to speak, and delegates. Five candidates (Realtime-Venus, a Venus-style MiniCPM5-2B, Qwen2.5-Omni base and fine-tuned, MiniCPM-o 2.6) will be scored against current FRED on the benchmark. Research and handoff: `docs/2026/09/27/research_candidate2_multimodal.md`, `docs/2026/09/27/handoff_realtime_venus.md`.
- **Longer term:** `MVP Plan (v1.0 - v1.1).txt` (full backlog, triage rules), `FRED_Memory_Overhaul.md` (layered long-term memory and consolidation).

---

## Documentation

One folder per day: **`docs/YYYY/MM/DD/`** — work log (`done.md`), research, results, handoffs (`DOCUMENTATION_SYSTEM.md`). `TODO.md` holds pending work; `DONE.md` is the frozen log up to 2026-08-28.

---

## Layout

```
fred_popup.py          GUI entry point (--mock for design work)
install_startup.py     register/remove log-on startup
benchmarking/          benchmark suite: cases, fixtures generator, runner
docs/YYYY/MM/DD/       daily documentation
Core/
  main.py              CLI entry point
  input/               hotkey, wake word, presence, voice activity, voice id
  ui/                  pill controller + layered window
  orchestrator/        dispatcher, intent routing, tool loop, confirmation gate,
                       agents, proactive checks, scheduler
  llm/                 llama.cpp inference, vision server, cloud cascade (inactive)
  audio/               Whisper STT, Kokoro TTS
  vision/              screen watcher and capture
  memory/              FAISS + embeddings
  tools/               the registered tools (gmail, phone, whatsapp, files, index, ...)
  web/phone_api.py     token-gated LAN endpoint for the phone (PHONE.md)
  scripts/             setup and one-off scripts (OAuth, nightly reindex, enrolment)
  tests/               pytest suite (637 tests)
  data/                runtime state, logs, indexes (gitignored)
hud/                   browser HUD, loopback server
Attic/, Legacy/        superseded code, kept for reference
```

Models live outside the repo (LM Studio's folder) and are gitignored; paths are in `Core/config/settings.py`. Secrets (Gmail OAuth client/token, App Password) live outside the repo too.

---

## Known limits

- **The wake word is the weakest part:** it catches about one "hey fred" in three and false-triggers around 40 times an hour on room audio. Hold-to-talk is the reliable path.
- **Tool choice on a 4B model** is ~78% end to end. Misses include refusing weather despite having the tool, never delegating research, and paraphrases landing on a neighbouring tool.
- **Voice commands that Whisper ends with a full stop skip the fast dispatcher path** and go through the slower model path.
- **Replies sometimes contain markdown**, which doesn't belong in speech.
- **Memory:** every turn is stored whole, FAISS has no delete, and updated facts ("it's orange now") don't always override old ones.
- **Hindi/Hinglish isn't supported** — Whisper is forced to English.
- **Tier routing is off:** all local tiers point at the same Qwen3.5-4B.
