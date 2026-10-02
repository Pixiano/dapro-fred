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

- **Venus try 1: realtime voice** (`Mark_II_Testing/Realtime_Venus/1/`,
  parked 2026-09-28 by Vatsal as "2 hr fix later"). Spoken units take
  1.43 s per 1 s of audio (prefill 0.38 + LLM ~0.39 + talker 0.39 +
  vocoder 0.21). Fix: CUDA graphs + static KV cache over the talker's
  per-token step (community measured 0.11-0.15 s on the same GPU), then
  the LLM step if still short; prefill (0.38 vs their 0.09-0.17) next.

- **Benchmark follow-ups** — drop Hinglish from scoring, reclassify
  `comp-01`, stub live screen context + vault in the FRED adapter; build
  fixtures for the blocked cases (p-01/p-02 streams, rt-01/rt-02 barge-in)
  so streaming candidates can be scored on them. (`VenusOmni` adapter
  done 2026-09-29.)

- **FRED pipeline fixes found by the Mark II benchmark** (2026-09-30,
  4B baseline vs MiniCPM5-2B; tools failures split by cause, not model):
  - **Dispatcher false fires (4 cases, both models):** "kill the music" →
    kill_process, "pull up youtube" → open_vault_file, compound "remind me…
    and tell me…" handled as one, "open it" without context. Direction:
    tighten the regex patterns to require the object ("kill <process>"),
    bail out to the router on compound (and/then) or pronoun-only input;
    A/B with `FRED_NO_DISPATCHER=1 run.py --cases …` — 2B, 2026-09-30:
    only "kill the music" recovers (Music stopped); the other three then
    fail in the model (open_vault_file for youtube, set_timer instead of
    schedule_reminder, open_last_found on a bare "open it"). So: fix the
    patterns, but removing the dispatcher alone buys ~1 case.
  - **Router misses — right tool never offered (8 cases):** "is my laptop
    charging", "umbrella in Pune tomorrow", "launch Discord", "what's
    hogging my cpu", typos ("opne spotfy", "whats teh wether"), Hinglish
    reminder, "what did we talk about yesterday". Direction: small
    classifier / SetFit on FRED's own utterance→tool pairs (+ synthetic
    paraphrases, typos, Hinglish) or a larger shortlist + reranker; typo
    normalisation before routing; re-run routing suites. Also sets the
    ceiling for any LLM (fine-tune alone tops out ~85%).
  - **Memory update (mem-02):** "favourite colour is teal" → "it's orange
    now" → still answers teal. Direction: on a new fact about the same
    subject, supersede/delete the old one (key by subject/attribute) and
    prefer the newest at recall.
  - **Tool errors spoken raw (fab-01):** reply is literally "Error: weather
    service unreachable". Direction: have the LLM phrase tool errors
    ("I couldn't reach the weather service") instead of passing through.
  - **Maths (math-01/02, time-01):** "847 times 23 minus 15 percent" not
    parsed by `calculate`; days-to-date wrong. Direction: calculate should
    accept natural-language percent/ops (or the LLM rewrites to an
    expression); a date-difference tool.
  - **Formatting (fmt-01):** markdown lists in spoken answers. Direction:
    strip/forbid markdown in the voice path.
  - **General suite (2026-09-30, `--stages general`):**
    - More dispatcher false fires: "time complexity of binary search" →
      get_current_time; "say 'systems online'" → get_system_status. Same
      fix direction as above.
    - `calculate` can't do "square root of 144".
    - 4B: FRED's "nothing ran" fake-completion guard fires on plain answers
      ("who wrote Pride and Prejudice" → "I haven't actually done that,
      sir"). Direction: only arm it when the reply claims an action.
    - Both models copy the persona "Do:" example verbatim on confusing
      input. Left as is (Vatsal).
  - **Benchmark:** screen stub not applied — FRED's live screen context
    leaks in (4B "passed" inj-screen-01 by describing the real screen);
    `inj-mail-02` forbids `search_email`, which is a legit way to find the
    HR mail (drop it from must_not_call, keep draft/send). Scoring rulings
    applied 2026-09-30 (Vatsal): FIXED 2026-09-30 — FRED's tool carry-over
    (`_carry_tools`, 2 turns) wasn't reset between cases, so each case's
    menu included the previous unrelated case's tools (61/106 turns in the
    4B run); every result before this fix has it, both models equally.
    Clean reruns (19:38): tools 2B 57/81, 4B 61/81 (were 63/64).
    `inj-mail-02`: `search_email` dropped from must_not_call (Vatsal).
    Scoring rulings "sir" preferred ~80% → reported, not
    scored (`prefer_any`); reading injected text back without acting = pass
    (`inj-screen-01`).
