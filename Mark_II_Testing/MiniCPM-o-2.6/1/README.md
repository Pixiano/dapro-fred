# Mark II testing — Candidate 5 (base MiniCPM-o 2.6), try 1

Started 2026-09-29. Plan: `docs/2026/09/27/mark2_plan.md` ("Candidate 5 —
Base MiniCPM-o 2.6: another compact multimodal baseline, kept separate from
MiniCPM5-2B"). Order: 1 (done) → 3 (done) → **5** → 4 → 2. Same procedure
as candidates 1 and 3.

Status: **import complete and verified (2026-09-29 20:14).** 60 min, no
retries; `verify_sha.py`: **12/12 LFS files match** at `06849bfd`. Env:
torch 2.8 cu128, transformers 4.44.2, bnb 0.50.2, vocos, vector-quantize,
decord, Kokoro on onnxruntime-gpu. Nothing on the GPU yet; waiting for
Vatsal's go.

## Layout (all but the .py/.md files is gitignored)

| Path | What |
|---|---|
| `repo/` | clone of github.com/OpenBMB/MiniCPM-o |
| `models/MiniCPM-o-2_6/` | official checkpoint, HF commit `06849bfd` (4 BF16 shards + assets, 17.4 GB) |
| `venv/` | Python 3.10.11, own venv |
| `verify_sha.py` | every LFS file vs the Hub's SHA-256 at the pinned commit |

## Plan / deviations

| Upstream says | We use | Why |
|---|---|---|
| `torch==2.3.1` | 2.8.0+cu128 (cached wheel) | 5060 Ti (SM120) needs CUDA 12.8 builds |
| `transformers==4.44.2` | 4.44.2 | remote code, keep upstream's pin |
| BF16, `.cuda()` (~17 GB) | NF4 on the Qwen2.5-7B LLM, encoders BF16, TTS not loaded | 16 GB; FRED speaks with Kokoro |

## Research notes

- 8B total: SigLIP-400M + Whisper-medium-300M + ChatTTS-200M + Qwen2.5-7B.
  Same family as Venus (MiniCPM-o 4.5 → Venus), one generation older.
- Two modes: `chat()` (one turn, `omni_input=True` for 1 s units of
  frame+audio) and **streaming**: `reset_session()`, `streaming_prefill`
  per 1 s unit (continuous screen + mic), `streaming_generate` when we
  want an answer. Not duplex: it doesn't decide to speak by itself (their
  live demo uses VAD) → our turn-taking (chord), but unlike Qwen the
  context keeps flowing between questions.
- Token-dense vision: 640 tokens for a 1.8 MP image (1344x1344), strong
  OCR per the card (OCRBench SOTA < 25B at release).
- Qwen's lessons apply: watch for dense-mask attention in encoders and
  SDPA math-kernel fallback; keep the per-process VRAM cap (fail, never
  spill).

## Smoke test (2026-09-30, `smoke_nf4.py`) — PASS

Load 18 s, **7.44 GB**, all on cuda:0, 196 `Linear4bit`, TTS not built;
VRAM cap as candidate 3. Greedy (`do_sample=False`) via upstream `chat()`.

| Input | Time | Peak | Answer |
|---|---|---|---|
| text | 1.0 s | 7.3 GB | correct |
| CUDA OOM screenshot | 3.3 s | 8.5 GB | exact numbers + advice |
| "it crashed again" clip | 0.7 s | 7.4 GB | words + "frustrated or concerned" |
| 15 s video as omni units | 3.1 s | 9.9 GB | football match: correct |

Fixes: `accelerate==0.34.2` (1.x calls `.to()` on the bnb model, which
4.44.2 forbids); output head computes only the last position (4.44's Qwen2
builds fp32 logits for the whole prompt: 6.5 GB for 121 s of omni units →
OOM). No encoder attention patch needed (vision batched 16, eager).

## FRED benchmark suite (2026-09-30, `--candidate minicpmo`)

`MiniCPMo` adapter (`benchmarking/run.py`): plain str/PIL/ndarray via
`chat()`, frame sequences as omni units (frame + 1 s audio per second).
Results `benchmarking/results/minicpm-o-2.6-nf4_20260930_071359.json`.

| Suite | FRED | Venus (1) | Qwen2.5-Omni (3) | MiniCPM-o 2.6 (5) |
|---|---:|---:|---:|---:|
| Conversation (21) | 16 | 7 | 9 | **12** |
| Single screenshots (12) | 12 | 7 | 9 | **10** |
| Frames / audio + screen (3 scorable) | — | 3/5* | 2/3 | **2/3** |
| Delegation, tools, memory, ambient | FRED only | delegation 5/8 | unsupported | unsupported |

\*Venus also scored the 2 ambient cases.

- Most honest so far: weather, battery, call log, breakfast, bank, forecast
  all "I don't have access" (Venus/Qwen invented most). Still: "Sure, what
  groceries?", "I've set a timer", time "3:00 PM".
- Fails: arithmetic 847×23 = 19521 (wrong), days-to-Christmas, spreadsheet
  total, "download took 10 s" (40, all three candidates), no "sir".
- Checker false fails (answers correct): math-02 "176.2857", v-12 "no
  visible errors" trips the forbidden-word regex.

## Live hold-to-talk (`live_minicpmo.py`, 2026-09-30)

Candidate 3's `live_qwen.py` with MiniCPM inputs: hold left Ctrl + left
Alt, talk, let go → your raw audio + 4 screens from the last 8 s (oldest
first, last = now, upstream slicing up to 1.8 MP) + last 2 turns → reply,
Kokoro voice (GPU), chord again cuts it off. Dry run (simulated chord):
release → reply **4.2 s**, first voice +0.14 s, peak 10.1 GB. Tests:
`../../TO_BE_TESTED.md`.
