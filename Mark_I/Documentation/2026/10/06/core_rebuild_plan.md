# Mark I Core: ground-up local rebuild of FRED (plan only, nothing built, 2026-10-06)

Target folder: `Mark_I/Initial/Core/`. Old FRED (`Old/Core`, v1.x) stays untouched as reference. FRED stays down until the whole revamp is done. This document is the plan to agree on before any code.

## 1. What the old FRED teaches us (from the code and the logs)
- **One class does everything.** `orchestrator.py` is 3,700 lines in one class; `_register_tools` alone is about 1,830 lines (111 inline registrations); `config/settings.py` is 87 KB; `llm_client.py` carries a cloud cascade that is switched off. Fixing anything means reading all of it.
- **The tool loop is the weak point** (see `fred_log_review.md`): arguments are not type-checked (`int.strip` crash), identical failing calls are repeated up to 5 times, the router narrows the tool list so chained requests miss tools, tools return raw errors to a 4B model, one search can crash the process (`os.walk`), black camera frames are described as scenes, presence and the LLM collide on the GPU (4,302 logged CUDA errors).
- **Logs mix tests with reality** ("boom", "cloud must not be called" are test output in the real session log).
- What worked and is worth keeping as design: regex dispatcher for obvious commands (no model call), cue + embedding router, confirmation gate for irreversible tools, read/send never share a turn, code-enforced agent allowlists, the benchmark suite, phrase cache and fillers, destructive-flag on tools.

## 2. Principles for the rebuild ("100% control")
1. **Fully local models.** Every model call goes to the playground (`http://localhost:8090/v1`: brain, embedder, vision) or runs in-process on the GPU (faster-whisper) or CPU where Vatsal allowed it (Kokoro, wake word, VAD, Smart Turn, voice ID). No cloud provider code at all, not even switched off.
2. **Small modules with sockets.** One file per job, each stating its sockets in the docstring; no class over about 400 lines; no inline tool registration.
3. **Tools are data.** One module per area; each tool is a function with a typed schema in one place. The dispatcher coerces and validates arguments from the schema before the function sees them.
4. **The tool loop is an engine with rules in code** (like the memory weekly engine): identical-failure refusal, failure cap per tool per turn, evidence of success before "done", honest fault statements, chain-aware tool offers, parallel calls.
5. **Every tool returns a structured result** (`ok`, `data`, `error_kind`, `speakable`), never a raw string the small model has to interpret.
6. **Separate logs.** Real session log, tool-call log, test output in different places. Verbose logging from day one (the memory decision log pattern).
7. **GPU is shared and scheduled.** One owner for GPU work; presence and screen models queue behind the brain instead of colliding.
8. **Small config.** Typed settings with defaults in code; secrets and personal data outside the repo (same rule as the memory).
9. **Tests per module**, a scripted fake model so the engine is tested without a GPU, and the old benchmark cases ported as the acceptance suite.

## 3. Proposed structure (`Mark_I/Initial/Core/`)
| Package | Job | Reference in old FRED |
|---|---|---|
| `models/` | client for the playground (chat, embeddings, vision), one adapter for `llm(prompt)` and `llm(messages)` (memory side needs both), health check, load window | `llm/llm_client.py`, `vision_server.py` |
| `engine/` | turn pipeline: classify, route, tool loop, confirmation gate, reply checks (false-completion guard) | `orchestrator.py`, `dispatcher.py`, `intent.py`, `tool_router.py` |
| `tools/` | registry + typed schema + arg coercion, one file per area | `tools/*` (110 tools) |
| `audio/` | mic, wake word, VAD, STT, TTS, fillers, phrase cache, audio devices | `audio/`, `input/` |
| `senses/` | face presence, screen watcher, camera (GPU-scheduled) | `input/presence.py`, `vision/` |
| `memory_bridge/` | sockets to `Mark_I/Initial/memory` (archive feed, recall, personal model, ask queue) | `memory/` (replaced) |
| `proactive/` | scheduler, reminders, proactive checks, naturalness gate | `orchestrator/scheduler.py`, `proactive_checks.py` |
| `ui/` | pill window, HUD, popup for the ask queue | `ui/pill_app.py`, `hud/` |
| `phone/` | adb phone control, token-gated LAN endpoint | `tools/phone_tools.py`, `web/phone_api.py` |
| `config/`, `logs/` | typed settings; real/tool/test logs | `config/settings.py` |

