# 05 — Presence Detection & Sleep Mode

Newest subsystem in FRED, built 2026-08-21 (same day as this doc). This
covers `Core/input/presence.py`, `Core/orchestrator/sleep_mode.py`,
`Core/tools/sleep_mode_tools.py`, `Core/scripts/enroll_face.py`, and the
presence hook inside `Core/orchestrator/proactive_checks.py`. It was built
against the design doc `fred-presence-sleep-mode-plan_2026-08-18.md`
(repo root) — that doc is broader than what actually got built; see
"Plan vs. built" at the end of this file for the exact delta.

## 0. Why this exists

Origin chain (from the plan doc): a webcam (iBall PHOCUS 40A) was ordered
for video calls. Idea chain: double-clap gesture trigger → realized that's
audio not vision → "what else could the cam do" → sleep-mode (run vault
consolidation while Vatsal is away from the desk) → also gate proactive
reminders on desk-presence so FRED doesn't nag an empty room.

Before this build, **no face/CV library existed in the repo at all** — no
opencv/dlib/mediapipe/insightface in requirements.txt, and
`tools/vision_tools.py`'s `look_through_camera`/`whats_on_screen` are
screen-capture + multimodal-LLM calls, not a local face model. This is
genuinely new infrastructure.

Separate concern, explicitly NOT this pipeline: `dapro-drive-face-recognition-plan.md`
covers Nextcloud's Recognize app clustering the *photo library* on DaPro
Drive (batch photo grouping). No code reuse between the two — different
job, different pipeline.

## 1. Hardware: which camera index, and how it was determined

`PRESENCE_CAMERA_INDEX = 1` (`Core/config/settings.py`, "PRESENCE DETECTION"
block, ~line 1142).

The desktop has **no built-in webcam**. Three camera indices are visible to
OpenCV on this machine:

- index 0 — Canon EOS Webcam Utility (virtual, shows an idle placeholder
  frame when queried)
- index 1 — **iBall PHOCUS 40A**, the only real hardware camera
- index 2 — OBS Virtual Camera (virtual)

This was confirmed live on 2026-08-21 by actually capturing a frame from
every index and looking at the result — index 1 produced a real captured
photo, the other two produced virtual/placeholder output. Settings.py's own
comment calls this "a solid identification, not a guess," specifically
because the desktop has no built-in camera to confuse with the real one —
but it also flags that if the camera setup ever changes (a new virtual-cam
app installed, the iBall unplugged and replaced with something else), the
same per-index capture-and-inspect check needs to be rerun rather than
assuming index 1 still holds. There is no camera-name-based lookup — it's
a bare integer index into `cv2.VideoCapture`.

## 2. Face-recognition pipeline

### Library choice

`insightface` + `buffalo_l` model, CPU-only via `onnxruntime`
(`providers=["CPUExecutionProvider"]`). Chosen (per the plan doc) over
`face_recognition`/dlib (Windows compile pain, dormant wrapper repo),
OpenCV LBPH (installs cleanest but meaningfully weaker at telling people
apart — the actual failure mode being guarded against), and `deepface`.
`insightface` 1.0.1+ is a pure-Python wheel on Windows, no CMake/compiler
step, gives real ArcFace embeddings + cosine similarity (distinguishes
Vatsal from anyone else, not just "a face exists"), and runs fine CPU-only
at a 15s poll rate. One license note carried from the plan: `buffalo_l`'s
pretrained weights are non-commercial-research-use only — irrelevant for
personal local use, would matter only if this were ever shared/open-sourced.

### Loading pattern

`presence.py._get_analyzer()` lazy-loads and caches a module-level
`FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])`,
`.prepare(ctx_id=0)`'d once, same "load-once-keep-warm" pattern as
`llm_client._get_model`. `enroll_face.py` constructs its own separate
`FaceAnalysis` instance directly in `main()` (it's a one-shot CLI script,
not a long-lived process, so no caching layer needed there).

