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


def test_open_that_folder_routes_to_last_result_not_launch_application():
    """
    Confirmed bug (session_2026-08-22.jsonl): "Open that folder." right
    after a create_text_file call fell through the pronoun rule (it only
    covered it/that/that one/one/file/result) into launch_application
    with app_name="that folder.", burning ~27s exhaustively searching
    PATH/registry/Start Menu before failing. Same fix as the "open that
    one" family above — "folder"/"that folder" join the pronoun list,
    and a trailing period (as STT produces) is tolerated too.
    """
    d = Dispatcher()
    for phrase in ("open that folder", "Open that folder.", "open the folder"):
        result = d.match(phrase)
        assert result["tool"] == "open_last_found", phrase
        assert result["tool"] != "launch_application", phrase


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


def test_create_text_file_sets_last_found_to_containing_folder(tmp_path, monkeypatch):
    """
    "Open that folder" right after create_text_file has nothing to
    reference unless create_text_file feeds the same found_cache slot
    open_last_found reads. Points at the parent, since that's what "the
    folder" means, not the file itself.
    """
    monkeypatch.setattr(found_cache, "CACHE_PATH", tmp_path / "found.json")

    import tools.system_tools as system_tools
    monkeypatch.setattr(system_tools, "resolve_user_path", lambda p: tmp_path / p)

    result = system_tools.create_text_file("note.txt", directory=str(tmp_path))
    assert result.startswith("Created file:")
    assert found_cache.get_last() == [str(tmp_path)]


def test_create_folder_sets_last_found_to_the_new_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(found_cache, "CACHE_PATH", tmp_path / "found.json")

    import tools.system_tools as system_tools
    monkeypatch.setattr(system_tools, "resolve_user_path", lambda p: tmp_path / p)

    new_folder = tmp_path / "homework"
    result = system_tools.create_folder("homework", directory=str(tmp_path))
    assert result.startswith("Created folder:")
    assert found_cache.get_last() == [str(new_folder)]


def test_vanished_paths_are_dropped_not_returned(tmp_path, monkeypatch):
    """A file moved since the search must not be "opened" from stale state."""
    monkeypatch.setattr(found_cache, "CACHE_PATH", tmp_path / "found.json")

    gone = tmp_path / "gone.txt"
    gone.write_text("x", encoding="utf-8")
    found_cache.set_last([gone])
    gone.unlink()

    assert found_cache.get_last() == []
