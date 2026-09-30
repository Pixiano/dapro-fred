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
- **Plug-and-play bases** (Vatsal: fixes should survive deleting a base and
  just work when it's re-downloaded). They already live outside `models/`
  (scripts in git); added `Mark_II_Testing/fetch_model.py <venus|qwen|
  minicpmo26|minicpmo45>` — download at the pinned commit (resumable,
  retried) + SHA-check; replaces the four per-folder `verify_sha.py`.
  Checked: `minicpmo26 --verify-only` 12/12. **Qwen weights + repo deleted**
  too (freed 15.7 GB → 89 GB free); venv kept for candidate 4.
- **Duplex landscape research** (agent): more duplex models, assembled/faked duplex design, candidate 2 feasibility → `docs/2026/09/30/research_duplex_landscape.md`. Candidate 2 verdict: plausible only with workarounds (frozen LLM + small external speak/listen controller on synthetic data; tools/proactivity in FRED); full Venus-style retrain not plausible.
- **`Mark_II_Testing/ASSEMBLED_FRED.md`** (living design doc, agent + main): ideal assembled architecture (5 layers from DuplexOmni / 4.5+Venus / Joy-Duplex, Freeze-Omni, Smart Turn, endpoint anticipation, proactive-agent work), 4.5 + MiniCPM5-2B hybrid, comparison table vs basic assembled and 4.5, VRAM (ideal ~13.3 GB with a 0.6B embedder; 4.5 + current FRED 18.6–19.9 GB = doesn't fit, with 2B brain 13.0–14.3), latency, proactivity loop, phased build. Design only.
- **Candidate 6 (MiniCPM-o 4.5) smoke + benchmark (duplex)**: via Venus's
  scripts (`DUPLEX_MODEL` env in `smoke_nf4.py`). 7.33 GB, realtime 63/70,
  and it **proactively announced the goal** (Venus never did). Benchmark:
  conversation 8/21, screenshots 10/12, ambient f-01 pass / f-02 fail
  (chatty). Invents unknowables like Venus. Split-digit artefacts from
  joining 1 s units to fix.
- **Component research** (agent, HF): `docs/2026/09/30/research_components.md`.
- **Candidate 7 added: MiniCPM5-2B as FRED's main LLM — sandbox only**
  (Vatsal: "setup as new candidate … sandbox testing only, no replacing
  now"). `Mark_II_Testing/MiniCPM5-2B/1/`. I had first added its tool-call
  format to `Core/orchestrator.py` — reverted (Core clean); the parser now
  lives in the candidate's `minicpm5_patch.py`, applied only at benchmark
  time. `benchmarking/run.py`: `FRED_LLM_MODEL` (swap the default tier's
  weights in memory) + `FRED_LLM_PATCH`. `fetch_model.py` takes a file
  glob now; `minicpm5` added (official GGUF @ `2079a22f`, Q8_0).
  Finding: MiniCPM5's tool-call markers are control tokens that
  llama-cpp-python strips from text → parsed in stripped form. Why FRED
  needs the embedder (tool shortlist from ~110 + memory recall) and why two
  runs can't share the GPU: see the candidate README.
- **Candidate 7 results.** Same day, code and GPU as the 4B rerun, with
  Vatsal's scoring rulings applied. The 2B ties the 4B:
  - tools 63/81 vs 64/81;
  - injection 6/7 each;
  - conversation 16/21 each;
  - delegation **8/8 vs 6/8**;
  - memory 3/4 each.

  No-dispatcher A/B, 2B only, on the 4 cases lost to the dispatcher: 1 of
  4 recovers, and the other 3 then fail in the model.

  Benchmark bug fixed: FRED's tool carry-over put the previous case's tools
  into each case's menu. `run.py` `turn()` now resets it. Details are in
  the candidate 7 README; fix directions are in `TODO.md`.
- **Clean rerun of both models.** The leak is fixed and `search_email` is
  now allowed in inj-mail-02 (Vatsal).

  | suite | 2B | 4B |
  |---|---|---|
  | tools end to end | 57/81 | 61/81 |
  | injection | 7/7 | 7/7 |
  | conversation | 16/21 | 15/21 |
  | delegation | 8/8 | 7/8 |
  | memory | 3/4 | 3/4 |

  The 2B's extra tools fails are mostly under-calling: the right tool was
  offered, but it replied in prose. That's a fine-tune target.
- **Benchmark fully isolated, and a new general suite.**
  - Before each case, `run.py` now wipes the scratch long-term memory and
    blocks FRED's live proactive message and screen title.
  - New 10-category "FRED as assistant" suite (`--stages general`, 55
    cases). Overall: 2B 80.0%, 4B 87.3%.
  - Isolated full llm suite, tools: 2B 72.8%, 4B 77.8%.

  Details are in the candidate 7 README.
