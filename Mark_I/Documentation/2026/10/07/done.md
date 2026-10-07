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

## Built-in skills completed (later the same day)
The rest of the decided built-in set, all in `Core/skills/core/`, each with `manifest.json`, `run.py`, `check.py`:
- `system/time`, `system/days_until` (holidays, "december 25" / "25 december", ISO dates, weekdays; past dates count "since"), `system/volume` (one skill, `action` get / set / up / down / mute / unmute), `system/system_stats` (one skill, `part` all / cpu / gpu / ram / disk, optional `drive`).
- `math/calculate` (ast only; found and fixed: float noise printed "2.0" for 2, and `1e999 * 1e999 ** 2` returned `inf`; nested huge powers are now refused, each step is size-checked).
- `self/read_own_docs`: reads Core README / HANDOFF / PLAN, `Progress/*.md` and `Mark_I/Documentation/**/*.md` locally, in pages; refuses anything else. GitHub is not used (same text locally; can be added as a second source).
- Stats are one skill with a `part` rather than four, by decision. GPU temperature comes from nvidia-smi. The CPU temperature is not available on this PC (Windows' ACPI thermal sensor reports "Not supported", psutil has no Windows sensors); the CPU answer says so. A sensor tool could fill `_cpu_temp` later.
- Built-in set now: memory (recall, remember, forget, correct), system (time, days_until, volume, system_stats), self (what_can_you_do, read_own_docs), math (calculate) = 11 skills, 70 examples, all rank first with the real embedder. 62/62 self-checks.
- `tests/brutal/engine_replay.py` stubs `volume` (recorded, never run) so replaying "mute" cannot touch the real speakers.

## Full-skill-set replay, a real wrong-action risk, and the guard
- Replay of 600 real utterances against all 11 skills (volume stubbed): 0 crashes, 0 unconfirmed destructive runs, 5 tool errors, 4 faults (two are the "nothing ran" honesty guard), median 1.45 s (the GPU was shared with other jobs, so an upper bound). The few labelled requests that map to built skills routed 4 of 6 correctly.
- Found: the 4B brain picked `volume` for old device commands ("set microphone to 20", "set speaker to 16", "change to speakers") in 59 of 600 turns; a real run would have changed the real speaker volume. Rewording the description did not help.
- Fix: a general `refuse_if` list in a skill manifest (regexes on what the user said). The engine refuses the call before it runs (`ToolSpec.refuse_if`, `engine/loop._pre`; invalid patterns break the manifest at load). `volume` refuses microphone, input, device-switching and lockdown wording. "set speaker to 16" still sets the speaker volume to 16, which is a fair reading of those words.
- EmbeddingGemma-2 test (agent): the GGUF (ggml-org, Q8_0, 296 MB) does not load; our llama.cpp b11433 says `unknown model architecture: 'gemma-embedding2'`. No comparison was possible, so no switch. Qwen3 baseline measured: 70/70 skill examples rank first, smallest margin 0.193, embed p50 60 ms. Gemma needs task prefixes the Core `Embedder` does not support, and its multimodal path needs an mmproj file. Revisit when llama.cpp gains the architecture. The test download was deleted and the Qwen3 embedder reloaded.
