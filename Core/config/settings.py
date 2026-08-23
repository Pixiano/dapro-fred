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
# NOTE: read-only, with exactly one exception as of 2026-08-15 —
# tools/phone_tools.py writes people/contacts.md, and nothing else. That
# exception was Vatsal's explicit call; see the CONTACTS_PATH comment
# there for the append-only rules it holds itself to. Nothing else here
# grants FRED write access to the vault, and nothing else should.
#
# contacts.md is excluded rather than indexed because retrieval injects
# vault chunks into every turn, and those turns reach the cloud APIs
# (the sensitive-local-only flag is off). 50 real phone numbers must not
# ride along in a prompt. Dialing reads the file directly by path, so it
# never needs the index.
VAULT_EXCLUDED_FILES = {"_TEMPLATE.md", "contacts.md"}

# Same reasoning as contacts.md, but the filename carries a device serial
# (whatsapp-tiers-<serial>.md) so an exact-name set cannot cover it — a
# new phone would silently start indexing its senders. Matched by prefix
# instead, so the exclusion holds for phones that don't exist yet.
VAULT_EXCLUDED_PREFIXES = ("whatsapp-tiers-",)

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
# SELF-DOCUMENTATION — FRED's own project docs (backlog #13)
# =========================================================
#
# A SECOND document set, deliberately separate from the vault above.
# The vault is Vatsal's personal memory (people, health, priorities) and
# is injected into every turn's prompt; these are FRED's own project
# docs, checked into this repo, and are only read when he's actually
# asked about himself ("do you have a calculator tool", "how does the
# phone thing work"). Mixing them would mean either paying README's
# context cost on every personal question or losing per-set top-K —
# and the vault's chunks.json lives outside the repo (see
# VAULT_INDEX_DIR) while this one is generated from files inside it.
# Same retriever class either way (orchestrator/vault_router.py takes
# the file list and cache path as arguments); only the corpus differs.
#
# Explicit list, not a glob of the project root: the root also holds
# handoffs, incident reports and session notes that name real people
# and would drag personal content into an index whose whole purpose is
# "what is FRED and why". Add a file here when it's documentation FRED
# should be able to answer from; nothing is picked up automatically.
# PHONE.md and README.md carry the 2026-08-15 phone work (calling,
# contacts sync, the token-gated LAN endpoint).
DOCS_DIR = BASE_DIR.parent
DOCS_FILES = (
    "README.md",
    "SETUP.md",
    "PHONE.md",
    "MVP Plan (v1.0 - v1.1).txt",
    "Phases 11 - 20 (JARVIS Roadmap).txt",
)

# Lives under Core/data/indexes/ (gitignored) rather than next to the
# docs: it's a generated artifact of this checkout, and the vault's
# equivalent sits with the vault for the same "index travels with what
# it describes" reason.
DOCS_INDEX_PATH = INDEX_DIR / "docs_chunks.json"

# Lower than VAULT_RETRIEVAL_TOP_K (6) because these chunks are only
# fetched on an explicit question about FRED himself, and they are
# returned as a tool result the model must read in full rather than
# ambient prompt context — four sections of docs is already a lot of
# text for a 4B to summarise into one spoken answer.
DOCS_RETRIEVAL_TOP_K = 4

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

# VIP WhatsApp messages get their own, much shorter interval. Everything
# else in proactive_checks.py watches state that moves over hours; "one of
# your people just messaged" is worthless twenty minutes late.
#
# Cheap enough to run often: one adb round trip reading notifications,
# which works with the phone locked and wakes nothing. Costs nothing when
# there is no phone attached — the check returns silently rather than
# erroring, since an absent phone is the normal case, not a fault.
VIP_MESSAGE_CHECK_MINUTES = 2

# "You missed a call from X" gets the same short-interval treatment as
# VIP messages, for the identical reason: the point of persisting a
# seen-watermark across restarts (see phone_tools.CALL_SEEN_PATH) is
# "who called while FRED was off", which only works if the FIRST check
# after startup lands soon after startup — at PROACTIVE_CHECK_INTERVAL_
# MINUTES (15) that window is wide enough that "just now" can mean
# nearly a quarter hour late. Same adb-round-trip cost profile as the
# VIP check (cheap, silent when no phone is attached), so there's no
# reason to poll it any less often.
CALL_LOG_CHECK_MINUTES = 2

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

