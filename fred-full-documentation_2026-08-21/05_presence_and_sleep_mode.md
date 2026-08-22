# 05 — Presence Detection & Sleep Mode

**Updated 2026-08-22** — this file was originally written 2026-08-21, the
day the subsystem was born, and at that point sleep mode was a bare
absence/presence state machine with no consolidation or reasoning behind
it. In the day since, four real pieces landed on top of it: sleep-mode
**consolidation** (a bundled day-summary + vault-gap recap spoken on wake),
a deep **reflection** job (a sleep-time reasoning pass over recent session
logs that writes to `people/*.md` and stages self-facts for review), a
**three-tier embedding system** replacing the old flat 50-embedding cap,
and a round of **false-positive fixes** (raised match threshold, a new
symmetrical present-side debounce). The "Plan vs. built" section (§6, old)
is folded into this rewrite rather than kept as a separate stale snapshot —
consolidation is no longer "not built," it's real, and this file says so.

This covers `Core/input/presence.py`, `Core/orchestrator/sleep_mode.py`,
`Core/orchestrator/consolidation.py`, `Core/orchestrator/reflection.py`,
`Core/tools/sleep_mode_tools.py`, `Core/scripts/enroll_face.py`, and the
presence hook inside `Core/orchestrator/proactive_checks.py`. It was built
against the design doc `fred-presence-sleep-mode-plan_2026-08-18.md`
(repo root) — that doc is broader than what actually got built; see §6 for
the current delta.

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
PRESENCE_MATCH_THRESHOLD_HIGH = 0.58   # raised from 0.45, 2026-08-21
```

Cosine similarity (`presence._cosine_similarity`, plain
`np.dot(a,b)/(||a||·||b||)`) between a detected face's `normed_embedding`
and every stored enrollment embedding; `_best_similarity` now scans across
all three embedding tiers (base/hard/dynamic — see §3.1) and returns both
the best score and which tier produced it, for diagnosability. There is
still only one threshold shared across all tiers — no per-tier threshold
exists (Vatsal's explicit 2026-08-21 precision-risk call).

Per detected face in `_frame_matches_enrollment(frame)`:
- similarity ≥ `PRESENCE_MATCH_THRESHOLD_HIGH` (0.58) → confident match.
  Logged (`presence_match`, similarity + tier). Reports present
  immediately, but embedding accumulation (§3.1) is gated separately by
  the present-debounce below, not by this match alone.
- similarity < `PRESENCE_MATCH_THRESHOLD_LOW` (0.30) → confident
  non-match for this face; move on and check the next detected face (if
  any) rather than immediately reporting absent — a frame can have
  multiple faces.
- otherwise (the 0.30–0.58 band) → **ambiguous**, falls back to a real
  vision-model comparison, `_vision_fallback_is_match(frame)`.

**HIGH was raised from 0.45 to 0.58 on 2026-08-21** after live use showed a
single high-confidence-but-wrong frame was enough to flip present, exit
sleep mode, fire the wake greeting, and pollute the enrollment set with a
bad embedding — Vatsal's own words, "the notifications are appearing like
every false match from the face recog." LOW is untouched; it only gates
the ambiguous-vision-fallback band, a separate concern. The stricter
single-frame bar plus the new present-debounce (below) are the two-part
fix — see §5 for the false-positive story end to end.

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

### 3.1 Three-tier embedding pool (2026-08-22, replaces the old flat cap)

`face_enrollment.json` used to be one flat list, capped at
`PRESENCE_MAX_EMBEDDINGS = 50` with no eviction once full — that constant
**no longer exists**. As of 2026-08-22 every stored entry is a tagged dict
(`{"embedding": [...], "kind": "base"|"hard"|"dynamic", "ts": iso|None}`,
old flat-list files migrated in place on first read by
`enroll_face._load_existing_embeddings`/`_migrate_embeddings` — legacy
entries are split by file-order position: the first
`PRESENCE_BASE_EMBEDDINGS_TARGET` become `"base"`, everything after that
becomes `"dynamic"`, since the old format had no per-entry marker to tell
deliberate enrollment from auto-accumulated apart, and nothing existing
gets dropped by the migration).

Three tiers (`Core/config/settings.py`):

| Tier | Target/cap | Populated by | Eviction |
|---|---|---|---|
| `base` | `PRESENCE_BASE_EMBEDDINGS_TARGET = 20` | Deliberate live 5-shot enrollment or `--seed`, `enroll_face.py`'s default flow | **Protected** — never auto-evicted, never auto-added-to |
| `hard` | `PRESENCE_HARD_EMBEDDINGS_TARGET = 15` | Deliberate adverse-condition capture, `enroll_face.py --hard` (dim light, turned away, angled/partial, looking down, side angle — a fixed 5-prompt guided sequence, `HARD_CONDITIONS`) | **Protected**, same reason |
| `dynamic` | `PRESENCE_DYNAMIC_EMBEDDINGS_CAP = 15` | `presence.py`'s ongoing confident-match accumulation, automatic | **FIFO** — oldest dynamic entry evicted before a new one is appended once the cap is hit |

The base/hard targets are enrollment-script *guidance*, not caps
`presence.py` enforces — those two tiers only ever grow through the
deliberate scripted flows, never automatically, so there's no runtime
eviction logic for them. `dynamic` is the only tier with active,
enforced eviction, since it's the only one that grows unattended.

A match against **any** tier counts the same at runtime (§2's single
shared threshold) — the tier is tracked only so a match is diagnosable
later (`presence_match` event log entries include `tier`), not because
match behavior differs by tier yet.

### 3.2 The rest of the enrollment flow

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
embedding, tagged `kind="dynamic"`, to the SAME `face_enrollment.json`
file `enroll_face.py` seeds, reusing `enroll_face.py`'s own
`_load_existing_embeddings`/`_append_embeddings` helpers directly
(imported via `from scripts.enroll_face import ...`) rather than
reimplementing the read-modify-write. This only ever fires on a
**confirmed positive match**: either a direct similarity ≥
`PRESENCE_MATCH_THRESHOLD_HIGH`, or an ambiguous-band result the vision
fallback resolved to `True`. Never on a non-match, and never on an
unresolved ambiguous result.

**Also gated on the present-debounce (2026-08-21 fix, see §5):** a
confirmed match on a frame that hasn't yet cleared
`PRESENCE_PRESENT_DEBOUNCE` (2 consecutive present polls) does not get
accumulated — closes the same false-positive window the wake-greeting
debounce closes, so a single wrong-but-confident frame can't pollute the
enrollment set even though it clears the match threshold.

**Capped at `PRESENCE_DYNAMIC_EMBEDDINGS_CAP = 15` (dynamic tier only)
with FIFO eviction** — replaces the old flat `PRESENCE_MAX_EMBEDDINGS = 50`
/ "stop once full" behavior (2026-08-22 tier redesign, §3.1). Once the
dynamic tier is at cap, the oldest dynamic entry (by file order /
insertion order) is dropped before the new one is appended; `base`/`hard`
entries are never touched by this path regardless of dynamic-tier size.
The embeddings list is re-read+reparsed from disk on every single
confirmed-match poll (every `PRESENCE_POLL_SECONDS`) — deliberately not
cached beyond the module-level `_enrollment_embeddings` lazy-cache used
for the match comparison itself, since a re-enrollment requires
restarting the process anyway (see next paragraph).

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
PRESENCE_ABSENT_DEBOUNCE  = 4   # Core/config/settings.py — raised from 3, 2026-08-22
PRESENCE_PRESENT_DEBOUNCE = 2   # added 2026-08-21, symmetrical return-trip debounce
```

