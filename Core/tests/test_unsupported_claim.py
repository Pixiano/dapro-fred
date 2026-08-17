# Core/tests/test_unsupported_claim.py
#
# The fabrication guard used to ask only "did any tool run?". One
# unrelated tool running was therefore enough to wave a falsehood through.
#
# Confirmed live 2026-08-17: asked to log the day, FRED called
# create_folder on a folder that had existed for two weeks, then said
# "File created: daily/2026-08/2026-08-17.md with the session log."
# Nothing wrote the file - its mtime never moved - but because
# create_folder HAD run, the guard stayed quiet and the claim was spoken.

from orchestrator.orchestrator import FREDOrchestrator


def _fred():
    return FREDOrchestrator.__new__(FREDOrchestrator)


def test_nothing_ran_and_a_claim_is_made():
    fred = _fred()
    assert fred._unsupported_claim("Deleted the file, sir.", [])


def test_the_real_failure_a_folder_ran_but_a_file_was_claimed():
    fred = _fred()
    results = ["Created folder: C:\\vault\\daily\\2026-08"]
    claim = "File created: `daily/2026-08/2026-08-17.md` with the session log."
    assert fred._unsupported_claim(claim, results)


def test_a_claim_the_tools_actually_support_passes():
    fred = _fred()
    results = ["File created: C:\\vault\\daily\\2026-08\\2026-08-17.md"]
    claim = "Created daily/2026-08/2026-08-17.md, sir."
    assert not fred._unsupported_claim(claim, results)


def test_absolute_windows_path_still_matches_a_relative_claim():
    # The tool reports an absolute path; the reply says a vault-relative
    # one. Matching on the basename is what makes those agree.
    fred = _fred()
    results = ["Saved to C:\\Users\\x\\vault\\daily\\2026-08\\notes.md"]
    assert not fred._unsupported_claim("Updated notes.md for you.", results)


def test_no_named_artifact_is_not_treated_as_evidence():
    # Something ran and the reply claims an action but names no file.
    # Too weak a signal to call a fabrication - stay quiet rather than
    # nag on ordinary successful turns.
    fred = _fred()
    assert not fred._unsupported_claim("Muted, sir.", ["Muted"])


def test_a_question_is_never_a_claim():
    fred = _fred()
    assert not fred._unsupported_claim("Shall I create notes.md?", [])


def test_quoting_docs_is_not_a_write_claim():
    # ask_about_myself returns documentation excerpts full of filenames
    # and words like "added" / "set". A reply built from those must not
    # read as claiming to have written a file - a false "I haven't
    # actually done that" on an ordinary question is its own bug, and one
    # was seen live on 2026-08-17 from the older guard.
    fred = _fred()
    results = ["From README.md: FRED has ~80 registered tools."]
    reply = ("PHONE.md says calling was added in August, and settings.py "
             "set the default tier to Qwen3-8B.")
    assert not fred._unsupported_claim(reply, results)