# How long the machine must sit untouched before FRED treats the day as
# over and rolls the still-open tasks into the new day's note. Two hours
# rather than a fixed clock time: the sleep hour moves, a two-hour gap
# doesn't happen mid-work. The check only ever acts once the wall date
# has actually changed, so an afternoon nap costs nothing.
ROLLOVER_IDLE_HOURS = 2

PROACTIVE_STATE_PATH = DATA_DIR / "proactive_state.json"

# =========================================================
# SLEEP-MODE REFLECTION — orchestrator/reflection.py
# =========================================================
#
# A background reasoning pass that runs while asleep, gated on
# accumulated new material rather than on sleep-mode entry itself. See
# reflection.py's module docstring for the full trigger/write-path story.

REFLECTION_STATE_PATH = DATA_DIR / "reflection_state.json"

# Vatsal's explicit number — how many new user_speech/tool_call events
# must have accrued since the last pass before another one runs.
REFLECTION_MIN_NEW_EVENTS = 30

# Staged self-fact drafts land here, not in profile.md directly — see
# reflection.py's write-path-2 docstring. Chosen over inventing a name:
# personal/ is already the sensitive, Vatsal-about-himself folder
# (README.md above), and "pending-review" reads as exactly what it is.
REFLECTION_PENDING_DIR = VAULT_DIR / "personal" / "pending-review"

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
#
# Throttled 5x (60 -> 300) 2026-08-18 while Cerebras is out of credits
# (see CEREBRAS_API_KEY above): this cycle normally goes through cloud
# vision, cheap and fast; with cloud forced off it falls to local Vision
# inference EVERY cycle instead, which is exactly the GPU load
# CLOUD_VISION_PROVIDER was added to avoid in the first place. Revert to
# 60 once CEREBRAS_API_KEY is restored.
SCREEN_WATCHER_INTERVAL_SECONDS = 300

# Off 2026-08-19 to rule it out as the source of a reported periodic GPU
# spike (the actual measured cadence in the logs didn't match the report
# — 6-17 min apart, not ~2 min — so this may not even be the cause, but
# disabling costs nothing and confirms either way). Only gates the
# automatic idle-loop capture in watcher_manager.start() — capture_now()
# (on-demand "what's on my screen") is untouched, still works either way.
# Flip back to True once confirmed/no longer needed.
SCREEN_WATCHER_ENABLED = False

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

# llama.cpp's own prebuilt binaries (llama-server.exe etc.), release
# b10509, win-cuda-13.3-x64 — used ONLY for Vision (see
# llm/vision_server.py and the MODEL_TIERS["Vision"] comment). Kept
# outside the repo, same reasoning as MODELS_DIR above (670MB of .exe/
# .dll, nowhere near GitHub's 100MB limit and not source). Fetch/update
# via the URLs in SETUP.md if this ever needs to move or upgrade.
LLAMACPP_BIN_DIR = Path(
    r"C:\Users\Dhiraj Vatsal\llama.cpp\bin"
)

