# Mark II testing — Candidate 3 (base Qwen2.5-Omni), try 1

Started 2026-09-29. Plan: `docs/2026/09/27/mark2_plan.md` ("Candidate 3 —
Base Qwen2.5-Omni: a strong existing multimodal baseline, no FRED
fine-tuning"). Order agreed 2026-09-29: 1 (done) → **3** → 5 → 4 → 2.
Same procedure as candidate 1 (`../../Realtime_Venus/1/README.md`).

Status: **import complete and verified (2026-09-29 18:06).** Download
took 62 min in one attempt (no retries); `verify_sha.py`: **7/7 LFS files
match** the Hub at `ae9e1690` (5 shards, `spk_dict.pt`, tokenizer).
Nothing loaded onto the GPU; waiting for Vatsal's go.

Next on his go (mirrors candidate 1): NF4 load + all-on-GPU check +
VRAM measurement (talker disabled) → smoke test (text, one screenshot,
one audio clip, a short video) with latency → `benchmarking/run.py`
adapter → live test with our own turn-taking.

## Layout (all but the .py/.md files is gitignored)

| Path | What |
|---|---|
| `repo/` | clone of github.com/QwenLM/Qwen2.5-Omni @ `d8a31ca` (cookbooks, web demo, low-VRAM demos) |
| `models/Qwen2.5-Omni-7B/` | official checkpoint, HF commit `ae9e1690` (5 BF16 shards, 22.4 GB) |
| `venv/` | Python 3.10.11, separate from FRED's `Core/venv` and Venus's venv |
| `verify_sha.py` | checks every LFS file against the Hub's SHA-256 at the pinned commit |

## Import steps taken

1. `git clone --depth 1` the GitHub repo into `repo/`.
2. venv from pyenv 3.10.11; `huggingface_hub==0.36.2`, `hf_transfer`.
3. `snapshot_download("Qwen/Qwen2.5-Omni-7B", revision=ae9e1690…)` into
   `models/`, `HF_HUB_DISABLE_XET=1` (plain HTTP resumes partials; Xet
   didn't, see candidate 1), 10-attempt retry loop. ~4 MB/s → ~1.5 h.
4. In parallel: torch 2.8.0+cu128 from candidate 1's cached wheel (no
   re-download), torchvision 0.23.0 / torchaudio 2.8.0 (cu128 index),
   `transformers==4.52.3` (upstream's pin), accelerate, bitsandbytes 0.50.2,
   `qwen-omni-utils==0.0.4`, soundfile, librosa, av, nvidia-ml-py. All
   import; torch sees the RTX 5060 Ti with SM120 kernels.

## Why the 7B, and how it should fit

- 7B is the fair match for Venus (Qwen3-8B LLM); the 3B stays a fallback.
- Official numbers (repo README): BF16 needs 31 GB for a 15 s video →
  doesn't fit 16 GB. Qwen's own GPTQ-Int4 / AWQ builds: 11.6–11.8 GB for
  15 s, but their "low-VRAM mode" gets there by **offloading modules to
  CPU memory** between steps → breaks the GPU-only rule, not used.
- Plan (as candidate 1): NF4 via bitsandbytes on the Thinker's LLM
  linears only, encoders/talker in BF16, all on cuda:0, spill guard.
  Official AWQ is the fallback if NF4 misbehaves (also Thinker-only).

## Research notes (for the post-import work)

- Native in transformers (`Qwen2_5OmniForConditionalGeneration`,
  `Qwen2_5OmniProcessor`) — no remote code this time.
- Thinker–Talker: Thinker (Qwen2.5-7B LLM + vision encoder + audio
  encoder) writes text; Talker turns its hidden states into speech
  (voices Chelsie / Ethan). `model.disable_talker()` saves ~2 GB; FRED
  uses Kokoro anyway.
- Audio output only works with Qwen's exact default system prompt; a FRED
  prompt means text-only output (fine: Kokoro speaks).
- TMRoPE aligns video frames with audio in time; `use_audio_in_video`
  must be set the same in preprocessing, processor and generate.
- **Not duplex.** It answers when given a turn; it never decides by
  itself each second to speak, unlike Venus. The live test needs our own
  turn-taking (hold-to-talk chord / wake word / VAD end-of-speech) and a
  rolling window of recent screen frames + mic audio as the turn's input.
  The benchmark adapter is the easy part (`see()` / `turn()` map
  directly). The "ambient" cases (unprompted interventions) can only be
  tested by asking it periodically "should you say something?" —
  decide that design with Vatsal.
- Windows: `qwen-omni-utils` falls back to torchvision for video (no
  decord wheel needed); upstream expects `ffmpeg` for URLs only.
