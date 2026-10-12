"""Core settings: one typed object, defaults in code, state outside the repo, env overrides.

Sockets
  in : env FRED_DATA (state/secrets/personal data dir, default ~/FRED_Private), env FRED_<FIELD> (any field below, upper-cased)
  out: get() -> Settings (cached); data_dir() -> Path; set_data_dir(path) re-points everything (tests use a temp dir, so test output never reaches the real logs)
Every threshold the later layers need is a field here, never a literal in the code that uses it.
Self-check: python config.py
"""
import os
import tempfile
from datetime import timedelta, timezone
from dataclasses import dataclass, fields
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent


@dataclass
class Settings:
    playground_url: str = "http://127.0.0.1:8090"   # /v1 is appended by the client. NOT "localhost": on Windows it adds a flat 2 s per request (IPv6 attempt first; the playground listens on IPv4 only)
    brain: str = "unsloth/qwen3.5-4b"                 # chat model id; Qwen only for now (Vatsal, 2026-10-06), override with FRED_BRAIN
    embedder: str = "local/qwen3-embedding-0.6b-q8_0"
    http_timeout_s: float = 600.0                     # one model call, incl. a cold load
    http_retries: int = 1                             # extra tries on a connection error only
    tool_timeout_s: float = 15.0                      # default per tool call
    gpu_default_timeout_s: float = 120.0
    alert_battery_pct: float = 10.0                   # proactive/alerts.py: unplugged battery at or below this is critical
    alert_disk_gb: float = 10.0                       # system drive with less free space than this (GB) is critical (a percentage would fire on a 2 TB drive with 100 GB free)
    alert_cooldown_s: float = 1800.0                  # one alert per kind this often
    ask_idle_s: float = 10.0                          # memory_bridge/ask.py: a conversation counts as over after this much silence (no turn, FRED not speaking); the ask queue's question comes then
    jobs_enabled: bool = True                         # memory_bridge/jobs.py: nightly/weekly/monthly/yearly runs through the GPU window (memory on only)
    jobs_check_s: float = 300.0                       # how often it looks whether a run is due
    jobs_quiet_s: float = 600.0                       # no turn or speech for this long before a run may start, and a turn during a run aborts it
    jobs_peek_s: float = 60.0                         # during a night run the camera watch is paused; before a model call, at most this often, one snapshot looks for a face
    jobs_retry_s: float = 1800.0                      # retry delay after an aborted or failed run
    jobs_gap_h: float = 4.0                           # at least this long between runs
    judge_threshold: float = 0.6                      # proactive judge: classifier p needed to speak (invented-case sweep: 0.5 P .96 R .77, 0.6 P .97 R .62, 0.7 P 1.0 R .47); retune on the shadow log
    judge_cooldown_s: float = 120.0                   # the judge stays quiet this long after FRED last spoke
    judge_every_s: float = 30.0                       # shadow look interval (also looks at once on a due reminder or a warning)
    event_ring_s: float = 600.0                       # EventLog in-memory window (~10 min)
    log_max_bytes: int = 20_000_000                   # one jsonl before it rotates
    # --- audio (P1a) ---
    sample_rate: int = 16000
    mic_device: str = "Lavalier"                      # input device name substring; "" = system default (Vatsal's lavalier, 2026-10-08)
    speaker_device: str = "Rockerz"                   # output device name substring; "" = system default (Vatsal's Rockerz headset)
    mic_ring_s: float = 6.0                           # raw audio kept for pre-roll / pre-trigger
    wakeword_model: str = str(CORE_DIR.parent.parent / "Old" / "Core" / "models" / "wakeword" / "hey_fred.onnx")   # FROZEN model, read in place, never copied or swapped
    wakeword_threshold: float = 0.35
    wake_cooldown_s: float = 2.0                      # refuse a second fire this soon after one
    wake_near_miss: float = 0.10                      # scores at/above this but under threshold are logged as audio.wake_near
    agc_target_peak: float = 0.7
    agc_max_gain: float = 40.0
    agc_min_peak: float = 0.03                        # quieter than this: hold the gain, do not amplify noise
    agc_smoothing: float = 0.3
    vad_highpass_hz: int = 200                        # the detector listens above this only (0 = raw); 50 Hz night hum falsely triggered 344 utterances, replayed: 6 onsets -> 0 while real speech stayed 26 of 26
    vad_mode: int = 3                                 # webrtcvad aggressiveness 0-3
    vad_engine: str = "silero"                        # silero | webrtc. webrtc fired on hum, room noise and gate clicks (2026-10-11 audit: 279 of 390 daytime and 368 of 386 night clips it opened hold no speech per Silero) and cut speech into pieces
    silero_threshold: float = 0.35                    # speech probability that counts as a voiced frame (0.35 on the 2026-10-11 clips: 48 of 60 junk clips below 10% voiced, 3 of 40 real clips; 0.5 lost 5 real ones)
    vad_onset_ms: int = 200                           # voiced time needed to call speech started
    vad_hangover_ms: int = 200                        # silence needed to call speech ended (L1 adds Smart Turn on top)
    whisper_model: str = "large-v3-turbo"
    whisper_language: str = "en"
    whisper_beam: int = 5
    whisper_device: str = "cuda"                      # GPU-only rule: change only for an explicit CPU test (FRED_WHISPER_DEVICE=cpu FRED_WHISPER_COMPUTE=int8)
    whisper_compute: str = "float16"
    stt_hotwords: str = "PM Modi, Narendra Modi, CBSE, NCERT, Anthropic, Claude, FRED"   # always-on vocabulary hint for Whisper (names it kept mishearing: Modi came out as PM1, BMW, E.M.O.D.); today's news names are added
    whisper_prompt: str = "Fred."                       # a longer list of commands came back as the transcript of room noise (2026-10-08 live session)
    stt_ghosts: tuple = ("thank you.", "thanks for watching.", "i'll see you next time.", "bye.", "you")   # what Whisper writes for faint TV/room noise
    stt_ghost_peak: float = 0.1                       # ... dropped only when the utterance was this quiet (a real thank-you at the mic is louder)
    stt_no_speech_prob: float = 0.6                   # a Whisper segment this likely to be non-speech AND with low confidence is dropped
    stt_min_logprob: float = -1.0
    stt_min_peak: float = 0.02                        # a buffer whose peak stays under this is silence: no Whisper pass
    max_utterance_s: float = 60.0
    # --- screen (P1b) ---
    screen_interval_s: float = 0.2                    # capture period; bench_screen.py compares 0.1 / 0.2 / 0.5 / 0.7 / 1.0 / 1.2 (0.2: 2.7% of a core, every flash >= 0.2 s seen)
    camera_index: int = 0                             # cv2 index; the real 2K webcam (PHOCUS 40A) is 0 since 2026-10-11 (it was 1 before the replug; index 1 is now a frozen virtual camera). Indices move on replug: re-check with a frame-diff probe; --probe in senses/camera.py checks
    camera_warmup: int = 6                            # frames read and thrown away before the real one
    camera_black_p99: float = 16.0                    # 99th-percentile brightness below this = dead frame
    camera_flat_std: float = 2.0                      # pixel std below this = dead (flat placeholder / covered lens)
    camera_retry_s: float = 0.5                       # wait before the one retry
    presence_poll_s: float = 7.0                      # camera snapshot period
    presence_present_polls: int = 1                   # face polls in a row before "present"
    presence_absent_s: float = 49.0                   # no face for this long (since the last one seen) before "absent"; noticed at the first poll after it
    faces_static_cap: int = 13                        # face references: deliberate shots (never auto-changed)
    faces_dynamic_cap: int = 7                        # face references: FIFO of recent confident matches (static : dynamic = 13 : 7)
    turn_prob_threshold: float = 0.5                  # Smart Turn: at or above = the turn is complete
    voice_id_enabled: bool = True                     # the speaker gate: ON since 2026-10-08, enrolled from 11 real lavalier clips (leave-one-out: Vatsal 0.39-0.62, TV or room noise 0.15-0.28)
    barge_voice_threshold: float = 0.33               # voice match needed to interrupt FRED while it speaks (0.33 worked well in the 2026-10-11 morning runs; 0.45 was too strict, the user's own speech scores 0.29 to 0.41, and stopped interruptions); the wake word interrupts without it
    voice_threshold: float = 0.27                     # cosine similarity at or above = Vatsal. 0.33 sits between the noise (max 0.28) and the quietest real clip (0.39), first guess, tune from the logs
    voice_min_s: float = 1.0                          # shorter clips are too short to judge (score None); 1 s clips scored median 0.50, 2 s 0.62, 0.6 s 0.40 (tests/voice_eval.py)
    voice_refs_cap: int = 20                          # enrolled voice references kept (most diverse)
    usage_clip_cap: int = 400                         # audio clips kept per component and day (usage.py); events are always kept
    usage_wake_clip_s: float = 2.0                    # the mic audio saved around every wake word and near-miss
    stt_save_audio: bool = True                       # save each final utterance + transcript to <FRED_DATA>/voice_data (real-voice data collection)
    clock_tick_s: float = 0.16                        # interaction clock tick
    eot_silence_ms: int = 200                         # VAD silence before Smart Turn is consulted
    eot_hard_cap_s: float = 0.9                       # silence that ends a turn regardless of Smart Turn (Vatsal 2026-10-06: 0.9 instead of the planned 1.2)
    barge_ms: int = 500                               # speech while FRED talks that counts as a real interruption
    barge_level: float = 1500.0                       # mic RMS (int16 units) needed to barge in when not on headphones
    listen_show_s: float = 0.3                        # an addressed utterance must last this long before the pill shows "listening"
    followup_voice_threshold: float = 0.33            # voice match needed for a follow-up WITHOUT the wake word (with it: voice_threshold)
    followup_min_s: float = 0.6                       # speech must last this long inside the follow-up window to count as an answer
    followup_s: float = 5.0                           # open-mic window after FRED speaks
    wake_grace_s: float = 2.0                         # unaddressed speech waits this long for a late wake word before it is ignored (the wake model fires about 1 s after the word)
    wake_min_speech_ms: int = 300                     # a wake event needs this much voiced audio in the wake_speech_window_s before it, else it is dropped (2026-10-11: 27 of 48 wake events had no speech at all; all 13 real ones did)
    wake_speech_window_s: float = 1.5
    wake_barge_s: float = 2.0                         # only this long after a wake may speech interrupt FRED without a voice check
    wake_open_s: float = 4.0                          # a wake word this long before (or during) speech addresses it
    max_tool_rounds: int = 4                          # model <-> tool round trips per turn
    max_calls_per_round: int = 4                      # tool calls executed from one model message
    sir_rate: float = 1.0                            # share of replies, interim and talker lines that end with "sir" (engine/sir.py); fillers too. 1.0 = every line (Vatsal, 2026-10-08, was 0.85); 0 turns it off
    tts_enabled: bool = True                          # False = nothing is spoken (text only)
    kokoro_model: str = str(CORE_DIR.parent.parent / "Old" / "Core" / "models" / "kokoro" / "kokoro-v1.0.onnx")      # read in place, never copied
    kokoro_voices: str = str(CORE_DIR.parent.parent / "Old" / "Core" / "models" / "kokoro" / "voices-v1.0.bin")
    tts_voice: str = "am_michael"                     # the voice Vatsal picked in the old build
    tts_speed: float = 1.17                           # Vatsal, 2026-10-08 (was 1.0 on 2026-10-07; the old build used 1.2)
    tts_preroll_s: float = 1.5                        # silence written first: a Bluetooth link ramps its volume at the start (measured on Vatsal's headphones); 0 on a wired output
    tts_preroll_gap_s: float = 3.0                    # the lead-in is skipped when the previous audio ended less than this long ago (a filler right before the answer): the link is still awake
    tts_postroll_s: float = 1.0                       # silence after the last word: the link buffers and would clip the end; 0 on a wired output
    filler_after_s: float = 0.8                       # a reply slower than this gets a 2-3 word spoken filler ("On it.") while the brain works
    talker: str = "openbmb/minicpm5-2b"               # small model for tool-free chat turns and for lockdown (playground slot talk); "" = the brain answers everything
    gmail_token_path: str = ""                        # OAuth token file of the user's Gmail client; empty = ~/VatsalDaPro/Keys/FRED_Gmail_token.json (outside the repo)
    weather_city: str = ""                            # default city for the weather skill when none is named (set it in your own settings, not in the repo)
    scamwatch_enabled: bool = True                    # warn (toast + bus event) when the screen shows a phishing or scam page; proactive/scamwatch.py
    scamwatch_cooldown_s: float = 1800.0              # the same domain is not warned about again for this long
    scamwatch_max_per_hour: int = 6
    pill_topmost_every_s: float = 1.0                 # while the pill is visible, re-assert it above other topmost windows (a borderless-fullscreen app or a video player can take the top spot after the pill appeared); 0 = only on show
    brain_max_tokens: int = 1200                      # cap on the brain's replies: a live run once rambled 62,000 characters for 190 s with no cap; news read-outs fit well inside this
    talk_max_tokens: int = 200                        # cap on the small talker's replies: spoken answers are short, and a 2B that does not stop must not run for minutes
    ambient_enabled: bool = True                      # skills with an ambient() hook may add a digest (web facts about a named person/event) before the model answers; False = never
    ambient_timeout_s: float = 10.0                   # an ambient digest slower than this is dropped, the answer goes ahead without it
    time_note_every: int = 5                          # turns (proactive ones too) without the time tool after which the model is quietly told the time, so it can suggest winding down; 0 = never
    lockdown_tools: tuple = ("time", "days_until", "calculate")   # the only tools that exist while locked down
    history_turns: int = 8                            # user/assistant pairs kept as conversation context
    engine_think: bool = False                        # reasoning on tool/chat turns (off: the thinking cap; arithmetic goes to the calculator tool)
    semantic_floor: float = 0.65                      # embedding similarity at which a tool counts as clearly requested
    news_offer_hold_s: float = 7200.0                 # an offered edition is not offered again for 2 h, restarts included (long enough for a study block and a restart or two; short enough that an unread morning edition is offered again at lunch)
    news_hours: str = "6,18"                         # local hours at which a news digest is built (06:00 and 18:00)
    memory_testing: bool = True                       # True: everything memory-related lives in Mark_I/Initial/Testing_Memory (kept clean of the real memory); False (or FRED_MEMORY_TESTING=0): <data_dir>/memory. Vatsal says when to switch back.
    vault_dir: str = ""                               # persona/profile/rules markdown read at start (outside the repo); empty = built-in fallback persona
    glance_mid_s: float = 3.0                         # while the user talks: one extra screen frame per this many seconds
    glance_max_frames: int = 3                        # frames per utterance, start and end included
    screen_settle_s: float = 0.5                      # quiet time after a burst of changes before screen.settled fires (the trigger for OCR / the describer)
    screen_monitor: int = 1                           # mss monitor index (1 = primary)
    screen_threshold: float = 0.990                   # a tile is changed when its similarity (1 - mean abs diff / 255) falls below this
    screen_grid_rows: int = 36
    screen_grid_cols: int = 64
    screen_stride: int = 2                            # sample every Nth pixel in each axis before tiling (CPU cost vs thin changes)
    screen_min_tiles: int = 1                         # tiles that must change to emit screen.change
    crop_pad_px: int = 24                             # native-resolution padding around the changed region
    ocr_enabled: bool = True                          # Windows OCR on the changed crop: approved by Vatsal 2026-10-06
    describe_model: str = "unsloth/qwen3.5-4b"        # vision slot model: Qwen3.5-4B, 18/20 on tests/live_vision_hard.py (MiniCPM-V 4.6 scored 9/20 and was deleted, 2026-10-07)
    describe_max_tokens: int = 300
    vision_shared: bool = True                        # describe_model == brain: ONE copy in the chat slot, loaded with its image projector, serves chat and image description (frees ~4 GB, F10 2026-10-11). False = the separate vision slot as before
    # --- live screen reading, speak gate, deadline watch (2026-10-11) ---
    screen_paused: bool = False                       # pause switch: stops the screen reading for the deadline watch (and later screen memory); the scam watch is exempt. Also: a voice command (screen_watch skill) and the file <data_dir>/screen_paused.flag
    screen_read_s: float = 1.0                        # the foreground window is read about once a second (proactive/screenread.py)
    screen_reread_s: float = 10.0                     # a window with only tiny changes (<= screen_noise_tiles) is still re-read this often
    screen_noise_tiles: int = 2                       # a screen.change of this many tiles or fewer (caret, clock) does not count as a change for the reader
    gate_after_fred_s: float = 120.0                  # no spoken offer within this long of FRED speaking
    gate_turn_s: float = 45.0                         # a turn that was addressed counts as "mid-turn" this long at most
    quiet_start: str = "23:30"                        # quiet hours: proactive offers are toasts only
    quiet_end: str = "06:00"
    quiet_latest: str = "01:00"                       # quiet hours start later (up to this) when the user was active in the last quiet_active_min
    quiet_active_min: float = 20.0
    offer_gap_s: float = 600.0                        # at most one spoken proactive offer per this many seconds
    offers_per_day: int = 10
    deadline_watch_enabled: bool = True               # proactive/deadlines.py
    deadline_urgent_h: float = 12.0                   # a deadline closer than this overrides quiet hours and the caps
    deadline_answer_s: float = 90.0                   # a yes/no within this long of the question goes to the watch, not the model
    # --- screen memory (2026-10-11): proactive/screenmem.py, senses/imgembed.py ---
    screen_mem_enabled: bool = True                   # remember what was on screen as low-weight memory events (type "screen"); the pause switch stops it too
    screen_mem_dwell_s: float = 10.0                  # one visit = a window dwell of at least this long
    screen_mem_chars: int = 1000                      # OCR text kept per visit
    screen_mem_dup: float = 0.8                       # same app+title and token overlap at least this = the same visit (merged, not stored again)
    screen_img_enabled: bool = True                   # Phase B: an image vector per stored visit (SigLIP-class, GPU, lazy); off = text vectors only
    screen_img_model: str = "google/siglip2-base-patch16-224"
    screen_img_idle_s: float = 60.0                   # unload the image model after this long without a visit to embed