## 4. The tool-calling engine (what to fix by design)
- Typed schema per tool; coercion (`int` to `str`, relative paths, env vars) before the call.
- Identical call that already failed this turn: refused with the earlier error. Two failures on one tool: stop, tell the user what failed and why.
- Tool offer by chain: if the request looks like two parts (or the model asks), offer the tool groups for both; keep the cue/embedding ranker for the first offer.
- Multi-tool turns: allow parallel independent calls; order dependent ones; hard cap per turn (the old log shows up to 5).
- Results are structured; the model never sees a raw traceback.
- A tool that cannot do its job says so (camera black frame, vision unavailable) and the engine speaks the fault, not a guess.
- Bounded everything: file searches, subprocesses, network calls (timeouts and depth limits).

## 5. Old modules: port, rebuild, drop, defer
- **Port (copy the logic, new shape):** tools whose logic is sound (time, weather, calculator, volume/mute/devices, reminders/timers/agenda, files read/write, window control, power/lockdown after arg fixes), dispatcher regexes, confirmation texts, system prompt, Kokoro wrapper, wake-word loader (the wake-word model itself stays frozen), phrase cache.
- **Rebuild:** the orchestrator and tool loop, tool registration, config, session/tool logging, presence scheduling, vision tools, `launch_application` and `find_file_smart`, memory integration.
- **Drop:** cloud LLM cascade and cloud vision, the old FAISS memory manager (replaced by the Mark I memory), `Attic/` and `Legacy/` code, `_tmp_*` scratch.
- **Defer (decide per item):** HaiSmart smart-AC vendor code (about 400 KB, cloud API), Gmail (Google API, network), WhatsApp/phone message reading (adb is local), web search and weather (network), face enrollment and headphone classifier training, wake-word training scripts.

## 6. Build order (each phase shippable and tested before the next; same method as the memory build)
0. **Foundations:** typed config, logging (real/tool/test), playground client with the two call shapes, tool registry with schema, coercion and a scripted fake model. Tests with no GPU.
1. **Text brain:** engine (classify, route, tool loop, gates) with a first set of ported tools; CLI to talk to it; port the old benchmark cases as the acceptance suite; reach at least the old scores (tools 77.8%, conversation 76%) with the new failure rules on.
2. **Memory bridge:** archive feed, `judged_search`, `PersonalModel.relevant`, nightly/weekly jobs on the playground (integration plan steps 1-5).
3. **Voice out:** Kokoro, fillers, phrase cache, "digging deeper".
4. **Voice in:** mic, wake word, VAD, STT, hotkey, turn handling; barge-in later.
5. **UI:** pill window, HUD; the popup/HUD for the ask queue (integration step 6).
6. **Senses:** presence, screen, camera behind a GPU scheduler.
7. **Integrations and tools:** the rest of the tool set, per the decisions in section 5.
8. **Proactive and scheduler;** precedents wiring (integration step 7); digest and alerts (step 8).
9. **Soak and cut-over:** long run, then extract from `Initial/` to `Mark_I/`.

## 7. Open decisions (need Vatsal)
1. **Scope:** is Mark I the faithful local rebuild of v1.x (voice loop, tools, memory, UI) with the fixes above, leaving the assembled architecture from the old Mark II plan (turn controller with barge-in, screen events, proactive judge, talker/judge model, 12 more models) for Mark II? Recommended: yes. It keeps Mark I buildable and testable; the assembled layers plug into the sockets later.
2. **Network tools:** "fully local" means local models. Should tools that need the internet (web search, weather, Gmail, smart AC) stay as optional tools, be rebuilt later, or be dropped?
3. **First tool set:** which tools carry over in phase 1? Recommended: the ones with working logic and few failures (time, weather, calculator, devices, volume/mute, reminders/timers, agenda, file read/write/search, windows, lockdown/power, screen description, camera); the rest later.
4. **Brain:** start with the old baseline (Qwen3.5-4B Q6_K) through the playground, and revisit the brain choice (gpt-oss low, Qwen3-8B, Mercury, others) with the new benchmark once phase 1 is working?
5. **Where the personal data lives** (memory folder, vault, secrets): outside the repo, as before; confirm the location.
