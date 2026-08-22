# Confirmed live leak, 2026-08-04. Three changes that were each fine
# alone combined into a privacy breach:
#
#   1. the vault indexer started indexing personal/ and people/,
#   2. retrieval runs at VAULT_RETRIEVAL_FLOOR = -1.0, so six chunks come
#      back on EVERY turn whether or not they're relevant,
#   3. the LLM cascade tries Groq and then Cerebras BEFORE any local
#      model.
#
# Net effect: Vatsal's health data and other people's details were being
# POSTed to a third party on ordinary turns. rules.md forbids this in as
# many words — "Never send personal/ or people/ anywhere. No hosted
# model, no API."
#
# utils/sensitive.py is the detector and LLMClient's local_only kwarg is
# the enforcement. Both halves are tested here, because either one alone
# is still the bug.

import inspect

import pytest

from utils import sensitive
from llm.llm_client import LLMClient


# =========================================================
# is_sensitive_path
# =========================================================

def test_sensitive_dirs_are_detected():
    """The two directories rules.md names. Nothing subtle here — this is
    the base case that has to hold before any of the format variations
    below are worth caring about."""
    assert sensitive.is_sensitive_path("personal/fitness.md")
    assert sensitive.is_sensitive_path("people/sara.md")


def test_retrieval_label_format_is_detected():
    """Retrieval doesn't hand the orchestrator a bare path — it builds a
    label like "personal/fitness.md — Biometrics" (path + heading) and
    that string is what reaches any_sensitive(). A matcher that assumed a
    clean path would pass every unit test and still leak in production,
    which is the only failure mode that actually matters here."""
    assert sensitive.is_sensitive_path("personal/fitness.md — Biometrics")


def test_matching_is_case_insensitive_and_works_at_any_depth():
    """The vault is browsed on Windows and edited in Obsidian, so casing
    of a directory is not something to bet a health record on. The check
    also has to fire on a nested component: paths arrive both relative to
    the vault root and prefixed with the vault folder name."""
    assert sensitive.is_sensitive_path("FRED/People/sara.md")


def test_windows_backslash_paths_are_detected():
    """FRED runs on Windows and pathlib hands back backslash-separated
    strings from anything that touched the filesystem. Splitting on "/"
    only would treat "personal\\fitness.md" as one opaque filename with
    no "personal" component — sensitive on disk, invisible to the
    guard, straight to Groq."""
    assert sensitive.is_sensitive_path("personal\\fitness.md")


def test_ordinary_vault_paths_are_not_flagged():
    """The counterweight to a deliberately paranoid matcher. Everything
    outside personal/ and people/ has to keep reaching the cloud cascade,
    or the leak fix quietly becomes "FRED is now slow on every turn" and
    someone turns it off."""
    assert not sensitive.is_sensitive_path("projects/fred.md")
    assert not sensitive.is_sensitive_path("knowledge/local-llms.md")


def test_empty_and_missing_paths_are_not_flagged():
    """A chunk with no source at all is common — some retriever shapes
    carry text only. It must return False rather than raising, because an
    exception here would propagate out of _build_messages and kill the
    turn."""
    assert not sensitive.is_sensitive_path("")
    assert not sensitive.is_sensitive_path(None)


# =========================================================
# is_sensitive_text
# =========================================================

def test_frontmatter_flag_is_detected():
    """personal/fitness.md carries `sensitive: true` in its frontmatter.
    This is the second line of defence: a sensitive file that gets moved
    or created outside the two known directories is still caught by its
    own declaration."""
    assert sensitive.is_sensitive_text("---\nsensitive: true\n---\nbody")


def test_frontmatter_flag_matching_ignores_case():
    """YAML written by hand over months is not consistently cased, and
    Obsidian doesn't normalise it. "Sensitive: True" is the same
    declaration and must be honoured as one."""
    assert sensitive.is_sensitive_text("---\nSensitive: True\n---\nbody")


def test_ordinary_text_is_not_flagged():
    assert not sensitive.is_sensitive_text("Just some notes about FRED.")
    assert not sensitive.is_sensitive_text("")


# =========================================================
# any_sensitive — the shape the orchestrator actually calls with
# =========================================================

class _Chunk:
    """Stand-in for an object-shaped retriever result (.source/.text)."""

    def __init__(self, source, text=""):
        self.source = source
        self.text = text


