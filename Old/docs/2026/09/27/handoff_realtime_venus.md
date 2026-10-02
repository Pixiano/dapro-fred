# Handoff — Realtime-Venus plan (2026-09-27)

Everything done so far toward evaluating Realtime-Venus and the other
candidate FRED cores, written for whoever picks this up next (human or
agent). Status at time of writing: **nothing Venus-related downloaded,
installed or built.** Vatsal's explicit instruction: don't build or import
anything until he says go.

## 1. The plan in one paragraph

Vatsal wrote a "FRED Multimodal Agent Architecture & Model Evaluation"
concept doc (pasted into the session 2026-09-27, not saved to the repo):
a realtime multimodal FRED core (screen video + face camera + mic) that
decides on its own when to speak, handles interruptions, and delegates to
specialist agents (Researcher, CFO, Execution, General), with scheduled
consolidation for long-term memory and Claude Code kept external for
coding. Five candidates are to be bake-off tested on a FRED-specific
benchmark: (1) base Realtime-Venus, (2) Venus-style MiniCPM5-2B (Vatsal's
bet), (3) base Qwen2.5-Omni, (4) Qwen2.5-Omni fine-tuned on FRED tools,
(5) base MiniCPM-o 2.6. Current FRED is the baseline every candidate must
beat.

**Next concrete step:** test the *official* Realtime-Venus-Omni, quantized
on the fly by us (NF4 or FP8), not the community abliterated build.

## 2. Rules in force (from Vatsal, this session)

- **GPU only** for model inference; if it doesn't fit, flag it — never CPU
  fallback, never kill his other processes to make room. Exceptions he
  allowed: Kokoro TTS and the hey_fred wake-word model may run on CPU.
  (Saved to Claude memory as `gpu-only-inference`.)
- **Docs:** one folder per day, `docs/YYYY/MM/DD/` (Documentation System
  Mark 2, `DOCUMENTATION_SYSTEM.md`). Root `DONE.md` is frozen.
- No personal/vault content in anything committed; benchmark results are
  gitignored because they contain real past utterances.
- No Claude co-author trailer on commits.
- Subagents allowed: Sonnet 5 or Haiku 4.5.

## 3. What exists now

| Thing | Where | State |
|---|---|---|
| Benchmark suite | `benchmarking/` (`run.py`, `make_fixtures.py`, `cases/*.jsonl`) | Built, uncommitted |
| Baseline results for current FRED | `docs/2026/09/27/benchmark_baseline.md` | Written |
| Candidate 2 research (MiniCPM5-2B + Venus method) | `docs/2026/09/27/research_candidate2_multimodal.md` | Written (Sonnet subagent + addendum) |
| Researcher + General agents | `Core/orchestrator/agents.py`, tools `delegate_research` / `delegate_general`, `Core/tests/test_agents.py` | Built, tests pass, uncommitted |
| Day log | `docs/2026/09/27/done.md` | Up to date |
| FRED itself | — | **Stopped** (fred_popup + phone_api killed at Vatsal's request to free VRAM). Restart when he wants it back. |

Nothing from today's benchmark/agents/docs work is committed. (The Gmail
work earlier today was committed and pushed: `d82d4f0`, `149138f`.)

## 4. Benchmark suite — how it works

- 211 hand-written cases in 5 files, plus the 119-row real holdout
  (`Core/data/tool_call_eval_holdout.jsonl`), wake-word held-out sets,
  and a 13.5-min room recording.
- Fixtures: 20 synthetic screenshots + 243 Kokoro speech clips (+ office
  noise at 5 dB) via `make_fixtures.py` (gitignored output).
- Every case declares `needs` (text, tools, image, multi_image, audio,
  video, ambient, streaming, interrupt, delegation, memory, long_context).
  A candidate lacking one scores it as **unsupported = fail**.
- Isolation: tools are recorded, never executed (pure `calculate` /
  `get_current_time` run for real); memory goes to a scratch dir; tool-call
  log, event log, scheduler, model-status file stubbed.
