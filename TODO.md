# TODO

- **Empty-room YOLO person-detection control** (`Core/input/presence.py`,
  `_frame_has_person`/`PRESENCE_YOLO_PERSON_CONFIDENCE`) — skipped 2026-08-27
  during verification because family was in the camera background (a real
  person in frame would correctly trigger detection, not give a clean
  negative). Run once the desk area can be genuinely empty: step fully out
  of frame, capture, confirm YOLO reports no `person` above
  `PRESENCE_YOLO_PERSON_CONFIDENCE` (0.5). Real no-face-with-person sample
  already confirmed (0.925 confidence); this is the missing negative
  control.

- **Voice enrollment** (`Core/scripts/enroll_voice.py`) — needed before
  `Core/input/voice_id.py` can be wired into anything (wake-word voice
  gating, etc.). Skipped 2026-08-27, Vatsal opted not to run the
  8-prompt recording that session. Run by hand: `python
  scripts/enroll_voice.py` (needs a real mic, ~48s of speaking on cue).

- **Mic-level VAD real-audio verification** (`Core/input/voice_activity.py`)
  — built 2026-08-27, tapping `wakeword.py`'s existing stream, but never
  measured against real audio (silence, ambient room noise, actual
  speech) per the source plan doc's verification standard. `webrtcvad.Vad(2)`
  is the stock "moderate" default, unmeasured. Not wired into any
  decision (presence, proactive gating) until this is done.

- **Venus try 1: realtime voice** (`Mark_II_Testing/Realtime_Venus/1/`,
  parked 2026-09-28 by Vatsal as "2 hr fix later"). Spoken units take
  1.43 s per 1 s of audio (prefill 0.38 + LLM ~0.39 + talker 0.39 +
  vocoder 0.21). Fix: CUDA graphs + static KV cache over the talker's
  per-token step (community measured 0.11-0.15 s on the same GPU), then
  the LLM step if still short; prefill (0.38 vs their 0.09-0.17) next.

- **Benchmark follow-ups** — drop Hinglish from scoring, reclassify
  `comp-01`, stub live screen context + vault in the FRED adapter; build
  fixtures for the blocked cases (p-01/p-02 streams, rt-01/rt-02 barge-in)
  so streaming candidates can be scored on them. (`VenusOmni` adapter
  done 2026-09-29.)

