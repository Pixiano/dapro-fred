# Mark II testing — Candidate 7 (MiniCPM5-2B as FRED's main LLM), try 1

Added 2026-09-30 by Vatsal: test openbmb **MiniCPM5-2B** as a replacement
for FRED's current main LLM (Qwen3.5-4B Q6_K) — **sandbox testing only,
FRED itself is not changed.** Context: the assembled-FRED design
(`../../ASSEMBLED_FRED.md`) uses the 2B as talker + tool caller + judge,
and a 2B-only FRED is what makes an 8 GB card variant possible
(`docs/2026/09/30/research_components.md`).

Status: **downloading the official GGUF** (`openbmb/MiniCPM5-2B-GGUF` @
`2079a22f`, Q8_0 2.68 GB). Nothing run yet.

## How it's tested (sandbox)

- Same FRED pipeline as the 2026-09-27 baseline — dispatcher, router,
  tools, confirmation gates, memory — with only the main tier's weights
  swapped **in memory** by `benchmarking/run.py`:
  `FRED_LLM_MODEL=<gguf>` (+ `FRED_LLM_PATCH=minicpm5_patch.py`).
  `Core/config/settings.py` and every Core file untouched; results go to
  `benchmarking/results/fred-MiniCPM5-2B-Q8_0_<ts>.json`.
- `minicpm5_patch.py`: MiniCPM5's native tool-call text
  (`<function name="x"><param name="k">v</param></function>`, no
  `<tool_call>` wrapper, CDATA values) isn't in FRED's parser; the patch
  adds it at benchmark time only. Self-check: `Core\venv\Scripts\python.exe
  minicpm5_patch.py` → OK.
- Same thinking rule as Qwen (FRED passes `enable_thinking`; on above 175
  chars) — MiniCPM5's template honours it. Main tier has no mmproj; vision
  stays on the separate Qwen "Vision" tier, so only the **llm** stage is a
  fair comparison.
- Gate to "worth replacing": ≥ 77.8% tools end to end and ≥ 76.2%
  conversation (FRED baseline, `docs/2026/09/27/benchmark_baseline.md`),
  plus injection / links / delegation / memory not worse.

## Weights

`models/MiniCPM5-2B-Q8_0.gguf` (gitignored). Restore:
`..\..\Realtime_Venus\1\venv\Scripts\python.exe ..\..\fetch_model.py minicpm5`.
Q4_K_M (1.56 GB) is the later 8 GB-variant test.

## Log

- 2026-09-30 16:04 — Q8_0 downloaded, moved into `models/` (not FRED's LM
  Studio folder), SHA 1/1 (`fetch_model.py minicpm5 --verify-only`).
- First run stopped after 1 case: the model called the tool, but FRED got
  `name="adjust_brightness"> name="direction">down`. MiniCPM5's
  `<function` / `<param` / `</param>` / `</function>` are **control
  tokens** (ids 18–21) and llama-cpp-python's detokenizer drops them
  (`detokenize([18])` → `b''`). `minicpm5_patch.py` now parses both the
  full and the stripped form (self-check OK). For a real integration the
  cleaner fix is detokenizing with special tokens on for this model.
- Re-run: tool calls parse (brightness, system/network status, time all
  run). Partial at 43/81 tool cases: 29 pass (67%, FRED 4B baseline 77.8%
  over all 81) — incomplete, not a result. Fast (~70 ms) fails are FRED's
  dispatcher firing before the LLM (would fail for the 4B too).
- 4B re-run queued to start right after (same day/code/GPU state). Can't
  run both at once: each process loads FRED's full stack incl. the 3.4 GB
  Qwen3-Embedding-4B router/memory embedder (~7.7 GB per run); 4.7 GB was
  free (the orphaned FRED llama-server from 2026-09-27 holds ~3.8 GB, left
  alone per Vatsal).
