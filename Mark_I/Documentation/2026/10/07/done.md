# 2026-10-07

**Embedder switched to Qwen3-Embedding-0.6B (Q8_0, 1024-d)** everywhere: `Core/config.py` (`embedder`), `memory/run_local.py` (`EMB`), `playground/models.json` (id `local/qwen3-embedding-0.6b-q8_0`, same id whether the playground lists it or finds it on disk). Reason: the assembled-FRED VRAM plan (`Old/ASSEMBLED_FRED_VRAM.md`) chose the 0.6B; the 4B is out on size. The GGUF was downloaded from `Qwen/Qwen3-Embedding-0.6B-GGUF`.
- Memory vectors are stored per model name, so nothing mixes; the real memory has no vectors yet, so nothing to migrate. New vectors fill in on the first `sync()`.
- Live check: 1024-d vectors; "what time is it" vs "tell me the current time" 0.86, vs unrelated 0.43-0.44.
- **Thresholds tuned on the 4B and not re-tuned** (no benchmarking, by standing rule): `semantic_floor` 0.65 (Core), `MIN_SIM` 0.45 (personal model), precedent `SIM` 0.75, `FIELD_SIM` 0.85. Smaller embedders usually score a little differently; watch for them being too strict or loose.
- The 4B GGUF and its `models.json` entry were deleted on request.

**Skills loader** (`Core/skills/loader.py`, `rank_check.py`): every tool is a folder with `manifest.json` + `run.py`; `core/` and `extra/` tiers; see the module docstrings. `ToolSpec.not_for` added.
