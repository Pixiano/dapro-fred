# Mark II testing — Candidate 1 (Realtime-Venus), try 1

Started 2026-09-27. Goal: official `inclusionAI/Realtime-Venus` Omni,
quantized on the fly by us, fully on the RTX 5060 Ti (16 GB), then scored
on `benchmarking/`. Background: `docs/2026/09/27/handoff_realtime_venus.md`.

Status: **import only.** Nothing loaded onto the GPU yet; waiting for
Vatsal's go after the import.

## Layout (all but this file is gitignored)

| Path | What |
|---|---|
| `repo/` | clone of github.com/inclusionAI/Realtime-Venus (examples, Harness, downloader) |
| `models/Realtime-Venus-Omni/` | official checkpoint, HF commit `ad42af5f` (4 BF16 shards, 18.7 GB + assets) |
| `venv/` | Python 3.10.11, separate from FRED's `Core/venv` |
| `download.log`, `pip_*.log` | import logs |

## Import steps taken

1. `git clone --depth 1` the GitHub repo into `repo/`.
2. venv from pyenv 3.10.11; `huggingface_hub`, `PyYAML`, `hf_transfer`.
3. `repo/download_models.py --model omni --local-dir models` (upstream's
   own downloader; pins one commit and verifies sizes).
4. torch 2.8.0 / torchaudio 2.8.0 / torchvision 0.23.0 from the cu128
   index, in parallel with step 3.

Measured link speed ~1.1 MB/s total, so the import takes hours, not
minutes.

### Import complete (2026-09-28, morning)

- **Model: done and verified.** All 4 shards + assets at commit
  `ad42af5f`; sizes match, and `verify_sha.py` checks all 11 LFS files
  (shards, token2wav, reference voice, sample videos) against the Hub's
  SHA-256: **11/11 match**.
- Gotcha: the dependency install upgraded `huggingface_hub` to 0.36 with
  `hf-xet`. Xet ignored the overnight partials and restarted from zero,
  and the new version also names `.incomplete` files without the old
  random suffix, so plain HTTP didn't find them either. Fix used: rename
  the largest partial per shard to the new name, then download with
  `HF_HUB_DISABLE_XET=1` (resumes via HTTP Range). Nothing was
  re-downloaded; the SHA check covers the splice.
- FRED was stopped (fred_popup + phone_api) by Vatsal's rule "when
  OOM-ing, kill FRED".
