# Core/config/settings.py

import os
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
#
# active-priorities.md joined 2026-08-03. Its own docstring says it's
# meant to be "scanned at the start of every session", but it was only
# retrieval-gated — confirmed broken: asked "what abt the general active
# priorities", its real content (## Open, ## Waiting on Vatsal) scored
# -0.002 to -0.141 (centered cosine) against that query and never reached
# the model; only its meta/intro paragraph scored well (0.389), so FRED
# answered from personal/goals.md instead, which happened to also be
# about priorities. The chunker already prefixes each section with
# "Active Priorities — <heading>" before embedding, so the title signal
# is there — it's just outweighed by the section's own technical content
# in a small local embedder. Costs ~8KB extra on every turn (this file
# alone is larger than persona+profile+rules combined) — accepted
# deliberately for a file whose whole job is "never miss this".
VAULT_HARDCODED_FILES = ("persona.md", "profile.md", "rules.md", "active-priorities.md")

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

# File types the vault indexer reads. PDFs were added 2026-08-04: two
# had been sitting in personal/ (workout_split_June.pdf,
# skill_progressions.pdf) since before the index existed, holding the
# actual training split — the indexer globbed "*.md" only, so FRED could
# not see them at all and answered questions about the routine from the
# prose summary in fitness.md instead of the plan itself.
#
# Adding a suffix here is not free: whatever reads it must produce text
# (see vault_router._chunk_file), and tools/vault_files.py opens indexed
# paths as UTF-8 text, so a new binary format needs a branch there too.
VAULT_INDEXED_SUFFIXES = {".md", ".pdf"}

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
#
# Raised 420 -> 1200 on 2026-08-01 after a confirmed fabrication: asked
# to review his fitness progress, FRED invented weight, BMI, body fat and
# a bloodwork panel. personal/fitness.md's Biometrics section is a
# markdown table that ran past 420 chars, so the model received it cut
# off mid-table and completed the pattern from imagination. 1200 clears
# every table in the sampled personal/ files. This is a real context-cost
# increase (6 chunks x 1200 = ~7.2k chars, order of 1.8k tokens against a
# 16,384-token window) accepted deliberately: truncating structured data
# mid-row is what invited the invention, and inventing personal health
# data is a far worse failure than a tighter context budget. Paired with
# the explicit anti-fabrication instruction in orchestrator._build_messages
# — neither is sufficient alone.
VAULT_CHUNK_INJECT_CHARS = 1200

# =========================================================
# PROACTIVE CHECKS — Observation B, 2026-08-01 feedback session
# =========================================================
#
# Three periodic checks over orchestrator/proactive_checks.py, each
# firing through utils/notifier.notify at most once per stretch (see
# that module's dedup state) so this never becomes the nagging persona.md
# explicitly warns against.

# How often the background checks run at all. Cheap (frontmatter reads,
# one Windows API call) — no reason to poll more often than this.
PROACTIVE_CHECK_INTERVAL_MINUTES = 15

# active-priorities.md's own `updated:` frontmatter date, not a per-item
# parse of its prose bullets — see proactive_checks.py for why a
# whole-file signal was chosen over trying to date-parse free text.
PROACTIVE_STALE_DAYS = 7

# Continuous machine use (no idle gap at least this long) before FRED
# flags a break. Reset by any idle gap at or above this length — that's
# what counts as "he took a break," not a fixed clock reset.
PROACTIVE_BREAK_IDLE_MINUTES = 15
PROACTIVE_LONG_SESSION_HOURS = 3

# A vault file's optional `deadline: YYYY-MM-DD` frontmatter field,
# flagged once it's within this many days out. No vault file uses this
# field yet (board exam dates aren't recorded as of 2026-08-01), so
# this check is currently a no-op scan — the read path exists for
# whenever a real deadline gets added, not speculative parsing of dates
# from prose.
PROACTIVE_DEADLINE_WARN_DAYS = 7

# How far ahead a task whose text says "due <day>" gets raised. Two
# days rather than the 7 used for frontmatter deadlines: a daily-note
# task is a same-week errand ("Chemistry journal completion — due
# Thursday in school"), and warning a full week out would fire on
# something that isn't actionable yet and train him to ignore it.
PROACTIVE_TASK_DUE_DAYS = 2