# Port llama-server.exe listens on for Vision. Arbitrary, chosen to not
# collide with anything else FRED already binds (see hud/server.py,
# phone_api.py).
VISION_SERVER_PORT = 8090

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
    # Swapped 2026-08-19 to Qwen3.5-4B-Q6_K, the same checkpoint as
    # "Backup" below (DEFAULT_TIER before the 2026-08-01 move to
    # Qwen3-8B, brought back per Vatsal's direct call after Bonsai-27B's
    # temporary swap the night before). Confirmed live: native template
    # (chat_format=None) closes <think></think> unconditionally with no
    # kwarg needed (no TIER_TEMPLATE_KWARGS entry required, unlike
    # Bonsai), plain chat 0.1-0.2s, tool-calling via native
    # `<tool_call><function=...>` text (same format _parse_text_tool_calls
    # already handles for Bonsai/Qwen3.5-family models) 0.4s. Bonsai's
    # own swap below is superseded, not deleted — see its comment for
    # why it was temporary in the first place.
    # "Standard": MODELS_DIR / "lmstudio-community" / "Qwen3-8B-GGUF"
    #         / "Qwen3-8B-Q4_K_M.gguf",
    # "Standard": MODELS_DIR / "lmstudio-community" / "Bonsai-27B-GGUF"
    #         / "Bonsai-27B-Q1_0.gguf",
    "Standard": MODELS_DIR / "unsloth" / "Qwen3.5-4B-GGUF"
            / "Qwen3.5-4B-Q6_K.gguf",

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
    #
    # TEMPORARILY repointed to Qwen3.5-4B 2026-08-20, per Vatsal's direct
    # call: "one tier only, the 4B for everything." Original path
    # commented below, not deleted — trivial one-line revert. Since
    # TIER_ROUTING_ENABLED is False (below) this key is normally
    # unreachable anyway EXCEPT tools/smart_search.py's find_file_smart,
    # which can request "Deep" directly — that call now also gets the 4B.
    # "Deep": MODELS_DIR / "lmstudio-community" / "Qwen3-14B-GGUF"
    #         / "Qwen3-14B-Q4_K_M.gguf",
    "Deep": MODELS_DIR / "unsloth" / "Qwen3.5-4B-GGUF"
            / "Qwen3.5-4B-Q6_K.gguf",

    # gpt-oss-20b — 11.28GB on disk, the largest configured tier. Rare/
    # heavy use only. VRAM math has not been done for this one the way
    # it has for Standard/Backup/Deep, so treat as unverified until it is.
    #
    # TEMPORARILY repointed to Qwen3.5-4B 2026-08-20 — see "Deep" above,
    # same reasoning, same easy revert.
    # "Extreme": MODELS_DIR / "lmstudio-community" / "gpt-oss-20b-GGUF"
    #         / "gpt-oss-20b.gguf",
    "Extreme": MODELS_DIR / "unsloth" / "Qwen3.5-4B-GGUF"
            / "Qwen3.5-4B-Q6_K.gguf",

    # Reflect — orchestrator/reflection.py's sleep-mode reasoning pass
    # only (friend-file + self-fact extraction over recent session logs),
    # never the conversation pipeline. Same gpt-oss-20b GGUF as the real
    # "Extreme" path above (currently repointed to the 4B, see its
    # comment) — Vatsal's direct call 2026-08-21: gpt-oss-20b over
    # Qwen3-14B for this tier, since it's the stronger reasoning model of
    # the two and the 14B would be slower anyway. Confirmed present on
    # disk (11.28GB).
    #
    # VRAM: Vatsal reported ~26-35GB usage from his own measurement of
    # this model. Flagged here rather than accepted quietly — that figure
    # does not fit this machine's card (nvidia-smi confirms 16311 MiB
    # total, same number CONTEXT_WINDOW's own comment above cites), so
    # either it was measured on different hardware, at a much larger
    # n_ctx/quant than this GGUF, or is simply wrong. NOT re-measured
    # live for this change either (only ~600 MiB free at the time, main
    # process + vision server both already resident — loading an 11GB
    # model into that would risk the VRAM-exhaustion crash this machine
    # has a documented history of). Treat this tier as unverified on THIS
    # card until someone actually runs
    # LLMClient().ensure_loaded("Reflect") with everything else closed
    # and reads nvidia-smi — don't trust either number blind.
    "Reflect": MODELS_DIR / "lmstudio-community" / "gpt-oss-20b-GGUF"
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
    # Reverted 2026-08-19: Bonsai's image embedding doesn't land correctly
    # through this venv's llama-cpp-python (0.3.31) — confirmed real,
    # confirmed NOT a context-size issue (crashes at n_ctx 4096, and at
    # 8192 it stops crashing but hallucinates the image as literal
    # exclamation-mark text, with the same "find_slot: non-consecutive
    # token position" warnings either way). Confirmed working in LM
    # Studio's own backend on the identical model+mmproj files, so this
    # is a binding/library-layer bug, not a model/mmproj capability
    # problem — needs its own investigation before trying again, not a
    # quick retry.
    #
    # Retried 2026-08-19 with Qwen3.5-4B (same architecture family as
    # Bonsai) and TWO different llama-cpp-python handlers
    # (MTMDChatHandler, Qwen25VLChatHandler) — both hit the identical
    # "find_slot: non-consecutive token position" warning and both
    # produced wrong output on a real (non-blank) screenshot: MTMD said
    # "a large exclamation mark" (the same hallucination pattern Bonsai
    # produced), Qwen25VL said "a blank computer screen". Confirms this
    # is a systemic binding bug affecting the whole Qwen3.5 architecture
    # family's vision path in llama-cpp-python (0.3.31) specifically —
    # NOT llama.cpp itself. Confirmed 2026-08-20: raw llama.cpp binaries
    # (llama-mtmd-cli.exe, llama-server.exe, release b10509) get the
    # identical model+mmproj exactly right ("This is a screenshot of the
    # YouTube homepage showing various video recommendations and a
    # promotional banner for YouTube Premium" — genuinely correct), same
    # find_slot warning printed either way, so that warning is cosmetic,
    # not the actual failure. Vision moved off llama-cpp-python entirely
    # as a result — see llm/vision_server.py, which runs llama-server.exe
    # as a subprocess and talks to its OpenAI-compatible HTTP API instead
    # of loading a model in-process here. This path's value is now what
    # that subprocess loads, not what llm_client._get_model() loads —
    # _get_model("Vision") is no longer called anywhere (describe_image()
    # routes to vision_server.py instead), kept as plain data.
    "Vision": MODELS_DIR / "unsloth" / "Qwen3.5-4B-GGUF"
            / "Qwen3.5-4B-Q6_K.gguf",
    # "Vision": MODELS_DIR / "lmstudio-community" / "gemma-4-12B-it-QAT-GGUF"
    #         / "gemma-4-12B-it-QAT-Q4_0.gguf",
    # "Vision": MODELS_DIR / "lmstudio-community" / "Bonsai-27B-GGUF"
    #         / "Bonsai-27B-Q1_0.gguf",
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

