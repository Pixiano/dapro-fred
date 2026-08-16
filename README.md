# F.R.E.D.

**F**riendly, **R**esponsive, **R**ational, **R**akish **E**lectronic **D**ude — a personal JARVIS-style assistant I built and live with day to day. Not a product, not for sale.

---

## Using it

Hold **left Ctrl + Alt**, speak, release. A small capsule appears at the bottom of the screen, listens, thinks, and answers out loud. Quit from the tray icon.

```bash
Core\venv\Scripts\python.exe fred_popup.py
```

| | |
|---|---|
| `python fred_popup.py` | the assistant (also `FRED_POPUP.bat`) |
| `python fred_popup.py --mock` | pill only — cycles every state with synthetic audio, loads no models. Use this for any visual work. |
| `python install_startup.py` | start automatically at log-on (`--status`, `--remove`) |
| `python Core\main.py` | CLI mode — text, or type `voice` (also `fred_cli.bat`) |

Press the hotkey again while FRED is speaking to interrupt it. First-time setup and dependency install: see `SETUP.md` (note: its file-structure section predates the current `fred_popup.py`/pill UI — the table above and `Core/main.py` are the source of truth for how to actually launch it).

---

## The stack

| Layer | What runs | Notes |
|---|---|---|
| **Ears** | `faster-whisper large-v3-turbo` | CUDA via CTranslate2. Warm RTF ~0.06–0.13 |
| **Brain** | Cloud-first, local-fallback | Tries a cloud API (Cerebras, `gpt-oss-120b`, no-retention terms) first for conversation/tool-calling; falls through untouched to a local llama.cpp tier (Qwen3-8B, thinking on) if every cloud attempt fails. Content flagged sensitive (personal/people vault data) can be pinned to the local-only path — currently disabled by explicit user choice, one flag re-arms it. Two more local tiers (Qwen3-14B, gpt-oss-20b) exist, configured, but aren't dynamically selected yet (`TIER_ROUTING_ENABLED = False`) — see `config/settings.py` |
| **Eyes** | Cloud vision (Cerebras `gemma-4-31b`), local `gemma-4-12B` GGUF as fallback | On-demand screenshot description (`whats_on_screen`) plus a background screen watcher; see Known limits |
| **Mouth** | Kokoro-82M | 1.2× speed. Returns real PCM, so the waveform reacts to actual amplitude |
| **Memory** | FAISS + Qwen3-Embedding-0.6B | Local embeddings, semantic recall per turn; also powers tool routing and vault retrieval below |
| **Face** | Native Win32 layered window (pill) + a browser-based HUD (`hud/`) | Real per-pixel alpha, click-through, never steals focus |

Hold-to-talk replaced an always-on wake word. Nothing listens at rest, there are no false triggers, no "Yes?" round trip, and key-release gives Whisper a precisely bounded utterance instead of a silence guess.

---

## What FRED can do

**~80 registered tools**, and the important part is that it is never shown all of them.

Two layers keep a small model from seeing the whole menu: a CHAT-vs-TOOLS classifier (conversation never sees tool definitions at all) and a category/cue-word router that offers only the tools matching the utterance — a handful instead of eighty. A separate semantic (embedding-similarity) router exists as a rescue path for phrasing the cue lists miss. See `Core/orchestrator/intent.py` and `Core/orchestrator/tool_router.py`.