PROACTIVE_STATE_PATH = DATA_DIR / "proactive_state.json"

# =========================================================
# SCREEN WATCHER — 2026-08-02 feedback session
# =========================================================
#
# Background screen-content awareness: after enough idle time, a
# separate OS PROCESS (not a thread — see screen_watcher.py's module
# docstring for why) periodically screenshots the desktop, describes it
# with the Vision tier, and caches the result for an on-demand "what's
# on my screen" tool. Killed immediately on hotkey press so it can
# never compete with a real conversation turn for the model or the GPU.

# How long since the hotkey was last used before the watcher is allowed
# to start. Short enough to actually be useful, long enough that normal
# pauses mid-conversation don't repeatedly spin it up only to kill it
# again seconds later.
SCREEN_WATCHER_IDLE_MINUTES = 5

# How often the watcher re-screenshots while it's running.
SCREEN_WATCHER_INTERVAL_SECONDS = 60

# Cross-process coordination. The main process's LLMClient writes its
# currently-resident tier here on every load/unload; the watcher child
# process reads it before loading its OWN model and skips a cycle
# entirely if anything is already resident in the main process.
# Necessary, not paranoia: the main LLM's idle-unload is a full hour
# (LLM_IDLE_UNLOAD_SECONDS below), so for nearly all of a 5-minute
# watcher window the conversation model is still loaded — without this
# check, Standard (~9.9GB) and Vision (a further multi-GB) would
# frequently both try to be resident at once, on a 16310 MiB card that
# has a documented history of crashing exactly that way.
LLM_STATUS_PATH = DATA_DIR / "llm_status.json"

# Where the watcher's latest description lives. Only ever short text —
# see screen_watcher.py for why the screenshot image itself never
# touches disk.
SCREEN_CONTEXT_PATH = DATA_DIR / "screen_context.json"

# A description older than this is treated as stale rather than shown
# as current — the watcher may have been killed mid-cycle by a hotkey
# press, or simply not run yet this session.
SCREEN_CONTEXT_MAX_AGE_SECONDS = 300

# =========================================================
# LLM SETTINGS — fully local inference via llama.cpp
# =========================================================
#
# No server, no API, nothing leaves this machine. Models are loaded
# directly from disk by llama-cpp-python.

MODELS_DIR = Path(
    r"C:\Users\Dhiraj Vatsal\.lmstudio\models"
)

