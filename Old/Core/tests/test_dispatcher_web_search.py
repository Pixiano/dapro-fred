# Bug #1 — web search losing conversation context because the
# deterministic Dispatcher intercepted it before the LLM ever ran.

from orchestrator.dispatcher import Dispatcher


def test_pronoun_led_search_declines_to_llm_path():
    d = Dispatcher()
    # Real transcript: session_2026-08-01_14-24-11.jsonl — dispatched
    # with query="it on the web..." before this fix, losing the
    # "Opus 5 pricing" topic from the prior turn.
    assert d.match("Search it on the web. It has been released.") is None


def test_self_contained_search_still_dispatches_instantly():
    d = Dispatcher()
    result = d.match("search for the weather in Paris")
    assert result == {
        "tool": "web_search",
        "arguments": {"query": "the weather in Paris"},
    }


def test_local_file_search_is_not_a_web_search():
    """
    Real transcript: session_2026-08-01_18-41-50.jsonl — "Search my
    desktop for dossier.pdf." dispatched to web_search and read out
    results about moving Windows folders and free PDF editors. A search
    scoped to this machine belongs on the LLM tool path, where
    search_files/find_file_smart are actually reachable.
    """
    d = Dispatcher()
    assert d.match("Search my desktop for dossier.pdf.") is None
    assert d.match("search my downloads for invoice") is None


def test_web_search_survives_the_local_cue_guard():
    """The guard must not swallow genuine web searches."""
    d = Dispatcher()
    for phrase in (
        "google the capital of France",
        "search for World Cup 2026 results",
    ):
        assert d.match(phrase)["tool"] == "web_search", phrase