`on_presence_poll(present: bool)` — called once per poll, right after
every `presence.poll_once()` call, from
`proactive_checks.check_presence()`:
- `present=True`: resets `_streak = 0`, increments `_present_streak`. Once
  `_present_streak >= PRESENCE_PRESENT_DEBOUNCE` (2) **and** it was
  sleeping, flips `_sleeping = False`, logs `sleep_mode_exit` with
  `reason="presence_returned"`, and calls `consolidation.on_sleep_exit()`
  (§4.2) to speak the bundled recap.
- `present=False`: resets `_present_streak = 0`, increments `_streak`.
  Once `_streak >= PRESENCE_ABSENT_DEBOUNCE` (4) and not already sleeping,
  flips `_sleeping = True`, logs `sleep_mode_enter` with the streak count,
  calls `consolidation.on_sleep_enter()` to start building the recap
  (§4.2), then calls `consolidation.append_pending(reflection.run_if_due())`
  to fold in the deep reflection pass's own audit line if that pass
  actually ran (§4.3).

At `PRESENCE_POLL_SECONDS = 15` per poll: 4 consecutive absences is
**60–75s** (~1 min) before sleep mode engages; 2 consecutive presences is **30s**
before it exits / the wake greeting fires / a confirmed match gets
accumulated into the dynamic embedding tier (§3.3). The present-side
debounce is deliberately smaller than the absent-side one — a
false-negative-then-correct only costs one missed exit, whereas the
actual complaint being fixed (§5) was the greeting/notification firing
repeatedly on noise, which needed the stricter bar on the return trip.

