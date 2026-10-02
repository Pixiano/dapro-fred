# Done

**FROZEN 2026-09-27.** Superseded by Documentation System Mark 2: every
day now has its own folder, `docs/YYYY/MM/DD/`, starting with
`docs/2026/09/27/`. This file is kept as the historical Mark 1 log
(2026-08-28 and earlier); nothing new gets added here. See
`DOCUMENTATION_SYSTEM.md`.

<!-- 2026-09-27's entry moved to docs/2026/09/27/done.md -->
## 2026-08-28

Everything below was originally logged across six separate same-day
sections (the old habit `DOCUMENTATION_SYSTEM.md` replaces) — merged
into one here when Mark 1 was adopted, in the order it actually
happened:

**Proactivity naturalness gate.** `notify()` (`Core/orchestrator/
proactive_checks.py`) now requires presence AND no other audio playing
(composite + suppress-busy), held for `PROACTIVE_INTERRUPT_STREAK`
consecutive 10s polls (calm technology, `security_watch.py`'s
streak-debounce shape), UNLESS a task boundary (foreground window
changed, or media that was playing just stopped) is observed that tick,
which skips the wait. Piggybacked on `check_presence`'s existing poll.
`notify()` gained an `urgent=True` bypass for VIP messages/recent
calls/headphone-switch announcements. 8 new tests
(`test_proactive_naturalness.py`). Commit `66df83f`.

**Review-driven fixes to 5 things built earlier** (built by a delegated
fork, per Vatsal's "set an agent to build them, not you"):
- Capped `focus_checkin`'s backoff at 3h, reset every 12h
  (`FOCUS_CHECKIN_MAX_MINUTES=180`, `FOCUS_CHECKIN_RESET_HOURS=12`).
  Commit `5e47a18`.
- Commitment agenda items nag only every 3rd carryover day instead of
  daily; `update_agenda_item`'s `done` description now covers a casual
  "never mind" dismissal. Commit `45680c8`.
- Capped the YOLO no-face presence fail-safe at
  `PRESENCE_YOLO_FAILSAFE_MAX_POLLS` (30, ~5 min) before normal absence
  handling resumes — was unbounded, could mask Vatsal leaving or
  suppress the stranger loop indefinitely. Commit `7f8161e`.
- Shortened the naturalness-gate wait to ~10s
  (`PROACTIVE_INTERRUPT_STREAK` 3 → 1). Commit `434ee84`.
- New "someone's behind you" awareness alert — instant (bypasses both
  gates) if a second face joins the frame while Vatsal IS present, any
  second face counts, debounced 2 polls, deduped per "episode", "Sir. "
  opener for a deliberate pause. `PROACTIVE_BEHIND_YOU_DEBOUNCE=2`.
  Commit `2c2fca0`.

Full suite: 618 passed (was 604 before this batch).

**Gmail IMAP bridge** (real Gmail API/OAuth blocked ~1 month on
Vatsal's GCP project limit — built as an explicit temporary bridge over
IMAP + a Google App Password; credential handling constraint honored
throughout, the password never passes through Claude):
- `Core/tools/gmail_imap.py` (new) — `check_missed_replies()`/
  `check_email_deadlines()`, both no-op until `GMAIL_ADDRESS`/
  `GMAIL_APP_PASSWORD` (`os.environ.get()`, same pattern as
  `GROQ_API_KEY`) are set. Commit `5904739`.
- Wired into `proactive_checks.py`, same shape as `check_vip_messages`/
  `check_recent_calls`, own `GMAIL_CHECK_MINUTES` cadence. Commit
  `873dde8`.
- `Core/scripts/setup_gmail_credentials.py` + `.bat` (new) — Vatsal
  runs this himself, `getpass.getpass()` for the App Password, `setx`
  only, never printed/logged/written to a file. Commit `f36d41d`.
- 9 new tests (`test_gmail_imap.py`), `imaplib.IMAP4_SSL` fully mocked.
  Full suite: 627 passed. Commit `8aca304`.

**On-demand email tool, long-session bug fix, email tiers** (Vatsal
found FRED defaulting to the WhatsApp reader for "get me my mail," no
email tool existed at all; flagged `check_long_session` as "always
wrong"; asked for a 3-tier classification system):
- Added `check_email` — the on-demand tool (`gmail_imap.
  read_recent_primary(count, llm)`), summarizes via the LOCAL model
  only (`local_only=True`), same privacy bar `session_summary.
  summarise_today` holds. Commit `2137f39`.
- Fixed `check_long_session`'s false "3 hours straight" claims — root
  cause was `last_break` persisting across restarts even through a
  FRED-was-down gap, silently counting downtime as continuous work.
  Now tracks `last_poll_at`; a gap ≥ 3x the poll interval resets the
  continuity clock. Commit `b570888`.
- Added three-tier email classification: `useless` (List-Unsubscribe
  header, never notifies), `vvip` (Vatsal emailed them first,
  bypasses the naturalness gate), `basic` (everything else, normal
  gate). Commit `f93729c`.

Full suite: 642 passed (was 628 before this batch).

**Post-restart log triage** — Vatsal restarted FRED and asked to check
the logs; three genuine bugs surfaced, all pre-existing (confirmed in
production logs back to 2026-08-25, not introduced this session):

- **YOLO weights re-download loop** (`Core/input/presence.py`).
  `YOLO("yolov8n.pt")` resolved relative to cwd, and the real running
  process's cwd isn't `Core/` — every no-face poll tried and failed to
  re-download ("curl return value 23") instead of finding the copy
  from manual testing. Pinned to `Core/models/yolov8n.pt` (same
  convention `voice_id.py`'s `_MODEL_CACHE_DIR` uses), moved the
  existing download there, gitignored. Commit `7f49fe7`.
- **`headphone_watch.py` UnboundLocalError on `frame`** — a
  media-triggered switch never captures a frame (deliberate, no camera
  check needed for a deterministic signal), but the confirm-prompt
  block referenced it unconditionally. Root-cause fix: skip the
  confirm-prompt entirely on that path, since it only makes sense for
  a camera-classifier switch anyway. Commit `7c84fa9`.
- **`media_state.is_media_playing()` COM error** — pycaw is COM-based;
  called from the background scheduler thread, COM was never
  initialized there. Same fix `audio/tts.py`'s own `_speak_internal`
  already uses for the identical error, applied once in the shared
  function every caller (headphone_watch, the naturalness gate) routes
  through. Commit `7ed5361`.

Full suite: 642 passed throughout.

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
