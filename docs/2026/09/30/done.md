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
