# "Open it" after a search. The spoken search result deliberately
# carries no path (persona.md forbids reading them aloud), which left
# the follow-up with nothing to name — so the referent lives in
# found_cache instead of in the conversation.

from orchestrator.dispatcher import Dispatcher
from tools import found_cache


def test_pronoun_forms_route_to_the_last_result():
    d = Dispatcher()
    for phrase in ("open it", "open that", "open that one", "open the file"):
        result = d.match(phrase)
        assert result["tool"] == "open_last_found", phrase
        assert result["arguments"] == {"which": 1}


def test_ordinals_pick_a_specific_result():
    d = Dispatcher()
    assert d.match("open the second one")["arguments"] == {"which": 2}
    assert d.match("open the third one")["arguments"] == {"which": 3}


def test_a_real_filename_is_not_swallowed_by_the_pronoun_rule():
    """"open dossier.pdf" must still be a file open, not "the last one"."""
    d = Dispatcher()
    assert d.match("open dossier.pdf")["tool"] == "open_path"


def test_last_results_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(found_cache, "CACHE_PATH", tmp_path / "found.json")

    real = tmp_path / "a.txt"
    real.write_text("x", encoding="utf-8")
    found_cache.set_last([real])

    assert found_cache.get_last() == [str(real)]


def test_vanished_paths_are_dropped_not_returned(tmp_path, monkeypatch):
    """A file moved since the search must not be "opened" from stale state."""
    monkeypatch.setattr(found_cache, "CACHE_PATH", tmp_path / "found.json")

    gone = tmp_path / "gone.txt"
    gone.write_text("x", encoding="utf-8")
    found_cache.set_last([gone])
    gone.unlink()

    assert found_cache.get_last() == []