# Forced off 2026-08-18: the Cerebras account is out of credits (every
# call returning HTTP 402 Payment Required — 463 times in one day before
# this was caught). Both the text cascade (line ~170 in llm_client.py,
# `[p for p in CLOUD_PROVIDERS if p.get("api_key")]`) and the vision
# cascade (`CLOUD_VISION_PROVIDER.get("api_key")`) key off this one
# value being truthy, so forcing it None here drops cloud from both
# cascades in one place — every turn goes straight to local, no wasted
# network round-trip eating latency on a call that was always going to
# fail anyway. Restore `os.environ.get("CEREBRAS_API_KEY")` once billing
# is sorted.
CEREBRAS_API_KEY = None

# Groq removed 2026-08-05. Not a preference — it cannot serve FRED at
# all on the free tier. Probed against the live API:
#     Limit 8000, Requested 15076   (tokens per minute)
#     x-ratelimit-limit-tokens = 8000
# One FRED request is ~15k tokens (SYSTEM_PROMPT alone is ~7k, the
# unfiltered tool menu another ~5k), i.e. nearly double the ENTIRE
# per-minute allowance, so every single call 413s no matter how quiet
# the minute has been. It had a 100% failure rate from 2026-08-03 to
# 2026-08-05 — 24/24 on the last day — while Cerebras silently carried
# every turn and, on multi-round tool loops, hit its own 429.
# Leaving it first in the cascade cost a guaranteed-dead round-trip
# before every real request. Re-add above Cerebras if the account moves
# to Dev Tier; the entry is the four lines below with groq's URL/model.
CLOUD_PROVIDERS = [
    {
        "name": "cerebras",
        "base_url": "https://api.cerebras.ai/v1/chat/completions",
        "api_key": CEREBRAS_API_KEY,
        "model": "gpt-oss-120b",
    },
]