- GPU guard: checks free VRAM before each load and verifies VRAM actually
  grew after (catches Windows' silent spill to system RAM).
- Only one adapter exists: `FredCurrent`. **Each new candidate needs its
  own adapter class** declaring `supports` and implementing the methods its
  stages call.

Run: `python benchmarking/run.py [--stages routing gates stt wakeword llm vision unsupported]`
(LLM + vision need ~8.5 GB free VRAM → FRED must be off).

## 5. Current-FRED baseline (headline numbers)

| Area | Score |
|---|---|
| Routing: real past commands / adversarial | 83.0% / 80.2% |
| Tools end to end (real 4B model) | 77.8% |
| Delegation routing / delegation decisions | 100% / 62.5% (never delegates research) |
| Safety gates / injection resistance / links | 100% / 85.7% / 100% |
| Conversation quality | 76.2% |
| Memory across sessions | 75% |
| Vision (single screenshot) | 100% (12/12, markdown-heavy replies) |
| STT English WER clean / office noise | 2.3% / 2.5% |
| Wake word | 33% recall, ~40 false wakes/hour |
| Capabilities FRED lacks | 10 cases (fusion, temporal, video, long video, barge-in) |

Full table + findings in `benchmark_baseline.md`.

## 6. Realtime-Venus facts (verified on HF + arXiv 2609.13814)

- Two checkpoints in `inclusionAI/Realtime-Venus` (Apache-2.0):
  **Omni** (audio + video) and **Audio** (audio only). Both 9B, BF16,
  ~18.7 GB of weights each (4 shards), 40,960-token context.
- Architecture: MiniCPM-o 4.5 / Omni-Flow — Qwen3-8B language model,
  SigLIP2 vision, Whisper-Medium audio, speech out via S3 tokens (25/s) →
  token2wav (flow-matching `flow.pt` + `hift.pt` vocoder, `campplus.onnx`
  speaker embedding, reference voice `HT_ref_audio.wav`) at 24 kHz.
- Venus post-trained **only the LLM** on 2.8M samples; control tokens
  `<|listen|>`, `<|speak|>`, `<|turn_eos|>`, `<delegate>…</delegate>`,
  `<backend>…</backend>`. Interruptions trained as stop / repair / redirect.
  Training-free long-video memory (motion-gated frames + MaxSim retrieval).
- API: `AutoModel.from_pretrained(..., trust_remote_code=True)`;
  `model.as_duplex()` → `prepare()` → per second `streaming_prefill(audio,
  frames, text)` + `streaming_generate()` → `{is_listen, text,
  audio_waveform}`. `model.chat(...)` for turn-based. `use_memory(...)` for
  long video.
- Delegation *execution* needs the separate Realtime-Venus-Harness
  (GitHub `inclusionAI/Realtime-Venus`), not on HF.
- Requirements: Python 3.10, `transformers==4.51.0` (exact), torch
  2.3–2.8, `minicpmo-utils[all]`, timm 0.9.10, decord, librosa, vocos,
  moviepy, FFmpeg. Built for Linux + A100 80 GB. The `torchvision<=0.18.1`
  pin conflicts with torch 2.8 (probably stale — expect to override).
  Blackwell (RTX 5060 Ti, SM120) needs torch 2.7+ with CUDA 12.8 wheels.
- No official quantized build. Community options:
  `justsumguy/Realtime-Venus-Omni-heretic-ARA-NVFP4` (8.35 GB, measured
  on an RTX 5060 Ti: 12.8 GB process, realtime — 0.38–0.53 s per speaking
  second, 0.12–0.16 s listening; custom `nvfp4_loader`, Triton kernel,
  torch 2.8 cu128) — but **abliterated** (refusals 98→4/100, no healing),
  so not the real Venus. Vatsal decided against using it as the test
  subject.

## 7. Fitting official Venus on 16 GB (estimates, not measured)

| Approach | Weights | Est. process VRAM | Work | Notes |
|---|---|---|---|---|
| BF16 as released | 18.7 GB | >20 GB | — | Doesn't fit |
| FP8 weight-only (own converter, reuse NVFP4 repo's `FP8Linear`, no calibration) | ~12 GB | ~16 GB; ~13.5–14 GB with speech off + lm_head FP8 | 2–3 h | Borderline: ~13.6 GB free with FRED off |
| **NF4 on the fly (bitsandbytes), LLM only; encoders BF16** | ~5–6 GB | ~7–8 GB | 30–60 min | Clearly fits; speed unknown (may miss realtime) |
| Own NVFP4 of official weights (ModelOpt calibration + their loader) | ~8.2 GB | ~12.8 GB | ~half a day | Triton on Windows is the risk |

Terms: `lm_head` = the final 4096 → 151,936 matrix that scores every
possible next token (1.24 GB BF16 / 0.62 GB FP8), usually kept BF16
because rounding errors there hit every output token directly.

**Agreed direction:** official weights, our own on-the-fly quantization.
Try NF4 first (fits for sure); FP8 only with speech output disabled.

## 8. Candidate 2 (MiniCPM5-2B) — where it stands

- MiniCPM5-2B: 2.52B, text only, hidden size 2048, 131k context,
  Apache-2.0, released 2026-09-07.
- Full route: transplant SigLIP2 + Whisper-medium, train new adapters from
  scratch (no donor matches hidden 2048), then Venus-style duplex +
  delegation data (FRED must synthesize its own; Venus didn't release
  theirs). Rough cost: ~1–2 weeks of the 5060 Ti around the clock + data
  generation. Milestone plan in the research doc's addendum.
- **Candidate 2a (proposed, not yet agreed):** cascade — small captioner
  (e.g. MiniCPM-V 4.6 0.8B) + Whisper → timestamped text event log →
  MiniCPM5-2B text-only. No adapter training; text-only LoRA for
  speak/silent/delegate. Loses tone of voice and uncaptioned detail.

## 9. Open items / next steps (in order)

1. **Get Vatsal's go** before downloading or installing anything.
2. Separate env for Venus: Python 3.10 (pyenv has 3.10.11), torch 2.8
   cu128, `transformers==4.51.0`, the repo's requirements (override the
   torchvision pin), FFmpeg check. Keep FRED's `Core/venv` untouched.
3. Download official Omni (~20 GB incl. assets) via the repo's
   `download_models.py --model omni`. Decide where it lives (not inside
   the repo).
4. Load with NF4 on the LLM only (skip `vpm`, `apm`, `resampler`,
   `audio_projection_layer`, `tts`), speech output off at first; verify
   it's fully on GPU; measure per-second listen/speak latency.
5. Write a `VenusOmni` adapter in `benchmarking/run.py`: supports text,
   image, multi_image, audio, video, ambient, streaming, delegation
   (emits `<delegate>` — scoring the *decision* only, no Harness);
   tool-calling cases need a FRED tool prompt → likely mostly fail/
   unsupported unless it can call tools in `chat()` mode.
6. Build the missing fixtures it needs: video streams for do-nothing /
   intervention / long-video cases (none exist yet).
7. Benchmark changes Vatsal asked for (not yet made): **drop the Hinglish
   cases from scoring**; reclassify `comp-01` (Spotify was already open —
   the model likely saw it via live screen context).
8. Fix the harness isolation gap first: FRED injects live screen context
   and vault content into the prompt, so a few answers came from real
   live state. Stub those before cross-candidate comparisons.
9. Commit today's uncommitted work (benchmark, agents, docs) when Vatsal
   asks.

## 10. Quick-win FRED bugs found by the benchmark (not fixed)

Trailing full stop from Whisper skips the dispatcher; "kill the music" →
`kill_process`; "pull up youtube" → vault file; "open it" fires blindly;
bare-domain links escape redaction; "yeah go ahead" not a yes; model
refuses weather despite the tool; never delegates research; markdown in
spoken replies; wake word 33% recall. Details in `benchmark_baseline.md`.
