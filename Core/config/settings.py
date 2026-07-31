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
# VAULT — FRED's identity and knowledge, outside this repo
# =========================================================
#
# persona.md/profile.md/rules.md are read directly (see
# personality/system_prompt.py); everything else in the vault reaches
# FRED through the vector store instead of being loaded wholesale — the
# vault is ~18,000 words, well past gemma4's 16,384-token context on its
# own. Not under git, not inside Project_FRED: a memory vault outliving
# any one project is the point.
VAULT_DIR = Path(r"C:\Users\Dhiraj Vatsal\VatsalDaPro\Projects\1_FRED_Memory\FRED")

# Read directly by personality/system_prompt.py, never through the vector
# router — orchestrator/vault_router.py imports this same tuple to exclude
# them from its index, so a file is never represented both ways at once
# (one list, not two that could drift apart).
VAULT_HARDCODED_FILES = ("persona.md", "profile.md", "rules.md")

# Unrestricted read access, with exactly one exception, measured rather
# than assumed. Every .md file that isn't hardcoded above is indexed —
# including MAP.md, INDEX.md and AGENT-BOOTSTRAP.md, all of which carry
# real content and were previously excluded for no good reason.
#
# _TEMPLATE.md is the exception, and it is not a restriction on access in
# any meaningful sense: those four files are blank skeletons whose entire
# text is placeholder prompts ("> One line: what this is and why it
# exists", "[decision] — reasoning"). They hold zero information about
# Vatsal, so indexing them grants FRED nothing — while actively costing
# it real answers. Measured on the live 313-chunk index:
#
#   "what are my current priorities"
#     with templates:    #1 jobs/_TEMPLATE.md (intro) 0.622
#                        active-priorities.md pushed to rank 7
#     without templates: active-priorities.md rises to rank 5
#
# Seven template chunks placed in the top 40 for that query. Short,
# generic, question-shaped placeholder text embeds close to almost any
# question, so they crowd out the file that actually answers it. Delete
# this set to index them anyway; the cost is the ranking above.
#
# NOTE: read-only. Nothing here grants FRED the ability to WRITE to the
# vault, and nothing should.
VAULT_EXCLUDED_FILES = {"_TEMPLATE.md"}

# Vector store for the vault router. Lives at <vault>/vectors/ so the
# index travels with the memory vault it describes rather than with
# Project_FRED — the vault is the thing that outlives any one project
# (see VAULT_DIR above), and a rebuilt-from-scratch index on every clone
# would otherwise cost a full re-embed. Contains only generated .json;
# it can never index itself because _iter_vault_files() globs *.md only.
# Re-embeds only files whose content hash changed since the last build
# (see VaultRouter.build).
VAULT_INDEX_DIR = VAULT_DIR / "vectors"
VAULT_INDEX_DIR.mkdir(parents=True, exist_ok=True)

# Raised 3 -> 6 when the floor went to 0.0. Three was calibrated against
# a 126-chunk index over 25 files; the vault has since grown to 313
# chunks over 60 files, so the same K now covers less than half as much
# of the corpus. Measured consequence at K=3: "what are my current
# priorities" returned projects/fred.md and personal/README.md while
# active-priorities.md — the file that literally answers it — sat at
# rank 5 and never reached the prompt. K=6 reaches it.
#
# Costs 6 x VAULT_CHUNK_INJECT_CHARS = ~2,520 chars (order of 600-700
# tokens, ~4% of gemma4's 16,384-token window) on every turn, since with
# floor 0.0 retrieval never returns fewer than K.
VAULT_RETRIEVAL_TOP_K = 6