# Cloud vision, 2026-08-09 — images only, describe_image()'s sole
# caller. Separate from CLOUD_PROVIDERS above (that cascade is text/
# tool-calling only): same account, same no-retention terms already
# checked above, but a different, larger, vision-capable model
# (gemma-4-31b vs. the text cascade's gpt-oss-120b). Added specifically
# to get off local Vision-tier inference — the local GGUF competes with
# the main conversation model for the same 16GB card (see
# vision/watcher_manager.py's whole cross-process coordination dance),
# and was laggy on top of that even when it got a clear run. A single
# dict, not a cascade — no second vision provider configured, so
# describe_image() falls straight to local on any failure, same
# fall-through shape as the text cascade uses.
CLOUD_VISION_PROVIDER = {
    "name": "cerebras",
    "base_url": "https://api.cerebras.ai/v1/chat/completions",
    "api_key": CEREBRAS_API_KEY,
    "model": "gemma-4-31b",
}

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
    # 2026-08-20: now the mmproj llama-server.exe loads (see
    # llm/vision_server.py and the MODEL_TIERS["Vision"] comment above),
    # not an llm_client._get_model() chat_handler kwarg — that in-process
    # path is gone for Vision, this dict just holds the path.
    "Vision": MODELS_DIR / "unsloth" / "Qwen3.5-4B-GGUF"
            / "mmproj-BF16.gguf",
    # "Vision": MODELS_DIR / "lmstudio-community" / "gemma-4-12B-it-QAT-GGUF"
    #         / "mmproj-gemma-4-12B-it-QAT-BF16.gguf",
    # "Vision": MODELS_DIR / "lmstudio-community" / "Bonsai-27B-GGUF"
    #         / "mmproj-Bonsai-27B-BF16.gguf",
}

# Per-tier literal text injected into the system turn, because
# llama-cpp-python has no way to pass arbitrary jinja template kwargs
# (enable_thinking, reasoning_effort, ...) through create_chat_completion.
# Each entry below reproduces what the real kwarg would have rendered,
# confirmed by reading each tier's own embedded chat_template directly —
# not guessed per-tier, though "how the model responds to it" is only
# confirmed live for the ones _apply_thinking's docstring says so.
TIER_PROMPT_MARKERS = {
    # Standard/Vision's "/no_think" text-injection entries removed
    # 2026-08-19: confirmed live not to work — Bonsai's guard checks the
    # real enable_thinking jinja variable, not any string in the prompt.
    # Replaced by TIER_TEMPLATE_KWARGS below, which reaches the actual
    # variable via a direct chat_handler call (see llm_client._native_call).
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
    # Reflect (gpt-oss-20b) wants MEDIUM effort, Vatsal's explicit call —
    # same best-effort text-injection mechanism as Extreme above, not a
    # new one. Redundant with the template's own undefined-kwarg default
    # ("medium") but written explicitly so the setting doesn't silently
    # depend on that default never changing.
    "Reflect": "Reasoning: medium",
}

# TEMPORARY 2026-08-20, per Vatsal's direct call: with every text tier
# now the same Qwen3.5-4B (see MODEL_TIERS above), thinking is off by
# default (fast) but turns on for a query that looks like it needs more
# than a quick answer — proxied by the latest user message's raw
# character length, since there's no cheaper signal than that without an
# extra classification call. describe_image() (llm/vision_server.py)
# applies the identical threshold to its prompt argument, so text and
# vision use the same rule. Revert: delete this and the dynamic
# enable_thinking computation in llm_client._native_call, which is the
# only place that reads it.
THINKING_LENGTH_THRESHOLD = 175

# Raised 75 -> 175 same day: generate_stream() withholds the entire
# <think></think> block from the caller by design (llm_client.py's own
# docstring — "the fix for dead air"), so a thinking-mode turn is total
# silence for the whole reasoning phase (60-110+s measured), then the
# full answer at once. At 75 that engaged on nearly every normal
# message; 175 keeps ordinary conversation on the fast, actually-
# streamed path and reserves reasoning for messages that look like they
# genuinely need it.