### Matching: two-threshold band + vision-model fallback for the middle

Constants (`Core/config/settings.py`, "PRESENCE DETECTION" block):

```
PRESENCE_MATCH_THRESHOLD_LOW  = 0.30
PRESENCE_MATCH_THRESHOLD_HIGH = 0.45
```

Cosine similarity (`presence._cosine_similarity`, plain
`np.dot(a,b)/(||a||·||b||)`) between a detected face's `normed_embedding`
and every stored enrollment embedding; `_best_similarity` takes the max
across all stored embeddings for that one detected face.

Per detected face in `_frame_matches_enrollment(frame)`:
- similarity ≥ `PRESENCE_MATCH_THRESHOLD_HIGH` (0.45) → confident match.
  Accumulate this embedding (see §3) and report present immediately.
- similarity < `PRESENCE_MATCH_THRESHOLD_LOW` (0.30) → confident
  non-match for this face; move on and check the next detected face (if
  any) rather than immediately reporting absent — a frame can have
  multiple faces.
- otherwise (the 0.30–0.45 band) → **ambiguous**, falls back to a real
  vision-model comparison, `_vision_fallback_is_match(frame)`.

**Runtime multi-face handling is deliberately asymmetric with enrollment.**
At runtime, ANY detected face matching is enough to report present — family
visiting (multiple people in frame) must never cause a false absent. At
enrollment time (`enroll_face.py`), the picker instead always takes the
single largest face by bounding-box area, since enrollment assumes one
specific person is the subject.