def test_any_sensitive_reads_dicts_by_source_path_or_file():
    """The orchestrator builds {"source": label, "text": text} dicts, but
    other call sites in the codebase use "path" and "file" for the same
    thing. All three key names have to work: a chunk whose key this
    doesn't recognise reads as clean, and clean means cloud."""
    assert sensitive.any_sensitive([{"source": "personal/fitness.md"}])
    assert sensitive.any_sensitive([{"path": "people/sara.md"}])
    assert sensitive.any_sensitive([{"file": "personal/fitness.md"}])


def test_any_sensitive_reads_dict_body_under_text_or_content():
    """Same argument for the body: the flag has to be found whichever key
    the chunk's text arrived under, since a file flagged in its own
    frontmatter may sit outside personal/ entirely."""
    assert sensitive.any_sensitive(
        [{"source": "notes/misc.md", "text": "sensitive: true"}]
    )
    assert sensitive.any_sensitive(
        [{"source": "notes/misc.md", "content": "sensitive: true"}]
    )


def test_any_sensitive_reads_plain_strings_and_objects():
    """Retrieval shapes differ between the vault retriever and the tool
    layer. Plain path strings and objects with .source/.text both occur,
    and neither may fall through the type dispatch untested."""
    assert sensitive.any_sensitive(["personal/fitness.md"])
    assert sensitive.any_sensitive([_Chunk("people/sara.md")])
    assert sensitive.any_sensitive([_Chunk("notes/misc.md", "sensitive: true")])


def test_one_sensitive_chunk_in_six_taints_the_whole_turn():
    """This IS the production case. Retrieval returns six chunks every
    turn with no relevance floor, so the realistic shape is five harmless
    project notes and one personal/ excerpt that got swept in. The whole
    prompt goes to one model, so a single sensitive chunk has to pin the
    entire turn local — any "mostly fine" arithmetic here sends the
    excerpt to Groq along with everything else."""
    chunks = [
        {"source": "projects/fred.md", "text": "orchestrator notes"},
        {"source": "knowledge/local-llms.md", "text": "quantisation notes"},
        {"source": "daily/2026-08/2026-08-04.md", "text": "tasks"},
        {"source": "projects/hud.md", "text": "kiosk notes"},
        {"source": "personal/fitness.md — Biometrics", "text": "..."},
        {"source": "knowledge/tts.md", "text": "kokoro notes"},
    ]
    assert sensitive.any_sensitive(chunks)


def test_no_sensitive_chunks_leaves_the_turn_on_the_cloud_path():
    """The false-positive side. A turn about code must not be forced onto
    the local model — that's the cost that makes people disable the
    guard."""
    chunks = [
        {"source": "projects/fred.md", "text": "orchestrator notes"},
        {"source": "knowledge/local-llms.md", "text": "quantisation notes"},
    ]
    assert not sensitive.any_sensitive(chunks)


def test_any_sensitive_handles_no_retrieval_at_all():
    """Turns with no vault hits pass [] or None. Raising here would break
    every non-vault turn in the app."""
    assert not sensitive.any_sensitive([])
    assert not sensitive.any_sensitive(None)


# =========================================================
# LLMClient.local_only — the enforcement half
# =========================================================

def _bare_client(monkeypatch, cloud_calls):
    """
    An LLMClient with no models loaded.

    __init__ loads GGUFs off disk and would make this a several-GB
    integration test, so the instance is built with __new__ and given
    only the attributes the generation paths actually read.

    Cloud entry points record their calls into `cloud_calls` and then
    report failure the way each real one does. Recording rather than
    raising is what the assertions rest on: generate() catches Exception
    around its cloud branch, so a stub that only raised would have its
    complaint swallowed and the test would pass while cloud was being
    called. Failing afterwards puts both cases down the local fallback,
    which makes local_only=True and local_only=False directly
    comparable.
    """
    client = LLMClient.__new__(LLMClient)
    client.default_tier = "Standard"
    client.temperature = 0.7
    client.top_p = 1.0
    client.max_tokens = 256
    client._loaded = {}

    def _cloud_generate(*args, **kwargs):
        cloud_calls.append("generate")
        raise AssertionError("cloud must not be called")

    def _cloud_stream(*args, **kwargs):
        # Returns None rather than raising, because the real one does:
        # it swallows each provider's failure itself and reports "no
        # cloud stream" by returning None, and generate_stream() does not
        # wrap the call in try/except. A raising stub would fail the
        # default-path test for the wrong reason.
        cloud_calls.append("stream")
        return None

    class _FakeModel:
        def create_chat_completion(self, **kwargs):
            return self._respond(**kwargs)

        # "Standard" is in TIER_TEMPLATE_KWARGS (see settings.py) —
        # generate_stream/generate_with_tools call model.chat_handler
        # directly via _native_call rather than create_chat_completion.
        # Real objects always have this attribute (None, or a real
        # handler); a bare object missing it entirely is what
        # create_chat_completion-only fakes never needed before.
        def chat_handler(self, **kwargs):
            return self._respond(**kwargs)

        @staticmethod
        def _respond(**kwargs):
            if kwargs.get("stream"):
                return iter([
                    {"choices": [{"delta": {"content": "local reply"}}]}
                ])
            return {
                "choices": [
                    {"message": {"role": "assistant",
                                 "content": "local reply",
                                 "tool_calls": None}}
                ]
            }

    monkeypatch.setattr(client, "_cloud_generate", _cloud_generate)
    monkeypatch.setattr(client, "_cloud_stream", _cloud_stream)
    monkeypatch.setattr(client, "_get_model", lambda tier: _FakeModel())
    monkeypatch.setattr(
        client, "_generate",
        lambda model, tier, messages, max_tokens=None, force_no_thinking=False: "local reply"
    )
    return client


