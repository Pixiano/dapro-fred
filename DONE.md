# Done

Running log of work completed this session, appended as it lands. See
`TODO.md` for what's still pending/deferred.

## 2026-08-27

- **Fixed `focus_checkin.py`'s refire cadence bug** (`Core/orchestrator/focus_checkin.py`).
  Root cause: `idle_minutes` was measured from a fixed `last_interaction`
  anchor while the growing `threshold_minutes` never re-anchored, so once
  cleared once it refired every ~15min poll instead of a growing backoff.
  Fix: anchor elapsed time to the last actual fire (`fired_at_iso`).
  Verified with a standalone simulation — gaps now grow (30→45→60→75→90→105 min).
  Commit `4453086`.

- **Generalized `agenda.py`'s carryover loop-closer beyond homework** —
  added a `"commitment"` kind (e.g. "I'll email them back") alongside
  homework/project/event, reusing the existing `carryover_candidates()`/
  `check_agenda_carryover()` plumbing with no new state. Tool-call schema
  and check-in phrasing updated to match. All 81 existing agenda tests
  pass. Commit `4453086`.

- **Added YOLO person-detection fail-safe to presence's no-face case**
  (`Core/input/presence.py`). When zero faces are detected, runs a
  lazy-loaded `yolov8n.pt` person check on the same frame and fails safe
  to the last known presence state instead of declaring absence outright
  — same pattern as the existing ambiguous-vision-fallback-failed path.
  `PRESENCE_YOLO_PERSON_CONFIDENCE = 0.5` (yolov8n stock default).
  Commit `84a8019`.

- **Fixed a real environment bug found during verification**: this venv
  had `torch==2.11.0+cu128` paired with a mismatched `torchvision==0.28.0`
  (needs 0.26.0), crashing every YOLO call with
  `RuntimeError: operator torchvision::nms does not exist`. Reinstalled
  `torchvision==0.26.0+cu128`. Not an sm_120/RTX-50-series limitation —
  cu128 already covers Blackwell. This was very likely also what killed
  the earlier failed headphone-detection YOLO attempt.

- **Verified the YOLO person fail-safe against real camera samples**:
  zero faces + chin-to-chest angle → YOLO person confidence 0.925 (well
  above the 0.5 threshold) — the fail-safe would have correctly held
  "present" instead of flipping to absent. Milder head-down angle still
  got a face match (0 vs 1 faces), confirming the fallback only kicks in
  for the harder case. Empty-room negative control skipped (family in
  background) — tracked in `TODO.md`.

- **Wired real head-pose logging into presence matches**
  (`Core/input/presence.py`). `buffalo_l`'s `1k3d68` landmark model
  already computes `face['pose']` (pitch/yaw/roll) on every detected
  face — now read and logged alongside `presence_match` events for
  diagnosability. Wave-1 scope only: no decision logic built on the
  values yet, per the source plan doc's own verification standard.
  Confirmed real values move sensibly with head angle (facing vs.
  moderate-down samples). Commit `7c6fd13`.

- **Fixed `test_focus_checkin.py`'s `test_threshold_grows_by_step_on_repeated_fires`**
  — it asserted an immediate back-to-back `check()` call fires again on
  unchanged idle time, which was literally the refire bug this session
  fixed, encoded as expected. Rewritten to assert the correct behavior:
  an immediate re-poll stays quiet until the grown threshold has
  actually elapsed since the last fire. Commit `6499d8f`.

- **Added mic-level VAD** (`Core/input/voice_activity.py`), tapping
  `wakeword.py`'s existing continuous capture stream rather than opening
  a second one — `webrtcvad` was already installed, unused. Re-chunks
  the same int16 block `wakeword.py` already builds for openwakeword
  into webrtcvad's fixed 20ms frames, exposes `is_voice_active()` (2s
  rolling window). Wave-1 scope only: not wired into any decision yet
  (presence composite signal, proactive-speech gating) — real
  live-audio verification (silence, ambient noise, actual speech) comes
  first. Voice enrollment (`scripts/enroll_voice.py`, needed for
  `voice_id.py` wiring) deferred — Vatsal opted not to run the
  8-prompt recording this session. Commit `f0541b3`.

- **Added a 3rd system-prompt mode, `PROACTIVE_SYSTEM_PROMPT`**
  (`Core/personality/system_prompt.py`), same base+addendum shape as
  the existing `LOCKDOWN_SYSTEM_PROMPT`. Rewired `focus_checkin.py`'s
  vision call to use it instead of a hand-rolled "You are FRED...
  address him as sir" prompt that had drifted disconnected from the
  vault's persona.md. `vision_server.describe_image()` gained an
  optional `system_prompt` param (it previously only ever sent one
  user message, no system role) — backward compatible, every other
  caller unaffected. 3 modes total: default (baseline), lockdown
  (existing), proactive (new) — didn't invent a 4th/5th for round
  numbers; a grep for other ad hoc "You are FRED" prompts across the
  codebase found only this one real duplication. Commit `3f59bbb`.
