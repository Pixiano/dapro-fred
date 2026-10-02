# Core/orchestrator/vault_intent.py
#
# Cue gate in front of VaultRouter.retrieve(). Same fix that already
# worked for tool routing (see orchestrator/intent.py): calibration on
# real queries showed cosine similarity alone can't separate "asking
# about vault content" from "plain chat that happens to use similar
# words" — "tell me a joke" (0.661) outscored the genuine "when are my
# board exams" match (0.533) against its own correct chunk. A global
# floor can't fix that; it's the same value on both.
#
# The gate doesn't try to score relevance better. It avoids asking the
# embedding question at all unless the surface text gives some reason
# to think this turn is about vault content — mirroring intent.py's
# CATEGORY_CUES, not reinventing it. A miss just means "no vault
# context this turn" (cheap); cues are kept over-inclusive on purpose
# so that's rare.

import re

from orchestrator.intent import looks_social, normalise

# One cue set per vault area (see VAULT_DIR layout: jobs/, projects/,
# knowledge/, personal/, reference/, daily/, people/, active-priorities.md).
# Broad and over-inclusive by design — a spurious pass just costs one
# embedding call and maybe one wasted chunk; a miss means real vault
# knowledge never gets offered at all.
VAULT_CUES = (
    # jobs/
    "job", "career", "resume", "cv", "interview", "salary", "offer letter",
    "internship",
    # projects/
    "project", "board exam", "board exams", "memory vault", "vault maintenance",
    # knowledge/
    "llm", "language model", "local model", "tts", "text to speech",
    "powershell", "sales",
    # personal/
    "health", "routine", "goal", "identity", "habit", "diet", "exercise",
    "sleep schedule",
    # reference/
    "machine", "gpu", "cpu", "vram", "spec", "hardware", "motherboard",
    # daily/
    "yesterday", "diary", "journal", "daily note", "daily log",
    # people/
    "who is", "contact",
    # active-priorities.md
    "priorit", "working on", "task list", "on my plate",
    # generic recall — the actual "ask FRED to remember something about
    # me" phrasing, as distinct from general knowledge questions
    "remember", "recall", "what did i", "when did i", "do you know about",
    "what do you know about", "tell me about my", "what's my", "whats my",
)


def _build_cue_regex(cues):
    """Word-boundary match for alphanumeric cues, literal (substring) for
    the rest — same approach as intent.py's _build_cue_regex, kept
    separate rather than imported since it's a five-line helper and the
    two cue sets are unrelated in content."""
    return re.compile(
        "(?:%s)" % "|".join(
            (r"\b%s" if c[0].isalnum() else r"%s") % re.escape(c)
            for c in cues
        ),
        re.IGNORECASE,
    )


_VAULT_RE = _build_cue_regex(VAULT_CUES)


def should_check_vault(text: str) -> bool:
    """
    False means "don't even embed this turn" — skip straight to no vault
    context. True means the surface text gives a reason to check, so the
    (already-calibrated, imperfect) semantic floor gets a chance to work
    on a pool of turns that are actually plausible vault queries, instead
    of on everything including "tell me a joke".
    """
    stripped = normalise(text)
    if not stripped:
        return False
    if looks_social(stripped):
        return False
    return bool(_VAULT_RE.search(stripped))
