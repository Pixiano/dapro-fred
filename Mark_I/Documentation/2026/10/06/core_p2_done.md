# 2026-10-06: Core P2 (L1 interaction clock) - in progress

Plan: `Initial/Core/PLAN.md` P2. Code in `Initial/Core/clock/`. Built so far: Smart Turn, voice ID (code only, nobody enrolled), voice data collection. Still to build: `turn_controller` (the 160 ms clock), the offline harness, echo handling, live gate.

## clock/smart_turn.py
Smart Turn v3.2 int8 ONNX (pipecat, BSD-2, 8 M parameters, CPU): probability that the speaker's turn is complete, from the raw audio (last 8 s, zero-padded at the front), Whisper-style log-mel via faster-whisper's feature extractor (no `transformers`). ~41 ms per call on one thread here. Found while wiring: the output named "logits" is already a probability (a second sigmoid squashed everything to 0.73). Quick sanity on Kokoro-spoken sentences: complete sentence + 0.3 s silence 0.98-0.99; cuts at arbitrary points mostly low-to-mixed (0.02-0.97, a TTS cut often lands on a word end), so the real accuracy gate is the labelled offline harness (next), on clause-boundary cuts and on Vatsal's own speech. Weights live in `Core/weights/` (git-ignored; recipe in Core/README).

## clock/voice_id.py + tests/voice_eval.py
ECAPA-TDNN speaker gate (CPU, ~80 ms), cosine against stored references; `score()` is `None` ("unknown", never "no" and never "yes") when nothing is enrolled or the clip is shorter than `voice_min_s`. References stored outside the repo. **Nobody is enrolled** (Vatsal asked not to train it yet; an enrollment I made from the old clips was deleted).
Evaluation on the new studio set (`Projects/Claude/Project_COPY/Data/Vatsal_v1`: 516 segments of 3-14 s at 24 kHz from 8 long recordings, speech-enhanced; read in place, numbers only):
- Same speaker, different recording (enrolled on 6 sources, tested on 2 held-out sources, 34 segments): similarity p5 0.66, median 0.75, min 0.63. Synthetic other voices: max 0.27. Clean separation.
- Length matters: first 0.6 s median 0.40; 1 s 0.50; 2 s 0.62; 4 s 0.71 (others stay under 0.27). At threshold 0.30: 0% reject at 1 s and longer, 8.8% at 0.6 s.
- **Domain gap**: the same enrollment against Vatsal's real live-mic "Hey FRED" 1-s clips scores median 0.21 (p95 0.35): the enhanced studio audio does not transfer to the live mic path. Enrolling from this set alone would reject him live (90% at 0.30). Enrollment therefore has to include live-mic speech, which is why utterance collection is now on.
- Settings now: `voice_threshold` 0.30 (provisional), `voice_min_s` 1.0, `voice_refs_cap` 20. Retune once live-mic utterances are collected; real strangers will sit closer to him than the synthetic voices used as imposters, so the false-accept side is a floor, not a measurement.

## Voice data collection (Vatsal's idea)
`stt_save_audio` (default on): every final, non-silent utterance Whisper transcribes is saved as `<FRED_DATA>/voice_data/YYYY-MM-DD/HHMMSS_mmm.wav` (16 kHz mono int16, exactly what Whisper heard) plus a line in `voice_data/index.jsonl` {ts, file, text, audio_s, ms, peak}. Outside the repo (personal data), a failed save never breaks a turn, partials and silence are not saved, tests use a temp data dir or switch it off (the live STT test does). Use: voice ID enrollment and retuning on the real mic, wake-word and STT evaluation sets. It is unlabelled (could include a TV or a visitor): label by voice score / review before using as training data.
