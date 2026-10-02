# Core/tests/test_self_docs.py
#
# Backlog #13, landed 2026-08-15: FRED answers "do you have a
# calculator tool" / "how does the phone thing work" from his own
# indexed docs instead of guessing. Three things can break that and all
# three are silent:
#   - the doc set stops resolving on disk (a rename in the repo root),
#   - retrieval returns the wrong document,
#   - the tool is unreachable because intent routing sends the very
#     phrasing it exists for ("what can you do") straight to chat.
#
# The embedder is a deterministic bag-of-words stand-in, not the real
# model: loading a GGUF in a unit test would make this the slowest file
# in the suite to prove plumbing. What is being pinned here is the
# wiring — corpus, cache path, routing — not embedding quality.

import math
import re

from orchestrator import intent
from tools import self_docs


_VOCAB = (
    "phone", "call", "contacts", "android", "tailscale", "token",
    "install", "setup", "python", "model", "roadmap", "phase",
    "wake", "word", "vault", "memory", "hud", "voice",
)


def _fake_embed(text, is_query=False):
    """Term-frequency vector over a fixed vocab, capped and L2-normalised
    — capped so a long document can't win on repetition alone, normalised
    because a real sentence embedder returns unit vectors and the router
    centers the corpus around them."""
    words = re.findall(r"[a-z]+", text.lower())
    vector = [float(min(words.count(term), 3)) for term in _VOCAB]
    norm = math.sqrt(sum(x * x for x in vector)) or 1.0
    return [x / norm for x in vector]


def test_doc_set_resolves_and_includes_todays_phone_docs():
    names = [rel for rel, _path in self_docs.iter_doc_files()]

    assert names, "no DOCS_FILES resolved on disk"
    # PHONE.md is the 2026-08-15 work; README is the one doc every
    # "what are you" question should be able to reach.
    assert "PHONE.md" in names
    assert "README.md" in names


def test_retrieval_finds_the_phone_doc_and_quotes_it(tmp_path, monkeypatch):
    # Own cache file per run: the real one is a build artifact of this
    # checkout and a test must not invalidate or poison it.
    monkeypatch.setattr(self_docs, "DOCS_INDEX_PATH", tmp_path / "docs.json")
    monkeypatch.setattr(self_docs, "_router", None)

    result = self_docs.ask_about_myself(
        "how do i call someone on the phone", embed_fn=_fake_embed
    )

    assert "PHONE.md" in result
    # The point of returning excerpts rather than a sentence: the model
    # is told to answer from this text and to admit when it can't.
    assert "don't cover it" in result


def test_no_embedder_degrades_instead_of_raising():
    assert "isn't available" in self_docs.ask_about_myself("what can you do")


def test_what_can_you_do_reaches_the_tool_despite_social_phrasing():
    # _SOCIAL matches this phrase outright — it is listed there — so
    # before the selfdoc override in classify() the tool built for this
    # exact question was never offered on it.
    needs_tools, names, _reason = intent.classify("what can you do")

    assert needs_tools
    assert "ask_about_myself" in names


def test_backlog_example_questions_all_offer_the_tool():
    # The three question shapes backlog #13 itself names. The last one
    # says nothing self-referential, so it also matches "phone" — the
    # menu is the union, which is the point: the model still gets to
    # choose between dialling and explaining.
    for phrase in (
        "do you have a calculator tool",
        "why were you built that way",
        "how does the phone thing work",
    ):
        needs_tools, names, _reason = intent.classify(phrase)
        assert needs_tools, phrase
        assert "ask_about_myself" in names, phrase


def test_ordinary_small_talk_is_still_chat():
    for phrase in ("how are you", "thanks", "who are you"):
        needs_tools, _names, _reason = intent.classify(phrase)
        assert not needs_tools, phrase
