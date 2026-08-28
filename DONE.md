# Done

Running log of work completed this session, appended as it lands. See
`TODO.md` for what's still pending/deferred.

## 2026-08-28 (single-instance lock, Gmail Primary scoping, email routing, audio device refresh)

- **Fixed FRED not launching at all** (`Core/utils/single_instance.py`,
  `fred_popup.py`). Two related bugs found live diagnosing why the app
  wouldn't start: (1) the singleton lock only checked
  `psutil.pid_exists(pid)`, true even if that PID had been recycled by
  Windows into a totally unrelated process — now verifies the PID's own
  cmdline actually names `fred_popup.py`. (2) `--mock` UI-test mode was
  acquiring the SAME lock a real instance uses, before its early return
  — a forgotten mock window (which never exits on its own) silently
  blocked every real launch indefinitely. Moved the lock acquisition
  after the `--mock` branch. Also hardened `Desktop/FRED.bat` (outside
  the repo) to kill stale `fred_popup.py`/`phone_api.py` processes
  before every launch. Commit `6b2e8c6`.

- **Scoped Gmail IMAP checks to the Primary category only**
  (`Core/tools/gmail_imap.py`). Was reading the whole flat INBOX —
  IMAP has no native concept of Gmail's Primary/Social/Promotions/
  Updates/Forums tabs, that's Gmail-web-UI-only. Uses Gmail's own
  `X-GM-RAW` IMAP extension to send a real `category:primary` query
  instead. Commit `5f977ba`.

- **Made Gmail nudges non-urgent, tightened cadence to 5 min**
  (`Core/orchestrator/proactive_checks.py`, `config/settings.py`).
  Vatsal's own calls: missed-reply/deadline nudges now go through the
  normal naturalness gate instead of bypassing it, and
  `GMAIL_CHECK_MINUTES` dropped from 15 to 5. Commits `3583421`,
  `4486e5c`.

- **Fixed `check_email` having no route to it at all**
  (`Core/orchestrator/intent.py`, `Core/orchestrator/tool_router.py`).
  Confirmed live: "Get me my mail" had zero cue words anywhere and no
  `tool_router.py` embedding example, so it either misrouted to
  `read_messages` (phone/WhatsApp, wrong tool) or hit the LLM
  ACTION/CHAT binary, which answered CHAT and the model fabricated a
  plausible-sounding reply with **no tool call logged at all** — worse
  than the wrong tool. New `email_read` intent category (mirrors
  `messages_read`/`messages_send`'s own read/send separation), cue
  words for mail/email/gmail/inbox, plus a routing example. Verified
  live via CLI: `[intent] tools (cues email_read -> 1 tools)` now
  correctly offers `check_email`. Separately surfaced (not fixed, not
  a regression): FRED has no cloud LLM fallback configured right now
  (`No cloud provider has an API key configured`), so every tool call
  this session ran on the local, not-yet-fine-tuned model alone — the
  exact reliability gap the pending LoRA fine-tune exists to close.
  Commit `2a23396`.

- **Added in-session audio device refresh, fixing a real PortAudio
  caching gap** (`Core/audio/device_info.py`, `Core/input/wakeword.py`).
  `sounddevice`/PortAudio enumerates devices once at process init and
  caches that list — a device plugged in after FRED started never
  showed up until a full restart, and nothing in this codebase worked
  around it. New `refresh_device_list()` (the standard, if private,
  `sd._terminate()`/`_initialize()` re-enumeration trick), wired into
  two safe moments: `list_input_devices()`/`list_output_devices()` (the
  HUD dropdown / "what devices do I have" queries) and `wakeword.py`'s
  existing resume()-failure self-heal retry (the stream's already torn
  down by the time that branch runs — exactly the device-topology-
  change case that retry exists for, it just wasn't refreshing the
  underlying list before re-resolving by name). Commit `e3d18d4`.

## 2026-08-28 (on-demand email tool, long-session bug fix, email tiers — built by a fork)

Vatsal found FRED had defaulted to the WhatsApp reader for "get me my
mail" (no email tool existed at all), flagged `check_long_session` as
"always wrong," and asked for a 3-tier email classification system.

- **Added `check_email` — the on-demand "check my email" tool**
  (`Core/tools/gmail_imap.py`'s `read_recent_primary(count, llm)`,
  registered in `Core/orchestrator/orchestrator.py` via a bound wrapper
  `_check_email`, same pattern as `_find_file_smart`). Fetches the N
  most recent Primary-category emails and summarizes them via the LOCAL
  model only (`local_only=True, force_no_thinking=True`) — same privacy
  bar `session_summary.summarise_today` already holds for unattended
  raw content. Falls back to a bare sender/subject list without an llm
  handle. Commit `2137f39`.

- **Fixed `check_long_session`'s false "3 hours straight" claims**
  (`Core/orchestrator/proactive_checks.py`). Root cause: `last_break`
  persists across restarts on purpose, but that's wrong when the GAP
  itself is a FRED-was-down stretch — confirmed the false firing at
  2026-08-28T14:45 landed mid-way through a stretch of repeated
  FRED crashes/restarts (unrelated single-instance-lock debugging).
  Now tracks `last_poll_at`; a gap >= `PROACTIVE_LONG_SESSION_RESTART_
  GAP_MINUTES` (3x the poll interval) resets the continuity clock
  instead of silently counting downtime as continuous work. Commit
  `b570888`.

- **Added three-tier email classification: useless/basic/vvip**
  (`Core/tools/gmail_imap.py`'s `check_email_tiers()`, wired into
  `proactive_checks.py`). Mirrors `whatsapp_tools.py`'s existing tier
  naming. `useless` (List-Unsubscribe header) never notifies. `vvip`
  (Vatsal emailed them first — pragmatic Sent-Mail proxy, not full
  earliest-message reconstruction) bypasses the naturalness gate,
  same `urgent=True` precedent as VIP WhatsApp/calls. `basic`
  (everything else) goes through the normal gate. Commit `f93729c`.

Full suite: 642 passed (was 628 before this batch).

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