`wake(reason: str)` — unconditional force-exit: zeroes both `_streak` and
`_present_streak`, and if currently sleeping, flips to awake, logs
`sleep_mode_exit` with the given reason string, and calls
`consolidation.on_sleep_exit()` same as the debounced path above. Two
confirmed callers:
- `Core/ui/pill_app.py` (~line 465-466): the hotkey handler calls
  `sleep_mode.wake("hotkey")` — comment there frames it as "the user
  manually did something," the same signal class as presence returning.
  Cheap no-op when not currently sleeping.
- `Core/tools/sleep_mode_tools.cancel_sleep_mode()` calls
  `sleep_mode.wake("cancel_command")`.

`is_sleeping() -> bool` — trivial getter, reads the module-level
`_sleeping` flag.

### What sleep mode actually changes in FRED's behavior

Sleep mode gates proactive notifications (`proactive_checks.notify()`,
same mechanism as before — see `06_proactive_and_memory.md` §2.1). **As of
2026-08-22, entering and exiting sleep mode also triggers real work**:
consolidation (§4.2) and, when there's enough new material, the deep
reflection pass (§4.3) — both run on the `on_presence_poll`/`wake` edges
described above. Pausing the pill UI / wake-word listening during sleep
mode is still **not** built — the only sleep_mode-related code in
`pill_app.py` is the hotkey's `wake()` call.

### 4.2 Consolidation (`Core/orchestrator/consolidation.py`)

The piece the plan doc originally motivated this whole subsystem with —
"run vault consolidation while Vatsal is away from the desk" — is now
real. Small, in-memory, fire-once module, same "a restart is a real
event, not a crash to recover from" reasoning `sleep_mode.py` itself
holds (no persistence file, deliberately).

- **`on_sleep_enter()`** — called from `sleep_mode.on_presence_poll()`
  the instant absence debounces into real sleep. Builds a **preview**
  (never writes) of two things and bundles them into one pending string:
  `tools.session_summary.preview_session_summary(llm=...)` (today's
  day-summary) and `tools.vault_map.preview_missing()` (a scan for vault
  files not yet listed in `MAP.md`). Both are read-only previews, same
  propose-then-write split those two tools already followed before this
  module existed. Never raises — a failure here must not block sleep-mode
  entry itself, caught and logged, `_pending` set to `None` on failure.
- **`append_pending(text)`** — lets `reflection.run_if_due()`'s own short
  audit line ("Updated people/x.md ...") fold into the *same* bundled
  recap rather than compete as a second proactive announcement.
  `sleep_mode.py` calls this immediately after `on_sleep_enter()`, same
  call site, same cycle.