If every face in frame resolves to non-match/unresolved-ambiguous, the poll
reports absent — except: when the vision fallback itself can't produce a
clear signal, the code fails safe toward the *last known persisted state*
(`is_present()` read before this poll's mutation) rather than flipping
unpredictably — logged as `presence_ambiguous_fallback_failed`.

**Threshold values are explicitly NOT calibrated.** Settings.py's own
comment: "NOT a measured constant — this repo had never run the model as
of 2026-08-21, so these are starting guesses (typical same-person ArcFace
similarity clusters 0.35-0.45 in the wild) to verify/retune against real
enrollment + real live frames, not settled numbers." **Flag this
prominently for any rebuild: these two numbers need real-world validation
against actual enrolled-vs-live-frame similarity scores before being
trusted.**

### Vision-model fallback mechanics (`_vision_fallback_is_match`)

Not a call to `vision_tools.describe_image()` (that only accepts one
image). Instead `presence.py` POSTs directly to FRED's own local
`llm/vision_server.py` HTTP endpoint (`vision_server._BASE_URL +
"/v1/chat/completions"`), constructing a request with **two** `image_url`
content parts in one user message: the persisted `face_reference.jpg` and
the current just-captured frame (both re-encoded to JPEG via
`cv2.imencode` and base64 data-URIs). Prompt asks the model to answer
"YES or NO" on whether the same person appears in both images.

Two request details called out in the code as load-bearing, confirmed
working the same night (2026-08-21) elsewhere in the codebase:
- `vision_server.ensure_running()` must be called first (starts
  `llama-server.exe` if not already up) — if it returns falsy, the
  fallback returns `None` (unresolved) rather than blocking.
- `chat_template_kwargs: {"enable_thinking": False}` **must** be set
  explicitly, or the model burns its whole token budget on a `<think>`
  block and returns an empty answer — same failure mode documented in
  `vision_server.describe_image`'s own docstring.

Response parsing: lowercased reply checked for `"yes"`/`"no"` substrings.
Both present, or neither → ambiguous, returns `None` (event-logged as
`presence_ambiguous_fallback_failed` note `"ambiguous reply"`). Exactly one
present → that's the verdict (`True`/`False`). Any network/JSON/key error
is caught and logged via `event_log.log_error`, also returns `None`.

**This fallback stays entirely on-device.** `vision_server.py` runs FRED's
own local `llama-server.exe`; the plan doc's privacy stance — face data is
treated with the same on-device-only discipline as `personal/` vault
content (`SENSITIVE_LOCAL_ONLY`/`SENSITIVE_TOOLS` in
`orchestrator/orchestrator.py`) — is upheld: no cloud model is ever in this
path. Before being wired into vision_server.py, the same YES/NO comparison
approach was proven live 2026-08-21 with Bonsai-27B through LM Studio
(correctly identified the same person across a hard side-angle shot, high
confidence) — the code now calls this repo's own already-working
vision_server.py pipeline instead of depending on LM Studio at runtime.

### No raw-frame persistence, with one narrow exception

`presence.py`'s module docstring states the rule directly: "No raw frames
are ever persisted by this module — a polled frame is matched and
discarded." The **one** deliberate exception is `enroll_face.py`, which
keeps exactly one reference photo on disk (`face_reference.jpg`) — needed
because the vision-fallback comparison requires an actual image to compare
against, not just a numeric embedding. This exception is explicitly called
out as narrow and script-specific, not a pattern presence.py itself
follows.

## 3. Enrollment flow (`Core/scripts/enroll_face.py`)

One-time, run-by-hand setup script — not wired into FRED's voice/turn flow,
same "run manually" convention as `tools/haismart_setup.py`. Not CI-safe
(needs a real camera and a real face in front of it); no automated test,
by design — "the only honest check is running it by hand and looking at
what it printed and saved."

### Two entry paths

1. **Live capture** (default, no `--seed` flag): fully automatic per
   Vatsal's 2026-08-21 call — no per-shot keypress, just a countdown.
   `PRESENCE_ENROLLMENT_SHOTS = 5` shots, `PRESENCE_ENROLLMENT_INTERVAL_SECONDS
   = 5` seconds apart. Opens the camera once (`PRESENCE_CAMERA_INDEX`),
   loops 5 times: sleep 5s (skipped before shot 1), read a frame, run
   `analyzer.get(frame)`. A frame with 0 faces is skipped ("no face
   detected"). A frame with 2+ faces is **not** rejected — picks the
   largest face by bounding-box area (closest to camera, presumed to be
   the enrolling person) and prints its bbox so a human watching the
   terminal can tell if it grabbed the wrong person and rerun. No
   interactive picker — deliberately simple.
2. **`--seed PHOTO_PATH`**: seeds enrollment from an existing,
   already-confirmed-clear photo instead of a live session (added because
   a real reference photo was already available and used in the same
   night's face-comparison tests via both LM Studio and vision_server.py).
   Standalone, doesn't touch the live-capture code path. Same
   largest-face-if-multiple picking logic.

Both paths funnel through the same `_append_embeddings` helper — **append,
never overwrite**: `--seed` and a later live run both contribute to the
same `face_enrollment.json`, coexisting, not replacing each other. Both
paths also share the "never overwrite an existing `face_reference.jpg`"
rule — whichever ran first keeps its reference photo permanently unless
that file is manually deleted.

### What gets stored where

- `Core/data/face_enrollment.json` — the accumulated store of face
  embeddings (list of raw embedding vectors as JSON arrays, under an
  `"embeddings"` key). **This file was NOT read or quoted in this doc
  research pass** (biometric/personal runtime data under `Core/data/`) —
  only its structural shape (`{"embeddings": [...]}` , read via
  `_load_existing_embeddings`) and location are documented here.
  Embeddings are stored as **5 separate shots, not averaged** — a
  deliberate design decision "for robustness across lighting/angle."
- `Core/data/face_reference.jpg` — exactly one reference photo (see §2's
  narrow-exception note), used only by the vision-model ambiguous-band
  fallback. Never overwritten once it exists.
- `Core/data/presence_state.json` — presence.py's own runtime state:
  `{"present": bool, "last_seen": iso-ts|None, "last_checked": iso-ts|None}`.
  Written via the same tmp-file-then-`.replace()` atomic-write pattern used
  elsewhere in this codebase (e.g. `phone_tools`' `CALL_SEEN_PATH`), and
  mirrored in an in-memory `_state_cache` for cheap reads.

### Ongoing accumulation (post-enrollment)

`presence.py._accumulate_embedding(face)` appends a live-polled face's
embedding to the SAME `face_enrollment.json` file `enroll_face.py` seeds,
reusing `enroll_face.py`'s own `_load_existing_embeddings`/
`_append_embeddings` helpers directly (imported via `from
scripts.enroll_face import ...`) rather than reimplementing the
read-modify-write. This only ever fires on a **confirmed positive match**:
either a direct similarity ≥ `PRESENCE_MATCH_THRESHOLD_HIGH`, or an
ambiguous-band result the vision fallback resolved to `True`. Never on a
non-match, and never on an unresolved ambiguous result. Capped at
`PRESENCE_MAX_EMBEDDINGS = 50` with **no eviction once full** — Vatsal's
explicit 2026-08-21 call: "up to 50, only the positive." Once the cap is
hit, `_accumulate_embedding` is a silent no-op (checks `len(...) >=
PRESENCE_MAX_EMBEDDINGS` before appending). The embeddings list is
re-read+reparsed from disk on every single confirmed-match poll (every
`PRESENCE_POLL_SECONDS`) — deliberately not cached beyond the
module-level `_enrollment_embeddings` lazy-cache used for the match
comparison itself, since a re-enrollment requires restarting the process
anyway (see next paragraph).

`presence.py`'s own `_get_enrollment_embeddings()` (used for match
comparisons, distinct from the accumulation helper above) is cached after
first successful load and **never automatically re-read** — "rerun
enroll_face.py and restart the process to pick up a re-enrollment."

## 4. Sleep mode (`Core/orchestrator/sleep_mode.py`)

Module docstring frames the split precisely: presence detection
(`input/presence.py`) is "done and just reports a raw per-poll camera
result"; `sleep_mode.py` is "what turns 'N misses in a row' into an actual
sleep-mode decision, and gates proactive nudges on it."

### State machine — confirmed from source

Module-level, **in-memory only** (`_streak = 0`, `_sleeping = False` — no
`STATE_PATH`, no persistence file, deliberately). Docstring rationale: "a
restart is itself a real, presence-independent event (screen watcher,
scheduler etc. all reinitialize fresh too), so there's no clear reason
sleep-mode needs to survive one." If that assumption turns out wrong, the
comment says to follow presence.py's own `STATE_PATH`/`_save_state`
pattern — not done as of this doc.

```
PRESENCE_ABSENT_DEBOUNCE = 3   # Core/config/settings.py
```

`on_presence_poll(present: bool)` — called once per poll, right after
every `presence.poll_once()` call, from
`proactive_checks.check_presence()`:
- `present=True`: resets `_streak = 0`. If it was sleeping, flips
  `_sleeping = False` and logs `sleep_mode_exit` with
  `reason="presence_returned"`.
- `present=False`: increments `_streak`. Once `_streak >=
  PRESENCE_ABSENT_DEBOUNCE` (3) AND not already sleeping, flips
  `_sleeping = True` and logs `sleep_mode_enter` with the streak count.

At `PRESENCE_POLL_SECONDS = 15` per poll, 3 consecutive absences is
**45–60s** of continuous absence before sleep mode actually engages (the
comment in settings.py phrases it as "3 * 15s ≈ 45-60s").

`wake(reason: str)` — unconditional force-exit: zeroes `_streak`, and if
currently sleeping, flips to awake and logs `sleep_mode_exit` with the
given reason string. Two confirmed callers:
- `Core/ui/pill_app.py` (~line 465-466): the hotkey handler calls
  `sleep_mode.wake("hotkey")` — comment there frames it as "the user
  manually did something," the same signal class as presence returning.
  Cheap no-op when not currently sleeping.
- `Core/tools/sleep_mode_tools.cancel_sleep_mode()` calls
  `sleep_mode.wake("cancel_command")`.

`is_sleeping() -> bool` — trivial getter, reads the module-level
`_sleeping` flag.

### What sleep mode actually changes in FRED's behavior — verified narrow

**Confirmed from source: sleep mode currently gates exactly one thing —
proactive notifications routed through `proactive_checks.notify()`.**
That wrapper (`proactive_checks.py` lines ~45-54) shadows
`utils.notifier.notify` (imported there as `_real_notify`): every proactive
check in the file (vault staleness, long session, deadlines, task
deadlines, agenda deadlines/prep/upcoming/carryover, VIP messages, recent
calls, the presence-wake greeting itself) funnels through this one
`notify()`, which checks `sleep_mode.is_sleeping()` first and silently
drops the nudge if true — "no queue/replay, matches reminders' own
precedent of 'fire once or not at all'."

**Nothing else was found gated on sleep mode in this codebase as of this
doc.** Specifically NOT confirmed/NOT present in source, despite being in
the plan doc:
- No pause of the pill UI / wake-word listening during sleep mode (grepped
  `Core/ui/pill_app.py` and `Core/orchestrator/orchestrator.py` — the only
  sleep_mode-related code in `pill_app.py` is the hotkey's `wake()` call).
- No consolidation job (day-summary generation, `map.md` vault-scan/append)
  triggered by entering sleep mode.
- No unprompted "here's what's still open" recap spoken automatically on
  camera-driven wake (`sleep_mode.wake()` itself does not speak or invoke
  any task/agenda listing — the only speech tied to a real debounced wake
  is the presence-check greeting described next).

### The wake greeting (lives in `proactive_checks.py`, not `sleep_mode.py`)

`proactive_checks.check_presence()` (the function `register()` schedules
every `PRESENCE_POLL_SECONDS / 60` minutes as `"proactive_presence"`):

```python
try:
    present = presence.poll_once()
except Exception as e:
    event_log.log_error("proactive_presence", e)
    return

was_sleeping = sleep_mode.is_sleeping()
sleep_mode.on_presence_poll(present)

if present and was_sleeping:
    notify(random.choice(_PRESENCE_GREETINGS), title="Welcome back")
```

Order matters and is commented explicitly: `was_sleeping` is captured
**before** `on_presence_poll()` mutates state, because that call is what
would flip `is_sleeping()` back to `False` for this very poll — reading
after would always see `False` and the greeting would never fire.
Greeting fires "only on a REAL sleep-mode wake" — i.e. only after the
3-poll debounce actually engaged sleep mode, not on every single
present-poll (so a person who never triggered the debounce, e.g. someone
who only stepped away for one 15s poll, never hears a greeting — there was
nothing to wake from).

`_PRESENCE_GREETINGS` is a 6-phrase pool (`"You there, sir?"`, `"Welcome
back, sir."`, etc.), same "sir-suffixed short-phrase-pool" style as
`canned_replies.py`'s `presence_check` category. This greeting call itself
goes through the same sleep-mode-gated `notify()` wrapper described
above, though by the time it fires `is_sleeping()` has already been
flipped back to `False` by the `on_presence_poll()` call two lines earlier,
so the gate is a pass-through here, not a blocker.

Never raises into the scheduler: both `presence.poll_once()` and the whole
block are wrapped so a camera hiccup or vision-model failure gets logged
via `event_log.log_error("proactive_presence", e)` and the function returns
— it does not crash the periodic scheduler.

### Cancel command (`Core/tools/sleep_mode_tools.py`)

One tool, `cancel_sleep_mode() -> str`:
```python
def cancel_sleep_mode() -> str:
    if not sleep_mode.is_sleeping():
        return "I wasn't in sleep mode, sir."
    sleep_mode.wake("cancel_command")
    return "Sleep mode cancelled, sir."
```
Registered in `orchestrator.py` as tool name `"cancel_sleep_mode"`.
`orchestrator/intent.py` fast-paths this as a **deterministic
exact/substring phrase match**, not an LLM call — tool group `"sleep_mode":
("cancel_sleep_mode",)` mapped to phrases `"cancel sleep mode"`, `"wake up
fred"`, `"wake fred up"`, `"exit sleep mode"`, `"stop sleeping"`, `"sleep
mode"` (`intent.py` ~lines 417-419). This matches the plan doc's intent
(deterministic fast-path, no LLM) though the plan specified exactly 5
phrase variants routed only through the HUD text box — the actual
implementation has 6 phrases and (per intent.py's fast-path mechanism)
is reachable through whatever input channel routes through the intent
dispatcher generally, not verified here to be HUD-text-only or also
voice-reachable — that distinction wasn't traced further in this pass.

`sleep_mode_tools.py`'s own docstring frames this as "the third, explicit
way" to exit sleep mode, alongside presence returning and the hotkey —
"for when neither of those happens to fire (e.g. Vatsal is in frame but
the camera missed a match)."

## 5. `proactive_checks.py` integration — full picture

Presence plugs into `proactive_checks.py` at exactly two points, both
already covered above but summarized together here:

1. `notify()` (the module's own wrapper, not `utils.notifier.notify`
   directly) gates on `sleep_mode.is_sleeping()` — this is how sleep mode
   suppresses ALL proactive nudges in the file, not just presence-related
   ones.
2. `check_presence()` is the sole function that calls
   `presence.poll_once()` and feeds its result into
   `sleep_mode.on_presence_poll()`; it's registered in `register()` as a
   periodic job at `PRESENCE_POLL_SECONDS / 60` minutes (a deliberate
   fractional-minute float — `15/60 == 0.25` exactly — fed to
   `scheduler.add_periodic`, which APScheduler turns into
   `timedelta(seconds=15)`; comment notes this avoids needing a
   seconds-native variant of `add_periodic` just for this one case).

**No other check function in `proactive_checks.py` consults
`presence.is_present()`/`last_seen()`/`last_checked()` directly.** Vault
staleness, long session (uses Windows `GetLastInputInfo` idle time, a
different signal from camera presence), deadlines, task deadlines, agenda
checks, VIP messages, recent calls — none of them read presence state
themselves; they're only indirectly affected in that sleep mode (driven by
presence) can suppress their `notify()` call. This confirms the plan doc's
originally-described design ("before any `notify()` call... check
`presence.is_present()`") was implemented as a single shared gate inside
`notify()` rather than each check calling `presence.is_present()`
individually — a cleaner version of the same idea, verified in source.

## 6. MVP scope boundary — plan vs. built

`presence.py`'s own module docstring is explicit: "MVP scope only, per
fred-presence-sleep-mode-plan_2026-08-18.md and Vatsal's own scoping call
2026-08-21: presence detection alone (is_present()/last_seen()/
last_checked()/poll_once()), nothing downstream yet — no sleep-mode, no
reminder-gating, no cancel phrases, no background poller/scheduler."
`settings.py`'s PRESENCE block docstring says the same thing.

**That docstring is now stale relative to what actually got built the same
day** — sleep-mode, the presence-gated `notify()` wrapper, and the cancel
tool/phrases all DO exist in source, contradicting the "nothing downstream
yet" framing. Read the actual state, not that comment, when rebuilding.
What's confirmed built vs. NOT, precisely:

**Built and confirmed in source:**
- Presence detection itself: enrollment, polling, ArcFace matching,
  vision-model ambiguous-band fallback, ongoing embedding accumulation.
- `sleep_mode.py`'s streak/debounce state machine
  (`on_presence_poll`/`wake`/`is_sleeping`).
- Presence-gated proactive notifications (the `notify()` wrapper in
  `proactive_checks.py`) — this covers the plan's "reminder-gating" item,
  just implemented as a blanket gate rather than a per-reminder
  hold-and-recheck-until-cap mechanism (see below).
- The cancel command / cancel phrases (`sleep_mode_tools.cancel_sleep_mode`
  + `intent.py` fast-path phrases + hotkey `wake("hotkey")`).
- Background poller/scheduler wiring: `check_presence` registered via
  `proactive_checks.register()` on FRED's existing `ReminderScheduler`,
  polling every `PRESENCE_POLL_SECONDS` (15s).

**NOT built (plan-only, verified absent from source):**
- **Consolidation on sleep entry** — the plan's core original motivation
  (day-summary generation via `session_summary`, and scanning the vault
  for files missing from `map.md` and appending them) has no code path
  anywhere. `sleep_mode.py` only flips a boolean and logs an event; it
  does not invoke `session_summary`, does not touch `map.md`, does not
  start or manage any "small, independently resumable" consolidation
  chunks as the plan's "Open decision #1" resolution described.
- **Pausing pill/wake-word during sleep mode** — not found; `pill_app.py`'s
  only sleep_mode touchpoint is the hotkey's `wake()` call.
- **Unprompted wake-time task/agenda recap** — not implemented; only the
  generic 6-phrase greeting pool fires, no `list_tasks`/`list_agenda_items`
  call is triggered by `sleep_mode.wake()` or by `check_presence()`.
- **Reminder hold-and-recheck-until-a-cap semantics** — the plan specified
  reminders should hold while absent and re-check every ~60-90s "until
  presence returns or a cap (e.g. 2 hours) is hit — never silently
  dropped." What's actually built silently drops the nudge for good if
  sleep mode is active at the moment the check fires (each check function
  still dedups so it may fire again on a later independent trigger of that
  same check, but there's no re-check/cap loop specifically for
  presence-absence).
- **Threshold-varies-by-time-of-day curve** — the plan's original
  `threshold(hour)` idea was explicitly abandoned in the plan doc itself
  (superseded by the incremental-start idea, which was itself not built —
  see above); the actual shipped mechanism is the flat, time-of-day-blind
  3-poll debounce (`PRESENCE_ABSENT_DEBOUNCE`).
- **`Core/orchestrator/dispatcher.py` changes** the plan proposed (5
  hardcoded phrases + HUD wiring) — the actual cancel-phrase fast-path
  lives in `orchestrator/intent.py` instead, with 6 phrases, not
  `dispatcher.py`.

In short: **presence detection is fully built and is the mature part of
this subsystem. Sleep mode is a real, working, but intentionally minimal
state machine — it only gates proactive notifications and exposes
wake/cancel; the consolidation/day-summary/map.md-scanning work that
motivated building this in the first place was never implemented.**

## 7. Known gaps to carry into a rebuild

- **Match thresholds are uncalibrated guesses**, not measured values —
  `PRESENCE_MATCH_THRESHOLD_LOW = 0.30` / `_HIGH = 0.45` were chosen from
  general ArcFace literature ("typical same-person similarity clusters
  0.35-0.45 in the wild"), not from this specific enrollment. Before
  relying on this in production, run real enrollment + real live polls and
  look at actual similarity scores to retune.
- **Camera index is a physical-setup fact, not portable config** — index 1
  only holds on the current desktop with its current camera app mix; a
  rebuild on different hardware must redo the per-index capture-and-
  inspect check, not assume `PRESENCE_CAMERA_INDEX = 1`.
- **`sleep_mode.py` has zero persistence** — a process restart silently
  resets `_streak`/`_sleeping`, which is a stated deliberate choice, not
  an oversight, but worth knowing before assuming sleep-mode state
  survives a crash/restart.
- **No consolidation logic exists** — if the original vault-consolidation
  motivation still matters, that's still greenfield work, not something to
  find partially built somewhere else in the repo.
- Do not read/quote `Core/data/face_enrollment.json` or
  `Core/data/face_reference.jpg` in future docs or commits — biometric,
  personal, stays out of anything that leaves the machine, per the
  no-personal-info-in-commits convention already in force elsewhere in
  this codebase.
