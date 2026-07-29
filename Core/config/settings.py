# Core/config/settings.py

from pathlib import Path

# =========================================================
# PROJECT PATHS
# =========================================================

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

MEMORY_DIR = DATA_DIR / "memory"
MEMORY_DIR.mkdir(exist_ok=True)

INDEX_DIR = DATA_DIR / "indexes"
INDEX_DIR.mkdir(exist_ok=True)

LOG_DIR = DATA_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

# One-off reminders persist here so they survive a restart — see
# orchestrator/scheduler.py. File-watches stay in-memory only.
SCHEDULER_DB_PATH = DATA_DIR / "reminders.sqlite"

# =========================================================
# USER SETTINGS
# =========================================================

DEFAULT_USERNAME = "default_user"

# =========================================================
# LLM SETTINGS — fully local inference via llama.cpp
# =========================================================
#
# No server, no API, nothing leaves this machine. Models are loaded
# directly from disk by llama-cpp-python.

MODELS_DIR = Path(
    r"C:\Users\Dhiraj Vatsal\.lmstudio\models"
)

MODEL_TIERS = {
    # tremendously quick - simple greetings, etc
    "low": MODELS_DIR / "lmstudio-community" / "gemma-2-2b-it-GGUF"
            / "gemma-2-2b-it.gguf",
    
    # routing, OS commands, quick facts — fast, cheap
    "nano": MODELS_DIR / "lmstudio-community" / "NVIDIA-Nemotron-3-Nano-4B-GGUF"
             / "NVIDIA-Nemotron-3-Nano-4B-Q8_0.gguf",

    # default daily conversation
    "standard": MODELS_DIR / "lmstudio-community" / "Qwen3.5-9B-GGUF"
                / "Qwen3.5-9B-Q8_0.gguf",

    # reasoning, coding, planning
    "deep": MODELS_DIR / "lmstudio-community" / "Qwen3-14B-GGUF"
            / "Qwen3-14B-Q4_K_M.gguf",

    # extreme uses
    "extreme": MODELS_DIR / "lmstudio-community" / "gpt-oss-20b-GGUF"
            / "gpt-oss-20b.gguf",
}

# "nano" (Nemotron 4B, 3.9GB) rather than "standard" (Qwen3.5-9B, 8.9GB)
# because GUI mode shares the GPU with Whisper. With the 9B loaded the
# budget was: 8.9 (LLM) + 1.1 (embeddings) + ~1.5 (Whisper CUDA context)
# + ~2.5 (desktop/browser) = ~14GB of 16.3GB, leaving too little for
# llama.cpp's compute buffers — which showed up as a hard access
# violation (0xc0000005) mid-turn rather than a clean OOM error.
# Nemotron brings that to ~9GB and leaves real headroom.
DEFAULT_TIER = "nano"

# Default context window, capped per-tier below. Asking for more than
# a model was trained on triggers llama.cpp's "training context
# overflow" warning and degrades quality — gemma-2-2b in particular
# only trained on 8192, so it must not get the global 16384.
CONTEXT_WINDOW = 16384

CONTEXT_WINDOW_BY_TIER = {
    "low": 8192,      # gemma-2-2b native context
    "nano": 16384,
    "standard": 16384,
    "deep": 16384,
    "extreme": 16384,
}

GPU_LAYERS = -1  # offload all layers to GPU; set lower if VRAM-limited

TEMPERATURE = 0.5
TOP_P = 0.9
# Generous headroom: some models (Nemotron/R1-style) emit a full
# <think>...</think> block before the real answer — too low a limit
# cuts them off mid-thought, leaking raw reasoning to the user.
MAX_TOKENS = 4096

# =========================================================
# MEMORY SETTINGS — fully local embeddings via llama.cpp
# =========================================================

EMBEDDING_MODEL_PATH = (
    MODELS_DIR / "Qwen" / "Qwen3-Embedding-0.6B-GGUF"
    / "Qwen3-Embedding-0.6B-f16.gguf"
)

MEMORY_TOP_K = 5

SHORT_TERM_MEMORY_LIMIT = 10

# =========================================================
# AUDIO SETTINGS
# =========================================================

TTS_ENABLED = True
STT_ENABLED = True