- **`on_sleep_exit()`** — called from both the debounced
  `on_presence_poll` return-trip and the unconditional `wake()`. Speaks
  the bundled recap **once** via `utils.notifier.notify` directly (title
  `"Welcome back"`) — deliberately **not** through
  `proactive_checks.notify()`'s sleep-mode gate, since by the time this
  runs `is_sleeping()` has already gone `False`, so that gate would pass
  through anyway; going direct also sidesteps a real import cycle
  (`sleep_mode.py` imports `consolidation.py`, so `consolidation.py`
  importing `proactive_checks` — which itself would need `sleep_mode` —
  would cycle back). Also offers reflection's staged self-fact review
  right alongside the recap in the **same** notify call, if
  `reflection.has_pending_review()` — an unreviewed draft can be sitting
  from days ago, so this offer can fire even when there's no fresh
  `_pending` recap at all. Clears `_pending` before checking whether
  there's anything to say, so a later wake with nothing new never
  re-speaks the same recap.
  **2026-08-22 fix:** "nothing new" wasn't actually true before this —
  `on_sleep_enter()` rebuilt and re-spoke the full recap on *every*
  sleep cycle regardless of whether anything changed (the no-LLM
  fallback in particular is a deterministic day-count sentence, always
  identical for an unchanged day). A module-level `_last_spoken` now
  tracks the last recap text actually spoken; `on_sleep_exit()` skips
  re-speaking it if unchanged — same exact-text dedup
  `tools/session_summary.py`'s `_LAST_RECAP_RE` already applies to the
  *written* note, now applied to the *spoken* half too.

