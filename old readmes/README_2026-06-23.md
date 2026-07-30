# F.R.E.D.

**F**riendly, **R**esponsive, **R**ational, **R**akish **E**lectronic **D**ude — a personal JARVIS, built to run entirely on local hardware.

Not a product. Not for sale. A personal assistant built to live with, on an RTX 5060 Ti, with nothing leaving the machine except two deliberate, explicit exceptions (live web search and weather).

## What's actually working right now

- **Local LLM inference** via `llama-cpp-python`, GPU-accelerated, with a tiered model strategy so FRED doesn't run a 14B model to tell you the time:
  - `nano` — quick replies, OS commands, trivial lookups (NVIDIA-Nemotron-3-Nano-4B)
  - `standard` — everyday conversation (Qwen3.5-9B)
  - `deep` — reasoning, planning, anything that actually needs it (Qwen3-14B)
- **A deterministic dispatcher** that catches obvious commands ("open Spotify", "what time is it", "weather in Tokyo") and executes them directly — zero LLM calls, true millisecond response.
- **Tool-calling** for everything the dispatcher doesn't catch — FRED can open apps/websites, create files/folders, check the time, search the web, and check the weather, all via the LLM deciding to call a tool and FRED actually doing it.
- **Long-term memory** — every exchange is embedded locally (also via `llama.cpp`) and stored in a FAISS index, retrieved by semantic relevance on every turn.
- **Voice** — offline STT (Vosk) and offline TTS (`pyttsx3`/Windows SAPI), plus passive wake-word detection ("Hey Jarvis" via openWakeWord). Text mode is always available as a fallback.
- **Fully local inference** — no API keys, no cloud LLM calls, nothing phoning home for the actual "thinking." The only two exceptions are `web_search` and `get_weather`, which by definition need the live internet.

## Project layout

```
Core/            the actual runtime (this is the only folder main.py needs)
  main.py        entry point — text mode by default, type "voice" for voice mode
  orchestrator/   dispatcher + tool-calling loop + conversation flow
  llm/            local llama.cpp inference, tiered model selection
  memory/         FAISS + local embeddings, long-term memory
  tools/          things FRED can actually do (system + web)
  audio/          STT, TTS, wake-word
  personality/    F.R.E.D.'s system prompt
  config/         all settings in one place
Legacy/          prior iterations of this project, kept for history
PC/              hardware-purchase reference docs
Presentation/    school/demo script
Video/           raw footage (~40GB, gitignored — not tracked)
Phases *.txt     the roadmap — where this came from and where it's going
```

## Running it

```
cd Core
venv\Scripts\python.exe main.py
```

Requires LM Studio's model files present at the paths configured in `Core/config/settings.py`, and an NVIDIA GPU for the CUDA-accelerated build of `llama-cpp-python`. See `Phases 11 - 20 (JARVIS Roadmap).txt` for what's built, what's next, and why.