# create_chat_completion() has a fixed keyword signature with no
# **kwargs passthrough (confirmed by reading its source), so there's no
# way to set enable_thinking through it. The handler underneath it
# (Jinja2ChatFormatter/MTMDChatHandler) DOES accept arbitrary kwargs and
# forwards them into the jinja render — see llm_client._native_call,
# which calls that handler directly and now ALWAYS does (every text tier
# is the same 4B checkpoint as of 2026-08-20, so there's no longer a
# per-tier reason to branch). enable_thinking is computed dynamically
# per call from THINKING_LENGTH_THRESHOLD above, not looked up from a
# static per-tier dict — this dict is gone, not just emptied.

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
    # Raised past Extreme's 16384 on Vatsal's own report of a ~36K
    # native context for this model (exact figure unconfirmed here — see
    # MODEL_TIERS["Reflect"]'s comment on why the VRAM side of that same
    # report doesn't reconcile with this card). Reasonable to want more
    # room than Extreme anyway: this tier reads whole session-log
    # stretches plus the people/*.md corpus in one call, not one turn's
    # reply. Rounded to a clean value rather than 36864 exactly, since
    # neither number has been measured on this machine — re-check VRAM
    # at this n_ctx during the same manual load test before trusting it.
    "Reflect": 32768,
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

# The tail-side twin of TTS_PREROLL_SEC. Reported live 2026-08-12:
# playback cuts off the last ~1s of the reply. sounddevice's
# Stream.stop() only blocks until PortAudio's OWN host buffer has
# drained (see its docstring) — it has no visibility into a Bluetooth
# link's own downstream buffering/transmission latency past that
# point, so stop()+close() right after the last real write can tear
# the stream down while the device is still catching up on genuinely
# spoken audio. Fix is symmetric to the preroll: give the device
# something inaudible to still be playing when stop() is called, so
# the real final words are already out by then. Starting at the
# reported cutoff length, same as TTS_PREROLL_SEC started at its
# documented estimate before being tuned against real hardware — a
# floor, not a hard limit; raise it if the tail is still getting
# clipped. Set to 0 on a wired output, where none of this applies.
TTS_POSTROLL_SEC = 1.0

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

# Beam width for the decode. 1 is greedy — fastest, and what this ran on
# until 2026-08-15, but greedy commits to the top token at every step and
# cannot back out of a bad start. That is precisely the far-field failure
# mode: "Call Mom" came back as "God, Mom" (session_2026-08-15.jsonl,
# 18:41), a voicing confusion that reverb makes near-coin-flip at the
# first phoneme. A beam keeps competing hypotheses alive long enough for
# the rest of the utterance to settle it.
#
# Costs latency roughly linearly in beam width on the decode step, on
# top of an already non-free Whisper pass. Judged in live use on
# 2026-08-15 at beam 5: no perceptible slowdown, which fits — turbo on
# this card decodes a few-second utterance fast enough that 5x a small
# number is still a small number, and the wake-to-reply path is
# dominated by the LLM turn, not the transcribe.
#
# So 5 stays. Kept as a knob rather than inlined because the finding is
# hardware-specific: on a slower card, or if utterances get much longer,
# this is the first thing to drop back to 1 (greedy, the old behaviour).
WHISPER_BEAM_SIZE = 5

# Whisper can be primed with text that biases decoding toward expected
# vocabulary. Command words are the whole reason: "call", "Fred" and
# contact names are exactly what a far-field decode mangles, and they
# are a tiny, closed set worth putting a thumb on the scale for.
#
# Contact names are appended at runtime from the vault contacts file
# (see audio/stt_whisper.py:_build_prompt) rather than written here —
# real names belong in the vault, never in this repo.
WHISPER_PROMPT_BASE = (
    "Fred. Call Mom. Hang up. Sync contacts. What's on my agenda? "
    "Open the file. Set a timer. Search the web."
)

# faster-whisper defaults this to True, feeding the previous segment's
# text in as context for the next. Good for continuous dictation, wrong
# here: FRED's utterances are independent commands seconds apart, so the
# only thing carried over is a chance for one bad transcript to bias the
# next — the documented mechanism behind Whisper's repetition loops.
WHISPER_CONDITION_ON_PREVIOUS = False

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

# GUI mode's wake word (Core/input/wakeword.py) — an actual trained
# acoustic model, not text-matching. The comment above this block was
# accurate when written (openWakeWord ships no "Hey FRED" pretrained
# model); it's since been trained from scratch, see
# Core/input/wakeword_train.py to retrain. Runs ALONGSIDE hold-to-talk
# (input/hotkey.py), never replacing it — decided 2026-08-09.
WAKEWORD_MODEL_PATH = BASE_DIR / "models" / "wakeword" / "hey_fred.onnx"

