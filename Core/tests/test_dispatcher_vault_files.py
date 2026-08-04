# Confirmed bug (session_2026-08-03.jsonl): "open up active priorities
# for me." and "open up the file called activepriorities.md please."
# both fell through the strict single-token website/file rule (they
# have spaces and filler words) into the generic launch_application
# catch-all, which then tried to launch an "app" literally named "up
# active priorities for me." — garbled arguments, always fails.
#
# The new vault-file rule sits ahead of that catch-all and declines
# (returns None, falling through unchanged) whenever nothing in the
# vault matches, so this must not regress ordinary app/website opens.

import tools.vault_files as vault_files
from orchestrator.dispatcher import Dispatcher


def _stub_resolver(monkeypatch, match_path):
    """Vault lookup used by the dispatcher's real-import inside its
    handler — patching the module-level function is what that import
    picks up."""
    monkeypatch.setattr(vault_files, "resolve_vault_file", lambda name: match_path)


def test_vault_phrasing_that_previously_misrouted(monkeypatch):
    _stub_resolver(monkeypatch, "/vault/active-priorities.md")
    d = Dispatcher()

    result = d.match("open up active priorities for me.")
    assert result == {
        "tool": "open_vault_file",
        "arguments": {"name": "active priorities"},
    }


def test_file_called_phrasing(monkeypatch):
    _stub_resolver(monkeypatch, "/vault/active-priorities.md")
    d = Dispatcher()

    result = d.match("open up the file called activepriorities.md please.")
    assert result == {
        "tool": "open_vault_file",
        "arguments": {"name": "activepriorities.md"},
    }


def test_non_vault_open_still_reaches_launch_application(monkeypatch):
    _stub_resolver(monkeypatch, None)
    d = Dispatcher()

    result = d.match("open spotify")
    assert result == {"tool": "launch_application", "arguments": {"app_name": "spotify"}}


def test_non_vault_website_still_reaches_open_website(monkeypatch):
    _stub_resolver(monkeypatch, None)
    d = Dispatcher()

    result = d.match("open youtube.com")
    assert result == {"tool": "open_website", "arguments": {"url": "https://youtube.com"}}


def test_open_it_pronoun_still_wins_over_vault_rule(monkeypatch):
    # Must never reach the vault resolver at all — "it" is a pronoun
    # follow-up (open_last_found), not a vault name.
    calls = []
    monkeypatch.setattr(
        vault_files, "resolve_vault_file",
        lambda name: calls.append(name) or None,
    )
    d = Dispatcher()

    result = d.match("open it")
    assert result == {"tool": "open_last_found", "arguments": {"which": 1}}
    assert calls == []
