"""Core settings: one typed object, defaults in code, state outside the repo, env overrides.

Sockets
  in : env FRED_DATA (state/secrets/personal data dir, default ~/FRED_Private), env FRED_<FIELD> (any field below, upper-cased)
  out: get() -> Settings (cached); data_dir() -> Path; set_data_dir(path) re-points everything (tests use a temp dir, so test output never reaches the real logs)
Every threshold the later layers need is a field here, never a literal in the code that uses it.
Self-check: python config.py
"""
import os
import tempfile
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
    max_tool_rounds: int = 4                          # P3 loop
    max_calls_per_round: int = 4
    tool_timeout_s: float = 15.0                      # default per tool call
    gpu_default_timeout_s: float = 120.0
    judge_threshold: float = 0.6                      # proactive judge: classifier p needed to speak (invented-case sweep: 0.5 P .96 R .77, 0.6 P .97 R .62, 0.7 P 1.0 R .47); retune on the shadow log
    judge_cooldown_s: float = 120.0                   # the judge stays quiet this long after FRED last spoke
    judge_every_s: float = 30.0                       # shadow look interval (also looks at once on a due reminder or a warning)
    event_ring_s: float = 600.0                       # EventLog in-memory window (~10 min)
    log_max_bytes: int = 20_000_000                   # one jsonl before it rotates
    # --- audio (P1a) ---
    sample_rate: int = 16000
    mic_device: str = ""                              # input device name substring; "" = system default
    mic_ring_s: float = 6.0                           # raw audio kept for pre-roll / pre-trigger
    wakeword_model: str = str(CORE_DIR.parent.parent.parent / "Old" / "Core" / "models" / "wakeword" / "hey_fred.onnx")   # FROZEN model, read in place, never copied or swapped
    wakeword_threshold: float = 0.35
    wake_cooldown_s: float = 2.0                      # refuse a second fire this soon after one
    wake_near_miss: float = 0.10                      # scores at/above this but under threshold are logged as audio.wake_near
    agc_target_peak: float = 0.7
    agc_max_gain: float = 100.0
    agc_min_peak: float = 0.01                        # quieter than this: hold the gain, do not amplify noise
    agc_smoothing: float = 0.3
    vad_mode: int = 2                                 # webrtcvad aggressiveness 0-3
    vad_onset_ms: int = 100                           # voiced time needed to call speech started
    vad_hangover_ms: int = 200                        # silence needed to call speech ended (L1 adds Smart Turn on top)
    whisper_model: str = "large-v3-turbo"
    whisper_language: str = "en"
    whisper_beam: int = 5
    whisper_device: str = "cuda"                      # GPU-only rule: change only for an explicit CPU test (FRED_WHISPER_DEVICE=cpu FRED_WHISPER_COMPUTE=int8)
    whisper_compute: str = "float16"
    whisper_prompt: str = "Fred. Call Mom. Hang up. What's on my agenda? Open the file. Set a timer. Search the web."
    stt_min_peak: float = 0.02                        # a buffer whose peak stays under this is silence: no Whisper pass
    max_utterance_s: float = 60.0
    # --- screen (P1b) ---
    screen_interval_s: float = 0.2                    # capture period; bench_screen.py compares 0.1 / 0.2 / 0.5 / 0.7 / 1.0 / 1.2 (0.2: 2.7% of a core, every flash >= 0.2 s seen)
    camera_index: int = 1                             # cv2 index; the real 2K webcam (PHOCUS 40A) is 1, indices 0 and 2 are frozen virtual cameras; --probe in senses/camera.py checks
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
    voice_id_enabled: bool = False                    # the speaker gate is OFF until it is trained on the captured live-mic recordings (2026-10-07: 89% of real live clips fell below the threshold); FRED_VOICE_ID_ENABLED=1 turns it on
    voice_threshold: float = 0.30                     # cosine similarity at or above = Vatsal. Measured on 1-s real clips: same-speaker median 0.42, synthetic other voices max 0.23; 0.5 would reject 78% of his clips (tests/voice_eval.py); retune with real other speakers
    voice_min_s: float = 1.0                          # shorter clips are too short to judge (score None); 1 s clips scored median 0.50, 2 s 0.62, 0.6 s 0.40 (tests/voice_eval.py)
    voice_refs_cap: int = 20                          # enrolled voice references kept (most diverse)
    stt_save_audio: bool = True                       # save each final utterance + transcript to <FRED_DATA>/voice_data (real-voice data collection)
    clock_tick_s: float = 0.16                        # interaction clock tick
    eot_silence_ms: int = 200                         # VAD silence before Smart Turn is consulted
    eot_hard_cap_s: float = 0.9                       # silence that ends a turn regardless of Smart Turn (Vatsal 2026-10-06: 0.9 instead of the planned 1.2)
    barge_ms: int = 300                               # speech while FRED talks that counts as a real interruption
    barge_level: float = 1500.0                       # mic RMS (int16 units) needed to barge in when not on headphones
    followup_s: float = 8.0                           # open-mic window after FRED speaks
    wake_open_s: float = 8.0                          # a wake word this long before (or during) speech addresses it
    max_tool_rounds: int = 4                          # model <-> tool round trips per turn
    max_calls_per_round: int = 4                      # tool calls executed from one model message
    sir_rate: float = 0.0                             # share of replies, interim and talker lines that end with "sir" (engine/sir.py); the 2-3 word fillers stay bare. OFF until the P8 tuning pass, then 0.85 (Vatsal's target)
    tts_enabled: bool = True                          # False = nothing is spoken (text only)
    kokoro_model: str = str(CORE_DIR.parent.parent.parent / "Old" / "Core" / "models" / "kokoro" / "kokoro-v1.0.onnx")      # read in place, never copied
    kokoro_voices: str = str(CORE_DIR.parent.parent.parent / "Old" / "Core" / "models" / "kokoro" / "voices-v1.0.bin")
    tts_voice: str = "am_michael"                     # the voice Vatsal picked in the old build
    tts_speed: float = 1.0                            # 1x only (Vatsal, 2026-10-07; the old build used 1.2)
    tts_preroll_s: float = 1.5                        # silence written first: a Bluetooth link ramps its volume at the start (measured on Vatsal's headphones); 0 on a wired output
    tts_postroll_s: float = 1.0                       # silence after the last word: the link buffers and would clip the end; 0 on a wired output
    filler_after_s: float = 0.8                       # a reply slower than this gets a 2-3 word spoken filler ("On it.") while the brain works
    talker: str = "openbmb/minicpm5-2b"               # small model for tool-free chat turns and for lockdown (playground slot talk); "" = the brain answers everything
    gmail_token_path: str = ""                        # OAuth token file of the user's Gmail client; empty = ~/VatsalDaPro/Keys/FRED_Gmail_token.json (outside the repo)
    weather_city: str = ""                            # default city for the weather skill when none is named (set it in your own settings, not in the repo)
    scamwatch_enabled: bool = True                    # warn (toast + bus event) when the screen shows a phishing or scam page; proactive/scamwatch.py
    scamwatch_cooldown_s: float = 1800.0              # the same domain is not warned about again for this long
    scamwatch_max_per_hour: int = 6
    pill_topmost_every_s: float = 1.0                 # while the pill is visible, re-assert it above other topmost windows (a borderless-fullscreen app or a video player can take the top spot after the pill appeared); 0 = only on show
    talk_max_tokens: int = 200                        # cap on the small talker's replies: spoken answers are short, and a 2B that does not stop must not run for minutes
    ambient_enabled: bool = True                      # skills with an ambient() hook may add a digest (web facts about a named person/event) before the model answers; False = never
    ambient_timeout_s: float = 10.0                   # an ambient digest slower than this is dropped, the answer goes ahead without it
    time_note_every: int = 5                          # turns (proactive ones too) without the time tool after which the model is quietly told the time, so it can suggest winding down; 0 = never
    lockdown_tools: tuple = ("time", "days_until", "calculate")   # the only tools that exist while locked down
    history_turns: int = 8                            # user/assistant pairs kept as conversation context
    engine_think: bool = False                        # reasoning on tool/chat turns (off: the thinking cap; arithmetic goes to the calculator tool)
    semantic_floor: float = 0.65                      # embedding similarity at which a tool counts as clearly requested
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


def _coerce(raw: str, like):
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
