# FRED

**A voice assistant that lives entirely on one PC, answers when spoken to, keeps its mouth shut when it has nothing to add, and never phones home.**

Built by **VatsalDaPro**. FRED listens for its name, thinks with a small local language model, uses tools to actually do things (not just talk about them), and speaks back. No cloud brain, no subscription, no "sorry, I can't help with that right now, please try again later".

> **See it in action:** [the video](https://www.youtube.com/watch?v=rGW58g9dIMM&t=13s) &nbsp;|&nbsp; **Follow the build:** [the channel](https://www.youtube.com/@VatsalDaPro)

---

## What FRED is (and is not)

FRED is a personal assistant built around three rules:

1. **Local first.** The models run on your own GPU through llama.cpp. Your voice, your screen and your files stay on your machine.
2. **Tools over talk.** If a request needs a clock, a calculator, your mail, your files or the web, FRED calls a tool instead of confidently making something up. (The 4-billion-parameter brain is smart, not omniscient. We keep it honest.)
3. **Polite, not pushy.** FRED can speak up on its own (a deadline on your screen, the morning news, a welcome back), but a gate decides when, quiet hours are respected, and a daily cap stops it from becoming the coworker who narrates everything.

It is not a chatbot wrapped in a pretty window. It is a runtime: microphone to wake word to turn-taking to speech recognition to brain to tools to voice, with a pill-shaped overlay showing what it is doing.

## What it can do

| Area | Skills |
|---|---|
| **Everyday** | time, countdowns ("how many days until..."), reminders, volume, system stats, calculator |
| **The web** | weather, web search with a quality filter (rejects junk results instead of reading them out) |
| **News** | a digest built at 06:00 and 18:00 (AI, India, boards, world, one general story); FRED offers it when you sit down, reads it one section at a time and can dig into a story |
| **Your stuff** | find files, open apps, read and draft email (drafts only; sending is always your call), edit notes with a confirm step and an undo copy |
| **Your screen** | look at the screen on request, watch for deadlines and lookalike pages, recall what you were doing earlier |
| **Memory** | remember, recall, correct and forget, with a memory pipeline that builds a daily log from what happened |
| **Self-knowledge** | "what can you do?" is answered from FRED's own docs, not vibes |

Skills are folders with a manifest (what it does, example phrases, what it is *not* for), a `run.py` and a `check.py`. Adding one means adding a folder. The router is an embedding match against the example phrases, not a pile of regexes.

## How it works

```
 mic -> wake word / hotkey -> VAD -> turn clock (is the speaker done?) -> speech to text
                                                                              |
   pill + HUD  <-  speaker (TTS)  <-  brain + tools + memory  <-  router (embeddings)
                                              ^
                    senses: screen, camera presence      proactive: deadlines, news, gate
```

- **Everything talks over an event bus.** Modules publish and subscribe; none of them import each other's internals. A broken subscriber cannot take the others down.
- **Models live in a "playground".** One local server (OpenAI-compatible, port 8090) loads models into named slots: `chat` (brain), `talk` (small talker), `embed`, `vision`. FRED asks for a slot; the playground swaps models on demand.
- **Everything has a socket.** Each module documents its inputs, outputs and failure behaviour in its docstring, and ships a runnable self-check.

### The current model line-up

| Job | Model | Quant |
|---|---|---|
| Brain (tools, reasoning) | Qwen3.5-4B | Q6_K |
| Talker (chat turns, lockdown) | MiniCPM5-2B | Q4_K_M |
| Embeddings (routing, memory) | Qwen3-Embedding-0.6B | Q4_K_M |
| Speech to text | faster-whisper | - |
| Text to speech | Kokoro | - |
| Wake word | custom "Hey FRED" ONNX model | - |
| Turn detection / speaker check | Smart Turn v3.2, ECAPA | CPU |

The quants are not guesses: [`Mark_I/Tests/Quants`](Mark_I/Tests/Quants) holds the comparison. Result in one line: the talker and embedder lose nothing at Q4; the brain does (it forgets to call the calculator), so it stays at Q6.

## Repository map

```
Mark_I/
  Core/            the runtime: audio, clock, senses, engine, skills, proactive, ui, news
  memory/          the memory pipeline (archive, store, recall, daily log)
  Initial/         the model playground and early experiments
  Tests/           benchmarks and comparisons (quants, and friends)
  Documentation/   dated notes, designs, flow charts
Old/               the previous FRED, kept for reference
```

Start with [`Mark_I/Core/README.md`](Mark_I/Core/README.md) (module-by-module tables) and [`Mark_I/Core/PLAN.md`](Mark_I/Core/PLAN.md).

## Running it

Requirements: Windows 11, an NVIDIA GPU with about 16 GB of VRAM (FRED keeps roughly 14.5 GB busy when fully loaded), Python 3.11, [llama.cpp](https://github.com/ggml-org/llama.cpp) server binaries, a microphone.

```powershell
# one-time: create the venv and install (CUDA torch is listed separately)
cd Mark_I\Core
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\pip install -r requirements-torch.txt

# 1. start the model playground
.venv\Scripts\python ..\Initial\playground\server.py

# 2. start FRED (the number is seconds to listen; default is 120)
.venv\Scripts\python engine\live.py 86400
```

Then say **"Hey FRED, what time is it?"**, or hold **Left Ctrl + Left Alt** and talk, or type in the pill.

Tests: `python tests\run_all.py` runs every module's self-check with fake models and no GPU needed. Personal data (memory, logs, voice and face references) lives in `~/FRED_Private`, outside the repo, and is git-ignored on purpose.

## Design notes worth knowing

- **Failing to offer a tool is mild; firing the wrong one is not.** The router only offers tools above a similarity floor, otherwise FRED just chats.
- **Unprompted lines never enter the conversation history.** A small model will happily parrot them back at you for every short reply, so they ride along as a background note.
- **No cloud for what you say or see.** Web search sends only the extracted subject, never the sentence, and anything read from the web locks outbound actions for that turn.
- **A quantization change is an embedding change.** Swap the embedder and every stored vector (and the trained proactive judge) has to be rebuilt. Ask us how we know.

## Status

Active, daily-driven, and opinionated. Mark I is the rebuilt FRED; Mark II is being planned. Expect rough edges, honest changelogs and the occasional "why did it say *that*" that turns out to be speech recognition hearing "UP Board" as "UPSC".

## License

Source-available, **permission required**: read it and run it privately to learn, but any other use (products, copies, modified versions, redistribution) needs the author's written permission. Third-party parts (model weights, llama.cpp, packages) are not the author's to license and keep their own terms. Full text in [LICENSE](LICENSE).

## Watch, follow, heckle

If you want to see FRED think out loud, break, and get fixed on camera, that is what the channel is for:

- **The video:** [Watch FRED run](https://www.youtube.com/watch?v=rGW58g9dIMM&t=13s) - FRED, running on a home PC.
- **The channel:** [Subscribe](https://www.youtube.com/@VatsalDaPro) - builds, experiments and the occasional post-mortem.

Made with stubbornness, a lot of VRAM and **VatsalDaPro**.
