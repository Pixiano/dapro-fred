# Core/personality/system_prompt.py
#
# FRED's identity now lives in the vault, not here. This module loads it —
# it is a loader, not a source of truth.
#
# Three files are read directly, always, on every FREDOrchestrator init —
# no vector retrieval, no chance of a bad embedding match dropping
# identity or a rule on some turn:
#
#   persona.md   who FRED is
#   profile.md   who Vatsal is
#   rules.md     hard behavioural rules
#
# Everything else in the vault (jobs/, projects/, knowledge/, daily/,
# reference/, personal/, people/, active-priorities.md) is reached through
# the vector store instead — those are the files that change, or that are
# only relevant to some turns, not every turn. Loading three ~900-word
# files always is a real, accepted cost (~4,200 tokens against gemma4's
# 16,384-token window) in exchange for identity and rules never depending
# on a retrieval match succeeding.
#
# persona.md itself currently claims the reverse of this file's role —
# "[system_prompt.py] is the runtime source of truth; if the two
# disagree, the system prompt wins and this file should be corrected."
# That line is now stale (the authority direction flipped this session)
# and hasn't been corrected here — vault write-back is deliberately
# deferred, so this file doesn't touch vault content, only reads it.

from config.settings import VAULT_DIR, VAULT_HARDCODED_FILES
from utils.vault_md import strip_frontmatter


def _load_vault_prompt() -> str:
    sections = []
    for name in VAULT_HARDCODED_FILES:
        path = VAULT_DIR / name
        content = strip_frontmatter(path.read_text(encoding="utf-8")).strip()
        if content:
            sections.append(content)
    return "\n\n---\n\n".join(sections)


# Pre-vault system prompt. Kept as a functional fallback, not just an
# inert copy — if the vault is unreachable (moved, renamed, machine
# without it), FRED still runs coherently rather than with an empty or
# missing system prompt. This is the exact text that was the sole system
# prompt before the vault swap.
_FALLBACK_SYSTEM_PROMPT = """
You are F.R.E.D. —
Friendly, Responsive, Rational, Rakish Electronic Dude.

Identity:
F.R.E.D. is an intelligent AI assistant inspired by the idea
of a calm, capable digital companion.

Core Behavioral Traits:
- Intelligent and observant
- Concise but natural
- Calm and confident
- Rational under pressure
- Slightly witty and charming
- Helpful without sounding robotic
- Conversational, not corporate

Response Style:
- Avoid overly long explanations unless requested
- Avoid sounding like a generic AI assistant
- Be direct and clear
- Use subtle personality, not constant jokes
- Maintain conversational continuity naturally

Reasoning:
- For anything involving arithmetic, multiple steps, comparison, or a
  question that could be a trick, work it out inside <think>...</think>
  first, then give only the conclusion outside the block.
- Never show your working in the visible reply. The reply is spoken
  aloud, so it must be the answer itself, not the derivation.
- Simple conversation needs no <think> block at all.

Important Rules:
- Never invent fake capabilities
- Never pretend actions succeeded if they failed
- Be transparent about limitations
- Prioritize usefulness over theatrics

F.R.E.D.'s tone should feel like:
a composed, intelligent assistant with personality —
not a cartoon character.
""".strip()


def _build_system_prompt() -> str:
    try:
        prompt = _load_vault_prompt()
        if not prompt:
            raise ValueError("vault files were empty after stripping frontmatter")
        return prompt
    except Exception as e:
        print(f"[system_prompt] vault load failed ({e}) — using fallback prompt")
        return _FALLBACK_SYSTEM_PROMPT


# Read once at import time, matching the previous behaviour (SYSTEM_PROMPT
# was a static string). Vault edits to persona/profile/rules take effect
# on the next FRED restart, not mid-session — these three files are
# expected to change rarely, so this trades hot-reload for simplicity.
SYSTEM_PROMPT = _build_system_prompt()
