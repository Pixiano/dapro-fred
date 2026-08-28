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

- **Run `Core/scripts/setup_gmail_credentials.bat`** — Gmail's IMAP
  bridge (`Core/tools/gmail_imap.py`, 2026-08-28) no-ops until
  `GMAIL_ADDRESS`/`GMAIL_APP_PASSWORD` are set. Needs a Google App
  Password (Google Account -> Security -> 2-Step Verification -> App
  Passwords). Restart FRED (or open a new Command Prompt) after running
  it — `setx` only affects new processes.

- **Swap the Gmail IMAP bridge for the real Gmail API** once Vatsal's
  GCP project limit clears (~1 month from 2026-08-28 — a deleted
  project's slot doesn't free up immediately). `tools/gmail_imap.py` is
  explicitly temporary; the `proactive_checks.py` wiring and dedup
  logic can likely stay as-is, only the transport changes. See
  `roadmap_pre-finetune_2026-08-26.md` section 3(d) for the OAuth setup
  steps to follow when that swap happens.
