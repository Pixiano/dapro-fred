# Tool output is spoken aloud, so "what does this return" and "what
# does this sound like" are the same question. These pin the cases
# where the answer was previously unlistenable.

from tools import machine_tools, web_tools


def test_web_search_never_returns_a_url(monkeypatch):
    """
    Confirmed in session_2026-08-01_14-24-11.jsonl: a search result's
    full YouTube link was read out character by character. Nothing
    downstream can follow a link anyway.
    """
    fake = [
        {
            "title": "Some Video",
            "body": "A description.",
            "href": "https://www.youtube.com/watch?v=CKZvWhCqx1s",
        }
    ]

    class FakeDDGS:
        def text(self, query, max_results=5):
            return fake

    monkeypatch.setattr(web_tools, "DDGS", lambda: FakeDDGS())

    result = web_tools.web_search("anything")
    assert "http" not in result
    assert "youtube.com" not in result
    assert "Some Video" in result


def test_web_search_trims_essay_length_snippets(monkeypatch):
    long_body = "word " * 500

    class FakeDDGS:
        def text(self, query, max_results=5):
            return [{"title": "T", "body": long_body, "href": "https://x.com"}]

    monkeypatch.setattr(web_tools, "DDGS", lambda: FakeDDGS())

    result = web_tools.web_search("anything")
    assert len(result) < 500
    assert result.rstrip().endswith("...")


def test_clipboard_is_capped_for_speech(monkeypatch):
    """A copied article shouldn't commit FRED to minutes of speech."""
    monkeypatch.setattr(machine_tools.pyperclip, "paste", lambda: "x " * 5000)

    result = machine_tools.get_clipboard()
    assert len(result) < 900
    assert "more characters" in result


def test_empty_clipboard_says_so(monkeypatch):
    monkeypatch.setattr(machine_tools.pyperclip, "paste", lambda: "")
    assert machine_tools.get_clipboard() == "Clipboard is empty."


def test_short_clipboard_passes_through(monkeypatch):
    monkeypatch.setattr(machine_tools.pyperclip, "paste", lambda: "hello there")
    assert machine_tools.get_clipboard() == "hello there"
