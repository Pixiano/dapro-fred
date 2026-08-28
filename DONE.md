# Done

Running log of work completed this session, appended as it lands. See
`TODO.md` for what's still pending/deferred.

## 2026-08-28 (Gmail IMAP bridge, built by a fork per Vatsal's own instruction)

Real Gmail API (OAuth) blocked ~1 month on Vatsal's GCP project limit —
built the two decided Gmail features (`roadmap_pre-finetune_2026-08-26.md`
section 3(d)) over IMAP + a Google App Password instead, as an explicit
temporary bridge. Credential handling constraint honored throughout: the
password never passes through Claude at any point (see below).

- **`Core/tools/gmail_imap.py`** (new) — `check_missed_replies()` flags
  an inbox email with no matching `[Gmail]/Sent Mail` reply after
  `GMAIL_MISSED_REPLY_DAYS` (3); `check_email_deadlines()` scans recent
  email bodies for date-like phrases (local regex, no cloud LLM call on
  raw bodies). Both dedup via their own Message-ID seen-sets, both
  no-op (return `""`) until credentials are set. `GMAIL_ADDRESS`/
  `GMAIL_APP_PASSWORD` in `config/settings.py` via `os.environ.get()`,
  same pattern as the existing `GROQ_API_KEY`. Commits `5904739`.
- **Wired into `Core/orchestrator/proactive_checks.py`** — two new
  checks (`check_gmail_missed_replies`/`check_gmail_deadlines`), same
  shape as the existing `check_vip_messages`/`check_recent_calls`,
  `urgent=True` (bypasses the naturalness gate, same reasoning as VIP
  messages/calls), own `GMAIL_CHECK_MINUTES` (15) cadence — slower than
  the adb checks since an IMAP round trip is heavier. Commit `873dde8`.
- **`Core/scripts/setup_gmail_credentials.py` + `.bat`** (new) — Vatsal
  runs this himself: `input()` for the address (not sensitive),
  `getpass.getpass()` for the App Password (never echoed), both
  persisted via `setx` as real Windows user env vars — never printed,
  logged, or written to any file Claude could read. Commit `f36d41d`.
- **9 new tests** (`test_gmail_imap.py`) — `imaplib.IMAP4_SSL` fully
  mocked, no real network; covers missed-reply detection with/without a
  matching sent reply, deadline-phrase detection, dedup-across-calls for
  both, no-op without credentials, and that connection/auth failures
  never raise. Full suite: 627 passed (was 618 before this batch).
  Commit `8aca304`.
- **Docs**: `TODO.md` now has the activation step (run the `.bat`,
  restart FRED) and the future real-API swap-back note; the roadmap
  doc's section 3(d) got a status update plus the OAuth setup steps
  written down for whenever the GCP limit clears, since they'd
  otherwise only have existed in chat.

## 2026-08-28 (review-driven fixes, built by a fork per Vatsal's own instruction)

Vatsal reviewed the 5 things built earlier today (focus_checkin backoff,
commitment agenda kind, YOLO presence fail-safe, naturalness gate, system
prompt modes) and gave specific corrections. All 5 built by a delegated
agent, not the parent session, per his explicit "set an agent to build
them, not you."

- **Capped focus_checkin's backoff at 3h, reset every 12h**
  (`Core/orchestrator/focus_checkin.py`). Unbounded growth basically
  stopped nagging by hour 3+; a 12h periodic reset (new `cycle_started_iso`
  state field) stops an all-day quiet stretch from sitting at the cap
  forever. `FOCUS_CHECKIN_MAX_MINUTES=180`, `FOCUS_CHECKIN_RESET_HOURS=12`.
  Commit `5e47a18`.
- **Commitment agenda items nag less, dismiss easier**
  (`Core/orchestrator/proactive_checks.py`, `Core/orchestrator/orchestrator.py`).
  `check_agenda_carryover` now re-asks about a commitment only every 3rd
  day of carryover (homework/project unchanged, still daily);
  `update_agenda_item`'s `done` description now explicitly covers a
  casual "never mind" dismissal instead of implying only "fully done".
  Commit `45680c8`.
- **Capped the YOLO no-face presence fail-safe** (`Core/input/presence.py`).
  Was unbounded — could mask Vatsal actually leaving or suppress
  security_watch's stranger loop indefinitely if YOLO kept seeing any
  person-shaped blob. New `_yolo_failsafe_streak` counter, reset by any
  real face match, capped at `PRESENCE_YOLO_FAILSAFE_MAX_POLLS` (30,
  ~5 minutes) before normal absence handling resumes. Commit `7f8161e`.
- **Shortened the naturalness-gate wait to ~10s**
  (`Core/config/settings.py`). `PROACTIVE_INTERRUPT_STREAK` 3 → 1 — the
  30s wait felt too slow in practice. Commit `434ee84`.
- **New "someone's behind you" awareness alert**
  (`Core/orchestrator/proactive_checks.py`). The opposite of
  security_watch's stranger-lockdown check: alerts instantly (bypasses
  both the naturalness gate and sleep-mode gate) if a second face joins
  the frame while Vatsal IS present — any second face counts, not just
  unrecognized ones, no gaze-direction reasoning (Vatsal's explicit
  simpler-is-better call). Debounced 2 polls (~20s), deduped once per
  "episode". Phrasing starts with a literal "Sir. " for a deliberate
  pause. Recognized-vs-stranger phrasing split explicitly deferred.
  `PROACTIVE_BEHIND_YOU_DEBOUNCE=2`. Commit `2c2fca0`.

Full suite: 618 passed (was 604 before this batch).

## 2026-08-28

- **Added the proactivity naturalness gate** (`Core/orchestrator/proactive_checks.py`)
  — principles 2-5 from the perception-features plan doc. `notify()` now
  requires: presence AND no other audio playing (composite + suppress-
  busy), held for `PROACTIVE_INTERRUPT_STREAK` (3) consecutive 10s
  polls (calm technology, security_watch.py's streak-debounce shape),
  UNLESS a task boundary (foreground window changed, or media that was
  playing just stopped) is observed that tick, which skips the wait.
  Piggybacked on `check_presence`'s existing poll, no new scheduled
  job. `notify()` gained an `urgent=True` bypass for VIP
  messages/recent calls/headphone-switch announcements — timing-
  sensitive or functional-status nudges that shouldn't wait for a
  "good moment." Principle 6 (observation phrasing) checked, already
  satisfied by existing wording, no changes made. 8 new tests
  (`test_proactive_naturalness.py`). Commit `66df83f`.

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
