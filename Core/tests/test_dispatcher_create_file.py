# Confirmed bug 2026-08-12 — the deterministic create-file/folder
# routes captured the ENTIRE rest of the sentence as a literal
# filename, including location, date substitution, and content
# instructions, none of which the route can actually apply (it never
# sends a `content` argument or resolves a date).

from orchestrator.dispatcher import Dispatcher


def test_compound_file_request_declines_to_llm_path():
    d = Dispatcher()
    # Real transcript, 2026-08-12: this dispatched with
    # filename="on the desktop called daily logs dash today's date and
    # then in it you can write three tasks which is CHEMMAS, second
    # one is ENGMAS and then third one is SSMAPS." — a garbage .txt
    # name, wrong location, no date resolved, no content written.
    text = (
        "create a file called daily logs dash today's date and then "
        "in it you can write three tasks which is CHEMMAS, second one "
        "is ENGMAS and then third one is SSMAPS."
    )
    assert d.match(text) is None


def test_simple_file_request_still_dispatches_instantly():
    d = Dispatcher()
    result = d.match("create a file called shopping list")
    assert result == {
        "tool": "create_text_file",
        "arguments": {"filename": "shopping list"},
    }


def test_compound_folder_request_declines_to_llm_path():
    d = Dispatcher()
    text = "create a folder called projects and then add three subfolders for each client"
    assert d.match(text) is None


def test_simple_folder_request_still_dispatches_instantly():
    d = Dispatcher()
    result = d.match("create a folder called homework")
    assert result == {
        "tool": "create_folder",
        "arguments": {"folder_name": "homework"},
    }