# Revised 2026-08-01, three times in one day. Pass 1 matched MODEL_TIERS
# to the real contents of MODELS_DIR. Pass 2 renamed to Title Case and
# dropped Mistral/Gemma-12B, back down to 3 tiers. Pass 3, this one:
# Qwen3-8B promoted to "Standard" (DEFAULT_TIER) with thinking on, on
# direct instruction after the user's own testing. The old Standard
# (Qwen3.5-4B) is demoted to "Backup", kept configured rather than
# removed — not currently reachable by anything (TIER_ROUTING_ENABLED
# is False, so DEFAULT_TIER is the only tier ever selected), but sitting
# here ready if Standard's new latency ever needs a fast fallback.
MODEL_TIERS = {
    # Qwen3-8B at Q4_K_M — DEFAULT_TIER as of this revision. The main/
    # always-resident model. Thinking ON: unlike Qwen3.5-4B, this
    # template's enable_thinking guard only pre-closes the block when
    # explicitly set to false (`{%- if enable_thinking is defined and
    # enable_thinking is false %}`), and llama-cpp-python can never
    # define that variable — so the guard never fires and reasoning
    # runs by default, no marker needed. Confirmed at the byte level,
    # same as Deep below. This is a real, accepted latency tradeoff on
    # the tier used every single turn: expect reasoning cost closer to
    # Deep's ~13s-for-a-trivial-reply than the old Standard's near-
    # instant one. Chosen anyway, deliberately, after live testing.
    "Standard": MODELS_DIR / "lmstudio-community" / "Qwen3-8B-GGUF"
            / "Qwen3-8B-Q4_K_M.gguf",

    # Qwen3.5-4B at Q6_K — demoted from Standard this revision. Reasoning
    # off by default (its own template pre-closes <think></think>
    # unconditionally when the kwarg is undefined, unlike Qwen3-8B/Deep
    # above/below) — kept specifically as the fast option if Standard's
    # new latency ever needs a quick fallback.
    "Backup": MODELS_DIR / "unsloth" / "Qwen3.5-4B-GGUF"
            / "Qwen3.5-4B-Q6_K.gguf",

    # Qwen3-14B-Q4_K_M — the "bigger model" tier: reasoning, coding,
    # planning, long/heavy requests. Also the vault-ingest converter's
    # model (see Core/web/vault_ingest.py — update that reference if
    # this key ever changes again).
    "Deep": MODELS_DIR / "lmstudio-community" / "Qwen3-14B-GGUF"
            / "Qwen3-14B-Q4_K_M.gguf",

    # gpt-oss-20b — 11.28GB on disk, the largest configured tier. Rare/
    # heavy use only. VRAM math has not been done for this one the way
    # it has for Standard/Backup/Deep, so treat as unverified until it is.
    "Extreme": MODELS_DIR / "lmstudio-community" / "gpt-oss-20b-GGUF"
            / "gpt-oss-20b.gguf",

    # Bonsai-27B: NOT added. Only mmproj-Bonsai-27B-BF16.gguf (0.87GB,
    # the vision projector) has downloaded so far — the actual model
    # weights aren't on disk yet, still downloading as of 2026-08-02.
    # Vision below deliberately targets gemma-4-12B instead, since it's
    # actually usable today; swap this key's path to Bonsai's once its
    # weights land (VRAM budget unverified for a 27B model — re-check
    # before swapping, don't assume it fits the way 12B was measured to).
    #
    # Vision tier — gemma-4-12B-it-QAT, background screen-analysis only
    # (Core/vision/screen_watcher.py), never the conversation pipeline.
    # Confirmed present on disk: weights (6.5GB) + its own mmproj vision
    # projector (168MB) — this is the ONLY configured model with a
    # matching mmproj file, so it's the only one that can actually see
    # an image. llama-cpp-python (0.3.31, this venv) confirmed to ship
    # Gemma4ChatHandler for it.
    "Vision": MODELS_DIR / "lmstudio-community" / "gemma-4-12B-it-QAT-GGUF"
            / "gemma-4-12B-it-QAT-Q4_0.gguf",
}

# Cloud cascade, 2026-08-03 — deliberately a SEPARATE system from
# MODEL_TIERS/DEFAULT_TIER above, not a replacement wired into it. The
# local tier system (Standard = Qwen3-8B, thinking-on, ~13s/turn) is
# left exactly as it was and untouched by this: llm_client.py tries
# CLOUD_PROVIDERS first, in order, and only falls through to the
# original local _pick_tier/_get_model flow — completely unmodified —
# if every cloud provider fails. That local flow is the tertiary rung,
# reached with no special-casing: it just runs as if the cloud attempt
# never happened.
#
# Checked before wiring this in, both providers' own data-processing
# terms: neither trains on API inputs/outputs.
#   Groq     — no retention by default; the only exception is a
#              transient abuse/troubleshooting log capped at 30 days,
#              itself opt-outable via GroqCloud's zero-data-retention
#              console setting (console.groq.com/docs/your-data).
#   Cerebras — no retention at all; inputs/outputs are processed for
#              the response and immediately discarded (cerebras.ai/privacy).
# Real exposure either way is "this leaves the machine and is processed
# on a third party's servers," not "they train on your vault" —
# acceptable for FRED's own conversation content, not a reason to start
# routing raw financial/ID documents through it.
#
# Order matches the user's own account setup: Groq main (free, until
# the $5 threshold in ~14 days), Cerebras secondary (smaller free-tier
# quota — 5 req/min, 2,400 req/day, 1M tokens/day — but same
# no-retention terms). Each entry tried in order in llm_client.py; only
# once ALL of these fail does control reach the original local tier
# system.
#
# openai/gpt-oss-120b picked for BOTH providers deliberately (it's on
# both catalogs) rather than a different model per provider: same
# reasoning-channel syntax on either leg, and TIER_PROMPT_MARKERS /
# _strip_thinking already parse gpt-oss's <|channel|> format correctly
# (Extreme tier below is gpt-oss-20b, same syntax, already confirmed
# live) — a provider failover mid-outage changes nothing about how the
# reply gets parsed.
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
CEREBRAS_API_KEY = os.environ.get("CEREBRAS_API_KEY")

