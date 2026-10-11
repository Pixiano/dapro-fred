"""Self-check for news: a stored edition, no network, no model."""
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

HERE = Path(__file__).resolve().parent


def _selfcheck():
    spec = importlib.util.spec_from_file_location("news_run", HERE / "run.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    ed = {"at": "06:00", "built": "2026-10-11T06:00:00", "items": [
        {"n": 1, "slot": "ai", "title": "T1", "short": "Short one.", "detail": "Detailed paragraph one.", "source": "Hugging Face blog", "url": "http://x/1"},
        {"n": 2, "slot": "boards", "title": "T2", "short": "CBSE dates out.", "detail": "Detail two.", "source": "The Hindu", "url": ""}]}
    m.LATEST = lambda: ed
    out = m.run()
    assert "1. [ai] Short one. (source: Hugging Face blog)" in out and "2. [boards]" in out and "Untrusted" in out
    assert m.run("boards").count("[boards]") == 1 and "[ai]" not in m.run("boards")
    assert "Detailed paragraph one." in m.run(number=1) and "http://x/1" in m.run(number=1) and "Source: The Hindu" in m.run(number=2)
    assert "nothing under world" in m.run("world")
    try:
        m.run(number=9)
        raise AssertionError("bad number accepted")
    except m.ToolError:
        pass
    m.LATEST = lambda: None
    assert m.run() == "I have no news digest yet."
    print("news ok")


if __name__ == "__main__":
    _selfcheck()