# 0.0 = no floor. The user explicitly asked for unrestricted read access
# to the vault, so retrieval now always returns the top-K nearest chunks
# and never suppresses a turn. This deliberately abandons the previous
# 0.55 threshold, which was calibrated against the real vault (7 relevant
# queries, 6 plain-chat) and never worked cleanly anyway: relevant hits
# ran 0.533-0.736 while plain chat ran 0.340-0.661, with "tell me a joke"
# (0.661) outscoring the genuine board-exams match (0.533) against its
# own correct chunk. There was no separating value to find, so nothing of
# proven worth is being given up here.
#
# The tradeoff is real and unconditional: VAULT_RETRIEVAL_TOP_K chunks
# are injected on EVERY turn regardless of relevance, costing roughly
# 6 x VAULT_CHUNK_INJECT_CHARS (~2,520 chars, order of 600-700 tokens)
# of prompt budget per turn against gemma4's 16,384-token context, plus
# whatever confusion an off-topic chunk causes.
#
# -1.0 rather than 0.0 because vault_router now CENTERS embeddings
# (subtracts the corpus mean from both chunks and query — see the
# comment block above _center() there). Centered cosines sit on a lower
# scale than raw ones and can legitimately be negative, so a 0.0 cutoff
# would silently drop real hits rather than mean "no cutoff". -1.0 is
# the minimum a cosine can take, so it is unambiguously "no floor".
#
# Restoring a floor here is NOT the way to cut retrieval noise — that
# was measured and does not work (see _center()'s comment: relevant and
# chat score ranges overlap on every metric tried). Centering plus a
# reworded projects/fred.md is what actually moved the numbers. If more
# is needed, the cue gate at the call site in vault_router.retrieve()
# is the lever, not this constant.
VAULT_RETRIEVAL_FLOOR = -1.0
# Injected chunk text is capped here — a section can run a few hundred
# words, and top_k=3 of those uncapped would be a real chunk of the
# context budget on every hit.
VAULT_CHUNK_INJECT_CHARS = 420

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

    # Gemma 4 E4B — "effective 4B", 4.97GB on disk. Reasons noticeably
    # better than nano (gets the bat-and-ball trap right where Nemotron
    # is inconsistent) and is the only tier here with real, switchable
    # thinking. See THINKING_TIERS / CHAT_FORMAT_BY_TIER below: it needs
    # its own chat template, not the chatml-function-calling override.
    "gemma4": MODELS_DIR / "lmstudio-community" / "gemma-4-E4B-it-GGUF"
            / "gemma-4-E4B-it-Q4_K_M.gguf",
}

# Tiers whose chat template gates reasoning behind an enable_thinking
# flag, activated by putting this marker at the top of the system turn.
#
# llama-cpp-python has no way to pass arbitrary jinja variables through
# create_chat_completion, so enable_thinking cannot be set directly.
# Gemma 4's canonical template renders '<|think|>\n' at the start of the
# first system turn when that flag is true, so prepending the marker to
# the system prompt produces a byte-identical prompt by another route.
THINKING_TIERS = {"gemma4"}
THINKING_MARKER = "<|think|>"

# Per-tier chat_format override. None means "use the template embedded in
# the GGUF". The global default (chatml-function-calling) exists because
# most local GGUFs' own templates have no provision for tool definitions,
# but Gemma 4's canonical template handles tool_calls AND thinking, and
# forcing chatml over it discards both.
CHAT_FORMAT_BY_TIER = {
    "gemma4": None,
}

# Gemma 4 E4B, replacing "nano" (Nemotron 4B). Better reasoning and it
# is the only tier with genuinely switchable thinking, via the
# enable_thinking flag in its canonical chat template.
#
# Size still matters because GUI mode shares the GPU with Whisper. The
# original "standard" tier is what crashed: 8.9 (Qwen3.5-9B) + 1.1
# (embeddings) + ~1.5 (Whisper CUDA context) + ~2.5 (desktop/browser)
# = ~14GB of 16.3GB left too little for llama.cpp's compute buffers,
# which faults (0xc0000005) rather than raising a clean OOM error. At
# 4.97GB this tier totals ~10GB.
#
# Thinking costs latency — reasoning tokens are generated before any
# audio can start — and the reasoning must be stripped before speaking,
# which llm_client._strip_thinking handles for both the <think> style
# (Nemotron) and Gemma's <|channel>thought ... <channel|> style.
DEFAULT_TIER = "gemma4"

# One model, always DEFAULT_TIER. When False this bypasses the word-count
# heuristic in llm_client._pick_tier, which otherwise overrides
# DEFAULT_TIER: its fallback is "low", so short utterances went to
# gemma-2-2b regardless of this file, and a 25-44 word one pulled in
# "standard" (8.9GB). _get_model caches each tier it loads, so a mixed
# session could hold several models in VRAM simultaneously.
#
# Deliberately kept simple for now — picking a tier per request is worth
# revisiting later, driven by the same classifier as orchestrator/intent.py
# rather than by word count, so routing decisions stay consistent.
TIER_ROUTING_ENABLED = False