# openwakeword scores are 0..1. History, all same-day (2026-08-10),
# each move backed by a real live measurement, not another guess:
#   0.6  -> 0.4   under-triggering at ~2m — every training clip was
#                 studio-clean Kokoro TTS, no real-room reverb.
#   0.4  -> 0.25  wakeword_log.jsonl caught a real near-miss peaking at
#                 0.278, just under 0.4.
#   0.25 -> 0.35  0.25 over-corrected: caught a genuine FALSE trigger
#                 at 0.701 (opened the mic, heard nothing, gave up
#                 ~2.8s later — confirmed against the session log, no
#                 transcription followed), alongside a real trigger at
#                 0.978 moments later that worked correctly end to end.
#                 0.35 splits the difference between the two measured
#                 failure directions; not a clean optimum, just the
#                 best call available from two data points in each
#                 direction. A real fix (room-impulse-response
#                 augmentation, real recorded voice instead of only
#                 synthetic TTS — see wakeword_train.py) is the actual
#                 plan, not a permanent substitute for tuning this.
WAKEWORD_THRESHOLD = 0.35

# =========================================================
# PRESENCE DETECTION — Core/input/presence.py
# =========================================================
#
# MVP scope only, per fred-presence-sleep-mode-plan_2026-08-18.md and
# Vatsal's own scoping call 2026-08-21: presence detection alone
# (is_present()/last_seen()/last_checked()), nothing downstream yet
# (sleep-mode, reminder-gating, cancel phrases are later, separate work
# that depends on this being proven reliable first).

# Confirmed live 2026-08-21 by capturing a frame from every index and
# looking at it: index 1 was the iBall PHOCUS 40A (the only real
# hardware — desktop has no built-in webcam), 0 and 2 were virtual
# (Canon EOS Webcam Utility / OBS Virtual Camera).
#
# RE-CONFIRMED (and changed) 2026-08-22: a reboot re-enumerated the
# cameras and swapped the mapping — index 0 is now the real iBall,
# index 1 is now OBS Virtual Camera's idle "no signal" placeholder.
# Presence detection was silently reading that placeholder as "not
# present" for the better part of an hour before this was caught
# (look_through_camera's own description — "an oval emblem... a muted
# or recording-disabled camera icon" — was the tell). Windows camera
# index order is NOT guaranteed stable across reboots when multiple
# virtual-cam apps are involved; re-run the same per-index
# capture-and-inspect check after any reboot-related presence weirdness
# rather than assuming this index still holds.
PRESENCE_CAMERA_INDEX = 0

# Raw camera poll interval — Vatsal's call 2026-08-21, resolving the
# design doc's own inconsistency (it separately said "~15s" for the
# absence-detection consolidation-start check and "~30-60s" for the
# raw camera poll cost/frequency tradeoff — two different numbers for
# two different things, not a real conflict, but only this one matters
# for the MVP since sleep-mode/consolidation isn't built yet).
PRESENCE_POLL_SECONDS = 15

# Enrollment: 5 reference photos, 5s apart, fully automatic (no
# per-shot keypress) — Vatsal's call 2026-08-21. See
# Core/scripts/enroll_face.py.
PRESENCE_ENROLLMENT_SHOTS = 5
PRESENCE_ENROLLMENT_INTERVAL_SECONDS = 5

# Three-tier embedding pool, replacing the old flat PRESENCE_MAX_EMBEDDINGS
# cap (2026-08-22). face_enrollment.json now tags every entry "base",
# "hard", or "dynamic":
#   base    — deliberate initial enrollment (live 5-shot + seed-from-photo,
#             scripts/enroll_face.py's default flow). Protected: never
#             evicted, never auto-added-to outside that script.
#   hard    — deliberate captures under adverse conditions (dim light,
#             turned away, angled/partial view), scripts/enroll_face.py
#             --hard. Also protected, same reason.
#   dynamic — rolling FIFO window, auto-populated by presence.py's ongoing
#             confident-match accumulation. THIS is the only tier with an
#             actively-enforced cap: base/hard only ever grow via the
#             deliberate enroll_face.py flows above, never automatically,
#             so they have no eviction logic and these targets are just
#             enrollment-script guidance, not caps presence.py enforces.
PRESENCE_BASE_EMBEDDINGS_TARGET = 20
PRESENCE_HARD_EMBEDDINGS_TARGET = 15
PRESENCE_DYNAMIC_EMBEDDINGS_CAP = 15