| Category | Tools |
|---|---|
| Info | time, weather, web search, calculator, system status, network status |
| Apps | launch app, open website, open file/folder, open a vault note |
| Audio | volume get/set/adjust, mute, media play-pause-skip, input/output device |
| Display | brightness get/set/adjust, screenshot |
| Vision | describe what's on screen (on-demand capture + cloud/local fallback) |
| Windows | list, focus, minimise, maximise, close |
| Files | create, append, read, list, search (incl. fuzzy `find_file_smart`), move, rename, delete |
| Processes | list, kill |
| Power | lock, sleep, restart, shutdown, "end of day", restart FRED itself |
| Schedule | reminders (clock times or offsets), recurring, timers, file watches, list, cancel |
| Phone | call by name or number, hang up, sync contacts — see `PHONE.md` |
| Messaging | WhatsApp: read recent messages, send to a trusted contact, list/change per-contact trust tier — driven over adb, no third-party API |
| Tasks / agenda | add, list, complete tasks; add/list/update/delete agenda items |
| Workout | split, today's workout, schedule workouts |
| Git | status, log, diff summary (for FRED's own repo or another) |
| Recap / recall | summarise today, save a daily summary, recall recent conversation |
| Self-docs | describe FRED's own live state, answer questions from his own docs |
| Vault | semantic retrieval + direct open over my personal markdown notes |
| Lockdown | a kill-switch — while engaged, every tool except unlock is refused; conversation still works |

**Destructive tools ask before acting** (`close_window`, `kill_process`, `delete_file`, `power_action`, `restart_fred`, `call_phone`, `delete_agenda_item`, `send_message`, `set_contact_tier`). FRED halts the whole batch when it sees one, so a confirmation can't smuggle another action alongside it. `call_phone` resolves the contact name before asking, so the number in the question is the number that gets dialled.

Reminders accept real clock times: *"remind me to call mum at 7pm"*, *"tomorrow at 8:30am"*, *"tonight at 10"*. A time already past rolls to the next day, and FRED reads the resolved time back so a misparse is audible immediately.

**WhatsApp senders are tiered, per contact, per phone:** *useless* (dropped before FRED ever reads the text — every unread WhatsApp message is attacker-controlled input going into a tool-using model, so a whole class of sender is cut before it can be a prompt-injection surface, not just filtered after the fact), *basic* (readable, never messaged, never interrupts), *trusted* (FRED may reply), *vip* (FRED may reply, and proactively speaks up when they message). Reading works with the phone locked; sending needs it unlocked, since it drives the real WhatsApp UI over adb rather than any API. Reading and sending are deliberately separate tool-router categories so a single turn can never both ingest untrusted message text and act on it.

---

## Things worth knowing

**It streams.** Speech starts on the first finished sentence rather than after the whole reply. Only conversation streams — a tool result can't be narrated before the tool has run.

**It gives VRAM back.** After 1 h idle the local LLM unloads; 15 min later Whisper follows. Reload starts on the *keypress*, concurrently with you speaking, so it normally costs nothing.

**It sees what you're doing, two ways.** A line of active-window context is attached to every turn, and a separate `whats_on_screen` tool can take and describe an actual screenshot (cloud vision first, local model or a cache with an honest staleness hedge if that's unavailable).

**Most conversation goes to a cloud API.** This is a change from FRED's original local-only design: text conversation and tool-calling now try a no-retention cloud provider first and fall back to a fully local model only if that fails, and screen-vision does the same. `web_search` and `get_weather` still need the internet by definition. Content flagged as sensitive personal/people data can be pinned to local-only inference; that pin is currently off by explicit user choice. See `Core/config/settings.py`'s cloud-cascade comments for the exact reasoning and provider terms checked.

**Your phone can drive it.** A token-gated LAN endpoint on `:8779` accepts a command and returns FRED's reply, sharing the same file bus as the HUD console. FRED can also dial contacts on a paired Android phone. Both in `PHONE.md`.

**The HUD holds the screen awake.** While it's open the display won't sleep or blank. Uses the browser's Screen Wake Lock, loopback-only server, so it works with no network and nothing touches your power plan.

---

## Layout

