# 2026-10-06: Core P0 (foundations) built

Plan: `Initial/Core/PLAN.md` (P0). Code: `Initial/Core/`, overview in `Initial/Core/README.md`. Every file carries its sockets in the docstring and a runnable self-check; `python tests/run_all.py` runs all nine (no GPU), `python tests/live_playground.py` is the live gate.

## What exists
| Module | Behaviour fixed here |
|---|---|
| `config.py` | settings from code defaults, `FRED_DATA` (default `~/FRED_Private`, outside the repo), `FRED_<FIELD>` env overrides; `set_data_dir()` for tests |
| `log.py` | `session.jsonl`, `tools.jsonl`, `crash.log` (faulthandler); size rotation; never raises; output dir resolved per write, so a test pointing at a temp dir cannot reach the real logs (asserted) |
| `events.py` | `Event(source, kind, data, ts)`, `EventBus` (sync fan-out, kind/source filters, a raising subscriber is logged and skipped), `EventLog` (ring window + daily jsonl) |
| `gpu.py` | one worker, priority queue (INTERACTIVE 0 / SENSE 5 / BACKGROUND 9), FIFO inside a priority, timeout from the call incl. queue wait; a queued job that times out is dropped, a running one cannot be killed (documented) and never deadlocks the queue; per-owner stats |
| `models/client.py` | `Chat.complete/stream/__call__`, `Embedder`, `Playground` (status, load with slot/ctx/vision, unload, window open/close), `image_part`; errors only as `ModelError(unavailable/timeout/bad_request/failed)`; retries connection errors only; schema-forced JSON via `response_format`; `think=False` and `effort` go to `chat_template_kwargs` (the thinking cap) |
| `models/fake.py` | `FakeChat` (scripted: text, message dict, `ChatResult`, exception, callable; an exhausted script fails loudly; records what was offered), `FakeEmbedder` (hashed bag of words) |
| `models/toolparse.py` | one `ToolCall` from native `tool_calls` or text (Hermes JSON, Qwen XML, Gemma, bare JSON); unknown names dropped; calls inside `<think>` ignored; invalid JSON arguments set `error` (engine answers bad_args, no crash); `strip_thinking`, `strip_debris`, `looks_leaked` (ported from the old orchestrator/llm_client) |
| `tools/coerce.py` | schema-driven coercion ("50" -> 50, "true", JSON strings, single value -> list, enum case fix, path env/`~`/relative expansion), errors for missing required / bounds / enum / length (never silent clamps), unknown keys dropped |
| `tools/registry.py` | `ToolSpec`, `Result`, `ToolError`; `call()` never raises, coerces first, bounded by a timeout, `isolate=True` runs in a killable child process; one `tools.jsonl` line per call; `schemas()` / `groups()` for the router |

## Playground change (`Initial/playground/server.py`)
- Slots generalised: chat 8191, embed 8192, **talk 8193, vision 8194** (each its own llama-server, fully on the GPU). A request/load may name `"slot"`; without it the model goes where it is already loaded, else chat (chat models) / embed (embedders). Embedders only in embed, chat models never there (400).
- **Window** `POST /api/window {action: open|close}`: open remembers every live slot (model, ctx, vision) and unloads all; close unloads the job model(s) and reloads the remembered stack. 409 on double open or close without open. `/api/status` reports `window_open`.
- Bug fixed: a request to a model already loaded in a slot used to reload it with default ctx/no mmproj, silently dropping vision. A loaded model is now used as loaded.
- `PLAYGROUND_PORT` env override so a second instance can run for tests.
- The running playground on :8090 still executes the OLD server code (started before this change); restart it to get the slots and the window.

## Measured (live gate, a second instance on :8095, RTX 5060 Ti, Qwen3.5-4B Q6, ctx 4096)
Load 4.7 s; text 2.1 s; schema-forced JSON 2.5 s; tool call parsed 2.6 s (`{'timezone': 'UTC'}`); streaming 2.3 s; embeddings dim 2560 (near pair 0.81, far 0.26); talk slot beside chat 7.8 s (two processes); vision (generated red square) answered "Red" in 7.0 s; window open -> Qwen3-8B job answers -> close restored chat + embed in 21.5 s total. All slots left empty. Tests used invented prompts and a generated image only.

## Decisions taken (change on request)
- Text-format parsers cover the families seen so far; pythonic and MiniCPM XML wait for the brain decision (one function in `TEXT_FAMILIES` each).
- Config default for `brain` is empty (undecided); the live gate defaults to `unsloth/qwen3.5-4b`.
- Subfolder modules use a 3-line `sys.path` bootstrap so `python <file>.py` works; imports are flat from the Core root.
- Stdlib only (no `requests`/`httpx`): P0 adds no dependency.

## Not done / next
P1 (L0 senses) needs the interpreter decision first (see HANDOFF.md). Nothing here is wired to the memory pipeline yet (P3b).

## Environment and brain (same day, Vatsal's call)
- Playground on :8090 restarted by me on the P0 server code (slots chat/embed/talk/vision, `/api/window` live).
- Two Python 3.11 venvs, gitignored: `Core/.venv` (everything: torch 2.11+cu128, faster-whisper/ctranslate2 4.8.1, onnxruntime, openwakeword, webrtcvad, speechbrain, kokoro-onnx, insightface, opencv, mss, pywin32/pycaw, plus the memory dependencies) and `memory/.venv` (memory only: numpy, faiss-cpu, fpdf2, pillow, networkx, cryptography). Versions pinned from the old FRED venv; recipe in `Core/README.md`, files `requirements.txt` + `requirements-torch.txt`.
- Verified in `Core/.venv`: every import loads, `torch.cuda.is_available()` True, ctranslate2 sees the GPU, `pip check` clean, all 9 self-checks pass. Memory venv: store/archive/recall/manager self-checks pass. This closes the "Core interpreter" open item.
- Brain: Qwen only for now. `config.brain` default `unsloth/qwen3.5-4b`.