- **bitsandbytes NF4 test: PASS** (`test_bnb.py`, on Vatsal's go):
  4096→12288 NF4 layer on the 5060 Ti, cosine 0.996 vs BF16, 0.075 ms at
  1 token / 0.31 ms at 64, all finite. Risk 1 below is cleared for our
  torch 2.8 + cu128 stack.
- **NF4 smoke test: PASS, realtime with speech off** (`smoke_nf4.py`,
  log `smoke_nf4.log`):

  | | |
  |---|---|
  | Load | 21 s, 7.94 GB, all weights on cuda:0, 252 `Linear4bit` |
  | Peak VRAM streaming | 8.96 GB |
  | Listening unit | 0.55 s mean |
  | Speaking unit | 0.79 s mean, 0.93 s max |
  | Under 1 s per 1 s unit | 69/70 (miss = 1.00 s warm-up) |

  Silent until asked; at 10 s described the match correctly, at 60 s "A
  player scores a goal for his team, making them lead 1-0"; went back to
  listening on its own both times.
  Fixes needed: `as_duplex()` always loads Token2wav (+3.2 GB → 0 GB
  free) → `model.init_tts` stubbed while speech is off; upstream shells
  out to `ffprobe` (not installed) → video length via OpenCV. Script
  aborts if free VRAM < 1 GB (spill guard).
- **Live duplex** (`live_duplex.py`): mic (default lavalier) + screen or
  camera, text + voice to the Windows default output, per-second GPU
  line. Vatsal's first run (text only): screen reading accurate, answers
  good; webcam mic too far away.
- **Voice output works, not realtime yet.** Per spoken 1 s unit:

  | Config | Prefill | LLM | Talker | Vocoder | Total |
  |---|---|---|---|---|---|
  | upstream (FP32, 10 steps, eager talker) | 0.38 | ~0.4 | — | — | 1.54 s |
  | FP16 vocoder, 8 steps, eager talker | 0.38 | ~0.38 | 0.46 | 0.22 | 1.57 s |
  | **FP16, 8 steps, SDPA talker (default now)** | 0.38 | ~0.39 | 0.39 | 0.21 | **1.43 s** |

  VRAM with voice: 10.5 GB (12.1 at upstream defaults). Fixes in
  `load_duplex`: trim Token2wav's 16-step flow cache (1.95 GB fp32) to
  the steps used; wrap upstream's FP16 `set_stream_cache` in autocast
  (Float/Half bug); talker attention eager → SDPA.
- **Parked ("2 hr fix later"):** CUDA graphs + static KV cache for the
  talker's per-token step (community measured 0.11–0.15 s vs our 0.39),
  maybe the LLM step too; prefill (0.38 vs their 0.09–0.17) is the other
  gap, likely bnb NF4 vs their NVFP4 kernels. Other options considered 2026-09-28 (FP8 weight-only via
  `torch._scaled_mm`, own NVFP4 via ModelOpt + the community MIT loader,
  AWQ/GPTQ) stay as fallbacks; the abliterated community NVFP4 build is
  only acceptable as a pipeline/latency probe, never as the scored model.

### Import state (2026-09-28, night)

The whole import ran over a USB-tethered phone hotspot (`Ethernet 2`,
RNDIS); the connection dropped several times.

- **Environment: done.** torch 2.8.0+cu128 (sees the RTX 5060 Ti, SM120
  kernels in the arch list), transformers 4.51.3, bitsandbytes 0.50.2,
  accelerate 1.15, minicpmo-utils 1.0.6 (`stepaudio2` Token2wav imports),
  timm 0.9.10, vocos 0.1.0, decord 0.6.0, onnxruntime 1.21.0 (CPU only, as
  expected), numpy 1.26.4. Everything imports cleanly. The torch wheel
  was fetched with resumable `curl -C -` into `models/wheels/` because pip
  lost 0.7 GB on the first drop.
- **Model: partial, ~2 GB of 19.4 GB.** Small files, `memory_adapter/`
  and assets are in; the four shards are partly downloaded as
  `.incomplete` files under `models/.cache/huggingface/download/`, which
  resume on rerun. The retry loop was stopped by Claude Code while the PC
  was low on system RAM (5.8 of 31 GB free); not restarted on purpose.
- **Not done (waiting for Vatsal's go):** the bitsandbytes 1-layer GPU
  test, any model load.

Resume the model download (same command, reuses partial files):

```
cd Mark_II_Testing\Realtime_Venus\1
venv\Scripts\python.exe repo\download_models.py --model omni --local-dir models --max-workers 4
```

## Deliberate deviations from upstream's pins

| Upstream says | We use | Why |
|---|---|---|
| `torch==2.4.0` (demos) / `torch<=2.8` (model card) | 2.8.0+cu128 | Blackwell (SM120) needs CUDA 12.8 builds; 2.4 has no SM120 kernels |
| `torchvision<=0.18.1` (model card) / 0.19.0 (demos) | 0.23.0 | must match torch 2.8; the old pin is stale |
| `transformers==4.51.0` (model card) / 4.51.3 (demos) | 4.51.3 | same minor; the remote code touches `DynamicCache.key_cache` directly, so stay on 4.51.x, never newer |
| Linux + A100 80 GB | Windows 11 + 16 GB | the whole point of this try |

## Research findings (to make the post-import work smooth)

### The paper (arXiv 2609.13814)
- Omni = SigLIP2 + Whisper-Medium → Qwen3-8B, adapted from MiniCPM-o 4.5.
  **Only the LLM ("Thinker") was post-trained**; the speech decoder is
  frozen.
- One-second streaming units: ~10 audio features/s in, 25 S3 speech
  tokens/s out. Control tokens `<|listen|>`, `<|speak|>`, `<|turn_eos|>`;
  interruptions end the stale turn with `<|turn_eos|>` and regenerate.
- Delegation: private `<delegate>…</delegate>` span (never spoken);
  results come back as `<backend>…</backend>` at chunk boundaries.
- Long-video memory: motion-gated frame archiving at 1 fps, max 128
  frames, MaxSim-style token matching + MMR diversity.
- **The paper says nothing about quantization, VRAM, or latency.**
- Scores: StreamingBench 70.2, OVO-Bench 64.7, Daily-Omni 81.3; tool F1
  86.0 / Pass@1 43.0; interruption response 75%. Authors' own caveat:
  Omni *misses* requests that need delegation (Audio over-delegates).

### How quantization is designed for this architecture
Venus adds no quantization of its own. But its config is **MiniCPM-o 4.5
with 4 extra tokens** (vocab 151748 → 151752), same module names, and
OpenBMB (the base model's authors) publish official quants of 4.5:

| Official build | Method | Kept un-quantized |
|---|---|---|
| `openbmb/MiniCPM-o-4_5-awq` (8.46 GB) | AWQ W4, group 128, zero-point, GEMM | `vpm`, `apm`, `resampler`, `audio_projection_layer`, `audio_avg_pooler`, `tts`, `lm_head` |
| `openbmb/MiniCPM-o-4_5-GPTQ` (8.47 GB) | GPTQ W4A16, group 128, sym, `desc_act=false`, via AutoGPTQ on the extracted LLM | same idea (LLM only) |
| `openbmb/MiniCPM-o-4_5-gguf` | llama.cpp | — |

So the authors' intended recipe is: **4-bit the Qwen3 LLM only, keep
every encoder, the projector, the talker and `lm_head` in BF16.** That is
exactly our NF4 plan's skip list — add `audio_avg_pooler` to it.

The community NVFP4 build (abliterated, not used) confirms the same split
and adds real numbers on *our exact GPU*: weights 8.2 GB, process 12.8 GB
in a duplex session, cold load 106 s, speaking unit 0.38–0.53 s, listening
unit 0.12–0.16 s — **but only with a custom sidecar** (static KV cache,
CUDA graphs, one `lm_head` per step, FP16 vocoder, 8 vocoder steps). The
stock HF path will be slower; expect try 1 to miss realtime and treat
that as a known gap, not a failure of the model.

### What the remote code means for quantizing
- `as_duplex()` **always** calls `init_tts()` → imports `stepaudio2.Token2wav`
  (from `minicpmo-utils[tts]`), even with `generate_audio=False`. The
  speech package must be installed regardless.
- dtype/device are read from `embed_tokens`, `apm.conv1`, `tts.emb_text`
  — none of which we quantize, so NF4 on the LLM linears is safe.
- The duplex decoder calls `llm.lm_head(...)` as a module — fine either
  way; kept BF16 per OpenBMB.
- Upstream examples do `from_pretrained(..., torch_dtype=bf16)` then
  `.eval().cuda()`. For a quantized load we use `device_map="cuda:0"`
  and skip `.cuda()`.
- Default voice `assets/HT_ref_audio.wav` + `assets/token2wav/` come with
  the download; if missing, the code silently fetches from
  `openbmb/MiniCPM-o-4_5` — set `HF_HUB_OFFLINE=1` to fail loudly instead.
- `minicpmo-utils` 1.0.6 has no torch ceiling (`>=2.3`), pins
  `transformers<4.53`, `onnxruntime<=1.21`, `librosa==0.9.0`,
  `decord==0.6.0` (has a Windows wheel).

### Risks going in
1. **bitsandbytes on Windows + SM120.** A 2026-05 issue (#1937) reports
   "no kernel image" on an RTX 5070 Ti — but with torch 2.11 + CUDA 13.
   On torch 2.8 + cu128 it may be fine; check with a 1-layer test before
   loading 18 GB. If it fails, fallback = FP8 weight-only via
   `torch._scaled_mm` (native in torch 2.8 on SM120, no bnb), ~12 GB.
2. **onnxruntime is CPU.** Token2wav's speaker embedding (`campplus.onnx`)
   runs through onnxruntime; the pinned range is the CPU package. It runs
   once per reference voice, but it's a GPU-only-rule exception to flag.
   Irrelevant while speech output is off.
3. **Realtime** on the stock HF path is unlikely; measure it, don't
   promise it.
4. Loading needs ~19 GB of shards streamed through RAM (31 GB) — fine,
   but close other heavy apps first.

## Plan after the import (on Vatsal's go)
1. Install the rest: `minicpmo-utils[tts]==1.0.6`, `transformers==4.51.3`,
   `accelerate`, `bitsandbytes`, `timm==0.9.10`, `librosa`, `soundfile`,
   `vector-quantize-pytorch==1.18.5`, `vocos==0.1.0`, `moviepy`,
   `opencv-python-headless`, `einops`, `sentencepiece`.
2. 1-layer bitsandbytes NF4 check on the GPU.
3. Load: NF4 (double quant, BF16 compute) with
   `llm_int8_skip_modules = [vpm, apm, resampler, audio_projection_layer,
   audio_avg_pooler, tts, lm_head]`, `device_map="cuda:0"`,
   `HF_HUB_OFFLINE=1`. Verify every parameter is on CUDA and VRAM grew.
4. Smoke test: `repo/frontend/.../duplex_chat.py` equivalent with
   `generate_audio=False`; log per-second listen/speak latency.
5. `VenusOmni` adapter in `benchmarking/run.py`.

### Live run 2 (2026-09-28): VRAM growth, unanswered questions

- Voice on, lavalier + screen. Venus VRAM crept 10.2 → 11.9 GB in about a
  minute; with ~3.6 GB held by other apps Windows paged to RAM and one
  unit took 17 s (lag 31.8 s). Cause: upstream duplex default
  `sliding_window_mode="off"` — ~75 tokens/s (64 vision + ~10 audio)
  accumulate in the KV cache (~0.14 MB/token) with no limit.
- Fix: `sliding_window_mode="basic"`, `--window 5000` (trim to 75% when
  exceeded, ≈ the last minute). Below 512 MB free the live loop drops the
  oldest context, shrinks the window for good and keeps running; it stops
  only if nothing is left to drop (something outside Venus eating VRAM).
- Most spoken questions got no answer. Unknown yet whether it's mic level,
  lag while speaking, or the model. Added: mic dB per second in the
  status line, `live_mic.wav` (exactly what Venus heard), `--gain`.
- Run 3: greeted "Hello, I'm Fred" (placeholder prompt works), then mostly
  silent. `live_mic.wav`: loudest second -38 dB, peak 0.1 FS, room -50 dB
  → too quiet. Default gain now 8× (+18 dB). The VRAM trim also fired too
  early (device-free only, ignoring torch's reusable cache) and cut the
  context to 1000 tokens; now counts both.
  - Context trimming: floor raised to 3000 tokens (~40 s); `--frame-every N` added (frames are 64 of ~75 tokens/s; N=2 roughly doubles the memory span). Vatsal: "the context is dropping SOO much".

### Voice: Kokoro instead of Venus's talker (2026-09-28)

- **Venus voice switched to Kokoro** (Vatsal: "switching the TTS to Kokoro
  is just the best option"). `live_duplex.py --voice kokoro` (default):
  Venus runs speech-off (realtime, ~1.4 GB less VRAM), its text is cut
  into sentences and synthesised by FRED's Kokoro (`Core/models/kokoro`,
  am_michael, 1.2x, CPU -- allowed exception) on a worker thread; a new
  reply drops the old reply's queued audio. `--voice venus|off` kept.
  Kokoro on CPU during a Venus run: 1.9 s to make 2.6 s of audio.
  kokoro-onnx 0.5.0 pulled numpy 1.26 -> 2.2.6; full Venus import stack +
  librosa checked OK on numpy 2. Found: importing decord before
  onnxruntime breaks ort's DLL init (pre-existing; Venus/Kokoro import
  ort first, so no effect).
- **Mic order** (Vatsal): headset (Rockerz) first, lavalier as backup
  (`--mic Rockerz,Lavalier`, falls through if the headset can't open).
  Per-mic gain: lavalier 8x, others 1x until the headset is measured.
  Note: using a Bluetooth headset's mic switches it to hands-free mode
  (lower-quality audio out too).
- Per-second decision confidence in `live_duplex.py`: wraps the decoder's first `decode()` of each unit and logs the raw P(speak) = 1 - P(<|listen|>) from that unit's logits (before temperature/top-k) as `speak NN%` in the status line and `live_duplex.log`. Duplex Venus has no text reasoning to log (one token decides each second). Gotcha: upstream `decoder.listen_id` is the tokenizer eos, not `<|listen|>`; used `duplex.listen_token_id`.

### Efficiency pass (2026-09-28)

- **Venus live run 4** (12 min, Kokoro voice, headset mic): VRAM flat at
  9.3 GB (window works, no trims needed), answered on-screen questions
  correctly (read README text, an email), spoke 45 of 719 s. Headset mic
  at 1x: silence ~-65 dB, speech -23..-31 dB -- fine. Speaking units still
  0.75-1.6 s (first unit of a reply slowest).
- **Efficiency items 1-4 (Vatsal: do 1-4, report, then 5-6):** (1)
  talker not built when Kokoro speaks (`init_tts=speech`); (2) barge-in:
  2x100 ms mic blocks above -28 dB (after gain) while the voice plays ->
  cut + mute rest of that reply (`--no-barge`, `--barge-db`); (3) first
  chunk of a reply may break at a comma after 40 chars; (4) prefill
  profiler (`profile_prefill`: img_prep, vision, audio_prep, audio_enc,
  llm_feed; smoke test always, live with `--profile`). Waiting for the
  live run to stop to measure.
- **Frame quality (Vatsal: small text unreadable):** duplex uses
  `max_slice_nums=1` -> whole screen resized to ~448x448 area (~600x340 on
  16:9), 64 tokens; terminal text is a few pixels tall. Plan for #5:
  `--slices N` (MiniCPM HD slicing: overview + up to N 448 px tiles, 64
  tokens each), paired with #6 (skip unchanged frames) to pay for it.
- **Efficiency 1-4 measured** (`profile.log`, 70 s sample, speech off):
  (1) talker not built: weights 7.94 -> 7.29 GB, streaming peak 8.96 ->
  8.26 GB, still 69/70 realtime, same answers. (4) prefill split per unit:
  screen grab 0.033 s (1920x1080), img_prep 0.019, vision 0.080,
  audio_prep 0.003, audio_enc 0.017, **llm_feed 0.311 s (75%)**. Feeding
  ~75 tokens/s into the NF4 LLM dominates; suspected cause: bnb dequant
  on every separate feed() call, and upstream feeds per embedding by
  default (`batch_vision_feed=False`, documented as the slower path).
  (2) barge-in and (3) first-clause start are live-only, untested yet.
  Waiting for Vatsal's go on 5 (batch_vision_feed, then --slices for
  small text) and 6 (skip unchanged frames).
- **#5/#6 built** (Vatsal greenlit; target "~2x pixels / ~3x cost"):
  `live_duplex.py --slices 2` default (overview + up to 2 HD tiles = 3x64
  tokens), `batch_vision_feed=True` always (image passes 3 -> 1). Frame
  skip: similarity = 1 - mean|diff|/255 of 64x36 grey thumbnails vs the
  last sent frame; send when < `--similar 0.996` (Vatsal's number) or
  every `--keepalive 5` s; `--no-skip` for A/B. sha256 considered and
  rejected: exact-match only, so caret blinks/clock would count as change.
  Real screen sample: sim 0.9792-0.9998 over 5 s; grab+thumb+compare
  25 ms. Status line shows `frame/skip (sim, sent n/units)`.
  Research agent (Sonnet) started on small-text readability options and
  the slicing goldilocks zone. Measuring batch feed at slices 1/2/4.
- Frame-skip threshold: Vatsal rejected the 0.996 guess ("another agent should test it"). Calibration agent (Sonnet, CPU only, numbers only, no screenshots saved) is measuring must-send changes (new terminal line, toast, dialog, window switch, scroll) vs must-skip noise (caret blink, clock tick) across metrics (64x36 global mean, larger thumbnails, max-over-tiles), plus a 2-min passive sample of the real screen; output `skip_threshold/results.md`. 0.996 stays only as a marked placeholder until then.
- **Slice/scale measurements** (70 s sample, 1920x1080 frames):
  batch_vision_feed at slices 1: llm_feed 0.311 -> 0.245 s (-21%), 69/70
  realtime, listen 0.50 s / speak 0.75 s mean. Slices 2 (batched): vision
  0.08 -> 0.24 s, llm_feed ~0.35 s, speaking units ~1.1 s -> not
  realtime. Research agent (Sonnet): for 16:9, max_slice_nums 3 = same
  [2,1] grid as 2 (wasted); real steps 2 -> 4 -> 6; token count (llm_feed)
  is the lever, so raising `scale_resolution` (processor attribute, 448
  default) keeps 64 tokens and only costs vision time (672 est. ~0.18 s)
  but is untested quality (trained at 448); also suggested active-window
  crop, overview + one targeted HD crop, Windows OCR side-channel.
  Vatsal: "try 672 scale resolution too". Added `--scale` (smoke + live)
  and `readtest.py`: synthetic 16 px Consolas terminal, random codes,
  exact-read score across (448,1) (672,1) (896,1) (448,2) (448,4).
- **Frame-skip threshold calibrated** (agent: `skip_threshold/calibrate.py`,
  `results.md`, 88 pairs + 2 min real-screen sample, no screenshots saved).
  Finding: the 64x36 global-mean metric can't see a new terminal line
  (0.9997, same as noise) at any thumbnail size. Worst-tile of a 16x9 grid
  on full-res grey fixes it. Agent recommended 0.986, but its own data shows
  that skips 4/8 new terminal lines (0.9853-0.9864); re-evaluated: **0.990**
  catches every terminal line / toast / dialog / window switch / scroll,
  skips caret (0.9970+) and clock (0.9979+), idle skip 37%; hover
  highlights (0.9866-0.9872) may send an extra frame (harmless). Typed
  characters (0.994-0.997) left to the 5 s keepalive: catching them drops
  idle skip to 18% and fires on noise. Implemented in `live_duplex.py`
  with a self-check (new line 0.9851 send, caret 0.9982 skip, 7.6 ms).
  Also fixed: live screen grab no longer thumbnails to 1280 px before the
  model (--scale/--slices need full res; matches the calibration).
- **672 speed run:** 65/70 realtime, listen 0.71 s / speak 0.95 s mean,
  vision 0.27 s, llm_feed 0.25 s (unchanged tokens).
- **Readability test (`readtest.py`, 16 px Consolas on 1920x1080, 9 exact
  reads each):** 448/1 0/9 (0.35 s prefill), 672/1 0/9 (0.57 s), 896/1
  0/9 (3.19 s, garbled), 448/2 0/9 (0.58 s), 448/4 2/9 (0.74 s). Near
  misses creep closer with pixels ("979" for 9779, "274" for 174) but no
  realtime setting reads terminal text, and Venus invents confident numbers
  instead of saying it can't read. Live default back to slices 1 / scale
  448. Proposed (not built): overview + one native-res HD crop around the
  tiles the skip detector saw change (128 tokens).
- Review agent (Sonnet) started: HF repo + remote code + upstream harness/
  demos + arXiv 2609.13814 + MiniCPM-o 4.5 docs, for speed, longer runs
  (long-video memory, context window mode), quality, delegation wiring to
  FRED's tools, and a fit verdict -> `review_venus_for_fred.md`.
- **Focus crop built** (Vatsal: "build the crop idea now"): on a sent frame,
  the tiles the skip detector saw change (> the same 0.990 per-tile
  threshold) get a 640x320 native-res crop (~= the 448^2 area the processor
  resizes to, so no downscale) added as a second image: 128 image
  tokens/s vs 192 (2 slices) / 256 (4 slices), ~0.16 s vision. No crop when
  nothing changed or the change covers > 50% of the screen (window switch,
  big scroll: overview is enough). `--no-focus` to disable. Shared helpers
  `grey_tiles`/`tile_diffs`/`focus_crop` in live_duplex.py; readtest.py
  reuses them (crop = text appearing on an empty terminal). Self-checks
  pass. Readtest running: 448/1 no crop vs 448/1 + crop vs 672/1 + crop.
- **Review agent report** (`review_venus_for_fred.md`): no static-cache /
  CUDA-graph / torch.compile path for duplex (DynamicCache only); unused
  knobs `force_listen_count`, `listen_top_k`, `max_new_speak_tokens_per_chunk`.
  Verified by me: long-video Memory requires `sliding_window_mode="off"`
  (`memory_adapter/model_adapter.py` duplex_factory_kwargs) -- conflicts
  with our basic window, but the memory package has its own engine
  (kv_cache.py, scheduler, archive retention), unread yet. Delegation:
  harness `DelegateBackend` protocol plan/execute/oralize/aclose
  (`repo/harness/core/backend.py:16`), `<backend>` results sanitised of
  protocol tokens; no mid-execute confirmation hook -> proposed: keep
  FRED's pending-action gate in FRED (backend returns "needs
  confirmation: preview", Venus speaks it, "confirm" is a second
  delegate). Project page: delegation accuracy 75.93% Omni / 68.89% Audio
  (FRED routing 78-83%). Fit: Venus as the ears/eyes/turn-taking layer
  with FRED's tools + gates behind it, not a straight swap.
- **Focus crop readability: 448 + crop 8/9 exact reads (vs 0/9 without),
  prefill 0.46 s/unit (+0.13).** 672 + crop 2/9 at 0.95 s -> native-res
  crop at the trained 448 beats a bigger resize. Live defaults now: 448,
  1 slice, focus crop on, tile-max skip < 0.990, keepalive 5 s, Kokoro
  voice, headset mic (lavalier backup), barge-in, 5000-token window.
- Delegation note (repo/demos/model/prompts.py): upstream's demo system
  prompt is just "Streaming Omni Conversation." -- delegation is learned
  behaviour, no tool list in the prompt.
- Vatsal is running the live replacement test himself ("I will test it");
  benchmark adapter for Venus deferred (would need its own venv bridge:
  run.py's model stages run in FRED's Core venv).
- **Live replacement test by Vatsal** (15:34, 293 s, focus crop on): "works
  well" but misses replies and has a felt ~1 s delay. `analyse_live.py`
  (new): answered 10/24 times he talked; misses had speak% peaks 7-53%
  (heard, chose listen; worse later in the session, 7 of last 8 missed).
  Replies start 1 s after he stops (sometimes 2-3 s) -- the 1 s unit
  boundary -- plus unit compute (median 0.65 s, p90 0.89, never lagged)
  plus Kokoro CPU first-sentence (~1-2 s). Frames: 178 frame+crop, 66
  frame, 49 skipped. Proposed next: listen_prob_scale / prompt A/B,
  wake-word/hotkey forcing a reply, Kokoro on GPU with timing, then the
  candidate-1 verdict.
- System prompt: removed the turn-taking instruction ("speak only when he talks to you or something on screen truly needs his attention") at Vatsal's request; prompt is now "Streaming Omni Conversation. You are FRED, Vatsal's personal assistant. You can see his screen and hear him. Be brief, friendly and direct." 2 fps discussed (2 frames/unit: +64 tokens +~0.08 s; or upstream stack_frames: same tokens, half-res per frame) -- neither addresses the 1 s decision delay or missed replies; deferred.

## Integration checklist — carry these into FRED's backend

Everything below was needed to make official Venus-Omni run properly on this
machine. The reference implementation is `smoke_nf4.py` (`load_duplex`,
`timed`, `profile_prefill`) + `live_duplex.py`. Status as of 2026-09-28.

### Environment
- Separate Python 3.10 venv (never FRED's `Core/venv`): torch 2.8.0+cu128,
  transformers **4.51.x only** (remote code touches `DynamicCache.key_cache`),
  bitsandbytes 0.50.2, minicpmo-utils 1.0.6, numpy 2.2.6 (kokoro-onnx needs
  >= 2; Venus stack verified on it), onnxruntime 1.21.0 (CPU build).
- Import **onnxruntime before decord**: decord first breaks ort's DLL init.
- `HF_HUB_OFFLINE=1`: otherwise missing assets are silently fetched from
  `openbmb/MiniCPM-o-4_5`.
- No `ffprobe` on this box: upstream's `minicpmo.utils.get_video_duration`
  shells out to it -> patched with OpenCV (only needed for video files).
- Checkpoint integrity / restore: `../../fetch_model.py venus` (11/11 LFS files vs the Hub).

### Loading (`load_duplex`)
- NF4 via bitsandbytes on the 252 Qwen3 LLM linears only; skip list =
  OpenBMB's own AWQ list: `vpm, apm, resampler, audio_projection_layer,
  audio_avg_pooler, tts, lm_head`. `device_map="cuda:0"`, never `.cuda()`.
- Assert every parameter/buffer is on CUDA after load (GPU-only rule).
- With FRED's own voice (Kokoro): `init_tts=False` in `from_pretrained`
  (talker never built, -0.65 GB) **and** stub `model.init_tts` before
  `as_duplex()` (it always loads Token2wav, +3.2 GB, even with speech off).
- If Venus's native voice is ever used: `enable_float16=True, n_timesteps=8`;
  trim Token2wav's `decoder.att_cache_buffer`/`cnn_cache_buffer` from 16 to
  the steps used (1.95 GB fp32 preallocated); wrap `set_stream_cache` in
  fp16 autocast (upstream Float/Half bug); talker attention eager -> SDPA.
  Still ~1.4 s per 1 s unit -> not realtime without CUDA graphs.
- `sliding_window_mode="basic"` with a token window (5000 ~= last minute):
  upstream default `"off"` grows the KV cache forever (~75 tokens/s, 0.14
  MB/token) until Windows pages VRAM to RAM (17 s units seen live).
  Note: upstream long-video Memory *requires* `"off"` (own engine, unread).
- VRAM guard every unit: room = device free + torch reserved - allocated;
  under 512 MB -> `decoder.enforce_window()` with a smaller window (floor
  3000 tokens) + `empty_cache`, keep running; stop only if nothing is left
  to drop. Never let it spill.

### Per-second loop
- `streaming_prefill(..., batch_vision_feed=True)` (one LLM pass for the
  image instead of three: llm_feed 0.311 -> 0.245 s).
- Full-resolution screen grab (no thumbnail); `scale_resolution` 448 (672
  and 896 tested worse/slower); `max_slice_nums=1`.
- Frame skipping: worst tile of a 16x9 grid on full-res grey, send when
  similarity < 0.990 or every 5 s (calibrated, `skip_threshold/`).
- Focus crop: 640x320 native-res crop around the changed tiles as a second
  image (small changes only): small-text reads 0/9 -> 8/9 (`readtest.py`).
- Mic: headset first, lavalier backup; per-mic gain (lavalier 8x). Pick
  MME entries and only accept a mic whose stream actually starts (raw
  Bluetooth hands-free endpoints stay listed while disconnected). Judge
  "talking"/"stopped" against the room's own noise floor
  (`speech_level.NoiseFloor`), never a fixed dB. Record what Venus heard
  (`live_mic.wav`) when debugging.
- Interrupting the voice: only deliberate signals (hotkey press, "hey
  FRED"); never mic loudness -- TV/room noise cut every reply. Through
  speakers, ignore wake words while the voice plays (it hears "Fred").
- Hold-to-talk chord needs >= 0.4 s (shortcut taps otherwise force replies).
- Voice: Kokoro (FRED's voice) from Venus's text, **on the GPU**
  (onnxruntime-gpu 1.21.1; set `ONNX_PROVIDER=CUDAExecutionProvider` --
  kokoro-onnx's own GPU detection never fires), **warmed up at start**, and
  chunked by words (first chunk at 3 words, then >= 6 per unit, sentence
  ends always) -- first audio 0.1 s after Venus starts replying. New reply
  drops the old reply's audio; barge-in cuts it (200 ms above -28 dB).
- "Answer me now": FRED's frozen hey_fred.onnx (threshold 0.35, FRED's
  AGC/cooldown) + FRED's left Ctrl + left Alt chord; once addressed and he
  stops talking, block `<|listen|>` at that second's first decode.
- Decision confidence: wrap the decoder's first `decode()` per unit. Use
  `duplex.listen_token_id` (`<|listen|>`); **`decoder.listen_id` is the
  tokenizer's eos, a different token.**
- `listen_prob_scale` multiplies the `<|listen|>` *logit* (not probability).

### Known open issues (not solved yet)
- Turn-taking: answered 10/24 and 13/41 times he spoke; no proactive speech
  (1 unprompted reply in 26, a greeting). Pick `--listen-scale` from logged
  logits (`analyse_live.py`); wake word / hotkey force-reply planned.
- Felt delay ~1.5-2.5 s: 1 s decision units + ~0.65 s compute + Kokoro CPU.
- Delegation to FRED's tools not wired: harness `DelegateBackend`
  (`repo/harness/core/backend.py`); FRED's confirmation gates must stay in
  FRED (backend answers "needs confirmation", confirm = second delegate).
- Screen text: invents confident numbers when it can't read (no "I can't
  read that").
- **Installed (after push, Vatsal's go):** in the Venus venv only --
  openwakeword 0.6.0 (+ melspectrogram/embedding feature models copied
  from FRED's Core venv) and onnxruntime-gpu 1.21.1 replacing the CPU
  onnxruntime (providers: TensorRT, CUDA, CPU).
- **Kokoro on GPU** (`--kokoro-device gpu`, default): 3-4x faster than CPU
  (1.2 s audio 0.37 -> 0.12 s; 5.0 s audio 1.12 -> 0.28 s;
  `test_voice_wake.py`). Gotcha: kokoro-onnx's GPU check looks for a module
  named "onnxruntime-gpu" (impossible, hyphen) so it always picks CPU; set
  `ONNX_PROVIDER=CUDAExecutionProvider`. ort perf notices muted.
- **"Answer me now"** (`Addressed` in live_duplex.py): FRED's frozen
  hey_fred.onnx with FRED's threshold 0.35 / AGC / 2 s cooldown (not
  imported: FRED's listener writes to FRED's real wake-word log), and FRED's
  left Ctrl + left Alt chord. When addressed and he stops talking (chord
  released / a quiet second, mic < -40 dB), `<|listen|>` is blocked at that
  second's decision so Venus answers; cleared when it speaks or after 10 s.
  Wake wiring test: 12/12 as expected on 3 synthetic voices (fires on "Hey
  Fred" 0.97-1.00, not on "Hey Ted", other speech or silence).
  `--no-wake`, `--no-hotkey`, `--selftest-force-at N` (simulated wake).
- **Live self-tests (Vatsal on break, simulated "hey FRED" at unit 20):**
  forced reply works (speak prob was 5%; with `<|listen|>` blocked it
  described the screen). Voice latency from Venus starting a reply to
  first audio: **2.1 s -> 0.1 s** after (a) Kokoro warm-up at start (first
  GPU call 1.09 s cold -> warm-up 0.77 s at launch; later chunks 0.10-0.13
  s) and (b) chunking by words instead of whole sentences (first chunk at 3
  words, then >= 6 words per unit, never splitting a half-written word).
  Still ~2 short gaps mid-reply (Venus writes barely faster than Kokoro
  speaks). VRAM peak 8.9 GB, units median 0.64 s.
- **Idle proactivity data** (47 idle s, nobody present, wallpaper
  animating): unprompted speech would start in 0/47 s at listen-scale 1.0,
  1/47 at 0.9, 6/47 at 0.85, 14/47 at 0.8, 41/47 at 0.7. Needs Vatsal's
  missed-turn data before choosing. `analyse_live.py` now also reports per
  reply: first-audio delay, gaps, forced or not, Kokoro synth times, wake /
  chord / barge-in / trim counts.
- Committed + pushed `9d2773c` (try 1 so far) and `2fff0bd` (Kokoro GPU,
  force-reply, event log).
- Live run 18:07 crashed at start: mic picker matched the Rockerz raw
  Bluetooth hands-free WDM-KS endpoint while the headset was disconnected
  ("Error starting stream ... WDM-KS GLE 0x48F", device not connected).
  `open_mic` now prefers MME entries and accepts a device only once its
  stream has actually started (tested: picks "Headset (Rockerz 512 ANC)";
  falls back to the lavalier). Silenced the ~180-line "weights not used"
  warning (the talker skipped on purpose).
- Live run 18:09 (lavalier 8x, Realtek speakers): room at -26 dB median
  after gain, above the fixed -40 dB "talking" line, so every second
  counted as speech -> 2 "hey FRED" detections (0.996, 0.691) but no
  forced reply (never a quiet second), and barge-in cut Venus's only reply
  (speaker bleed). Fixes: `speech_level.NoiseFloor` (10th percentile of
  the last 30 s; talking = floor + 8 dB, stopped = within 4 dB; self-check
  in `__main__`) used by the force-reply, barge-in (floor + 12 dB per
  100 ms block) and `analyse_live.py`; `--barge auto` = off when the
  output device looks like speakers. Ctrl+Alt now needs a >= 0.4 s hold
  (the 16:01 run's 8 "presses" were sub-second taps on UI shortcuts).
- Re-analysed 18:09 with the adaptive floor: 3 turns, 0 answered.
  listen-scale data: 0.9 -> 0/6 missed-turn s answered, 1/166 idle s
  speaks; 0.85 -> 1/6, 5/166; 0.8 -> 4/6, 32/166. No scale fixes missed
  replies without chatter -> addressed replies should come from the
  wake-word / chord force; 0.9 is a candidate for light proactivity.
  Default stays 1.0 until a cleaner run (3 turns, noisy room).
- **Barge-in on TV / room noise** (Vatsal: "audio output stops on noise
  and bg tv sounds too"): run 21:56 (headset) had 2 replies, both cut by
  energy barge-in at -37 / -31 dB. Loudness can't tell his voice from a TV.
  New default `--barge intent`: only a Ctrl+Alt press (any tap) or "hey
  FRED" interrupts; `--barge energy` keeps the old behaviour (quiet room +
  headphones only), `off`. Through speakers the mic hears Kokoro (Kokoro
  saying "Fred" scored ~1.0 on the wake model), so a wake word heard while
  the voice plays on speakers is ignored for both interrupting and
  addressing (`wake_word_ignored` event). barge_in events log `why`.
- Talk-over interrupt (Vatsal: "what if I speak over it?"): when Venus stops its reply while he is talking (noise-floor "talking" that second), the queued Kokoro audio is cut (`barge_in why=venus_yielded`); a normal ending plays out. Relies on Venus's trained interruption handling (paper: 75% interruption response, 86% continues through background speech). Not yet tested live (needs his voice). Self-test with TV on (intent mode): forced reply played in full, 0 barge-ins, first audio 0.27 s.
- Proactivity test trigger (Vatsal picked "a video starts playing"): `--alert` (default) appends "Whenever a video starts playing on his screen, say so in one short sentence, even if he hasn't spoken." to the system prompt; test = press play on any video; `--alert ""` disables. Logged prompt in run_start shows it.
- **Test plan for live run ~22:08 (as given to Vatsal):**
  1. Proactive video (the alert): stay silent 20 s, then press play on a
     YouTube video and stay silent 15 s. Pass = Venus says a video started,
     unprompted.
  2. Proactive negative: pause the video, just scroll a page silently for
     20 s. Pass = Venus stays quiet (nothing it was told to flag).
  3. Hey FRED: say "Hey FRED, what's on my screen?" then stop. Pass =
     forced reply within ~2 s describing the screen.
  4. Talk over it: hold Ctrl+Alt, ask "describe everything on my screen in
     detail", release; once it's mid-answer, say "stop, that's enough"
     without pressing anything. Pass = voice stops (barge why=venus_yielded).
  5. TV / noise robustness: with the TV or a video playing aloud, hold
     Ctrl+Alt and ask "what time does my clock show?", release, stay quiet.
     Pass = the reply plays to the end, no barge-in.
- **Results of the 5-test run (22:09, Vatsal's verdicts + log):**
  1. Proactive video: works (22:11:24, unforced, p_speak 0.65: "A video of
     a person speaking has started playing") BUT two big mistakes first:
     "A video is playing on your screen" at 22:09:27 (chord-forced) and
     22:09:47 before any video played, and it then repeated every ~30 s.
  2. Proactive negative: correct, stayed quiet.
  3. Hey FRED: fail -- 0 wake-word detections with the TV on; forced
     replies never fired (force waited for a quiet second the TV never
     gave); replies got cut on their own.
  4. Talk over: worked.
  5. TV robustness: wrong -- answers to his questions were "A video clip
     is playing on his screen" (the alert rule parroted, incl. "his").
  Root causes + fixes: (a) the standing alert rule hijacks an 8B duplex
  model's answers -> `--alert-video` opt-in only; (b) "venus_yielded" cut
  3 natural endings because TV = "talking" -> only cut when Venus stops
  mid-sentence (no final .!?); (c) force-reply needed a quiet second ->
  chord release forces immediately, wake word waits for quiet or 2.5 s;
  (d) wake word vs TV speech: model limitation, not fixed.
- **False-positive proactive reply** (run 22:16, Vatsal: "got a false positive"): 22:16:55 "I'll help you check your system" in silence (mic -67 dB = floor), unforced, alert off, p_speak 0.15. Upstream samples the listen/speak decision (temperature 0.7), so low-probability speech fires at random; same mechanism as the 0.26-0.44 false video alarms (the real alert was 0.65). Added `--speak-threshold 0.5` (default): the first decode of a unit returns `<|listen|>` unless Venus's own P(speak) >= 0.5 (mirrors upstream's listen path: `decoder.context += " "`); forced replies and the reply wording unaffected; `thresholded` logged per unit; 0 = upstream sampling.
- Run 22:16 misread: 22:19:56 unprompted (p_speak 0.61, silent) "You have a video chat open with two people on screen" -- it was a terminal (read correctly earlier); 22:20:09 "The call has ended..." (p 0.48, now blocked by the 0.5 threshold). That unit had the overview only (change > 50% of screen -> no focus crop by design), ~600x340 for the whole screen. Known limitation: speak confidence says nothing about vision accuracy; big-change frames are low-res. Not fixed (options: sharper frame after big changes = slower).
- Soak-test prep (Vatsal: run for hours, noise + TV, no direct commands, screensaver on): screen grab failures (locked screen / secure desktop) now skip that frame instead of crashing (`grab_failed` event); live_mic.wav now PCM_16 (float32 was ~230 MB/h); analyse_live prints a health line (VRAM range, min free, max context, unprompted replies per hour, seconds blocked by the speak threshold, grab failures). Baseline from run 22:16 (old code, no threshold): 20.6 unprompted replies/h, VRAM 7.6-8.9 GB, context capped at 4995 tokens.
- **Soak test started** ~22:2x (Vatsal): hours, TV/noise, no direct commands, screensaver allowed; defaults (speak threshold 0.5, intent barge, alert off). Previous run 22:16 stopped (baseline 20.6 unprompted/h without threshold).
  Soak conditions (Vatsal): overnight, mostly quiet room + screensaver, only ~half an hour of noise -> report quiet-hours and noisy-stretch false-alarm rates separately.

### Overnight soak test (2026-09-29)

- **Venus try 1 overnight soak test** (run `20260928_222918`, 22:29 -> 06:34,
  7.78 h, 28,005 units; mostly quiet room + screensaver, ~30 min TV/noise,
  no direct commands; defaults: speak threshold 0.5, intent barge, alert
  off). `analyse_live.py` + ad-hoc breakdowns:
  - **Unprompted replies: 0 in 7.8 h** (was 20.6/h before the 0.5 speak
    threshold). VRAM reserved flat 7.6-8.9 GB, context capped ~5000 tokens,
    0 trims, 0 crashes, 37 lock-screen grab failures skipped cleanly.
  - **Realtime lost from 02:00:** other GPU usage 4.9 -> 6.6 GB and GPU at
    ~96%; llm_feed 0.25 -> ~9 s per unit; the loop kept every second of
    audio and fell up to **43 minutes** behind until morning. Likely one of
    the nightly GPU jobs (DaProDrive_NightlyRecognizeWindow 01:50,
    DaProDrive_PhotoLocationTagger 01:00, FRED_NightlyReindex 03:00) --
    GPU time, not VRAM (4.8 GB still free). Per hour: 22-01h median unit
    0.37 s, lag > 1 s in ~1% of seconds; 02-06h p90 5-9 s, lag in almost
    every second.
  - **Wake word: 19 false triggers** (11 in the TV/noise stretch before
    midnight; 8 after 02:00, mic context unreliable due to lag). Each forced
    a reply to nothing, and the replies were invented ("You have a message
    from your mother", "Vatsal just got a new call from T...", "The meeting
    has started", "shared note titled 'Important Info'"). Some forced
    replies also repeated themselves ("Hello, Vatsal. How can I help you
    today?Hello, V...").
  - Kokoro: 45 chunks, synth median 0.26 s; one 8.8 s outlier during the
    GPU contention.
- **Fixes** (`live_duplex.py`): (1) stale-audio drop -- if the backlog
  exceeds 3 s, discard it and continue from the latest second
  (`lag_drop` event); (2) a wake word only forces a reply once he actually
  speaks after it (noise-floor "talking" within 3 s), else
  `wake_unconfirmed` and it's dropped. The wake model itself is frozen;
  false wakes during TV speech remain -> the held Ctrl+Alt chord is the
  reliable way to address Venus.
- Open: the nightly GPU jobs vs an always-on Venus (priority / scheduling
  is Vatsal's call); forced-reply repetition.

### FRED benchmark suite (2026-09-29)

`VenusOmni` adapter in `benchmarking/run.py`; run from this folder's venv:
`venv\Scripts\python.exe ..\..\..\benchmarking\run.py --candidate venus --stages llm vision unsupported`
(repo-root relative: `Mark_II_Testing/Realtime_Venus/1/venv/Scripts/python.exe benchmarking/run.py ...`).
Conversation 7/21 (FRED 16/21), delegation 5/8 with zero `<delegate>`
spans, screenshots 7/12 (FRED 12/12), frames/audio 3/5; tools and memory
unsupported. Invents facts it can't know (weather, balance, time). Full
table and reading in `VERDICT.md`.

### Weights deleted (2026-09-30, disk space)

Vatsal ran out of disk: `models/Realtime-Venus-Omni/` (weights + assets,
19 GB) and `repo/` deleted — freed 14.7 GB (it was NTFS-compressed).
Kept: `venv/` (candidate 6, MiniCPM-o 4.5, runs on this exact stack),
`models/wheels/` (torch 2.8 cu128 wheel every new venv installs from), all
scripts/logs/results. `sample_1_real.mp4` moved to `../../shared/` (used by
candidates 3 and 5's smoke tests). To bring Venus back: re-run the
`../../fetch_model.py venus` (pinned `ad42af5f`, SHA-checked).
