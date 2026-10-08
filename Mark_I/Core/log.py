"""Core logging: three streams under <data_dir>/logs, JSON lines, thread-safe, size-rotated.

Sockets
  in : session(kind, **data)  conversation/engine facts (the real log)
       tool(name, **data)     one line per tool call (args, result kind, ms)
       install_crash()        faulthandler -> logs/crash.log (native crashes, hangs; call once at process start)
  out: logs/session.jsonl, logs/tools.jsonl, logs/crash.log under config.data_dir(); the dir is resolved on every write,
       so config.set_data_dir(tmp) in a test sends output to the temp dir and never to the real logs.
Nothing here raises: a failing log write must never break a turn (it is counted in `dropped`).
Self-check: python log.py
"""
import faulthandler
import json
import threading
import time

import config

_lock = threading.Lock()
_crash_file = None
dropped = 0


def _dir():
    d = config.data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write(stream: str, rec: dict):
    global dropped
    try:
        line = json.dumps({"ts": round(time.time(), 3), **rec}, ensure_ascii=False, default=str)
        with _lock:
            p = _dir() / f"{stream}.jsonl"
            if p.exists() and p.stat().st_size > config.get().log_max_bytes:
                p.replace(p.with_suffix(f".{int(time.time())}.jsonl"))
            with open(p, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        dropped += 1


def session(kind: str, **data):
    _write("session", {"kind": kind, **data})


def tool(name: str, **data):
    _write("tools", {"tool": name, **data})


def install_crash():
    """Route faulthandler to logs/crash.log (kept open for the process lifetime)."""
    global _crash_file
    if _crash_file is None:
        _crash_file = open(_dir() / "crash.log", "a", buffering=1)
        faulthandler.enable(_crash_file, all_threads=True)


def _selfcheck():
    import tempfile
    from pathlib import Path
    real = config.data_dir()
    before = (real / "logs/session.jsonl").stat().st_size if (real / "logs/session.jsonl").exists() else 0
    with tempfile.TemporaryDirectory() as t:
        config.set_data_dir(t)
        session("hello", text="héllo")
        tool("get_time", ok=True, ms=3)
        rows = [json.loads(x) for x in (Path(t) / "logs/session.jsonl").read_text(encoding="utf-8").splitlines()]
        assert rows[0]["kind"] == "hello" and rows[0]["text"] == "héllo"
        assert json.loads((Path(t) / "logs/tools.jsonl").read_text().splitlines()[0])["tool"] == "get_time"
        session("obj", x=object())   # non-serialisable must not raise
        assert dropped == 0
        config.set_data_dir(real)
    after = (real / "logs/session.jsonl").stat().st_size if (real / "logs/session.jsonl").exists() else 0
    assert after == before   # the real log was not touched
    print("log ok")


if __name__ == "__main__":
    _selfcheck()