CLOUD_PROVIDERS = [
    {
        "name": "groq",
        "base_url": "https://api.groq.com/openai/v1/chat/completions",
        "api_key": GROQ_API_KEY,
        "model": "openai/gpt-oss-120b",
    },
    {
        "name": "cerebras",
        "base_url": "https://api.cerebras.ai/v1/chat/completions",
        "api_key": CEREBRAS_API_KEY,
        "model": "gpt-oss-120b",
    },
]

# Whether retrieved personal/ or people/ content pins a turn to the
# local model (utils/sensitive.py + the orchestrator's per-turn latch).
#
# Turned OFF 2026-08-04 at Vatsal's explicit instruction: "allow the
# APIs to access everything, keep local as last resort". This overrides
# the vault's rules.md line about never sending personal/ to a hosted
# model — his data, his call, and both providers' terms are in the
# comment above. The enforcement machinery is left fully wired
# (LLMClient.local_only, sensitive.py, SENSITIVE_TOOLS); flip this back
# to True to re-arm it in one line.
SENSITIVE_LOCAL_ONLY = False

# mmproj (vision projector) path, only for tiers that are actually
# multimodal — absence here means "this tier has no vision handler",
# checked explicitly in _get_model rather than assumed.
MMPROJ_PATH_BY_TIER = {
    "Vision": MODELS_DIR / "lmstudio-community" / "gemma-4-12B-it-QAT-GGUF"
            / "mmproj-gemma-4-12B-it-QAT-BF16.gguf",
}

# Per-tier literal text injected into the system turn, because
# llama-cpp-python has no way to pass arbitrary jinja template kwargs
# (enable_thinking, reasoning_effort, ...) through create_chat_completion.
# Each entry below reproduces what the real kwarg would have rendered,
# confirmed by reading each tier's own embedded chat_template directly —
# not guessed per-tier, though "how the model responds to it" is only
# confirmed live for the ones _apply_thinking's docstring says so.
TIER_PROMPT_MARKERS = {
    # gpt-oss-20b's template (confirmed present, unlike Standard/Deep,
    # which have none of their own): reasoning_effort defaults to
    # "medium" and unconditionally renders "Reasoning: medium\n\n" at a
    # fixed point in the system turn when the kwarg is undefined —
    # exact jinja: `{%- if reasoning_effort is not defined %}
    # {%- set reasoning_effort = "medium" %}{%- endif %}`. Unlike Gemma
    # 4's marker below, this default line is NOT conditional on
    # anything we can suppress — it always renders. This entry is a
    # best-effort override placed alongside it, not a confirmed
    # replacement: not yet tested live whether the model follows this
    # injected "Reasoning: high" over its own auto-generated "Reasoning:
    # medium", since both may end up present in the same prompt.
    "Extreme": "Reasoning: high",
}

# Per-tier chat_format override. None means "use the template embedded in
# the GGUF". The global default (chatml-function-calling) exists because
# most local GGUFs' own templates have no provision for tool definitions,
# and would otherwise silently discard tool_calls support.
CHAT_FORMAT_BY_TIER = {
    # Qwen3-8B ships a template that handles tool_calls AND reasoning, so
    # it keeps its own rather than having chatml-function-calling forced
    # over the top of it.
    "Standard": None,
    # Same reasoning applies to Qwen3.5-4B's own template.
    "Backup": None,
    # Same reasoning applies to Qwen3-14B's own template.
    "Deep": None,
    # Extreme (gpt-oss-20b) deliberately absent: its Harmony-format
    # template has its own tool-calling/channel conventions that
    # chatml-function-calling would replace, which is very likely worse
    # for a model specifically trained on that format — but this has
    # not been tested either way, only reasoned about.
}

DEFAULT_TIER = "Standard"