# Name fragment matched against installed Windows SAPI voices
# (run: python -c "import pyttsx3; [print(v.name) for v in pyttsx3.init().getProperty('voices')]")
# Only used by the legacy SAPI path in audio/tts.py, which the CLI still
# runs. GUI mode speaks through Kokoro (audio/tts_kokoro.py) instead.
TTS_VOICE = "David"

# =========================================================
# KOKORO TTS — GUI mode's voice (audio/tts_kokoro.py)
# =========================================================
#
# Replaces SAPI for GUI mode. Two reasons it's worth the extra model
# files: SAPI's voices are a generation behind, and — the part that
# actually changed the UI — pyttsx3/SAPI never exposes raw PCM, only
# word-boundary events, so the pill's speaking waveform could not react
# to real amplitude. Kokoro hands back float32 samples, so it can.
#
# Model files are release downloads, not pip data, and are too big for
# git (see .gitignore):
#   https://github.com/thewh1teagle/kokoro-onnx/releases (model-v1.0)
KOKORO_DIR = BASE_DIR / "models" / "kokoro"
KOKORO_MODEL_PATH = KOKORO_DIR / "kokoro-v1.0.onnx"
KOKORO_VOICES_PATH = KOKORO_DIR / "voices-v1.0.bin"

# Any name from Kokoro.get_voices(). Voicepacks are plain embedding
# tensors, so blending two of them (see KOKORO_VOICE_BLEND) is the
# supported way to get a voice that isn't in the stock list — there is
# no published finetuning pipeline for this model.
KOKORO_VOICE = "am_michael"

# Optional ("other_voice", weight) to linearly blend into KOKORO_VOICE,
# weight being the *other* voice's share. None = use KOKORO_VOICE alone.
KOKORO_VOICE_BLEND = None

KOKORO_SPEED = 1.0

# =========================================================
# FASTER-WHISPER STT — GUI mode's ears (audio/stt_whisper.py)
# =========================================================
#
# GUI mode is hold-to-talk, which hands Whisper exactly what it wants:
# a complete, pre-segmented utterance (key release *is* the endpoint).
# That removes the reason Vosk was preferable — Vosk streams partial
# results, Whisper can't, but nothing in the pill UI needs partials.
# The CLI still uses Vosk via audio/stt.py.
#
# Auto-downloaded to the HF cache on first use, not stored in the repo.
WHISPER_MODEL = "large-v3-turbo"

# "auto" prefers CUDA when CTranslate2 can find CUDA+cuDNN, else CPU.
# Note CTranslate2 does its own CUDA discovery and does not use torch,
# so a CPU-only torch install is irrelevant here.
WHISPER_DEVICE = "auto"
WHISPER_COMPUTE_TYPE = "auto"
WHISPER_LANGUAGE = "en"

# Hard ceiling on one held utterance, so a stuck key can't grow the
# recording buffer without bound.
MAX_UTTERANCE_SECONDS = 60

# Indian-English-tuned model (1.5GB). Only invoked on-demand after the
# wake word (or a typed command) triggers a full transcription — never
# runs continuously — so the larger size doesn't affect the overlay's
# idle CPU budget. Not committed to git (see .gitignore): too large for
# GitHub's 100MB per-file limit, same as the Legacy copy it came from.
STT_MODEL_PATH = BASE_DIR / "models" / "vosk-model-en-in-0.5"
STT_SAMPLE_RATE = 16000

# Wake detection via Vosk transcription + text matching, not a
# trained acoustic model (openWakeWord only ships fixed pretrained
# phrases like "hey jarvis" - "Fred"/"F" would need training a new
# model from scratch). Trade-off: short/common words below mean this
# triggers easily, including on background speech - that's the
# intentional choice here over precision.
WAKE_WORD_ENABLED = True

WAKE_PHRASES = [
    "hey fred",
    "fred",
    "freddie",
    "hey freddie",
    "hi fred",
    "hello fred",
    "what's up fred",
    "whats up fredie",
    "yo fredie",
]

# =========================================================
# TOOL SETTINGS
# =========================================================

# Off for now. With the nano tier (Nemotron 4B) the tool router misfires
# badly — "Hello Fred, how are you doing?" selected open_website and
# launched google.com. Small models are much weaker at tool selection
# than the 9B this was tuned against, so tool use and DEFAULT_TIER need
# revisiting together, not separately. When TOOLS_ENABLED is False the
# orchestrator skips the tool loop entirely and just generates a reply
# (see orchestrator.py::_generate_with_tools).
TOOLS_ENABLED = False
