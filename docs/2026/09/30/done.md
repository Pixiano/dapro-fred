# 2026-09-30 — work log

- **Candidate 5 (MiniCPM-o 2.6) loaded, smoke test + benchmark** (Vatsal:
  "execute 1 to 3"). NF4 7.44 GB all on GPU; smoke PASS (text/screenshot/
  audio/15 s omni video, 0.7–3.3 s). Fixes: accelerate 0.34.2 pin;
  last-position-only logits (4.44 Qwen2 prefill logits = 6.5 GB on 121 s
  of frames). First benchmark run was my adapter's bug (Qwen-style dicts
  silently dropped by `chat()` → "Hello, how can I assist you?" to every
  case) — fixed, re-run. Benchmark vs FRED / Venus / Qwen: conversation
  12/21 (16/7/9), screenshots 10/12 (12/7/9), frames+audio 2/3; most
  honest candidate so far. Details:
  `Mark_II_Testing/MiniCPM-o-2.6/1/README.md`.
- Candidate 5 added to `Mark_II_Testing/TO_BE_TESTED.md` (live script =
  step 4, not built yet).
- **Candidate 5 live script** `live_minicpmo.py` (from `live_qwen.py`; 4
  screens + audio via `chat()`). Dry run 4.2 s release → reply, peak
  10.1 GB. Row in `TO_BE_TESTED.md` now runnable.
- **Candidate 4 (Qwen2.5-Omni + FRED tool fine-tune) try 1 set up:**
  `Mark_II_Testing/Qwen2.5-Omni-FREDtools/1/`. Reuses candidate 3's
  verified weights + venv (+ `peft==0.15.2`) — disk at 60 GB free. FRED's
  110 tool definitions exported (13 confirm-gated). Real tool-call log:
  295 unique (utterance → tool) pairs over 54 tools after removing every
  holdout/benchmark utterance. Plan: QLoRA on the Thinker, tool menu from
  FRED's router, real + synthetic data de-duplicated against the
  benchmark. Waiting on Vatsal: synthetic data writer (local Qwen3.5-4B vs
  Claude) and whether to use the real log. `Mark_II_Testing/**/data/`
  gitignored.
- **Candidate ranking (Vatsal), likeliness to replace current FRED:**
  1. MiniCPM-o 2.6, 2. Venus, 3. Qwen-tuned (cand. 4), 4. Qwen (maybe out).
  Comparison table vs base FRED given in chat (to fold into the verdicts).
- **Candidate 6 added: base MiniCPM-o 4.5** (Vatsal). Venus's own base
  (only its LLM was post-trained) → the control for Venus; natively
  full-duplex + proactive (1 Hz). Import started: `openbmb/MiniCPM-o-4_5`
  @ `503e7542`, 20 GB; reuses candidate 1's venv and scripts (same
  transformers 4.51 stack). `Mark_II_Testing/MiniCPM-o-4.5/1/`.
- **Venus weights deleted** (Vatsal: out of disk). Freed 14.7 GB (74 GB
  free). Kept Venus's venv (candidate 6 reuses it), the torch wheel, all
  code/results; sample clip moved to `Mark_II_Testing/shared/` and
  candidates 3/5 smoke paths updated. Re-downloadable at pinned `ad42af5f`.
  Not deleted: MiniCPM-o 2.6 (ranked #1, not tested live yet) and Qwen
  weights (candidate 4's base).
