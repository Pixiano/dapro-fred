# The vault has marked provenance since 2026-07-29 — `[stated 2026-07-31]`
# inline, `source: stated` in frontmatter — and until utils/confidence.py
# nothing READ those markers at runtime. FRED retrieved a chunk and spoke
# it with identical certainty whether Vatsal had said it outright or some
# past session had guessed it. rules.md: "Don't launder guesses into
# facts. If you inferred it, say you inferred it."
#
# What's being guarded is a grading function, and the expensive failures
# are all at its edges: the wrong default, the wrong winner when markers
# disagree, and a regex that reads ordinary prose as provenance. Those
# three get the most attention below.

import pytest

from utils import confidence


def test_levels_are_ordered_so_callers_can_threshold():
    """Callers gate on comparisons, not equality — orchestrator.py asks
    ">= DERIVED" for "solid enough to act on without asking". If these
    were bare unordered constants that expression would still evaluate
    and would silently mean something else."""
    assert (
        confidence.SPECULATIVE
        < confidence.INFERRED
        < confidence.DERIVED
        < confidence.CONFIRMED
        < confidence.STATED
    )


def test_inline_marker_is_read():
    """The dominant form in the vault: a fact followed by its bracketed
    provenance. personal/fitness.md is written almost entirely this way,
    so if the inline form doesn't parse, nothing does."""
    assert confidence.classify("weight is 73.5 kg `[stated 2026-07-31]`") == confidence.STATED


def test_inline_inferred_marker_is_read():
    """The half that actually costs something to get wrong. An inferred
    line read as stated is the laundering rules.md forbids, spoken aloud
    with no hedge."""
    assert confidence.classify("cardio resumes in Nov `[inferred 2026-07-31]`") == confidence.INFERRED


def test_frontmatter_marker_is_read():
    """Older vault files declare provenance once at the top instead of
    per line. Both conventions are in the vault simultaneously and
    neither is being migrated, so both have to parse."""
    assert confidence.classify("---\nsource: stated\n---\nbody") == confidence.STATED


def test_unmarked_text_defaults_to_inferred_not_stated():
    """The single most consequential line in the module. Most of the
    vault predates the marker convention, so unmarked is the COMMON case,
    not a rare one — defaulting it to stated would make FRED assert the
    entire historical vault as Vatsal's direct word, which is precisely
    the failure this module exists to prevent."""
    assert confidence.classify("no marker at all") == confidence.INFERRED


def test_empty_text_defaults_to_inferred():
    """Empty chunks reach this from truncated retrieval
    (VAULT_CHUNK_INJECT_CHARS can cut a chunk down to nothing). Must
    return the cautious default rather than raising and killing the
    turn."""
    assert confidence.classify("") == confidence.INFERRED


def test_highest_marker_wins_within_one_chunk():
    """Highest-wins is a deliberate choice, not an accident of using
    max().

    personal/fitness.md is almost entirely `[stated]` and closes with one
    `[inferred]` line about when cardio resumes. A chunk spanning that
    boundary contains both markers. Lowest-wins would let that single
    cautious footnote drag a thoroughly well-sourced file down to
    "inferred", and FRED would then hedge his actual stated weight — the
    hedging becomes noise, and hedging that fires on everything is
    hedging nobody listens to.

    The asymmetry is intentional: the cost of under-hedging one line is
    smaller than the cost of over-hedging a whole file into
    uselessness."""
    chunk = (
        "weight is 73.5 kg `[stated 2026-07-31]`\n"
        "cardio resumes in Nov `[inferred 2026-07-31]`"
    )
    assert confidence.classify(chunk) == confidence.STATED


def test_bare_prose_word_is_not_mistaken_for_a_marker():
    """The regex is anchored to the bracket for a reason. "confirmed",
    "stated" and "derived" are ordinary English words that appear in vault
    prose constantly — "he confirmed the booking", "she stated her
    preference". An unanchored pattern would upgrade any note mentioning
    a confirmation to CONFIRMED and strip its hedge, inventing certainty
    out of sentence structure."""
    assert confidence.classify("he confirmed the booking") == confidence.INFERRED
    assert confidence.classify("she stated her preference over dinner") == confidence.INFERRED


def test_name_returns_the_lowercase_word():
    """name() output goes into prompt text, so it has to be the plain
    word — a constant leaking through as "5" or "STATED" would read as
    machine noise to the model."""
    assert confidence.name(confidence.STATED) == "stated"
    assert confidence.name(confidence.SPECULATIVE) == "speculative"


def test_high_confidence_gets_no_hedge():
    """A hedge on something Vatsal said outright is worse than no
    provenance system at all: he corrects it, and every subsequent hedge
    reads as FRED being unsure of things he was told."""
    assert confidence.hedge(confidence.STATED) == ""
    assert confidence.hedge(confidence.CONFIRMED) == ""


def test_low_confidence_gets_a_spoken_hedge():
    """And the converse — a guess must arrive audibly flagged. These are
    spoken through clean_for_speech(), which strips [brackets] as machine
    noise, so the hedge has to be a real phrase rather than a tag."""
    assert confidence.hedge(confidence.SPECULATIVE).strip() != ""
    assert "[" not in confidence.hedge(confidence.SPECULATIVE)


@pytest.mark.parametrize(
    "level",
    [
        confidence.SPECULATIVE,
        confidence.INFERRED,
        confidence.DERIVED,
        confidence.CONFIRMED,
        confidence.STATED,
    ],
)
def test_describe_covers_every_level(level):
    """describe() writes a line into the system prompt for whatever level
    the turn's context graded at. A level with no entry would fall to a
    generic fallback and the model would lose the distinction entirely —
    silently, since a missing dict key here produces plausible prose
    rather than an error."""
    assert confidence.describe(level).strip() != ""
