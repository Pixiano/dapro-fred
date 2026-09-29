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

## Smoke test (2026-09-29, `smoke_nf4.py`, log `smoke_nf4.log`) — PASS

Load 17 s, **7.95 GB**, all weights on cuda:0, 196 `Linear4bit`, talker
never built. Timed (after warm-up), greedy:

| Input | Time | Peak VRAM | Answer |
|---|---|---|---|
| text | 1.2 s (29 tok/s) | 7.7 GB | correct |
| CUDA OOM screenshot (native 1280x720) | 3.6 s | 10.9 GB | read "2.00 GiB … 412.00 MiB free" exactly |
| "it crashed again" clip | 0.8 s | 7.8 GB | words + "you sound a bit frustrated" |
| 15 s video (30 frames, 448 px) + audio | 4.8 s | 8.6 GB | soccer match, players walk on, shake hands: correct |

Fixes needed:
- **Encoder attention (root cause of 2 failed runs).** transformers
  4.52's SDPA path for the vision and audio encoders builds one dense
  `[seq, seq]` block mask: the screenshot reserved ~7 GB, 15 s of video
  asked for 111 GB (full-size frames) or spilled into system RAM at 15 GB
  (448 px frames; 100% GPU, no progress for 7 min, killed). Patched to run
  SDPA per `cu_seqlens` segment (what flash-attn varlen does; no Windows
  wheel). `test_segmented_attention.py`: identical to upstream, max diff
  1e-7.
- **Spill guard:** `set_per_process_memory_fraction(free at start − 0.5 GB)`
  → overflow raises OOM instead of silently paging (GPU-only rule).
- Video given as a frame list bypasses qwen-omni-utils' pixel cap → cap
  per frame at 448x252 (`VIDEO_FRAME_PIXELS`).

## FRED benchmark suite (2026-09-29, `--candidate qwen`)

`QwenOmni` adapter in `benchmarking/run.py`: one turn per case, question as
text, greedy. Frame sequences → one 1 fps video, audio placed at its
timestamp (TMRoPE `use_audio_in_video`). Results
`benchmarking/results/qwen2.5-omni-7b-nf4_20260929_185735.json`, log
`benchmark_run.log`. 2 min.

| Suite | FRED | Venus (cand. 1) | Qwen2.5-Omni-7B |
|---|---:|---:|---:|
| Conversation (21) | 16/21 | 7/21 | **9/21** |
| Single screenshots (12) | 12/12 | 7/12 | **9/12** |
| Frames over time / audio + screen | unsupported | 3/5 | **2/3** (+2 ambient unsupported) |
| Delegation decisions (8) | 5/8 | 5/8 | unsupported (no delegate token, no tools) |
| Tools, injection, links, memory | 78–100% | unsupported | unsupported |

- Better than Venus at answering: honest on weather/Delhi forecast, maths
  right (math-02 "176.2857" is correct but the case only accepts
  176.29/176.3/176.286 — false fail, case left as FRED was scored),
  meeting + title, "build failed three tests", syntax colon.
- Still invents: battery "75%", breakfast, "someone from marketing
  called", time "14:30", "Of course! What would you like to order?", "I'll
  set a timer" (no tools).
- Markdown in answers (`**`, lists) fails the voice-format cases.
- Misreads: spreadsheet total 3,880 vs 4,280, log missing 503, blank
  desktop → "appears to be blank" + error talk; download took "10 s" (40).
- Ambient cases (speak up unprompted) can't be scored: it always answers
  when given a turn. p-01/p-02/rt-* also need fixtures.

## Live hold-to-talk (`live_qwen.py`, 2026-09-29)

Hold left Ctrl + left Alt, talk, let go. Sends raw mic audio (no STT),
the last 8 s of screen at 1 fps (448 px) and the current screen at native
res (≤1080p), plus the last 2 turns (audio + reply) for follow-ups. Reply
printed and spoken by Kokoro (GPU, FRED's voice, markdown stripped);
pressing the chord again cuts it off. Deps added: sounddevice 0.5.6,
kokoro-onnx 0.5.0, onnxruntime-gpu 1.21.1 (same as candidate 1).

- Dry run (simulated chord, real mic/screen/Kokoro): release → reply
  **4.2 s** (3.3k input tokens, prefill-bound), first voice +0.1 s, peak
  8.9 GB.
- Fix found on the way: the per-segment encoder attention passed 3-D
  tensors, so SDPA used its math kernel (fp32 score matrix: 6.4 GB for a
  1080p screen → the VRAM cap raised OOM, as designed). 4-D inputs →
  memory-efficient kernel, 0.05 GB. Re-verified identical.
- Real-world test list: `../../TO_BE_TESTED.md`.
