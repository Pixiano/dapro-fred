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

DEFAULT_TIER = "standard"

CONTEXT_WINDOW = 16384
GPU_LAYERS = -1  # offload all layers to GPU; set lower if VRAM-limited

TEMPERATURE = 0.7
TOP_P = 0.9
# Generous headroom: some models (Nemotron/R1-style) emit a full
# <think>...</think> block before the real answer — too low a limit
# cuts them off mid-thought, leaking raw reasoning to the user.
MAX_TOKENS = 2000

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
TTS_VOICE = "David"

STT_MODEL_PATH = BASE_DIR / "models" / "vosk-model-small-en-us-0.15"
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

TOOLS_ENABLED = True