```
fred_popup.py          GUI entry point (--mock for design work)
install_startup.py     register/remove log-on startup
Core/
  main.py              CLI entry point
  input/hotkey.py      low-level keyboard hook (hold-to-talk)
  ui/
    pill_app.py        controller: hotkey -> STT -> LLM -> TTS
    pill/              layered window, renderer, two indicator styles
  orchestrator/
    orchestrator.py    dispatcher, tool loop, confirmation gate, tool registration
    intent.py          chat-vs-tools router + category subsetting
    tool_router.py     semantic (embedding) tool rescue-router
    vault_router.py    semantic retrieval over the personal markdown vault
    scheduler.py       reminders, timers, file watches
  llm/                 llama.cpp local inference + cloud-API cascade, load/unload
  audio/                Whisper STT, Kokoro TTS, legacy Vosk/SAPI for CLI
  vision/               screen watcher/capture, screen-context cache
  memory/               FAISS + local embeddings
  tools/                the registered tools (machine, files, phone, whatsapp, vault, git, workout, agenda...)
  web/phone_api.py     token-gated LAN endpoint for the phone (see PHONE.md)
  utils/                notifier, model lifecycle, CUDA bootstrap
  state/                lockdown flag, persisted across restarts
  data/                 conversation memory, indexes, logs (gitignored)
hud/                    browser-based always-on-top HUD (Phase 16), loopback server
Attic/                  superseded implementations, kept readable
Legacy/                 an earlier, abandoned generation of the project — not current
old readmes/            previous versions of this file
```

Models live outside the repo (LM Studio's folder) and are gitignored — see `Core/config/settings.py` for paths. Kokoro's model files are separate release downloads; the URL is in that file.

---

## Roadmap

The authoritative, actively-maintained scope document is **`MVP Plan (v1.0 - v1.1).txt`** at the repo root. Summary:

- **v1.0 (MVP, due June 28 2026):** cut-down Phases 16-20 — HUD with live transcript, screen vision, a faster personality-iteration loop, memory split into real categories, and a permission-gate audit plus "stay in conversation" voice mode.
- **v1.1 (due Jan 31 2027):** the full backlog cut from v1.0 — dispatcher self-learning, account integrations (Spotify/YouTube/etc.) behind a proper credentials vault, an opt-in "extreme of extreme" cloud tier for the hardest tasks, real desktop-shell UI, voice cloning, and more.
- **v2+ (unscheduled):** bigger, deliberately-deferred ideas — self-improvement, a home-network "JARVIS mode" (device inventory, read-only logs, allow-listed actuators like Wake-on-LAN, gated on local-model trust before anything touches the network), and FRED placing/answering short phone calls himself. Both were scoped in depth on 2026-08-16 specifically to record their blockers, not to schedule them.

Don't duplicate that document's detail here — read it directly for the reasoning behind any of the above, the triage rule for new ideas, and what's explicitly out of scope.

**Two unrelated things are both called "fine-tune" in this project — don't conflate them.** The v1.1 item above is a TTS voice-clone fine-tune: cloning my own voice so FRED speaks in it instead of Kokoro's stock one. Separately, `Fine-Tune MVP Plan (2026-08-09).md` and `Fine-Tune Plan Draft (2026-08-12).md` at the repo root scope a LoRA fine-tune of FRED's own tool-calling model (Qwen3-8B, the Standard tier) on logged real usage, once the tool-calling path is stable enough that the training data isn't just baking in today's known bugs. Both docs are planning-only — nothing has been trained yet — and neither is folded into the phase list above.

---

## Known limits

- **Most conversation now depends on a third-party API being up.** The local fallback is real and untouched, but it's the tertiary path, not the default, as of the 2026-08-03 cloud cascade. See "Things worth knowing" above.
- **Tool choice on a small model.** Genuine action requests still depend on the model picking correctly within a routed subset, and that accuracy falls with model size. The router shields the common cases deterministically; it can't shield everything.
- **Memory is unfiltered within a turn.** Every turn is stored whole, and FAISS `IndexFlatL2` has no delete — a wrong memory needs an index rebuild. Real category-based organisation is Phase 19 (v1.0 scope, not yet landed as of this writing — verify against `Core/memory/memory_manager.py` before relying on this).
- **Dynamic tier selection isn't wired up.** Three local tiers are configured (Standard/Deep/Extreme) but only Standard is ever picked; smarter routing between them is explicitly v1.1 scope.

See `MVP Plan (v1.0 - v1.1).txt` for what's built, what's next, and why.
