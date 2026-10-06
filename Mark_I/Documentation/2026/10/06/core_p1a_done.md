# 2026-10-06: Core P1a (audio senses) built

Plan: `Initial/Core/PLAN.md` P1 1a. Code in `Initial/Core/audio/`; every file has its sockets in the docstring and a self-check (`Core\.venv\Scripts\python tests/run_all.py`: 15/15). Screen (1b) and camera (1c) are next.

## Modules
| File | Behaviour |
|---|---|
| `audio/mic.py` | the single owner of the microphone: one stream, 16 kHz mono int16, 80 ms chunks, fan-out to subscribers (broken subscriber isolated and counted), 6 s raw ring for pre-roll, `feed_array()` replays recordings through the identical path, `pause()/resume()` release/reopen the device, device by name substring (`mic_device`), one refresh-and-retry on a failed open |
| `audio/wakeword.py` | ported AGC (instant down, slow up, hold on near-silence) + the FROZEN `hey_fred.onnx` (read in place from `Old/Core/models/wakeword/`, never copied or swapped) + threshold + cooldown; emits `audio.wake` and `audio.wake_near` (tuning data); model failures are one event per 60 s, never a crash |
| `audio/vad.py` | webrtcvad per 20 ms frame -> `audio.vad {speaking}` start/end with a leaky onset counter and a short hangover (200 ms; L1 adds Smart Turn); frames carried across odd chunk sizes; model socket for Silero |
| `audio/stt.py` | faster-whisper large-v3-turbo through `gpu.run`, CUDA only (no CPU fallback: a failure raises `SttError` + `stt.error`), silence short-circuits without a GPU pass, `Capture` collects mic audio with pre-roll and a hard cap; "windowed" = `snapshot()` + `transcribe(final=False)` on the clock's cadence (no thread until L1 needs one) |
| `audio/hotkey.py` | Left Ctrl + Left Alt hold-to-talk via a low-level hook, own message-pump thread, observe-only, `audio.hotkey {down}` |
| `audio/wav.py` | any wav -> mono int16 16 kHz |
All thresholds are `config.Settings` fields (agc_*, wake*, vad_*, whisper_*, stt_min_peak, max_utterance_s), none literal.

## Gate results (2026-10-06)
**Wake word + VAD on labelled recordings** (`tests/replay_audio.py`, sets read in place from `Old/Core/data/wakeword_training`, real frozen model, threshold 0.35, cooldown 2 s, baseline as-is, no tuning):
| set | clips | result |
|---|---|---|
| real_positive (Vatsal's recordings) | 131 | wake fired 78.6%, median peak score 0.87; VAD saw speech 99.2% |
| synthetic positive_test (TTS) | 24 | fired 54.2%; VAD 100% |
| real_negative (400 of 2864; these are clips saved around real fires/cancels, i.e. hard negatives) | 400 | no fire 62.5% (37.5% false fires) |
| synthetic negative_test (400 of 848, includes look-alike words) | 400 | no fire 86.5% |
Reading: this is the frozen model's behaviour (the model is not to be swapped), reproduced faithfully through the new path. The old threshold history (0.6 -> 0.25 -> 0.35) already traded these two errors; real tuning waits for P8 and the deferred real-voice recording plan. A harness bug found on the way: the first run showed 0% recall because the new venv lacked openWakeWord's feature models, and the detector reported only an event; the harness now aborts on `wake_error`.
**STT** (`tests/live_stt.py`, Kokoro-synthesised invented sentences, real Whisper on the GPU): model load 4.2 s, +2.2 GB VRAM; decode about 190-200 ms for 2-4 s of audio; WER 0% on 3 of 4 sentences and 14% on the fourth, which is only "thirty percent" vs "30%" (numerals); silent buffer short-circuits; Capture -> transcript works; unload frees VRAM (+125 MB left).
**Mic**: live check, 24 chunks in 2 s from the default device.

## Setup note (documented in Core/README)
openWakeWord needs its `melspectrogram.onnx` and `embedding_model.onnx` in the venv (and ships `silero_vad.onnx`, the Silero VAD for the L1 socket). They are not in the pip wheel: either `python -c "import openwakeword.utils as u; u.download_models()"` (network) or copy the three .onnx files from `Old/Core/venv/Lib/site-packages/openwakeword/resources/models/` (done here).

## Decisions taken (change on request)
- Wake model path points at the old folder, read-only, so there is exactly one copy of the frozen model.
- No partial-transcript thread: `Stt.transcribe(final=False)` is the primitive; L1 sets the cadence.
- VAD hangover 200 ms because the clock's end-of-turn rule is silence >= 200 ms AND Smart Turn >= 0.5.
- Dropped from the old listener: its jsonl score log (replaced by `audio.wake_near` events plus the EventLog), the "pause the stream to open another" dance (one Mic owner), and the phone-contact vocabulary in the Whisper prompt (contacts arrive with the tools phase).

## Not done in P1a
Gate item "STT transcript events on a recorded set" is met with synthesised speech only; a recorded-voice set comes with the real-voice plan. Smart Turn and voice ID are P2.
