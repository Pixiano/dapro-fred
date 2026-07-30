# F.R.E.D.

**F**riendly, **R**esponsive, **R**ational, **R**akish **E**lectronic **D**ude — a personal JARVIS that runs entirely on local hardware.

Not a product. Not for sale. An assistant built to live with, on one RTX 5060 Ti, with nothing leaving the machine except two deliberate exceptions (live web search and weather).

---

## Using it

Hold **left Ctrl + Alt**, speak, release. A small capsule appears at the bottom of the screen, listens, thinks, and answers out loud. Quit from the tray icon.

```bash
Core\venv\Scripts\python.exe fred_popup.py
```

| | |
|---|---|
| `python fred_popup.py` | the assistant |
| `python fred_popup.py --mock` | pill only — cycles every state with synthetic audio, loads no models. Use this for any visual work. |
| `python install_startup.py` | start automatically at log-on (`--status`, `--remove`) |
| `python Core\main.py` | CLI mode — text, or type `voice` |

Press the hotkey again while FRED is speaking to interrupt it.

---

## The stack

| Layer | What runs | Notes |
|---|---|---|
| **Ears** | `faster-whisper large-v3-turbo` | CUDA via CTranslate2. Warm RTF ~0.06–0.13 |
| **Brain** | Gemma 4 E4B (`gemma4` tier) | Thinking enabled — reasons privately, speaks the conclusion |
| **Mouth** | Kokoro-82M | 1.2× speed. Returns real PCM, so the waveform reacts to actual amplitude |
| **Memory** | FAISS + Qwen3-Embedding-0.6B | Local embeddings, semantic recall per turn |
| **Face** | Native Win32 layered window | Real per-pixel alpha, click-through, never steals focus |

Hold-to-talk replaced an always-on wake word. Nothing listens at rest, there are no false triggers, no "Yes?" round trip, and key-release gives Whisper a precisely bounded utterance instead of a silence guess.

---

## What FRED can do

**40 tools**, and the important part is that it is never shown all of them.

A router classifies each turn first. Conversation never sees tool definitions at all, and an action turn sees only the matching category — an average of **4.2 tools instead of 40**. Handing a small model forty options with nothing meaning "just reply" is what made it open google.com in response to "Hello Fred, how are you?"

| Category | Tools |
|---|---|
| Info | time, weather, web search, calculator, system status, network status |
| Apps | launch app, open website, open file/folder |
| Audio | volume get/set, mute, media play-pause-skip |
| Display | brightness get/set, screenshot |
| Windows | list, focus, minimise, maximise, close |
| Files | create, append, read, list, search, move, rename, delete |
| Processes | list, kill |
| Power | lock, sleep, restart, shutdown |
| Schedule | reminders (clock times or offsets), timers, file watches, list, cancel |

**Four tools ask before acting** — `close_window`, `kill_process`, `delete_file`, `power_action`. FRED halts the whole batch when it sees one, so a confirmation can't smuggle another action alongside it.

Reminders accept real clock times: *"remind me to call mum at 7pm"*, *"tomorrow at 8:30am"*, *"tonight at 10"*. A time already past rolls to the next day, and FRED reads the resolved time back so a misparse is audible immediately.

---

## Things worth knowing

**It streams.** Speech starts on the first finished sentence rather than after the whole reply. Time-to-first-text measured at 2.12 s against 7.43 s unstreamed. Only conversation streams — a tool result can't be narrated before the tool has run.

**It gives VRAM back.** After 1 h idle the LLM unloads; 15 min later Whisper follows, freeing ~5.7 GB. Reload starts on the *keypress*, concurrently with you speaking, so it normally costs nothing — audio capture needs no model at all. First use after a long idle can wait 2–3 s.

**It sees what you're doing.** One line of context per turn names the active window, so "what's this" has something to resolve against. Title only — no screenshot, no vision model yet.

**Nothing leaves the machine** except `web_search` and `get_weather`, which by definition need the internet. No API keys, no cloud inference.

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
    orchestrator.py    dispatcher, tool loop, confirmation gate
    intent.py          chat-vs-tools router + category subsetting
    scheduler.py       reminders, timers, file watches
  llm/                 llama.cpp inference, load/unload
  audio/               Whisper STT, Kokoro TTS, legacy Vosk/SAPI for CLI
  memory/              FAISS + local embeddings
  tools/               the 40 tools
  utils/               notifier, model lifecycle, CUDA bootstrap
  data/memory/         conversation memory (gitignored)
Attic/                 superseded implementations, kept readable
Legacy/                earlier generations of the project
old readmes/           previous versions of this file
Phases *.txt           the roadmap
```

Models live outside the repo (LM Studio's folder) and are gitignored — see `Core/config/settings.py` for paths. Kokoro's model files are separate release downloads; the URL is in that file.

---

## Known limits

- **Latency.** Thinking generates reasoning tokens before any audio can start. Streaming hides most of it, not all.
- **Tool choice on a 4B.** Genuine action requests still depend on the model picking correctly within a category, and that accuracy falls with model size. The router shields the common cases deterministically; it can't shield everything.
- **Memory is unfiltered.** Every turn is stored whole, and FAISS `IndexFlatL2` has no delete — a wrong memory needs an index rebuild. Selective memory is Phase 19.
- **No vision yet.** The multimodal projector is downloaded and `Gemma4ChatHandler` exists, so this is wiring rather than research. Phase 17.

See `Phases 11 - 20 (JARVIS Roadmap).txt` for what's built, what's next, and why.
