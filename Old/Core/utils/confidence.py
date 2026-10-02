# Core/utils/confidence.py
#
# Five confidence levels for anything FRED knows about Vatsal, so a
# personalised answer can say how much it should be trusted.
#
# This is not a new convention invented here — the vault ALREADY marks
# provenance and has since 2026-07-29, both in frontmatter (`source:
# stated`) and inline (`[stated 2026-07-31]`, `[inferred 2026-07-31]`,
# `[confirmed 2026-07-31]`). rules.md is explicit about why:
#
#   "Don't launder guesses into facts. If you inferred it, say you
#    inferred it. This matters double when writing to this vault — an
#    unmarked guess becomes a 'fact' every future session inherits."
#
# Nothing read those markers at runtime. FRED retrieved a chunk and
# spoke its contents with identical certainty whether Vatsal had stated
# it outright or some past session had guessed it. That is precisely the
# laundering the rule forbids, happening in the voice rather than on
# disk. This module is the read side of a convention that was previously
# write-only.
#
# The levels are ordered so a caller can threshold on them (">= DERIVED"
# is "solid enough to act on without asking").

import re

SPECULATIVE = 1   # a guess with little support; never act on it alone
INFERRED = 2      # reasoned from evidence, but he never said it
DERIVED = 3       # mechanically extracted from something he authored
CONFIRMED = 4     # checked against evidence, or re-confirmed later
STATED = 5        # he said it directly — the highest there is

NAMES = {
    SPECULATIVE: "speculative",
    INFERRED: "inferred",
    DERIVED: "derived",
    CONFIRMED: "confirmed",
    STATED: "stated",
}

_BY_NAME = {name: level for level, name in NAMES.items()}

# Inline markers, e.g. "[stated 2026-07-31]" or a bare "[inferred]".
# Anchored to the bracket so ordinary prose using the word "confirmed"
# in a sentence doesn't get mistaken for a provenance marker.
_INLINE = re.compile(
    r"\[(stated|confirmed|derived|inferred|speculative)\b[^\]]*\]",
    re.IGNORECASE,
)

# Frontmatter form: "source: stated".
_FRONTMATTER = re.compile(
    r"^\s*source:\s*(stated|confirmed|derived|inferred|speculative)\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# How FRED should hedge when speaking a fact at each level. Spoken
# aloud, so these are phrases that fit in front of a claim rather than
# bracketed tags — clean_for_speech() (audio/tts_kokoro.py) strips any
# [bracket] as machine noise, so a tag would be silently swallowed.
HEDGES = {
    STATED: "",                        # he said it; state it plainly
    CONFIRMED: "",                     # verified; no hedge needed either
    DERIVED: "going by your notes, ",
    INFERRED: "I think, though you never said outright, ",
    SPECULATIVE: "I'm guessing here, but ",
}


def classify(text: str, default: int = INFERRED) -> int:
    """
    The confidence level of a piece of vault text.

    Highest marker wins when several appear: a chunk that contains both
    a stated fact and an inferred aside is anchored by the stated one,
    and the alternative (lowest-wins) would drag well-sourced files down
    to the level of their most cautious footnote — fitness.md, which is
    almost entirely `[stated]` but closes with one `[inferred]` line
    about when cardio resumes, would otherwise report as inferred.

    `default` applies when nothing is marked at all. INFERRED rather
    than STATED on purpose: unmarked means nobody recorded a source, and
    treating that as his direct word is exactly the laundering rules.md
    forbids.
    """
    if not text:
        return default

    found = [
        _BY_NAME[match.group(1).lower()]
        for match in _INLINE.finditer(text)
    ] + [
        _BY_NAME[match.group(1).lower()]
        for match in _FRONTMATTER.finditer(text)
    ]

    return max(found) if found else default


def name(level: int) -> str:
    return NAMES.get(level, NAMES[INFERRED])


def hedge(level: int) -> str:
    """The phrase to put in front of a claim at this level."""
    return HEDGES.get(level, HEDGES[INFERRED])


def describe(level: int) -> str:
    """One short line for a prompt, telling the model how far to trust
    the context it was just handed."""
    return {
        STATED: "Vatsal stated this directly — you may say it plainly.",
        CONFIRMED: "This was verified — you may say it plainly.",
        DERIVED: "This was extracted from his own notes — attribute it to them.",
        INFERRED: "This was inferred, not stated — say so before relying on it.",
        SPECULATIVE: "This is a guess — flag it as one, or ask instead.",
    }.get(level, "Provenance unknown — treat it as an inference.")
