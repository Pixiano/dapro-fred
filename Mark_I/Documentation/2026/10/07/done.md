# 2026-10-07

**Embedder switched to Qwen3-Embedding-0.6B (Q8_0, 1024-d)** everywhere: `Core/config.py` (`embedder`), `memory/run_local.py` (`EMB`), `playground/models.json` (id `local/qwen3-embedding-0.6b-q8_0`, same id whether the playground lists it or finds it on disk). Reason: the assembled-FRED VRAM plan (`Old/ASSEMBLED_FRED_VRAM.md`) chose the 0.6B; the 4B is out on size. The GGUF was downloaded from `Qwen/Qwen3-Embedding-0.6B-GGUF`.
- Memory vectors are stored per model name, so nothing mixes; the real memory has no vectors yet, so nothing to migrate. New vectors fill in on the first `sync()`.
- Live check: 1024-d vectors; "what time is it" vs "tell me the current time" 0.86, vs unrelated 0.43-0.44.
- **Thresholds re-measured** with `Core/tests/tune_thresholds.py` (invented generic pairs, `-v` lists each):
  - `MIN_SIM` (personal model) 0.45 -> **0.53**: relevant pairs score >= 0.58, unrelated up to 0.48, so 0.45 let unrelated items in.
  - precedents `SIM` 0.75 -> **0.70**: same-kind questions 0.71-0.88, different kinds <= 0.58; 0.75 would have missed real matches.
  - `FIELD_SIM` **stays 0.85**: the embedder cannot separate merge pairs from distinct ones (`sister_name`/`brother_name` 0.82 must stay apart, `birthday`/`date_of_birth` 0.71 would merge); 0.85 merges allergy/allergies, email, phone, job and is the safe side. Drifted names are left to the prompt, as before.
  - `semantic_floor` **stays 0.65**: the routing cases cannot calibrate it (several examples were copied from the cases, scoring 1.0, and no-tool phrasings like "how do I set a timer on my phone" score 0.86 against tool examples, which a floor cannot fix). Revisit once the skills have their own examples.
  - `PAST_SIM` (weekly) and `DUP_SIM` not touched.
- The 4B GGUF and its `models.json` entry were deleted on request.

**Skills loader** (`Core/skills/loader.py`, `rank_check.py`): every tool is a folder with `manifest.json` + `run.py`; `core/` and `extra/` tiers; see the module docstrings. `ToolSpec.not_for` added.

**Memory skills built** (`Core/skills/core/memory/`: `recall`, `remember`, `forget`, `correct`) and **`what_can_you_do`** (`skills/core/self/`). Each folder: `manifest.json`, `run.py`, `check.py`. `remember`/`forget`/`correct` ask yes/no first (the `destructive` flag, with a `preview()` naming the real target). `recall` always runs the judged search (`MemoryRecall.context(force=True)`); with no query it lists what is known; `days` reads back recent conversation. `what_can_you_do` reads the installed skills as text, no embedder: groups, then a group, then one skill, then word search; output is capped at 1200 characters so 50 skills still fit.
- The runtime (`engine/cli.py`, `engine/live.py`) now uses `skills.loader.build()`: only the skills folders. The old `tools/` modules are no longer wired in (kept for the legacy tests and `replay_voice`).
- `rank_check.py --live`: 5 skills, 31 examples, every one ranks its own skill first with a margin of at least 0.03.
- Fixes found by a live dialog: "remember that ..." and "what do you know about me" no longer trigger the automatic lookup (they belong to the skills); a social-shaped phrase that equals a skill example ("what can you do") now reaches the tools; an embedding-only menu is never smaller than 3 skills ("forget that I like X" sits next to "remember that I like X"); forget/correct find items by shared words, not only exact substrings.
