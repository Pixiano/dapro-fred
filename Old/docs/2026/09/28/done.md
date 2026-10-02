# 2026-09-28 — work log

- **Venus try 1 import (overnight, over the phone hotspot):**
  environment finished -- torch 2.8.0+cu128 sees the 5060 Ti with SM120
  kernels; transformers 4.51.3, bitsandbytes 0.50.2, minicpmo-utils 1.0.6
  and the rest all import. Model checkpoint only ~2 of 19.4 GB: the
  download loop was stopped by Claude Code while system RAM was low and
  deliberately not restarted. Partial shards resume on rerun. Details and
  the resume command: `Mark_II_Testing/Realtime_Venus/1/README.md`.
- **Venus try 1 import complete.** Checkpoint downloaded over Wi-Fi;
  all 11 LFS files (4 shards, token2wav, voice, sample videos) match the
  Hub's SHA-256 (`Mark_II_Testing/Realtime_Venus/1/verify_sha.py`).
  Overnight partials were reused after renaming them (hf-xet in
  huggingface_hub 0.36 restarted from zero and the new version renamed
  `.incomplete` files); downloaded with `HF_HUB_DISABLE_XET=1`.
- FRED stopped (fred_popup + phone_api) under Vatsal's new rule: kill
  FRED processes when memory runs out.
- **Waiting for Vatsal's go** before any GPU work (bnb test, NF4 load).
- **bitsandbytes NF4 GPU test: PASS** (`test_bnb.py`). bnb 0.50.2 on torch
  2.8+cu128, RTX 5060 Ti SM120, Windows: 4096->12288 NF4 layer, cosine
  0.996 vs BF16, 0.075 ms/call at 1 token, 0.31 ms at 64, all finite.
  The Windows/SM120 risk (issue #1937, torch 2.11/CUDA 13) doesn't apply
  to our cu128 stack. VRAM at test time: 5.9 GB used by other apps.
- **Venus NF4 smoke test: PASS, realtime (text).** `smoke_nf4.py`: official
  Omni, NF4 on the 252 LLM linears (OpenBMB skip list), everything else
  BF16, loaded in 21 s, 7.94 GB, all weights on GPU. 70 s of the sample
  video through the duplex loop, speech off: listening units 0.55 s mean,
  speaking units 0.79 s mean / 0.93 s max, 69/70 under 1 s (the miss was
  the 1.00 s warm-up). Peak 8.96 GB. Stayed silent until asked, answered
  both questions correctly (incl. "a player scores ... 1-0" at 60 s), then
  went quiet on its own. Two fixes on the way: `as_duplex()` loads
  Token2wav (+3.2 GB, left 0 GB free) even with speech off -> skipped;
  upstream needs `ffprobe` (not installed) for video length -> OpenCV.
- **Venus voice output (try 1).** Works end to end (samples
  `smoke_speech_fp32_10steps.wav`, `smoke_speech.wav`), not realtime yet.
  Per spoken 1 s unit, best config (FP16 vocoder, 8 steps, SDPA talker):
  prefill 0.38 + LLM ~0.39 + talker 0.39 + vocoder 0.21 = 1.43 s (was
  1.54 s at upstream defaults). Venus VRAM with voice 10.5 GB (was 12.1).
  Fixes: Token2wav's flow cache preallocated for 16 steps (1.95 GB fp32)
  trimmed to the steps used; upstream FP16-vocoder bug in
  `set_stream_cache` (Float/Half) wrapped in autocast; talker switched
  from forced eager attention to SDPA (0.46 -> 0.39 s). Remaining fix
  parked by Vatsal ("2 hr fix later"): CUDA graphs + static cache for the
  talker (community: 0.11-0.15 s) and possibly the LLM step.
- `live_duplex.py` (mic + screen/camera, voice to the Windows default
  output, per-second GPU/VRAM line + console title, placeholder FRED
  prompt, lavalier mic default). Vatsal's first live run (text only,
  webcam mic): very good; webcam mic too far -> lavalier.
- Other apps hold ~3.8-4.2 GB VRAM on the card (NVIDIA Overlay respawns
  when killed; Instant Replay off; Resolve closed); free VRAM swings 0 <->
  4.2 GB from outside Venus.
- **Venus live run 2 (voice on) failed on VRAM growth.** Upstream ships
  duplex with `sliding_window_mode="off"`: ~75 tokens/s into the KV cache
  forever; Venus grew 10.2 -> 11.9 GB in ~1 min, Windows paged to RAM, one
  unit took 17 s (lag 31.8 s). Vatsal also said it didn't answer most
  spoken questions. Fixes in `live_duplex.py`/`smoke_nf4.py`: basic
  sliding window (default 5000 tokens ~= last minute); on <512 MB free,
  drop oldest context + shrink the window permanently and keep running
  (Vatsal: "keep running after clearing" instead of stopping), stop only
  if nothing is left to drop; per-second mic dB in the status line;
  `live_mic.wav` records exactly what Venus heard; `--gain`.
- **Venus live run 3:** greeted as "Fred" (placeholder prompt works), then
  mostly silent. `live_mic.wav` analysis: loudest second -38 dB (peak 0.1
  FS) over a -50 dB room -- Venus barely heard him. Default `--gain 8`
  (+18 dB). Also the VRAM trim fired too early (counted only device-free,
  not torch's reusable cached blocks) and shrank context to 1000 tokens;
  now counts both.
- Context trimming: floor raised to 3000 tokens (~40 s); `--frame-every N` added (frames are 64 of ~75 tokens/s; N=2 roughly doubles the memory span). Vatsal: "the context is dropping SOO much".
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
- Live run 15:43 (after the prompt change): answered 13/41 times Vatsal
  spoke -- still mostly silent; no logit data yet.
- Proactivity: there is no proactive switch in upstream (only the
  harness's `feedback.proactive_progress` = spoken progress of delegated
  tasks). Unprompted speech is purely the model's per-second decision;
  lever = `listen_prob_scale`. Added `--listen-scale` (default 1.0) plus
  per-second raw `<|listen|>` logit / best-other logit logging;
  `analyse_live.py` now computes, from data, which scale would have
  answered missed turns vs how often idle seconds would speak.
- Wake word + Kokoro-on-GPU install (openwakeword, onnxruntime-gpu) was
  rejected by Vatsal before running; nothing installed.
- **Verbose diagnostics** (Vatsal): `live_events.jsonl` -- run_start (all
  args, devices, VRAM, prompt), one `unit` record per second (decision,
  text, p_speak, listen logit, top-5 tokens, mic dB/peak/clipping, frame
  skip/crop box/changed tiles, prefill split, unit time, lag, context
  tokens, VRAM alloc/reserved/other/free, GPU util, voice queues),
  voice_queued / voice_synth (synth time, RTF, queue wait) /
  voice_audio_start (latency from reply start to first audio) /
  voice_dropped_stale, barge_in, context_trim, run_end. Prefill profiling
  always on.
- **FRED integration checklist** added to the try-1 README: every patch,
  gotcha and setting FRED's backend must carry over (Vatsal: "keep
  documenting so that when we import this as the backend for FRED it
  works properly").
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
