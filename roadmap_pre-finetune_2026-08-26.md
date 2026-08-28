# Roadmap — Pre-Fine-Tune (as of 2026-08-26)

## 1. Framing

**Scope narrowed 2026-08-26: one model, one fine-tune, mostly tool-calling.**

Target is `unsloth/Qwen3.5-4B-GGUF` — already the model loaded for BOTH
the `Standard` tier (tool-calling, `config/settings.py:469`) and the
`Vision` tier (presence's ambiguous-match fallback, `config/settings.py:580`),
same weights, two separate processes. There's no separate 8B tool-calling
model in play — Standard was already re-pointed to this same 4B
checkpoint (Qwen3-8B/Bonsai-27B sit commented-out as alternatives). A
single LoRA trained mostly on `Core/data/tool_call_log.jsonl` naturally
carries into the Vision tier too, since it's the same base weights — no
separate vision-training track is planned. Whether that carry-through
helps, hurts, or is neutral for the vision-fallback specifically is
untested; check it AFTER training, not a reason to block on it now.

**This is the one "the freeze" gates.** `MVP Plan (v1.0 - v1.1).txt`'s
v1.2 section locks the tool-calling surface before this trains, so
training data doesn't bake in bugs from a still-moving target.

**Key insight this doc is organized around**: most open work — the whole
perception layer, proactivity, Gmail/PA work — is **orthogonal to the
freeze** and can proceed in any order relative to it. Only a small set of
items are genuinely freeze-blocking: the v1.2 hardening pass itself, plus
the tool-calling pre-fine-tune data/eval checklist.

Today is 2026-08-26. v1.2's target date was **2026-08-23 — yesterday
relative to today**.

---

## 2. Freeze-blocking track

Nothing here is optional before the tool-calling LoRA trains.

### v1.2 pre-freeze hardening pass (`MVP Plan (v1.0 - v1.1).txt`, lines 98-165)

Logged 2026-08-22 (`handoff_2026-08-22.md`), three ordered steps:

1. Local-first pass / Raspberry Pi migration where plausible (presence
   /face-recognition flagged as strongest candidate — CPU-only already,
   but `buffalo_l`'s real Pi-ARM performance is unverified).
2. Full subagent-team codebase audit (bugs, robustness, verbose logging,
   LOC reduction, dependency re-evaluation, modularity refactor,
   documentation — locally and on GitHub).
3. A handoff file for future agents explaining the freeze.

**Status: very likely NOT started.** Verified by `git log --since=2026-08-22`
(48 commits) — every commit is wake-word retraining, presence/headphone
work, voice_id, or dispatcher/crash fixes. None reads as Pi-migration or
subagent-audit work. No handoff file exists dated after
`handoff_2026-08-22.md` — the next one would have been this document's
sibling. The three steps got deprioritized by a large volume of unrelated
work since: wake-word retraining, the presence false-absence
investigation, gods-eye-view research, perception-features planning
(`plan_perception_features_2026-08-25.md`), proactivity research, and
Gmail/PA-task research. State this plainly — it did not happen, not "may
be behind."

### Tool-calling pre-fine-tune checklist (`MVP Plan (v1.0 - v1.1).txt`, lines 167-208, added 2026-08-26)

- **Data volume**: `Core/data/tool_call_log.jsonl` currently **1085 rows**
  (re-checked this session, matches the 1085 noted earlier — stable).
  Whether that's "enough" to train on is still unanswered — not yet
  decided, don't guess.
- **Synthetic examples, planned (2026-08-26)**: real rows are almost
  certainly thin on the harder bucket — undirected/ambiguous, general-
  conversation turns where a tool should fire without being explicitly
  named (the ~35-40%-accuracy case vs. ~70-80% for directed calls, per
  Vatsal's own estimate). Plan is to generate synthetic examples of this
  shape specifically to thicken that bucket, not to pad directed-call
  volume which is already the stronger half. Not yet built — needs a
  generation approach decided (which model, how examples get labeled/
  verified before entering the training set) before it's more than an
  intent.
- **Eval holdout: DONE 2026-08-26.** `Core/scripts/build_eval_holdout.py`
  froze **119 rows (15%)** of the **794 clean, non-bug-artefact** tool-call
  rows (not all 1085 log lines — that count includes feedback/interrupt
  rows and known-bug artefacts filtered out via `tool_call_report.py`'s
  own `EXCLUSIONS`) into `Core/data/tool_call_eval_holdout.jsonl`
  (gitignored). Deterministic split (seed 3), script refuses to
  overwrite once frozen — re-running it after this point is a no-op by
  design. **675 rows stay train-eligible.** Any training run — real
  data, synthetic-augmented, or both — MUST exclude these 119 turn_ids;
  never train on a holdout row, or the eval number stops meaning
  anything.
- **Baseline eval run: still NOT done.** The holdout set exists now, but
  nothing has actually queried the live orchestrator against those 119
  utterances to record today's real accuracy number. That's the
  concrete next step — needs to happen before training starts, or
  there's nothing to compare the tuned model against afterward.
- **Toolchain/hardware**: undecided. Trainer likely Unsloth (matches
  `unsloth/Qwen3.5-4B-GGUF` being the target model already). GPU
  candidate is an RTX 5060 Ti (16GB, clears the ~10GB LoRA requirement
  for this 4B model), not yet confirmed physically present.
- **Vision-tier side effect, not a separate track**: since the Vision
  tier already runs these exact weights, this LoRA will also apply when
  the model is loaded for presence's ambiguous-match fallback
  (`input/presence.py`, `_vision_fallback_is_match()`). Untested whether
  that helps/hurts/is neutral there — check after training, doesn't
  block it. See section 5 below for what a dedicated vision-training path
  would have needed, now superseded by this single-fine-tune scope.

---

## 3. Orthogonal track (parallel with the freeze, blocks nothing)

### (a) Live bug: `focus_checkin.py` refire cadence — cheap, diagnosed, unfixed

`Core/orchestrator/focus_checkin.py:202-240`. Root cause confirmed this
session, **fix not yet applied**:

- `_check()` measures `idle_minutes` from `last_interaction` (line 222),
  a fixed anchor that only resets on a genuine new interaction (line
  212-216) — not from the last time this check actually fired.
- `threshold_minutes` grows by `FOCUS_CHECKIN_STEP_MINUTES` (10) each fire
  (line 239), but the poll interval (`PROACTIVE_CHECK_INTERVAL_MINUTES` =
  15) is larger than that step — so once `idle_minutes` first clears
  threshold, it stays cleared every subsequent poll, since `idle_minutes`
  keeps climbing off the same stale anchor faster than the threshold can
  outrun it.
- Net effect: refires roughly every ~15 minutes (the poll cadence) instead
  of a growing ~3hr cadence. Confirmed via real webcam-capture timestamps
  on three separate days (08-22, 08-24, 08-26), all showing exact
  ~15-minute gaps.
- This is the bug that triggered the proactivity-naturalness research in
  (c) below. Small, already root-caused — arguably do first.

### (b) Perception-layer items (`plan_perception_features_2026-08-25.md`)

Sequenced 1→2→3→5 in the source doc, each independently useful/testable,
none blocks the others; #4 held pending a concrete use case.

1. YOLO person-detection fallback for presence's no-face case
   (`Core/input/presence.py`, `_frame_matches_enrollment`'s `if not
   faces:` branch) — supersedes the killed motion-detection attempts.
2. Mic-level VAD (`Core/input/voice_activity.py`, new — taps
   `input/wakeword.py`'s existing stream, no new capture pipeline).
3. Head-pose/gaze — `face['pose']` is already computed by `buffalo_l`,
   just unread; refines the *matched*-face case, not the no-face gap.
4. General object detection — **held**, no concrete use case yet.
5. voice_id wiring — module exists (`Core/input/voice_id.py`, built
   2026-08-25) but is never called; needs `scripts/enroll_voice.py` run
   first (no `data/voice_enrollment.json` yet).

**Additional camera-signal candidates** (not prioritized/scoped): genderage
field, multi-face count, `det_score` trend as a soft presence signal, YOLO
pose instead of plain person-detection, face bbox size as a distance
proxy. Pull one in only when it maps to a real need.

### (c) Proactivity naturalness principles (plan doc, "Proactivity naturalness principles" section)

Six principles, all buildable today from existing modules, no new sensor
needed:

1. Backoff from last fire, not a fixed clock (this is the fix for the bug
   in (a)).
2. Interrupt at task boundaries (`screen_watcher.py` context-change,
   `media_state.py` media-just-stopped), not blind polling.
3. Composite interruptibility — combine `presence.py` + `media_state.py`
   + `headphone_watch.py`, not presence alone.
4. Calm technology — require persistence across multiple polls before
   speaking (reuse `security_watch.py`'s streak-debounce pattern).
5. Suppress entirely during known-busy states.
6. Phrasing as observation, not notification — prompt-template change
   only, zero new sensors.

Gap: true mid-sentence/frustration-aware avoidance needs real VAD/sentiment
detection — confirmed no in-app VAD module exists yet (item (b)#2 above is
the prerequisite).

### (d) Gmail / PA-task proactivity (plan doc, "PA-level task proactivity — Gmail-first")

No email/calendar integration exists in the codebase today. Slots into
`proactive_checks.py`'s existing `check_vip_messages`/`check_recent_calls`
pattern. **Recommended starting scope already decided**: `gmail.readonly`
only, features 1 and 2 below, plus item 6 flagged separately as likely
best ROI-per-effort:

1. Missed-reply nag (Med effort) — thread-state polling, no reply from
   Vatsal within N days.
2. Deadline-in-email-body surfacing (Med-High) — local extraction only,
   body text stays off the cloud.
3. Noise triage digest (Med) — held out of starting scope.
4. Draft-for-approval, never auto-send (High) — `gmail.compose`, not
   `.send`; held out of starting scope.
5. Calendar-aware doc/meeting nudge (High) — needs a second new
   integration (calendar); held out of starting scope.
6. **Generalize `agenda.py`'s carryover loop-closer** beyond homework
   ("I'll email them back" as a tracked commitment) — pure local-vault
   work, **no Gmail needed**, reuses `check_agenda_carryover`'s existing
   plumbing. Flagged in the source doc as possibly the best ROI-per-effort
   of the whole PA-task list, Gmail included.

Explicitly out of scope per the source doc: auto-summarizing every unread
email, cloud-LLM sentiment/priority scoring on raw bodies, inbox-zero
automation, unsubscribe-bots.

**Status 2026-08-28: features 1 and 2 are LIVE**, but via a temporary
IMAP bridge (`Core/tools/gmail_imap.py`), not the OAuth Gmail API above
— Vatsal hit his GCP project limit the same day, and deletion/reuse
won't clear for ~1 month. Same `gmail.readonly`-equivalent scope,
same features, different transport (a Google App Password over IMAP
instead of an OAuth token). No-ops until
`scripts/setup_gmail_credentials.bat` is run. Swap back to the real API
once the project limit clears — see `TODO.md`.

**OAuth setup steps, for when that swap happens** (not yet built, this
is just so the steps aren't lost):
1. https://console.cloud.google.com/ → New Project.
2. "APIs & Services" → "Library" → enable "Gmail API".
3. "OAuth consent screen" → External user type → add the
   `gmail.readonly` scope → add Vatsal's own Gmail as a test user
   (stays in "Testing" mode, no need to publish).
4. "Credentials" → "Create Credentials" → "OAuth client ID" →
   Desktop app → download the `client_secret_*.json`.
5. That file goes outside the repo (vault-style handling, never
   committed) — hand the path to whoever's building the swap.

### (e) Phone screen-recording tool

Filed 2026-08-27, Vatsal's own framing: **not** a FRED-automates-this-for-
me capability like the rest of this doc — a personal tool he wants for
himself (capturing his own phone screen), not something FRED needs to
initiate, watch, or reason about. Not scoped yet (target device/OS,
trigger — on-device app vs. `adb screenrecord` vs. something else, where
output lands). Genuinely orthogonal to the freeze in both directions: it
doesn't touch the tool-calling surface v1.2 needs locked, and nothing
about it depends on the freeze finishing first — pick it up whenever, no
sequencing constraint either way.

---

## 4. Suggested near-term order

Synthesis of what's already decided in the source docs — effort level and
dependency, not new priorities:

1. **Fix `focus_checkin.py`'s refire bug** (3a) — cheap, already
   root-caused, live/annoying in daily use, zero new dependencies.
2. **Item 6, `agenda.py` carryover generalization** (3d#6) — local-only,
   no OAuth, flagged as best ROI-per-effort of the whole PA list.
3. **Perception item 1, YOLO person-fallback** (3b#1) — local-only, no new
   dependency (`ultralytics` already installed), fixes the same
   false-absence problem class the focus_checkin bug lives in.
4. **Perception item 2, VAD** (3b#2) — local-only, unlocks proactivity
   principle 4's frustration-avoidance gap and item 1's composite signal.
5. **Perception item 3, head-pose** and **item 5, voice_id wiring**
   (3b#3, 3b#5) — local-only, smaller/refinement-scale.
6. **Proactivity principles 2-6** (3c) — layer on top of 3b's signals as
   they land; principle 1 is already covered by step 1 above.
7. **Gmail features 1 and 2** (3d#1-2) — first item needing new external
   setup (OAuth), deliberately sequenced after the local-only work above.
8. **v1.2 freeze-blocking track** (section 2) — can run in parallel with
   any/all of the above at any point Vatsal chooses to prioritize it; nothing
   above blocks or is blocked by it. The only thing that must happen
   before it is picked back up is: nothing — it's just been sitting,
   unstarted, since 2026-08-23.
9. **Tool-calling checklist gaps** (data-volume decision, baseline eval)
   — do once the v1.2 pass is actually run, since the audit may change
   what's in `tool_call_log.jsonl`'s surface.

---

## 5. Vision fine-tune — superseded (2026-08-26), kept for reference

**No longer a separate track.** Scope narrowed to one model, one
fine-tune, mostly tool-calling (section 1) — since `Standard` and
`Vision` tiers already share the exact same `unsloth/Qwen3.5-4B-GGUF`
weights, a dedicated vision-training pass isn't planned; the
tool-calling LoRA's effect on the vision fallback is checked after
training instead (see section 2's "Vision-tier side effect" note).

What follows is the original vision-specific plan from
`plan_perception_features_2026-08-25.md`'s "Candidate: fine-tune the
existing local vision model" section, kept here as background on gaps
that would still apply if a dedicated vision-training pass is ever
picked back up:

- `unsloth` not installed in `Core/venv` — no training toolchain yet.
- No training data saved to disk — `_vision_fallback_is_match()` logs a
  text note on ambiguous replies but never saves the actual frame pair.
- GPU undecided — RTX 5060 Ti (16GB, clears the ~10GB LoRA requirement)
  vs. an unspecified Tesla card, not yet finalized or confirmed present.
- Not scoped whether a fine-tuned local 4B model would even beat the
  current base model's accuracy — needs real measurement before it
  replaces anything live, same standard as everything else in this
  project.
