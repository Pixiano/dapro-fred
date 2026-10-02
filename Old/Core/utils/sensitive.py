# Core/utils/sensitive.py
#
# Decides whether a turn is carrying vault content that must never leave
# this machine.
#
# The vault's rules.md is unambiguous about this and it is the one rule
# with a real-world cost attached:
#
#   "Never send personal/ or people/ anywhere. No hosted model, no API,
#    no paste, no export, no repo. They hold precise identifying details
#    about a minor, health information, and other people's information."
#
# Until this module existed nothing enforced that at runtime. FRED's LLM
# cascade (llm/llm_client.py) tries Groq and then Cerebras BEFORE any
# local model, on every single turn — so the moment vault retrieval
# started returning personal/ chunks, the contents would have been
# POSTed to a third party. Indexing personal/ and enforcing this landed
# in the same change on purpose; one without the other is the bug.
#
# Deliberately conservative: this returns True on anything that even
# looks sensitive, because a false positive costs one slower local turn
# and a false negative is unrecoverable — once it's sent, it's sent.

from pathlib import PurePosixPath

# Vault directories whose contents are sensitive, matching rules.md.
# Names are compared case-insensitively against every path component, so
# "personal/fitness.md" and "FRED/People/sara.md" both match.
SENSITIVE_DIRS = frozenset({"personal", "people"})

# Frontmatter flag some vault files carry (personal/fitness.md has
# `sensitive: true`). Honoured wherever it appears, so a sensitive file
# living outside the two directories above is still caught.
SENSITIVE_FLAG = "sensitive: true"


def is_sensitive_path(path) -> bool:
    """True if `path` is inside a sensitive vault directory."""
    if not path:
        return False
    parts = PurePosixPath(str(path).replace("\\", "/")).parts
    return any(part.lower() in SENSITIVE_DIRS for part in parts)


def is_sensitive_text(text: str) -> bool:
    """True if the text carries the frontmatter sensitivity flag."""
    return bool(text) and SENSITIVE_FLAG in text.lower()


def any_sensitive(chunks) -> bool:
    """
    True if any retrieved vault chunk is sensitive.

    `chunks` is whatever the retriever returns — dicts with a "source"/
    "path" key, plain path strings, or objects with a .source attribute.
    Unknown shapes are treated as NOT sensitive on their path but still
    scanned for the frontmatter flag, so a shape this doesn't recognise
    degrades to "check the text" rather than silently passing.
    """
    for chunk in chunks or []:
        if isinstance(chunk, dict):
            source = chunk.get("source") or chunk.get("path") or chunk.get("file")
            text = chunk.get("text") or chunk.get("content") or ""
        elif isinstance(chunk, str):
            source, text = chunk, chunk
        else:
            source = getattr(chunk, "source", None) or getattr(chunk, "path", None)
            text = getattr(chunk, "text", "") or ""

        if is_sensitive_path(source) or is_sensitive_text(text):
            return True

    return False
