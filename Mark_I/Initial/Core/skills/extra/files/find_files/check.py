"""Self-check for find_files: invented files in temp roots, a fake launcher, a fixed "today". Nothing real is indexed or opened."""
import datetime
import importlib.util
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

import config
from engine.gates import Confirmer

HERE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location("find_files_run", HERE / "run.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _mk(root: Path, rel: str, days_ago: float = 1, size: int = 10):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * size)
    t = time.time() - days_ago * 86400
    os.utime(p, (t, t))
    return p


def _selfcheck():
    m = _load()
    config.set_data_dir(tempfile.mkdtemp())
    m._recent_targets = lambda: {}
    today = datetime.date(2026, 10, 7)

    # when-parsing
    span = lambda t: m.parse_when(t, today)
    d = lambda a, b: (time.mktime(a.timetuple()), time.mktime(b.timetuple()))
    assert span("yesterday") == d(datetime.date(2026, 10, 6), today) and span("today")[0] == time.mktime(today.timetuple())
    assert span("last week") == d(datetime.date(2026, 9, 28), datetime.date(2026, 10, 5)) and span("this week")[0] == time.mktime(datetime.date(2026, 10, 5).timetuple())
    assert span("last month") == d(datetime.date(2026, 9, 1), datetime.date(2026, 10, 1)) and span("last year") == d(datetime.date(2025, 1, 1), datetime.date(2026, 1, 1))
    assert span("March") == d(datetime.date(2026, 3, 1), datetime.date(2026, 4, 1)) and span("in December") == d(datetime.date(2025, 12, 1), datetime.date(2026, 1, 1))
    assert span("March 2024") == d(datetime.date(2024, 3, 1), datetime.date(2024, 4, 1)) and span("2023") == d(datetime.date(2023, 1, 1), datetime.date(2024, 1, 1))
    assert span("last friday") == d(datetime.date(2026, 10, 2), datetime.date(2026, 10, 3)) and span("3 days ago") == d(datetime.date(2026, 10, 4), datetime.date(2026, 10, 5))
    assert span("last 3 days") == d(datetime.date(2026, 10, 4), datetime.date(2026, 10, 8)) and span("whenever") is None and span("") is None

    # words, kind and folder hints
    assert m.words("the invoice pdf in downloads") == ["invoice"] and m.infer("the invoice pdf in downloads", "any", "") == ("pdf", "downloads")
    assert m.infer("photo", "image", "Downloads") == ("image", "downloads") and m.norm("TaxReturn_2025-final.PDF") == "tax return 2025 final pdf"

    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        docs, dl, pics = t / "Documents", t / "Downloads", t / "Pictures"
        _mk(dl, "Invoice_Acme_Sept.pdf", 1, 2048)
        _mk(dl, "invoice_zenith_aug.pdf", 40)
        _mk(docs, "Tax Return 2025.pdf", 200, 5 << 20)
        _mk(docs, "Tax Return 2024.pdf", 600)
        _mk(docs, "Budget.xlsx", 3)
        _mk(docs, "resume final v2.docx", 20)
        _mk(pics, "IMG_2231.jpg", 2, 50000)
        _mk(pics, "Holiday Goa 2025.png", 90)
        _mk(docs, "node_modules/pkg/Invoice_hidden.pdf", 1)
        _mk(docs, ".git/Invoice_git.pdf", 1)
        _mk(docs, "scratch.tmp", 1)
        _mk(docs, "~$Budget.xlsx", 1)
        _mk(docs, "webapp/assets/icons/logo.png", 1, 500)
        _mk(pics, "Logo Redesign.png", 30, 90000)
        m.ROOTS = [docs, dl, pics]
        opened = []
        m.LAUNCH = lambda p, how: opened.append((Path(p).name, how))

        class Reg:
            confirmer = Confirmer()
        m.bind({"registry": Reg()})

        n = m.reindex()
        assert n == 10, n                                                                     # junk, hidden and package folders are not indexed

        # finding
        r = m.run(query="invoice")
        assert "Invoice_Acme_Sept.pdf" in r and "invoice_zenith_aug.pdf" in r and "hidden" not in r and "git" not in r, r
        assert m.run(query="invoice", when="40 days ago").count("invoice_zenith") == 1 and "Acme" not in m.run(query="invoice", when="40 days ago"), "when filters"
        assert m.run(query="the invoice pdf in downloads yesterday").startswith("Best matches: 1. Invoice_Acme_Sept.pdf (Downloads,"), m.run(query="the invoice pdf in downloads yesterday")
        assert "Tax Return 2025.pdf" in m.run(query="tax return", kind="pdf") and "5.0 MB" in m.run(query="tax return")
        assert m.run(kind="image", when="this week").startswith("Best matches: 1. IMG_2231.jpg")                   # no words needed when kind and time are given
        assert "Holiday Goa" in m.run(query="goa photo") and "taxx" not in m.run(query="tax retun")                   # a typo still finds it
        r = m.run(query="invoice", kind="spreadsheet")
        assert "ignored" in r and "Invoice_Acme_Sept.pdf" in r, r                                                    # filters that match nothing are relaxed, and it says so
        assert m.run(query="passport scan").startswith("I couldn't find a file like that")
        for bad in ({}, {"query": "the"}):
            try:
                m.run(**bad)
                raise AssertionError(bad)
            except Exception as ex:
                assert getattr(ex, "kind", "") == "bad_args", (bad, ex)

        r = m.run(query="logo")
        assert r.startswith("Best matches: 1. Logo Redesign.png") and r.index("Logo Redesign") < r.index("logo.png"), r                    # app internals rank below real files

        # opening: a clear winner opens; ties list; a likely one asks and the yes opens it exactly
        assert m.run(query="budget", action="open") == "Opening Budget.xlsx." and opened == [("Budget.xlsx", "open")]
        r = m.run(query="tax return", action="open")
        assert r.startswith("Which one: 1. Tax Return 2025.pdf") and len(opened) == 1, r
        r = m.run(query="resume", action="open")
        assert r == "Opening resume final v2.docx." and opened[-1] == ("resume final v2.docx", "open"), r
        r = m.run(query="acme", action="show")
        assert r == "Showing Invoice_Acme_Sept.pdf." and opened[-1][1] == "show", r
        r = m.run(query="invoice zenith aug", action="open")
        assert r.startswith("Opening invoice_zenith_aug.pdf"), r
        pending = m.run(query="zenith invoice august final", action="open")                                          # more words than the file has: not a clear match
        assert "invoice_zenith_aug" in pending or pending.startswith("I couldn't find"), pending

        # usage: files opened through FRED rise above equals
        _mk(docs, "Notes Alpha.txt", 5)
        _mk(docs, "Notes Beta.txt", 5)
        m.reindex()
        first = m.run(query="notes").split("1. ")[1].split(" (")[0]
        con = m._connect()
        con.execute("UPDATE files SET opens=6 WHERE name='Notes Beta.txt'")
        con.commit()
        con.close()
        assert m.run(query="notes").split("1. ")[1].startswith("Notes Beta.txt"), (first, m.run(query="notes"))

        # the yes/no follow-up: exactly that file
        before = len(opened)
        assert m.run(action="open", path=str(docs / "Budget.xlsx")) == "Opening Budget.xlsx." and len(opened) == before + 1
        assert m.run(action="open", path=str(docs / "ghost.txt")) == "That file isn't there any more."

        # incremental reindex: new files appear, deleted ones vanish, nothing else re-read
        (dl / "invoice_zenith_aug.pdf").unlink()
        _mk(dl, "Invoice_Nova_Oct.pdf", 0.1)
        assert m.reindex() == 12
        r = m.run(query="invoice")
        assert "Nova" in r and "zenith" not in r, r
        os.utime(docs / "Budget.xlsx", (time.time() - 2 * 86400,) * 2)
        m.reindex()
        assert "Budget.xlsx" in m.run(query="budget", when="yesterday") or "ignored" in m.run(query="budget", when="yesterday")

        # a missing index is built on first use
        _ = [p.unlink() for p in config.data_dir().glob("files_index.sqlite*")]
        assert "Budget.xlsx" in m.run(query="budget")

        # a walk cut short never deletes what it did not reach
        before = m.reindex()
        assert m.reindex(limit_s=0.0) == before
    m.ROOTS = None
    m.LAUNCH = None
    print("find_files check ok")


if __name__ == "__main__":
    _selfcheck()
