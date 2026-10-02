# Mark II testing — Candidate 4 (Qwen2.5-Omni + FRED tool fine-tuning), try 1

Started 2026-09-30. Plan: `docs/2026/09/27/mark2_plan.md` ("Candidate 4 —
take Qwen2.5-Omni and fine-tune it for FRED's tool ecosystem … how much
improvement comes from simply teaching an already-capable multimodal model
how FRED's tools work?"). Order: 1, 3, 5 done → **4** → 2.

Status: **set up, nothing trained, nothing on the GPU.** Waiting for
Vatsal's go + the two decisions below.

## What's reused (disk: 60 GB free)

- Base model: candidate 3's official Qwen2.5-Omni-7B @ `ae9e1690` (weights deleted for disk 2026-09-30; `fetch_model.py qwen` restores them before training)
  (`../../Qwen2.5-Omni/1/models/`, SHA 7/7 verified when first downloaded).
- Env: candidate 3's venv + `peft==0.15.2` (only addition). A separate venv
  would cost ~8.7 GB for identical packages.
- Loader/fixes: candidate 3's `smoke_nf4.py` (NF4, encoder attention patch,
  VRAM cap).

## Data (in `data/`, gitignored — real utterances stay local)

| Source | Size | Notes |
|---|---|---|
| `data/fred_tools.json` | 110 tools, 13 confirm-gated | exported from FRED's registry (description + JSON schema) |
| `Core/data/tool_call_log.jsonl` | 835 calls + 293 feedback marks, 2026-07-30 → 09-25 | after removing every holdout turn and every utterance in the benchmark: **295 unique (utterance → tool)** pairs, 54 tools, 55 with feedback |

295 real examples over half the tools is too few alone → synthetic
examples from the tool definitions fill the rest.

## Plan

1. **Format:** Qwen2.5's native tool calling (`<tool_call>{json}</tool_call>`,
   tool schemas in the system prompt). The prompt gets the **tool menu FRED's
   router would offer** (dispatcher + cue/embedding router, ~5–15 tools),
   not all 110 (~15k tokens) — same as current FRED, so the comparison
   isolates "the model" not "the router".
2. **Training set** (~2–3k examples): real log (above) + synthetic per tool
   (paraphrases, args, Hinglish-free), plus behaviours FRED is scored on:
   no-tool turns, ask-before-acting on the 13 gated tools, ignore
   instructions inside tool results, say "couldn't" on tool errors, short
   speakable replies, "sir". Every synthetic line de-duplicated against the
   holdout and `benchmarking/cases/` (n-gram overlap) so the benchmark
   stays unseen.
3. **Training:** QLoRA on the Thinker LLM only (NF4 base, LoRA r=16 on
   attention + MLP, text-only turns; encoders/talker untouched), on the
   5060 Ti. Estimate ~1–3 h; adapter ~100–200 MB.
4. **Score:** `QwenOmniTools` adapter in `benchmarking/run.py` running a tool
   loop with the benchmark's stubs (like `FredCurrent.turn`) → tools end to
   end, injection, links, conversation, vision (regression check vs
   candidate 3), delegation (`delegate_research`/`delegate_general` are
   tools). Same live script as candidate 3 + tool execution later.

## Decisions for Vatsal

1. **Who writes the synthetic examples?**
   - (a) FRED's own local Qwen3.5-4B from the tool definitions only —
     never saw the benchmark; slower, needs the GPU for a while.
   - (b) Claude writes them in-session — faster, higher quality, but Claude
     has read the benchmark cases, so the de-dup check matters more.
2. **Use the real log?** It's Vatsal's actual utterances (some mention
   vault files / contacts). Stays local in `data/`, never committed; the
   trained adapter would carry traces of it.