IST = timezone(timedelta(hours=5, minutes=30), "IST")                 # every stored time and day bucket is India time (owner decision 2026-10-08)


def _coerce(raw: str, like):
    if isinstance(like, tuple):
        return tuple(x.strip() for x in raw.split(",") if x.strip())
    return (raw.lower() in ("1", "true", "yes", "on")) if isinstance(like, bool) else type(like)(raw)


def _build() -> Settings:
    s = Settings()
    for f in fields(s):
        raw = os.environ.get("FRED_" + f.name.upper())
        if raw is not None:
            setattr(s, f.name, _coerce(raw, getattr(s, f.name)))
    return s


_cache: Settings | None = None
_data: Path | None = None


def get() -> Settings:
    global _cache
    if _cache is None:
        _cache = _build()
    return _cache


def memory_root() -> Path:
    """Where the memory (sqlite, archive, job log) lives right now."""
    if get().memory_testing:
        p = Path(__file__).resolve().parent.parent / "Initial" / "Testing_Memory"
        p.mkdir(parents=True, exist_ok=True)
        return p
    return data_dir() / "memory"


def data_dir() -> Path:
    global _data
    if _data is None:
        _data = Path(os.environ.get("FRED_DATA") or Path.home() / "FRED_Private")
        _data.mkdir(parents=True, exist_ok=True)
    return _data


def set_data_dir(path) -> Path:
    """Re-point state to `path` (tests, replays). Callers that cached a path under the old dir must re-open."""
    global _data
    _data = Path(path)
    _data.mkdir(parents=True, exist_ok=True)
    return _data


def reload() -> Settings:
    global _cache
    _cache = None
    return get()


def _selfcheck():
    s = get()
    assert s.max_tool_rounds == 4 and s.playground_url.startswith("http") and "qwen" in s.brain
    os.environ["FRED_MAX_TOOL_ROUNDS"] = "7"
    os.environ["FRED_EVENT_RING_S"] = "30"
    assert reload().max_tool_rounds == 7 and get().event_ring_s == 30.0
    del os.environ["FRED_MAX_TOOL_ROUNDS"], os.environ["FRED_EVENT_RING_S"]
    reload()
    real = data_dir()
    with tempfile.TemporaryDirectory() as t:
        assert set_data_dir(t) == Path(t) and data_dir() == Path(t)
    set_data_dir(real)
    print("config ok")


if __name__ == "__main__":
    _selfcheck()
