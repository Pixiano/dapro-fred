# Plan: gaze/head-pose, mic-level VAD, object detection, body-pose estimation

## Context

Tonight's investigation started from one problem: presence detection false-negatives when Vatsal looks down to write, eventually mis-triggering sleep-mode/reflection while he's still at his desk. Two motion-based fixes were tried and killed on real measurement (raw frame-diff swamped by sensor noise; background-subtraction broke because this codebase's camera-access pattern reopens `cv2.VideoCapture` fresh every poll, resetting auto-exposure each time and producing noise, not a real empty-vs-occupied signal).

Vatsal then asked for a broader plan covering four techniques he wants built: gaze/head-pose estimation, mic-level/voice-activity detection, object detection, and body-pose estimation (explicitly excluding Bluetooth proximity and keyboard/mouse idle-time).

**Key finding that reshapes this plan**: none of the four need a new Python dependency.
- `insightface`'s `buffalo_l` pack (already loaded by `presence.py`'s analyzer on every poll) includes the `1k3d68.onnx` 3D-landmark model, which — confirmed by reading `venv/Lib/site-packages/insightface/model_zoo/landmark.py:64-72` — already computes `face['pose'] = [pitch, yaw, roll]` for every detected face, unused today. Head-pose is a data field already sitting in memory, not a new capability to build.
- `ultralytics` (YOLO) is already installed from the earlier (unsuccessful) headphone-detection experiment. It covers BOTH object detection (COCO's pretrained classes, including `person`) and body-pose estimation (`yolov8n-pose.pt`, a different pretrained-weights download, same package, same API shape) — no `mediapipe` needed.
- `webrtcvad` is already installed and unused. `input/wakeword.py` already runs a continuously-open `sd.InputStream` at exactly the rate VAD wants (`SR=16000`, `CHUNK=1280` = 80ms @ 16kHz — confirmed `input/wakeword.py:36-37`) — VAD taps the existing stream, no new capture pipeline.

This also changes the recommended fix for tonight's original presence problem: **YOLO `person` detection**, not background subtraction. It answers "is a person-shaped thing in frame" directly, on a single frame — no continuous-camera architecture change needed, works with the existing open-read-release-per-poll pattern presence.py already uses everywhere else.

## Priority and scope

Ranked by how directly each solves a real, already-identified need vs. being a capability for later use:

### 1. YOLO person-detection fallback for presence's no-face case (supersedes tonight's motion-detection attempts)
**File**: `Core/input/presence.py`, `_frame_matches_enrollment`'s `if not faces:` branch (currently returns `False, None, None` unconditionally — this is the exact gap from tonight).
- Add a lazy-loaded YOLO model (`yolov8n.pt`, nano — fast, COCO-pretrained, `person` is one of its best-covered classes, unlike `headphones` which failed earlier). Mirror `_get_analyzer()`'s lazy-singleton pattern.
- When zero faces detected: run YOLO on the same frame, check for a `person` detection. If found (confidence above a threshold to be measured, not assumed — same standard as tonight), fail safe toward `is_present()`'s last known state (reuses the exact pattern already in this function for the ambiguous-vision-fallback-failed case) instead of declaring absence.
- Log a structured event (`presence_no_face_person_failsafe`, confidence) mirroring `presence_ambiguous_fallback_failed`'s existing shape, for diagnosability.
- `PRESENCE_ABSENT_DEBOUNCE` (currently 9, 90s) stays as the backstop for genuine absence — a truly empty frame (no face, no YOLO person) still counts toward it normally.
- **Verification**: real measurement before shipping, same standard as tonight — run YOLO person-detection against a real empty-room sample and a real head-down-writing sample, confirm the confidence/presence split is real before wiring in the fail-safe. Kill it and report back if it doesn't hold, same as the two prior attempts tonight.

### 2. Mic-level / VAD signal
**File**: new `Core/input/voice_activity.py` (mirrors `audio/media_state.py`'s shape: one small function, one focused concern), tapping into `input/wakeword.py`'s existing `_callback`/stream.
- `webrtcvad.Vad(mode)` operates on 16-bit PCM at 16kHz in 10/20/30ms frames. Wakeword's stream is float32 at 16kHz/80ms chunks — needs a dtype conversion (float32 → int16 bytes) and frame re-chunking (80ms → 3×20ms or similar) inside the existing callback, not a new stream.
- Expose a simple `is_voice_active() -> bool` (last-N-frames rolling result, e.g. "voice detected in the last 2s"), same shape as `media_state.is_media_playing()`.
- Use case to wire in first: a presence signal precisely for "someone's talking near the desk" (helps the same no-face gap as #1, from a completely different sense — useful together, e.g. voice-active is strong presence evidence even with zero camera signal). Second use case (not first-wave): gate proactive speech so FRED doesn't talk over an ambient conversation — bigger scope, defer until the presence use is proven.
- **Verification**: real live audio — silence, ambient room noise, and actual speech, measured, before trusting a threshold.

### 3. Head-pose / gaze awareness
**File**: `Core/input/presence.py` (read `face.pose` where already computed) and/or `Core/vision/screen_watcher.py`.
- No new model call — `face['pose']` is already computed whenever `_get_analyzer().get(frame)` returns a face with a 3D landmark result (confirmed above). Just read it.
- Scope for first wave: refine presence's *matched* case, not the no-face case (pose needs a detected face to exist at all, so it's a smaller-angle refinement layered on top of the existing ArcFace flow — e.g. distinguishing "moderate downward glance, still trackable" from "borderline angle about to lose the face" — not a fix for the fully-hidden-face case, which #1 (YOLO) and #2 (VAD) cover instead).
- **Verification**: log real pitch/yaw values across a real range of head positions (facing camera, moderate down-angle, fully down) to confirm the numbers behave as expected before using them for any decision.

### 4. General object detection (YOLO, broader COCO classes)
Flagged as the most speculative of the four — no concrete use case identified yet, unlike 1-3 which each map to a specific, already-discussed need. Building broad "detect everything" object detection without a target use risks the same dead-end the original headphone-detection YOLO attempt hit (0% recall on a class that mattered, because "works in general" doesn't guarantee "works for this specific desk/object/angle").
- Recommendation: hold this one until there's a specific object/use case to point it at (e.g. "detect when my phone is in frame" for a specific proactive check) — the underlying capability (`ultralytics`, already installed) is a few lines away whenever that need shows up, reusing whatever lazy-loaded YOLO singleton #1 establishes.

### 5. Voice-gated wake-word (voice_id)
**Files**: `Core/input/voice_id.py` (built 2026-08-25, standalone, never wired anywhere) + `Core/input/wakeword.py`.
- On a wake-word trigger, run `voice_id.is_match()` against the trigger audio before FRED acts. Rejects TV/visitor/video-call triggers — an independent signal against false positives, doesn't touch the model's recall problem.
- Requires `scripts/enroll_voice.py` to actually be run first — no `data/voice_enrollment.json` exists yet.
- Cheap add-on once wired: feed the same match score into presence's no-face fallback ("person visible OR enrolled voice heard nearby") alongside #1.
- **Verification**: real accept/reject test against your own voice vs. TV/other-speaker audio before trusting the threshold — `MATCH_THRESHOLD=0.5` in voice_id.py was only measured against synthetic Kokoro voices, not a real human pair.

## Additional camera-signal candidates (discussed 2026-08-26, not prioritized/scoped yet)

All reuse fields `buffalo_l`/YOLO already compute or expose — no new model, no new capture path. Not sequenced against 1-5 above; pull one in when it maps to a real need, same standard as #4.

- **Genderage field** — `buffalo_l`'s `genderage.onnx` likely already runs in the same landmark pass as pose (unconfirmed — check `_get_analyzer()`'s output before relying on it). Cheap adult/child gate for `security_watch.py`'s stranger-face case.
- **Multi-face count** — `presence.py`/`enroll_face.py` already detect every face per frame and discard all but the largest. Counting the rest is free and flags "someone else is in the room."
  **Picked up 2026-08-28, Vatsal's own idea — "someone's behind you" awareness alert.**
  Concrete scope, opposite of `security_watch.py`'s existing stranger-
  lockdown check (which only fires when Vatsal is NOT present): while
  Vatsal IS present, if a second face (known family OR unrecognized —
  deliberately not filtered to strangers only, per Vatsal's own call) shows
  up in the same frame, speak up immediately rather than waiting for a
  natural pause — the whole point is catching someone approaching while
  he's focused on his screen and might not notice. Explicitly NOT gated on
  head-pose/gaze direction for this first pass (considered and rejected —
  keep it simple: any second face counts). Bypasses the proactivity
  naturalness gate (this session's other addition) the same way
  `check_camera_obstruction()` already does — urgency here matters more
  than good timing. Phrasing starts with "Sir. " (the period reads as a
  natural pause, Vatsal's own ask, "for that startled feeling to go
  away"), 3-5 phrase variations, picked at random. Deferred explicitly:
  differentiating the phrasing once the second face resolves to a
  RECOGNIZED family member vs. a genuine stranger — ship generic phrasing
  first, split later if it turns out to matter in practice.
- **`det_score` as a soft presence signal** — detection confidence is already computed per frame, used pass/fail only today. A downward trend across polls is an earlier warning than the current binary face/no-face flip — could tighten the debounce instead of just widening it.
- **YOLO pose (`yolov8n-pose`) instead of plain `person`** — sharper than #1's coarse person-detection: can distinguish "head down, hands on desk" (writing) from "not there," which is the actual shape of the false-absence bug, not just a fallback for it.
- **Face bbox size as a rough distance signal** — already computed, unread. Could gate HUD auto-scale or TTS volume by how close you are to the camera.

## Candidate: fine-tune the existing local vision model for the ambiguous-fallback path (2026-08-26, superseded same day — see note)

**Superseded 2026-08-26**: scope narrowed to a single fine-tune, mostly tool-calling, on `unsloth/Qwen3.5-4B-GGUF` — see `MVP Plan (v1.0 - v1.1).txt`'s pre-fine-tune checklist and `roadmap_pre-finetune_2026-08-26.md`. Since `Standard` and `Vision` tiers already share these exact weights, that single LoRA will also apply here; a dedicated vision-training pass isn't planned. Section kept below as background on what a dedicated pass would still need if picked up later.

`presence.py`'s `_vision_fallback_is_match()` (the ambiguous-similarity-band case) already calls a **local** model — `llm/vision_server.py` runs `unsloth/Qwen3.5-4B-GGUF` via llama-server.exe, base/non-tuned, confirmed in `config/settings.py:580`. This is the one place this whole perception layer already touches an LLM-shaped call; nothing here proposes adding a new one, just possibly fine-tuning the one already wired in.

[Unsloth's Qwen3.5 4B Vision fine-tune guide](https://unsloth.ai/docs/models/qwen3.5/fine-tune) / [Colab notebook](https://colab.research.google.com/github/unslothai/notebooks/blob/main/nb/Qwen3_5_(4B)_Vision.ipynb): LoRA on ~10GB VRAM in bf16 (not QLoRA per their own guidance), exports to GGUF — i.e. straight back into the exact format `vision_server.py` already loads.

**What's missing before this is buildable, not just plannable:**
- `unsloth` isn't installed in `Core/venv` (confirmed 2026-08-26) — no training toolchain yet.
- No training data exists — `_vision_fallback_is_match()` logs a text note (`event_log.log("presence_vision_fallback", ...)`) on an ambiguous reply, but never saves the actual frame pair to disk. A fine-tune needs the frames, not just the log line.
- GPU: candidate is an RTX 5060 Ti (16GB, Blackwell, real bf16 tensor cores) vs. an unspecified Tesla card — 5060 Ti clears the ~10GB LoRA requirement either way, decision not yet finalized, card not yet confirmed present in this machine.
- **Not scoped yet** — needs the same real-measurement standard as everything else here (does a fine-tuned local 4B model actually beat the current base model's accuracy) before it replaces anything live.

## Proactivity naturalness principles (research, 2026-08-26)

Triggered by a real bug: `focus_checkin.py` was found refiring roughly every 15 minutes instead of a sane growing cadence — `idle_minutes` was measured from a stale fixed anchor (`last_interaction`) instead of from the last fire, so once triggered it could never outrun the poll interval (`PROACTIVE_CHECK_INTERVAL_MINUTES`=15 > `FOCUS_CHECKIN_STEP_MINUTES`=10). Fix not yet applied. Confirmed via real evidence: webcam-capture timestamps on 3 separate days (08-22, 08-24, 08-26), all showing exact ~15-minute gaps between fires.

Vatsal's actual goal is bigger than fixing that one bug: proactivity that feels like a good human PA's judgment, not a bot on a timer. Six principles came out of dedicated research, each grounded in real HCI work and cross-checked against what FRED already has:

1. **Backoff from last fire, not a fixed clock** — the root cause of the bug above; every real notification-fatigue backoff scheme anchors to the last intervention, never absolute wall-clock.
2. **Interrupt at task boundaries, not mid-task** (Bailey & Iqbal's task-switch-boundary research) — gate on "screen context just changed" (`screen_watcher.py`) or "media just stopped" (`media_state.py`) instead of polling blindly.
3. **Composite interruptibility, not one signal** (Horvitz's Bayesian attention models) — combine `presence.py` + `media_state.py` + `headphone_watch.py` (e.g. suppress if headphones on AND media playing) instead of gating on presence alone.
4. **Calm technology: require persistence before speaking** (Weiser & Seely Brown) — don't fire on the first "quiet" reading; require the same condition across multiple polls, reusing `security_watch.py`'s existing streak-debounce pattern.
5. **Suppress entirely during known-busy states**, like OS focus-modes — cheaper than clever timing, buildable from signals already in FRED.
6. **Phrasing: observation, not notification** — "You've been at it a while, sir" vs. a reminder-style ping. Pure prompt-template change in `focus_checkin.py`, zero new sensors.

All six buildable today from existing modules — no new capability needed. Gap: true mid-sentence/frustration-aware avoidance needs real VAD or sentiment detection; confirmed no in-app VAD module exists (only unused vendored `openwakeword`/`faster_whisper` library code under `venv`).

**Scope note**: this section covers *general* presence/focus/attention-timing proactivity — the "when is it okay to interrupt" layer. It does NOT cover PA-level task proactivity (email, calendar, follow-ups) — see the next section.

## PA-level task proactivity — Gmail-first (research, 2026-08-26)

Vatsal's framing: the naturalness principles above are still "presence/work/focus stuff" — what he actually wants is PA-level *task* proactivity, the content of what a good human PA proactively does, and Gmail is the biggest single addition on this front. No email/calendar integration exists anywhere in this codebase today.

**Grounding**: `proactive_checks.py` already has a slot-in pattern for exactly this — `check_vip_messages`/`check_recent_calls` keep their own watermark/seen-set instead of the shared dedup state, the template a Gmail check would follow. `agenda.py` (subject-keyed, due-dates, carryover) is the closest existing analog to "waiting on a reply from X" state, but nothing today tracks "I said I'd follow up and didn't." `config/settings.py`'s external-service convention is API-key dicts (`CLOUD_PROVIDERS`) — Gmail OAuth would be the first OAuth flow in this codebase. `SENSITIVE_LOCAL_ONLY` (currently off) is the existing norm to respect: email is denser personal data than anything FRED currently touches, so scope/architecture should default toward minimizing what reaches the cloud regardless of the flag's current state.

Prioritized, Gmail-first:

1. **Missed-reply nag** — "You never replied to [person] about [subject], it's been 4 days." Read-only (`gmail.readonly`), thread-state polling: last message FROM someone else, no reply FROM Vatsal within N days. Slots into `proactive_checks.py` exactly like `check_vip_messages`. **Med** effort.
2. **Deadline-in-email-body surfacing** — "That vendor email said the quote expires Friday." Needs a LOCAL extraction pass (keep body text off the cloud, per the sensitivity precedent) feeding the same `deadline:` mechanism `check_deadlines` already reads. **Med-High.**
3. **Noise triage / "3 things actually need you" digest** — only PA-like if it's a real judgment call (signal vs. newsletter), not a raw inbox dump. **Med.**
4. **Draft-for-approval, never auto-send** — `gmail.compose` scope (not `.send`), always requires explicit confirmation — same "opt-in for anything consequential" norm as `SENSITIVE_LOCAL_ONLY`. **High.**
5. **Calendar-aware "you haven't looked at the doc, meeting in 10 min"** — needs a calendar integration (also net-new) plus `screen_watcher.py`'s existing screen-context read. **High**, second new integration, not free-riding on Gmail.
6. **Generalize `agenda.py`'s carryover loop-closer beyond homework** — "I'll email them back" tracked as a real commitment, not just school/project deadlines. Pure local-vault work, **no Gmail needed**, reuses `check_agenda_carryover`'s existing plumbing. **Low-Med** — flagged as possibly the best ROI-per-effort of the whole list, Gmail included.

**Explicitly not PA-like, skip**: auto-summarizing every unread email, cloud-LLM sentiment/priority-scoring on raw bodies, full inbox-zero automation, unsubscribe-bots — tropes, not judgment, and several would violate the local-first spirit `SENSITIVE_LOCAL_ONLY` already establishes.

**Recommended starting scope**: `gmail.readonly` only, features 1 and 2, wired into `proactive_checks.py` the same way `check_vip_messages` already is.

## Sequencing

Build and verify 1 → 2 → 3 → 5 one at a time, each with real live measurement before moving to the next (same discipline as tonight — no threshold shipped without a real number behind it). Hold 4 and the additional camera candidates until a concrete use case picks one. Each item is independently useful and independently testable; none blocks the others.

## Verification (all three)

- Existing test suite must stay green throughout (`Core/venv/Scripts/python.exe -m pytest tests/ -q` — baseline 593 passing).
- Each new signal gets a pure-logic pytest (mocked model/audio, same convention as `test_media_state.py`), plus a real, live, measured verification before the threshold ships — mirroring tonight's own standard, not skipped for speed.
- FRED killed before any camera-exclusive verification session, restarted after — same convention as every capture session tonight.
