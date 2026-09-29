# 2026-09-29 — work log

- **Venus try 1 overnight soak test** (run `20260928_222918`, 22:29 -> 06:34,
  7.78 h, 28,005 units; mostly quiet room + screensaver, ~30 min TV/noise,
  no direct commands; defaults: speak threshold 0.5, intent barge, alert
  off). `analyse_live.py` + ad-hoc breakdowns:
  - **Unprompted replies: 0 in 7.8 h** (was 20.6/h before the 0.5 speak
    threshold). VRAM reserved flat 7.6-8.9 GB, context capped ~5000 tokens,
    0 trims, 0 crashes, 37 lock-screen grab failures skipped cleanly.
  - **Realtime lost from 02:00:** other GPU usage 4.9 -> 6.6 GB and GPU at
    ~96%; llm_feed 0.25 -> ~9 s per unit; the loop kept every second of
    audio and fell up to **43 minutes** behind until morning. Likely one of
    the nightly GPU jobs (DaProDrive_NightlyRecognizeWindow 01:50,
    DaProDrive_PhotoLocationTagger 01:00, FRED_NightlyReindex 03:00) --
    GPU time, not VRAM (4.8 GB still free). Per hour: 22-01h median unit
    0.37 s, lag > 1 s in ~1% of seconds; 02-06h p90 5-9 s, lag in almost
    every second.
  - **Wake word: 19 false triggers** (11 in the TV/noise stretch before
    midnight; 8 after 02:00, mic context unreliable due to lag). Each forced
    a reply to nothing, and the replies were invented ("You have a message
    from your mother", "Vatsal just got a new call from T...", "The meeting
    has started", "shared note titled 'Important Info'"). Some forced
    replies also repeated themselves ("Hello, Vatsal. How can I help you
    today?Hello, V...").
  - Kokoro: 45 chunks, synth median 0.26 s; one 8.8 s outlier during the
    GPU contention.
- **Fixes** (`live_duplex.py`): (1) stale-audio drop -- if the backlog
  exceeds 3 s, discard it and continue from the latest second
  (`lag_drop` event); (2) a wake word only forces a reply once he actually
  speaks after it (noise-floor "talking" within 3 s), else
  `wake_unconfirmed` and it's dropped. The wake model itself is frozen;
  false wakes during TV speech remain -> the held Ctrl+Alt chord is the
  reliable way to address Venus.
- Open: the nightly GPU jobs vs an always-on Venus (priority / scheduling
  is Vatsal's call); forced-reply repetition.
- **Nightly GPU jobs checked** (Vatsal: "does the photo tagger even use
  the GPU?"): PhotoLocationTagger = EXIF + Nominatim/reverse_geocoder, CPU
  only; FRED_NightlyReindex = file_index (no models), CPU/disk; only
  DaProDrive_NightlyRecognizeWindow uses the GPU -- Nextcloud Recognize
  face scan in Docker, TensorFlow GPU, 02:00-04:00 (its script already
  stops FRED because "it shares the GPU"). Matches the soak slowdown; lag
  until ~06:00 was the backlog draining. FRED's orphaned llama-server
  (started 2026-09-27 16:20) is still holding VRAM -- left alone.
- **Candidate 1 verdict written:** `Mark_II_Testing/Realtime_Venus/1/VERDICT.md`
  -- viable as FRED's perception / turn-taking layer with FRED's tools and
  gates behind it, not a drop-in replacement.
