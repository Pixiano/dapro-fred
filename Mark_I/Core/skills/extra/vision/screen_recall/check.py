"""Self-check for screen_recall: invented visits in a temp memory (fake embedder), time windows, text search, image search, nothing found."""
import importlib.util
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

import config
from proactive import screenmem
from skills.testkit import memory_ctx

HERE = Path(__file__).resolve().parent


def _selfcheck():
    config.set_data_dir(tempfile.mkdtemp())
    config.get().screen_img_enabled = False                  # no real image model here; the image path is tested with a fake
    spec = importlib.util.spec_from_file_location("screen_recall_run", HERE / "run.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    now = datetime.now(config.IST)
    yday = (now - timedelta(days=1)).replace(hour=15, minute=5, second=0, microsecond=0)
    with tempfile.TemporaryDirectory() as t:
        mem, ctx = memory_ctx(t)
        m.bind(ctx)
        screenmem._table(mem)
        v = {"red": np.eye(8, dtype=np.float32)[3], "doc": np.eye(8, dtype=np.float32)[0]}

        def visit(ts, app, title, text, img=None):
            eid = mem.store.add_event("screen", f"Screen {ts:%d %b %H:%M} {screenmem.caption(app, title, text)}\n{text}", importance=0.1, ts=ts.isoformat())
            mem.store.db.execute("INSERT INTO screen_visits VALUES(?,?,?,?,?,1,?,?)", (eid, app, title, ts.isoformat(), ts.isoformat(), "fake-img" if img is not None else None, img.tobytes() if img is not None else None))
            mem.store.db.commit()

        visit(yday, "chrome.exe", "Physics lab report - Docs", "Lab report three\nforce and acceleration results", v["doc"])
        visit(yday + timedelta(minutes=20), "code.exe", "plot.py - project", "import matplotlib\nplot the graph", v["red"])
        visit(yday - timedelta(hours=5), "chrome.exe", "Cooking blog", "pasta with tomatoes\nboil water")
        visit(now - timedelta(minutes=1), "notepad.exe", "todo.txt", "buy milk\ncall the plumber")
        r = m.run(when="yesterday at 3pm")
        assert "Physics lab report" in r and "plot.py" in r and "Cooking" not in r and "todo.txt" not in r and "untrusted" in r, r      # a 90-minute window around 15:00
        assert m.run(when="yesterday").count("\n") == 3                                                                       # the whole day: 3 visits (+ header)
        assert "todo.txt" in m.run(when="today") and "Physics" not in m.run(when="today")
        r = m.run(query="what was that page about the lab report", when="yesterday")
        assert r.splitlines()[1].split(" | ")[2] == "Physics lab report - Docs", r
        assert "tomatoes" in m.run(query="pasta recipe").split("\n", 1)[1]                                                   # no time at all: searches everything
        m.IMAGE = type("I", (), {"model": "fake-img", "embed_text": staticmethod(lambda q: v["red"] if "red" in q else v["doc"])})
        r = m.run(query="the red chart", when="yesterday")
        assert "plot.py" in r.splitlines()[1] or "plot.py" in r.splitlines()[2], r                                           # image similarity helps the ranking
        assert m.run(query="zebra", when="last year") == "I have nothing from the screen for that."
        mem.close()
    try:
        spec2 = importlib.util.spec_from_file_location("screen_recall_run2", HERE / "run.py")
        m2 = importlib.util.module_from_spec(spec2)
        spec2.loader.exec_module(m2)
        m2.bind({})
        raise AssertionError("bound without memory")
    except Exception as ex:
        assert type(ex).__name__ == "Unavailable"
    print("screen_recall ok")


if __name__ == "__main__":
    _selfcheck()