# DEFAULT_TIER has moved twice on 2026-08-01, in opposite directions.
#
# Morning: gemma4 (Gemma 4 E4B) -> Qwen3.5-4B, FOR speed. Measured on
# the same query ("tell me something interesting about black holes"),
# same system prompt, model already warm:
#
#                       gemma4 E4B Q4_K_M     Qwen3.5-4B Q6_K
#   file size                     4.97 GB             3.28 GB
#   load                            1.92s               1.68s
#   first token                     1.19s               1.27s
#   silent reasoning block          5.87s               none
#   full generation      8.72s / 589 tok     3.74s / 204 tok
#
# 2.3x faster end to end, 1.7 GB less VRAM. Qwen3.5-4B answered with no
# <think> block because its template pre-closes one unless
# enable_thinking is passed as a jinja variable, which llama-cpp-python
# cannot do — reasoning was off by default, structurally, not a choice.
#
# Evening: Qwen3.5-4B -> Qwen3-8B, DESPITE speed, on direct instruction
# after the user's own live testing. Qwen3-8B's template has the
# opposite default: its enable_thinking guard only fires when explicitly
# set to false, which llama-cpp-python can never do — so reasoning runs
# by default here. Expect the morning's speed win to be substantially
# undone; Qwen3.5-4B is kept configured as "Backup" specifically for
# this reason, in case Standard's new latency ever needs a fast option.

# One model, always DEFAULT_TIER. When False this bypasses the word-count
# heuristic in llm_client._pick_tier. That heuristic itself still names
# only the 3 tiers now configured (previously also referenced "nano" and
# "low", both deleted from MODEL_TIERS in the 2026-08-01 revision) — kept
# in sync with this file, but genuinely dormant until re-enabled.
#
# Deliberately kept simple for now — picking a tier per request is worth
# revisiting later, likely as part of the planned "offer the bigger
# model, ask before switching" flow rather than silent word-count
# routing, so a tier change is never a surprise mid-conversation.
TIER_ROUTING_ENABLED = False

# Default context window, capped per-tier below. Asking for more than
# a model was trained on triggers llama.cpp's "training context
# overflow" warning and degrades quality.
CONTEXT_WINDOW = 16384

# Standard raised 16384 -> 24576 on 2026-08-02. Qwen3-8B trains at
# 32768, so 16384 left real capacity unused, and the vault-chunk budget
# went up the same day (VAULT_CHUNK_INJECT_CHARS 420 -> 1200) which eats
# into every turn's window.
#
# Measured on the venv's CUDA build (the interpreter FRED actually runs
# — see the note on GPU_LAYERS), with a 4026-token prompt:
#
#   n_ctx    VRAM peak    free after    generate
#   16384     8172 MiB     6954 MiB       4.3s
#   24576     9877 MiB     5361 MiB       4.2s
#   32768    11610 MiB     3522 MiB       4.4s
#
# Speed is flat across all three — this is purely a VRAM decision. The
# full budget is what ruled 32768 out: Whisper turbo adds ~1287 MiB when
# resident, and the non-FRED baseline on this box has been observed
# anywhere from 1.2 to 3.1 GB depending on what's open. At 32768 the
# worst case lands within ~300 MiB of the 16310 MiB card, and this
# machine has a documented history of hard access-violation crashes
# (0xc0000005) from exactly that. 24576 keeps ~2 GB of worst-case
# headroom for a 50% context gain, which is the better trade.
#
# 32768 IS reachable if the GPU is otherwise idle — raise it knowingly,
# not by default.
#
# Deep/Extreme deliberately NOT raised: bigger models, so the same
# increase costs more VRAM, and _get_model keeps only one tier resident
# (see llm_client) precisely because two at once don't fit.
CONTEXT_WINDOW_BY_TIER = {
    "Standard": 24576,
    "Backup": 16384,
    "Deep": 16384,     # native 32768 (Qwen3-14B) — capped, VRAM-bound
    "Extreme": 16384,
    # Small on purpose — a screen description is a sentence or two, not
    # a conversation. Keeping this tight also keeps the tier's VRAM
    # footprint down, which matters more here than anywhere else: this
    # is the one tier that can be resident in a SEPARATE process from
    # the conversation model (see vision/screen_watcher.py), so unlike
    # every other tier its footprint doesn't automatically get the
    # single-process eviction guarantee below.
    "Vision": 4096,
}