Deliberately does **not** import `orchestrator.sleep_mode` at module
level (see the module's own docstring) — `sleep_mode.py` imports this
module, not the reverse, so a top-level back-import would cycle.

**This is genuinely new since the original write-up of this file**: the
old version of this doc stated flatly "no code path anywhere" for
consolidation. That is no longer true.

### 4.3 Deep reflection (`Core/orchestrator/reflection.py`)

A second, separate sleep-time job — deliberately its own module rather
than folded into `consolidation.py`, because `consolidation.py`'s own
docstring is explicit that it *never* writes anything, and reflection
does; keeping them apart keeps that "never writes" property visibly true
rather than something you have to go verify.

**What it does:** re-reads recent session-log events (plus the existing
`people/*.md` corpus, so the model knows who already has a file) with the
new **`"Reflect"` model tier** (gpt-oss-20b, medium reasoning effort — see
`02_llm_and_model_tiers.md`) and extracts two kinds of durable facts:

1. **Friend-file facts** — something learned about a specific named
   person Vatsal talks about (not Vatsal himself). Written **directly,
   unattended** to `people/*.md` — Vatsal's own explicit ask, the concrete
   mechanic he named by name. New file (from a template with a
   "recorded by FRED's sleep-mode reflection pass, correct if wrong"
   disclaimer) or an appended bullet to an existing one, chosen by the
   model's own `file_action` ("new"/"update") judgment against the
   corpus it was shown.
2. **Self facts** — a durable observation about Vatsal himself. **Staged
   only**, never written unattended: appended to a dated markdown file
   under `VAULT_DIR/personal/pending-review/`, offered for review on the
   next wake ("Sir, I made a few notes about you while you were away —
   review them, or keep working?"), and re-offered on every subsequent
   wake until Vatsal says yes.

**Trigger gate: accumulated new material, not sleep-mode entry itself.**
Checked every time `sleep_mode.py` calls `run_if_due()` on entry — below
`REFLECTION_MIN_NEW_EVENTS = 30` new `user_speech`/`tool_call` events
since the last pass, this is a silent no-op that does **not** touch
`reflection_state.json`, so a quiet stretch keeps accumulating toward the
threshold instead of resetting it. State (`REFLECTION_STATE_PATH`,
`Core/data/reflection_state.json`) tracks `last_run_ts`; the very first
run ever bootstraps the watermark to "now" without ingesting any
pre-existing log history.

**Interrupt safety, at chunk granularity.** A single `llm.generate()` call
can't be interrupted mid-generation (no `stopping_criteria` hook on that
path — see `02_llm_and_model_tiers.md`'s cancellation section), so
interruption granularity here is "between chunks," where one chunk = one
session-log file. Before starting each chunk, `sleep_mode.is_sleeping()`
is checked; the instant it's gone `False` (Vatsal's back), everything
buffered for the run is discarded — no writes, no state update — so the
same unprocessed material is picked up whole on the next qualifying
window rather than half-credited. A second check runs after the loop
finishes, in case presence returned in the exact instant the final chunk
completed.

**Privacy:** the reflection LLM call passes `local_only=True` — not
optional, since it reads `people/`- and `personal/`-shaped content,
exactly what `rules.md` forbids sending anywhere but a local model.

**Review flow (`review_pending_reflection` tool, wired to
`reflection.review_pending`):** opens the oldest un-reviewed staged draft
with its default program (`os.startfile`) and moves it into
`REFLECTION_PENDING_DIR/reviewed/`, so the periodic wake-offer stops
re-offering it. The offer primes the orchestrator's tool-carry-forward
mechanism (same `_prime_carry` plumbing `proactive_checks.py`'s
`on_agenda_ask` uses) so a bare "yes" on the next turn routes straight to
this tool instead of falling through to chat.

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

if was_sleeping and not sleep_mode.is_sleeping():
    notify(random.choice(_PRESENCE_GREETINGS), title="Welcome back")
```

Order matters and is commented explicitly: `was_sleeping` is captured
**before** `on_presence_poll()` mutates state, because that call is what
would flip `is_sleeping()` back to `False` for this very poll — reading
after would always see `False` and the greeting would never fire. The
condition is now the actual `is_sleeping()` `True → False` edge (compared
before vs. after), not "this one poll came back present" — since
`PRESENCE_PRESENT_DEBOUNCE` means a single present poll no longer
necessarily flips the flag. Greeting fires "only on a REAL sleep-mode
wake" — i.e. only after both the absence debounce actually engaged sleep
mode AND the return debounce (2 consecutive present polls) actually
cleared it — not on every single present-poll and not on a single
high-confidence-but-wrong frame.

`_PRESENCE_GREETINGS` is a 6-phrase pool (`"You there, sir?"`, `"Welcome
back, sir."`, etc.), same "sir-suffixed short-phrase-pool" style as
`canned_replies.py`'s `presence_check` category. This greeting call itself
goes through the same sleep-mode-gated `notify()` wrapper described
above, though by the time it fires `is_sleeping()` has already been
flipped back to `False` by the `on_presence_poll()` call two lines earlier,
so the gate is a pass-through here, not a blocker.

**Two separate notifications can now fire on the same wake.**
`sleep_mode.on_presence_poll()` (called two lines above the greeting
check) already invoked `consolidation.on_sleep_exit()` internally, which
speaks the bundled day-summary/vault-gap recap (and/or the reflection
review offer) via `utils.notifier.notify` directly, title `"Welcome
back"` — a **separate** call from this greeting's own `notify()`. Both
share the same title by coincidence, not by one calling the other.
Nothing in either module currently suppresses one when the other also
has something to say — this is current, verified behavior, not
necessarily the intended long-term UX; flagged for whoever next touches
either module.

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

**Built and confirmed in source, as of 2026-08-22:**
- Presence detection itself: enrollment (now three-tier, §3.1), polling,
  ArcFace matching (now with the raised threshold + present-debounce,
  §2/§5), vision-model ambiguous-band fallback, ongoing dynamic-tier
  embedding accumulation.
- `sleep_mode.py`'s streak/debounce state machine
  (`on_presence_poll`/`wake`/`is_sleeping`), now symmetrical
  (absent-debounce and present-debounce both exist).
- Presence-gated proactive notifications (the `notify()` wrapper in
  `proactive_checks.py`) — this covers the plan's "reminder-gating" item,
  just implemented as a blanket gate rather than a per-reminder
  hold-and-recheck-until-cap mechanism (see below).
- The cancel command / cancel phrases (`sleep_mode_tools.cancel_sleep_mode`
  + `intent.py` fast-path phrases + hotkey `wake("hotkey")`).
- Background poller/scheduler wiring: `check_presence` registered via
  `proactive_checks.register()` on FRED's existing `ReminderScheduler`,
  polling every `PRESENCE_POLL_SECONDS` (15s).
- **Consolidation on sleep entry/exit** (§4.2) — day-summary preview +
  `MAP.md` gap-scan preview, bundled and spoken as one recap on wake.
  Propose-only: nothing is written to the vault by this path itself.
- **Deep reflection** (§4.3) — a sleep-time reasoning pass over recent
  session logs, volume-gated (not sleep-entry-gated), writing directly to
  `people/*.md` and staging self-facts for review.

**Still NOT built (plan-only, verified absent from source):**
- **Pausing pill/wake-word during sleep mode** — not found; `pill_app.py`'s
  only sleep_mode touchpoint is the hotkey's `wake()` call.
- **Unprompted wake-time task/agenda recap** — the consolidation recap
  covers day-summary + vault-gaps, not a `list_tasks`/`list_agenda_items`
  reading; no such call is triggered by `sleep_mode.wake()` or by
  `check_presence()`.
- **Reminder hold-and-recheck-until-a-cap semantics** — the plan specified
  reminders should hold while absent and re-check every ~60-90s "until
  presence returns or a cap (e.g. 2 hours) is hit — never silently
  dropped." What's actually built silently drops the nudge for good if
  sleep mode is active at the moment the check fires (each check function
  still dedups so it may fire again on a later independent trigger of that
  same check, but there's no re-check/cap loop specifically for
  presence-absence).
- **Threshold-varies-by-time-of-day curve** — the plan's original
  `threshold(hour)` idea was explicitly abandoned in the plan doc itself;
  the actual shipped mechanism is the flat, time-of-day-blind 3-poll
  absence debounce / 2-poll present debounce.
- **`Core/orchestrator/dispatcher.py` changes** the plan proposed (5
  hardcoded phrases + HUD wiring) — the actual cancel-phrase fast-path
  lives in `orchestrator/intent.py` instead, with 6 phrases, not
  `dispatcher.py`.

In short: **presence detection is mature and now tuned once against real
false-positive reports, not just launched. Sleep mode is a real, working
state machine with two real jobs riding on its wake/sleep edges —
consolidation (propose-only recap) and deep reflection (unattended
friend-file writes + staged self-fact review). The one piece of the
original plan still genuinely unbuilt is the reminder hold-and-recheck-
until-cap behavior; everything else the plan named either shipped or was
explicitly superseded.**

## 7. Known gaps to carry into a rebuild

- **Match thresholds are still not fully calibrated, though HIGH has now
  been tuned once against a real false-positive report.**
  `PRESENCE_MATCH_THRESHOLD_LOW = 0.30` is still an unmeasured starting
  guess; `_HIGH` was raised 0.45 → 0.58 on 2026-08-21 in direct response
  to live false-positive greetings/notifications, but that's a reactive
  bump, not a systematic recalibration against real enrolled-vs-live-frame
  similarity distributions. Don't treat 0.58 as final either.
- **Camera index is a physical-setup fact, not portable config** — index 1
  only holds on the current desktop with its current camera app mix; a
  rebuild on different hardware must redo the per-index capture-and-
  inspect check, not assume `PRESENCE_CAMERA_INDEX = 1`.
- **`sleep_mode.py` has zero persistence** — a process restart silently
  resets `_streak`/`_present_streak`/`_sleeping`, which is a stated
  deliberate choice, not an oversight, but worth knowing before assuming
  sleep-mode state survives a crash/restart. `consolidation.py` and
  `reflection.py`'s in-memory `_pending` state is similarly lost on
  restart (reflection's `reflection_state.json` watermark is the one
  exception — that one does persist).
- **The "Reflect" tier's VRAM footprint is unverified on this machine.**
  gpt-oss-20b is configured for it, but `settings.py`'s own comment flags
  a reported 26-35GB figure that doesn't fit this card's 16GB — not
  re-measured live as of this doc because there wasn't enough free VRAM
  to safely test it. Don't trust either number until someone actually
  runs it with everything else closed and reads `nvidia-smi`.
- **The two "Welcome back" notifications on the same wake are unreconciled**
  (§4, end) — the greeting and the consolidation recap can both fire,
  neither suppresses the other. Not confirmed to be an intentional design
  choice.
- Do not read/quote `Core/data/face_enrollment.json` or
  `Core/data/face_reference.jpg` in future docs or commits — biometric,
  personal, stays out of anything that leaves the machine, per the
  no-personal-info-in-commits convention already in force elsewhere in
  this codebase. The same applies to anything under
  `VAULT_DIR/personal/pending-review/` or `VAULT_DIR/personal/images/`
  (focus-checkin captures, `06_proactive_and_memory.md`) — personal,
  stays local.