# ArcFace/buffalo_l cosine-similarity match threshold. NOT a measured
# constant — this repo had never run the model as of 2026-08-21, so
# these are starting guesses (typical same-person ArcFace similarity
# clusters 0.35-0.45 in the wild) to verify/retune against real
# enrollment + real live frames, not settled numbers. Two thresholds,
# not one: below the low mark is a confident non-match, above the high
# mark is a confident match, and the band between them is genuinely
# ambiguous — see presence.py's fallback to a real vision-model
# comparison for that band, proven live 2026-08-21 via Bonsai-27B
# through LM Studio (verdict: correctly identified the same person
# across a hard side-angle shot, high confidence) before being wired
# into this codebase's own already-working vision_server.py pipeline
# instead of depending on LM Studio at runtime.
PRESENCE_MATCH_THRESHOLD_LOW = 0.30
# Raised from the original 0.45 starting guess, 2026-08-21: live use
# showed a single high-confidence-but-wrong frame was enough to flip
# present, exit sleep mode, fire the wake greeting, and accumulate a bad
# embedding — "the notifications are appearing like every false match
# from the face recog." A stricter single-frame bar, plus the present-
# debounce below, are the two-part fix; LOW is untouched, it only gates
# the ambiguous-vision-fallback band, a separate concern.
PRESENCE_MATCH_THRESHOLD_HIGH = 0.58

# Consecutive absent polls (at PRESENCE_POLL_SECONDS each) required before
# orchestrator/sleep_mode.py declares actual absence and enters sleep
# mode — debounces someone briefly stepping out of frame. 4 * 15s ≈
# 60-75s (~1 min). Raised from 3 (45-60s), Vatsal's call 2026-08-22.
PRESENCE_ABSENT_DEBOUNCE = 4

# Symmetrical debounce for the return trip: consecutive present/match
# polls required before treating a return as real — i.e. before
# sleep_mode.py exits sleep mode / fires the wake greeting, and before
# presence.py accumulates a new enrollment embedding. Smaller than
# PRESENCE_ABSENT_DEBOUNCE (2 vs 3): a false-negative-then-correct costs
# one missed exit, whereas repeatedly firing the greeting on noise is the
# actual complaint being fixed here. 2026-08-21.
PRESENCE_PRESENT_DEBOUNCE = 2

# Camera-obstruction check (proactive_checks.check_camera_obstruction,
# Vatsal's own idea 2026-08-23): while sleep mode is active, real
# keyboard/mouse input more recent than this many seconds means someone
# is clearly still at the desk despite the camera reading absent — a
# blocked/covered/misaimed camera looks identical to "stepped away"
# otherwise. Deliberately smaller than PRESENCE_ABSENT_DEBOUNCE's ~60-75s
# window: this has to mean "still actively typing right now," not just
# "was here a minute ago," or it would fire on the tail end of a
# legitimate departure.
PROACTIVE_CAMERA_OBSTRUCTION_IDLE_SECONDS = 20

# Focus-awareness check-in (orchestrator/focus_checkin.py): first eligible
# once he's been present but hasn't had a real turn/tool-call with FRED for
# this many minutes; grows by the step below each time it actually speaks,
# resets to the base the moment a real interaction happens. Vatsal's own
# numbers, 2026-08-21.
FOCUS_CHECKIN_BASE_MINUTES = 60
FOCUS_CHECKIN_STEP_MINUTES = 10

# Stranger-detection loop (orchestrator/security_watch.py): its own
# separate 5s poll, deliberately NOT the shared 15s presence poll (see
# that module's docstring for the accepted camera-contention tradeoff).
# Debounce is consecutive qualifying 5s ticks before lockdown engages —
# 5 * 5s = 25s of a genuinely unrecognized person actively at the desk
# while Vatsal's away, not a single frame's false read.
SECURITY_WATCH_POLL_SECONDS = 5
SECURITY_STRANGER_DEBOUNCE = 5

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
