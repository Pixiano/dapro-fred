# Mark II testing — Candidate 5 (base MiniCPM-o 2.6), try 1

Started 2026-09-29. Plan: `docs/2026/09/27/mark2_plan.md` ("Candidate 5 —
Base MiniCPM-o 2.6: another compact multimodal baseline, kept separate from
MiniCPM5-2B"). Order: 1 (done) → 3 (done) → **5** → 4 → 2. Same procedure
as candidates 1 and 3.

Status: **import running.** Nothing on the GPU yet; waiting for Vatsal's go.

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
