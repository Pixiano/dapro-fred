# "Open <thing>" has to tell a filename from a hostname. Confirmed
# failure 2026-08-02: FRED had just found dossier.pdf on the Desktop;
# "Open dossier.pdf" opened a browser at https://dossier.pdf.

from orchestrator.dispatcher import Dispatcher


def test_document_opens_as_a_file_not_a_url():
    d = Dispatcher()
    assert d.match("Open dossier.pdf") == {
        "tool": "open_path",
        "arguments": {"path": "dossier.pdf"},
    }


def test_media_and_archive_names_are_files_too():
    d = Dispatcher()
    for name in ("report.docx", "clip.mp4", "backup.zip", "notes.md"):
        result = d.match(f"open {name}")
        assert result["tool"] == "open_path", name


def test_bare_domains_still_open_in_the_browser():
    d = Dispatcher()
    assert d.match("open youtube.com") == {
        "tool": "open_website",
        "arguments": {"url": "https://youtube.com"},
    }


def test_unknown_tlds_still_go_to_the_browser():
    """The suffix list names files, not TLDs, so a new TLD needs no
    maintenance here."""
    d = Dispatcher()
    assert d.match("open something.quux")["tool"] == "open_website"


def test_explicit_scheme_is_always_a_url():
    d = Dispatcher()
    assert d.match("open https://example.com/a.pdf") == {
        "tool": "open_website",
        "arguments": {"url": "https://example.com/a.pdf"},
    }