# Default context window, capped per-tier below. Asking for more than
# a model was trained on triggers llama.cpp's "training context
# overflow" warning and degrades quality — gemma-2-2b in particular
# only trained on 8192, so it must not get the global 16384.
CONTEXT_WINDOW = 16384

CONTEXT_WINDOW_BY_TIER = {
    "low": 8192,      # gemma-2-2b native context
    "gemma4": 16384,
    "nano": 16384,
    "standard": 16384,
    "deep": 16384,
    "extreme": 16384,
}

GPU_LAYERS = -1  # offload all layers to GPU; set lower if VRAM-limited

# =========================================================
# IDLE MODEL UNLOADING
# =========================================================
#
# FRED runs all day now (see install_startup.py), so holding every model
# resident costs VRAM you might want back. Measured on this machine:
#
#   LLM (Gemma 4 E4B) : +4814 MiB, reload 1.90s
#   Whisper turbo     : +1287 MiB, reload 2.94s (+~1.4s if re-warmed)
#
# Both runtimes genuinely release on close — 4566 and 1072 MiB reclaimed
# respectively, so this is worth doing rather than theatre. Together it's
# ~6.1GB, about 37% of a 16GB card.
#
# The reload cost is mostly hidden because loading starts on the hotkey
# press, concurrently with speech, rather than on demand after it. Audio
# capture needs no model at all, so recording begins instantly either way
# and only a very short utterance can outrun the load.
#
# Whisper waits for the LLM to have been gone a while before going itself:
# it's the more expensive one to reload and the one needed soonest after a
# keypress, so it earns a longer grace period.
LLM_IDLE_UNLOAD_SECONDS = 60 * 60          # 1 hour of no use
WHISPER_UNLOAD_AFTER_LLM_SECONDS = 15 * 60  # + 15 min => 1h15m total
MODEL_WATCHDOG_TICK_SECONDS = 30

# Re-run Whisper's warm-up decode after a reload. The first CUDA
# transcription in a fresh process cost ~14s against ~0.25s warm; after an
# unload the CUDA context survives, so a reload may not need the full
# warm-up. Left on because a slow first utterance is more annoying than
# 1.4s spent while the user is still talking.
WHISPER_WARMUP_ON_RELOAD = True

# Low for repeatability: the same question should get the same answer,
# which is what makes an assistant feel dependable.
#
# Do not expect accuracy from this. Measured across 3 arithmetic tasks,
# 8 samples each: 22/24 correct at 0.5 vs 21/24 at 0.2 — a one-trial
# difference, i.e. noise. A smaller run looked like a large effect
# (5/6 vs 3/6 vs 2/6) purely by chance, so ignore small samples here.
# Reasoning quality on this tier is bounded by the model, not by
# sampling; see the reasoning note in personality/system_prompt.py.
TEMPERATURE = 0.2
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

KOKORO_SPEED = 1.2

# Silence written before the first real samples of each utterance.
#
# Bluetooth outputs attenuate or ramp the first ~0.5-1s while the link
# wakes from idle and the codec stabilises, which swallowed the beginning
# of every reply. Measured and confirmed to be the device, not the
# pipeline: Kokoro generates the opening at full amplitude (higher RMS
# than the rest of the utterance), and the playback loop shows no
# underrun — writes never return early and settle at 41.5ms against a
# 42.7ms block. So the fix is to give the link something inaudible to
# ramp through.
#
# Costs this much extra time-to-first-word, once per utterance. Set to 0
# on a wired output, where none of this applies.
TTS_PREROLL_SEC = 0.35

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

# Caveat at small DEFAULT_TIER values: these ~30 tool definitions were
# tuned against the 9B, and small models select among them much worse.
# On Nemotron 4B, "Hello Fred, how are you doing?" chose open_website and
# launched google.com — which is what motivated the intent gate in
# orchestrator/intent.py, so that conversational turns are never shown
# the tools at all. Genuine action requests still depend on the model
# picking the right tool, and that accuracy does fall with size. Raising
# DEFAULT_TIER fixes routing but reintroduces the VRAM pressure that was
# crashing llama.cpp, so the two have to be chosen together. Set False to
# skip the tool loop entirely and just generate a reply.
TOOLS_ENABLED = True