MESSAGES = [{"role": "user", "content": "what's my weight trend?"}]


@pytest.mark.parametrize("method", ["generate", "generate_stream", "generate_with_tools"])
def test_every_generation_entry_point_takes_local_only(method):
    """All three entry points are reachable from a turn carrying vault
    context — chat streams, chat falls back to generate(), and a tool
    turn goes through generate_with_tools() and feeds the tool RESULT
    back to the model. Miss the kwarg on any one of them and that path
    is still POSTing personal/ to Groq while the other two look fixed."""
    assert "local_only" in inspect.signature(getattr(LLMClient, method)).parameters


def test_generate_never_touches_cloud_when_local_only(monkeypatch):
    """The load-bearing assertion of this whole file. `_cloud_generate`
    records any call before it raises, so this cannot pass by having the
    exception swallowed — generate() catches Exception around the cloud
    branch, and an assert that relied on the raise propagating would be
    silently defeated by that handler."""
    cloud_calls = []
    client = _bare_client(monkeypatch, cloud_calls)

    reply = client.generate(MESSAGES, tier="Standard", local_only=True)

    assert cloud_calls == []
    assert reply == "local reply"


def test_generate_does_try_cloud_by_default(monkeypatch):
    """Proves the previous test isn't vacuous. If cloud were unreachable
    for some unrelated reason — a renamed method, a stub that never fires
    — the local_only test would pass while enforcing nothing. The default
    path must demonstrably hit cloud first for "it didn't" to mean
    anything."""
    cloud_calls = []
    client = _bare_client(monkeypatch, cloud_calls)

    reply = client.generate(MESSAGES, tier="Standard")

    assert cloud_calls == ["generate"]
    assert reply == "local reply"  # cloud failed, local answered


def test_generate_stream_never_touches_cloud_when_local_only(monkeypatch):
    """The streaming path is the one most turns actually take — the
    orchestrator's process_stream() is the default conversation route, so
    a leak here is a leak on ordinary chat, not an edge case."""
    cloud_calls = []
    client = _bare_client(monkeypatch, cloud_calls)

    pieces = list(client.generate_stream(MESSAGES, tier="Standard", local_only=True))

    assert cloud_calls == []
    assert "local reply" in "".join(pieces)


def test_generate_stream_does_try_cloud_by_default(monkeypatch):
    cloud_calls = []
    client = _bare_client(monkeypatch, cloud_calls)

    list(client.generate_stream(MESSAGES, tier="Standard"))

    assert cloud_calls == ["stream"]


def test_generate_with_tools_never_touches_cloud_when_local_only(monkeypatch):
    """A tool that reads personal/ puts the file's contents into a
    tool-result message, and the tool loop sends that message BACK to the
    model for phrasing. So the sensitive text traverses this path on the
    SECOND round even if the first round's prompt was clean — retrieval-
    side checking alone doesn't cover it."""
    cloud_calls = []
    client = _bare_client(monkeypatch, cloud_calls)

    message = client.generate_with_tools(
        MESSAGES, tools=[], tier="Standard", local_only=True
    )

    assert cloud_calls == []
    assert message["content"] == "local reply"


def test_generate_with_tools_does_try_cloud_by_default(monkeypatch):
    cloud_calls = []
    client = _bare_client(monkeypatch, cloud_calls)

    client.generate_with_tools(MESSAGES, tools=[], tier="Standard")

    assert cloud_calls == ["generate"]
