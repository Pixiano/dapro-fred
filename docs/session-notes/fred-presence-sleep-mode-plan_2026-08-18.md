# FRED presence detection, reminder gating, and sleep-mode consolidation

## Context

Ordered an iBall PHOCUS 40A webcam (video calls primary use). Idea chain from
conversation: double-clap gesture trigger → realized that's audio, not
vision → led to "what else could the cam do" → sleep-mode (run vault
consolidation while Vatsal's away from the desk) → also gate proactive
reminders on desk-presence, so FRED doesn't nag an empty room.

Checked before writing this: **no face/CV library exists in this repo at
all** (no opencv/dlib/mediapipe/insightface in requirements.txt,
`vision_tools.py`'s `whats_on_screen` is a screen-capture + multimodal-LLM
call, not a local face model). This is genuinely new infrastructure, not a
rewire of something already built.

**Separate from `dapro-drive-face-recognition-plan.md`** — that plan is
Nextcloud's Recognize app clustering the *photo library* on DaPro Drive, a
different pipeline for a different job (batch photo grouping vs. one live
face-presence check). No code reuse between them. Worth knowing: that plan's
nightly window already **stops FRED entirely 1:50-4:05 AM** to free GPU for
the photo scan — so this plan's deep-night band is largely moot most nights,
FRED isn't running to act on it.

## Decisions already made in conversation

- **Double-clap: dropped.** Not worth the scope — left out entirely, not built.
- **Cancel command semantics**: cancels sleep-mode entirely for the rest of
  the day. Not "force sleep-mode now."
- **Cancel channel resolved**: the HUD text box was fixed 2026-08-03 (memory
  was stale, corrected) — "cancel" routes through it directly, no fallback
  listener needed.
- **Absence threshold varies by time of day as one curve**, not three
  special-cased bands (2pm/5-6pm/2am collapsed into `threshold(hour)`) — but
  see revised numbers below, the first draft was wrong.
- **Sleep-mode exit is camera-driven, not voice-driven**: the camera
  detecting Vatsal's face is what ends sleep-mode and restarts FRED — not a
  spoken command. If tasks/agenda items were left pending, FRED announces
  them by voice on wake, unprompted.
- **Face-recognition library: research alternatives first, install nothing
  until the webcam physically arrives.** See open decision #2.

## Open decisions — need your call

1. **Resolved — threshold approach abandoned, replaced with incremental
   start.** A single "wait N minutes" gate can't win: short enough to
   actually sleep during a real break means false-triggering on quick
   absences, long enough to avoid false-triggers (the first draft's
   mistake — 75 min, *above* the real break ceiling) means it barely ever
   sleeps at all, which defeats the point.

   New method: presence polling every ~15s, and on the FIRST absence
   detected (past a small ~60-90s debounce just to skip a "reach for
   water" blip, not a break-length gate), start consolidating immediately
   — but as small, independently resumable units (one map.md file
   appended at a time, one summary section at a time), not one monolithic
   job. A quick return just stops the current chunk cleanly; nothing was
   ever risked by starting early, so there's no need to wait and guess
   first. No threshold numbers to tune at all.
2. **Face-recognition library — researched, recommendation ready, still no
   install** (waits for the iBall to physically arrive + your sign-off).
   Compared `face_recognition` (dlib), OpenCV `cv2.face` (LBPH), `insightface`
   (ArcFace/buffalo_l via onnxruntime), and `deepface`. Recommendation:
   **`insightface` + `buffalo_l`, CPU-only onnxruntime.** As of its current
   1.0.1 release it's a pure-Python wheel on Windows — no CMake/compiler
   step, the classic `dlib`-on-Windows pain point doesn't apply here. Real
   ArcFace embedding + cosine-similarity verification (distinguishes
   Vatsal from anyone else at the desk, not just "a face exists"), runs
   fine CPU-only at a 30-60s poll rate, actively maintained. `dlib`'s
   wrapper repo is largely dormant and still needs a compile or an
   unofficial prebuilt wheel on Windows; OpenCV's LBPH installs cleanest
   of all but is meaningfully weaker at telling people apart, which is
   the actual failure mode being guarded against here. One license note:
   `buffalo_l`'s pretrained weights are non-commercial-research-use only
   — a non-issue for personal local use, only matters if this ever gets
   shared or open-sourced.
3. **Resolved**: `map.md` is an existing vault file (old notes) that maps
   every other file in the vault — hand-maintained, already works, just has
   no code wired to it yet (zero references in this repo, checked). The
   consolidation job for it is narrow: scan the vault for files not yet
   listed in map.md, append them in whatever format the file already uses.
   Since the vault lives outside this repo, its exact current format can't
   be checked from here — needs a real look at the live file at
   implementation time, not guessed now.
4. **Resolved**: HUD text box was already fixed 2026-08-03 (memory note was
   stale, corrected) — cancel routes straight through it, no fallback
   listener needed.
5. **Dropped**: double-clap isn't being built.

## Architecture

### 1. Presence detection — `Core/input/presence.py` (new)
- One-time enrollment: capture a few reference photos of Vatsal, compute
  and store face embeddings locally.
- Polling, not streaming: grab a single frame via `cv2.VideoCapture` every
  ~30-60s (configurable), match, release the capture immediately — same
  "snapshot, not continuous feed" shape as `whats_on_screen`, and far
  cheaper than live video.
- Exposes `is_present()` / `last_seen()`. No raw frames persisted — a frame
  is matched and discarded; only the enrollment embedding and a
  present/absent timestamp are kept.
- **Privacy**: face embeddings are personal biometric data — same treatment
  this codebase already gives `personal/` vault content (see
  `SENSITIVE_LOCAL_ONLY` / `SENSITIVE_TOOLS` in `orchestrator.py`). Never
  leaves the device, no cloud model in this path, ever.

### 2. Presence-gated reminders — `scheduler.py` / `proactive_checks.py` (modified)
- Before any `notify()` call for a reminder or proactive nudge: check
  `presence.is_present()`.
- Absent → hold, re-check every ~60-90s, until presence returns or a cap
  (e.g. 2 hours) is hit — never silently dropped, matches this codebase's
  existing "missing is fatal, over-inclusive is cheap" stance from
  `intent.py`. At the cap, fire anyway rather than lose it.
- Present → fires immediately, unchanged from today.

### 3. Sleep-mode state machine — `Core/orchestrator/sleep_mode.py` (new)
- Watches `presence`; on absence, starts a timer against
  `threshold(current_hour)` (open decision #1).
- Threshold crossed → enters sleep-mode: pauses pill/wake-word, runs
  consolidation — day summary via existing `session_summary` tooling, plus
  scanning the vault for files missing from `map.md` and appending them
  (open decision #3, resolved: map.md already exists, just isn't wired to
  any code yet).
- **Presence returns → camera-driven wake, not voice-driven:** exits
  sleep-mode, resumes pill/wake-word, restarts FRED's normal turn-taking
  automatically. If any tasks/agenda items are still open, FRED speaks a
  short recap unprompted (reuse `list_tasks`/`list_agenda_items` — same
  data, just spoken proactively instead of waiting to be asked). Any
  in-flight consolidation is short enough to just let finish rather than
  building interrupt/resume logic for it.
- The 2 AM case is just the low end of the same curve, not a separate code
  path — confirms the earlier "one function, not three branches" call.

### 4. Cancel command
- 5 hardcoded phrase variants typed into the (already-working) HUD text
  box, deterministic exact/substring match, no LLM call — same fast-path
  shape `dispatcher.py` already uses for known commands elsewhere in this
  codebase.
- Cancels sleep-mode through end of day; tomorrow is unaffected.

## Files touched (proposed, once open decisions land)
- `Core/input/presence.py` — new
- `Core/orchestrator/sleep_mode.py` — new
- `Core/orchestrator/scheduler.py` — presence gate on reminder firing
- `Core/orchestrator/proactive_checks.py` — presence gate on proactive nudges
- `Core/orchestrator/dispatcher.py` — 5 cancel phrases, fast-path match; HUD input wiring for them
- `requirements.txt` — face-recognition library, pending research (open decision #2), installed only once the webcam arrives

## Verification (proposed)
- Presence: sit in frame → `is_present()` true; leave → false within one
  poll interval; confirm no raw frame ever written to disk.
- Reminder gating: schedule a reminder, leave the desk before it fires,
  confirm it holds and fires on return (or at the cap).
- Sleep-mode: force a short threshold, leave, confirm consolidation runs
  once and pill/wake-word pause; confirm returning cancels the pause.
- Cancel command: trigger sleep-mode, issue each of the 5 phrasings
  separately, confirm each cancels and pill/wake-word resume.
