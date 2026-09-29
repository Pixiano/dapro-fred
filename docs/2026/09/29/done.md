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
- **Venus scored on FRED's benchmark suite** (Vatsal: "was this tested on
  the whole suite… yes build the adapter and run it"). `benchmarking/run.py`
  gained `--candidate venus` (`VenusOmni` adapter, runs in the Venus venv),
  capability-aware skipping (`needs()`: tool cases count as unsupported,
  not silently passed), and frame-sequence/audio/ambient cases via
  `watch()`. FRED vs Venus: conversation 16/21 vs 7/21, delegation 5/8 vs
  5/8 (Venus never emits `<delegate>`), screenshots 12/12 vs 7/12,
  frames/audio unsupported vs 3/5; tools/injection/memory unsupported for
  Venus. Venus invents unknowable facts (weather, battery, bank balance,
  time) when forced to answer. Upstream's bare prompt gives identical
  scores. Details: `Mark_II_Testing/Realtime_Venus/1/VERDICT.md`.
- **Mark II order agreed:** 1 (done) → 3 → 5 → 4 → 2 last. Vatsal's
  multimodal plan (shared 2026-09-27, only in the chat until now) saved
  verbatim as `docs/2026/09/27/mark2_plan.md`.
- **Candidate 3 (base Qwen2.5-Omni) try 1 started:**
  `Mark_II_Testing/Qwen2.5-Omni/1/`, same procedure as candidate 1. Repo
  cloned, venv built (torch 2.8 cu128 from the cached wheel,
  transformers 4.52.3, bnb 0.50.2), official Qwen2.5-Omni-7B @ `ae9e1690`
  (22.4 GB) downloading. 7B chosen as the fair match for Venus's 8B; NF4
  on the GPU planned (Qwen's official low-VRAM mode offloads to CPU → not
  used). Not duplex: live test needs our own turn-taking. Waiting for
  Vatsal's go after the import.
  - Import done 18:06: 62 min, no retries; SHA-256 7/7 match.
