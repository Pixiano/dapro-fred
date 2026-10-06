# Mark I Core

The rebuilt FRED runtime, built layer by layer (plan: [PLAN.md](PLAN.md), handoff: [HANDOFF.md](HANDOFF.md)). Fully local models, reached only through the playground (`../playground`, http://localhost:8090/v1).

## Status
| Phase | State |
|---|---|
| P0 Foundations | built, see below |
| P1 L0 senses, P2 L1 clock, P3 L3 thinker, P4 L2 voice, P5 UI, P6 L4, P7 L5, P8 soak | not started |

## P0 modules
Each file has its sockets (inputs, outputs, failure behaviour) in its docstring and a runnable self-check.

| File | Job |
|---|---|
| `config.py` | typed settings: code defaults, `FRED_DATA` dir outside the repo (default `~/FRED_Private`), `FRED_<FIELD>` env overrides |
| `log.py` | `session.jsonl`, `tools.jsonl`, `crash.log`; never raises; tests write to a temp dir |
| `events.py` | `Event`, `EventBus` (sync fan-out, broken subscriber isolated), `EventLog` (10 min ring + daily jsonl) |
| `gpu.py` | one queue/worker for in-process GPU work: priorities, timeouts, per-owner stats |
| `models/client.py` | `Chat` (text, tools, schema, streaming, vision), `Embedder`, `Playground` (slots, load/unload, window), `ModelError` kinds |
| `models/fake.py` | `FakeChat` (scripted), `FakeEmbedder`, same surface as the client: tests need no GPU |
| `models/toolparse.py` | one `ToolCall` from native or text formats (Hermes, Qwen XML, Gemma, bare JSON) + thinking/debris cleaners |
| `tools/registry.py` | `ToolSpec`, `Result`, `ToolError`; `call()` never raises, bounded by a timeout, optional child-process isolation |
| `tools/coerce.py` | schema validation and type coercion before any tool runs |

## Environment
Two Python 3.11 venvs, both gitignored: `Core/.venv` (everything Core needs, includes the memory dependencies since Core runs memory in-process) and `memory/.venv` (the memory pipeline alone). Brain: Qwen only for now (`brain` default `unsloth/qwen3.5-4b`).
```
# Core (from Mark_I/Initial/Core)
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-torch.txt     # CUDA 12.8 torch, own index
.venv\Scripts\python -m pip install -r requirements.txt
# memory (from Mark_I/Initial/memory)
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```
Run Core files with `.venv\Scripts\python`; the playground itself is stdlib only and runs on any Python.

## Run
- All no-GPU self-checks: `.venv\Scripts\python tests/run_all.py`
- One module: `python <file>.py` (from this folder)
- Live gate against a real playground (GPU, one run at a time): `python tests/live_playground.py [chat-model-id]`; address from `FRED_PLAYGROUND_URL`.

## Conventions
- Subfolder modules start with a 3-line path bootstrap so `python models/client.py` works from anywhere; imports are flat from the Core root (`import config`, `from models import toolparse`).
- Anything above the model takes a `Chat`-shaped object, so `FakeChat` drops in; anything that reports takes the `EventBus`.
- No personal data, vault text or real logs in code, tests or docs.