# Genuinely active: the venv's llama-cpp-python 0.3.31 is a CUDA build
# (llama_supports_gpu_offload() is True) and all layers offload to the
# RTX 5060 Ti.
#
# Worth knowing, because it caused a wrong diagnosis on 2026-08-02:
# there are THREE Pythons on this machine with three different
# llama_cpp installs — the venv here (CUDA, correct), a pyenv 3.10.11
# (CPU-only), and system Python 3.11 (CUDA, but missing FRED's other
# dependencies). Benchmarks run with the wrong interpreter reported
# ~92s per turn where the venv does the same work in ~4.3s. Always
# benchmark with Core/venv/Scripts/python.exe.
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

# Kokoro joins the same waterfall (after Whisper, once it's ALSO been
# gone this long), added for consistency with the pair above — but read
# this before assuming it buys what LLM/Whisper unload buys. Verified:
# kokoro_onnx hardcodes CPUExecutionProvider, and this environment's
# onnxruntime has no GPU provider installed at all. Unloading Kokoro
# frees ~340MB of ordinary RAM, not VRAM — a different, much less scarce
# resource than the ~6.1GB LLM+Whisper reclaim above. Wired in mainly so
# a phrase_cache-only session (see audio/phrase_cache.py) doesn't hold
# the model resident for no reason, not because the RAM was tight.
KOKORO_UNLOAD_AFTER_WHISPER_SECONDS = 15 * 60  # + 15 min => 1h30m total

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

# Upgraded 2026-08-03 from Qwen3-Embedding-0.6B-f16 (MTEB ~64) after that
# model's ranking couldn't surface active-priorities.md's real content
# for "what are my priorities" (scored -0.002 to -0.141 centered cosine —
# see settings.py's VAULT_HARDCODED_FILES note for the numbers). 4B
# (MTEB ~69) over 8B: this model runs synchronously on CPU, uncached, up
# to 3x per turn (memory retrieval, tool routing, vault routing — see
# memory_manager.py's n_ctx comment), so inference speed matters as much
# as ranking quality here. Q8_0 over f16: near-lossless for embeddings
# at roughly half the size/compute of full f16 (4.28GB vs 8.05GB).
#
# Q8_0 -> Q4_K_M 2026-08-03: side-by-side tested against the real vault
# (goals.md "Priority order" scored 0.338/0.412/0.326 vs Q8_0's
# 0.324/0.429/0.313 across the 3 calibration queries, same #1/#2 ranking
# every time) — negligible quality loss, half the disk/VRAM (2.5GB vs
# 4.28GB).
#
# Any embedding-model swap invalidates every cached vector: FAISS memory
# indexes self-heal on dimension mismatch (see
# MemoryManager._load_or_create_index), but vault_router.py's chunks.json
# cache trusts a content-hash match regardless of which model produced
# the stored vector, so it must be deleted by hand after a swap or it
# will silently mix old- and new-model vectors.
EMBEDDING_MODEL_PATH = (
    MODELS_DIR / "Qwen" / "Qwen3-Embedding-4B-GGUF"
    / "Qwen3-Embedding-4B-Q4_K_M.gguf"
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
# 0.35 undershot the ~0.5-1s figure documented above — still audibly
# quiet for the first second even after the filler/captions/reply merge
# in pill_app.py stopped the ramp happening a SECOND time per turn (one
# continuous stream now, one sd.OutputStream, so this preroll is paid
# once per turn, not once per real reply).
#
# Raised to 1.0 first, matching the documented ceiling. Confirmed on
# real hardware afterwards: real words were consistently full volume,
# but the filler itself was still quiet — meaning this device's actual
# ramp runs longer than the ~0.5-1s estimate above, not just up against
# it. Raised again to 1.5, past that original estimate, on that direct
# feedback rather than the documented figure. If the filler is ever
# quiet again, that number is still a floor, not a hard limit.
#
# Costs this much extra time-to-first-word, once per turn. Set to 0 on a
# wired output, where none of this applies.
TTS_PREROLL_SEC = 1.5

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
