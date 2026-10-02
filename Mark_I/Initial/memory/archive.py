"""Mark I memory, Layer 1: raw archive. Append-only, complete, never edited.

Everything FRED produces is written as one JSON line to <root>/YYYY/MM/DD/<stream>.jsonl
(UTC day). Streams are free-form names; suggested: conversation, tool_call, session_state, speech,
perception, system, error, generated, import. Files and artifacts go in as content-addressed blobs.

Sockets:
  - write(stream, data, session, ts, source_id): the one call every producer makes. Later integration
    points FRED's event bus here; nothing in this file knows about FRED.
  - on_write: list of callables(record) run after each write (episodic extraction plugs in here).
  - Archive(root, store): both injected. root should live outside the git repo (personal data).
  - Historical imports set kind="historical" so they are never mistaken for live observation.
"""
import hashlib, json, shutil, threading, uuid
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = Path(__file__).with_name("schema_archive.sql")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Archive:
    def __init__(self, root, store):
        self.root, self.store, self.on_write = Path(root), store, []
        self.lock = threading.Lock()
        self.root.mkdir(parents=True, exist_ok=True)
        store.db.executescript(_SCHEMA.read_text(encoding="utf-8"))

    def new_session(self) -> str:
        sid = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:6]
        self.write("session_state", {"event": "start"}, session=sid)
        return sid

    def write(self, stream, data, session=None, ts=None, source_id=None, kind="live") -> dict:
        ts = ts or _now()
        rec = {"ts": ts, "stream": stream, "session": session, "kind": kind, "source_id": source_id, "data": data}
        day = ts[:10]
        path = self.root / day[:4] / day[5:7] / day[8:10] / f"{stream}.jsonl"
        line = json.dumps(rec, ensure_ascii=False, default=str) + "\n"
        with self.lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
            self.store.db.execute(
                "INSERT INTO archive_files VALUES(?,?,?,1,?,?) ON CONFLICT(path) DO UPDATE SET "
                "records=records+1, last_ts=excluded.last_ts", (path.relative_to(self.root).as_posix(), stream, day, ts, ts))
            self.store.db.commit()
        for hook in self.on_write:
            hook(rec)
        return rec

    def put_file(self, src, session=None, name=None) -> str:
        """Copy a file in by content hash (dedupes identical content) and log the reference. Returns sha256."""
        src = Path(src)
        sha = hashlib.sha256(src.read_bytes()).hexdigest()
        dst = self.root / "blobs" / sha[:2] / sha
        with self.lock:
            if not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)
            self.store.db.execute("INSERT OR IGNORE INTO archive_blobs VALUES(?,?,?,?,?,?)",
                                  (sha, dst.relative_to(self.root).as_posix(), dst.stat().st_size, name or src.name, session, _now()))
            self.store.db.commit()
        self.write("generated", {"blob": sha, "name": name or src.name}, session=session)
        return sha

    def read(self, stream, day):
        path = self.root / day[:4] / day[5:7] / day[8:10] / f"{stream}.jsonl"
        if not path.exists():
            return []
        return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l]

    def verify(self) -> list:
        """Return paths whose line count disagrees with the index (empty list = archive intact)."""
        bad = []
        for r in self.store.db.execute("SELECT path, records FROM archive_files"):
            p = self.root / r["path"]
            n = sum(1 for _ in open(p, encoding="utf-8")) if p.exists() else -1
            if n != r["records"]:
                bad.append(r["path"])
        return bad


if __name__ == "__main__":  # self-check: python archive.py
    import tempfile
    from store import MemoryStore
    with tempfile.TemporaryDirectory() as d:
        s = MemoryStore(":memory:")
        a = Archive(Path(d) / "archive", s)
        seen = []; a.on_write.append(seen.append)
        sid = a.new_session()
        a.write("conversation", {"role": "user", "text": "héllo"}, session=sid, ts="2026-03-04T10:00:00+00:00")
        a.write("conversation", {"role": "assistant", "text": "hi"}, session=sid, ts="2026-03-04T10:00:05+00:00")
        a.write("tool_call", {"name": "time", "args": {}}, session=sid, ts="2026-03-04T10:00:03+00:00")
        a.write("conversation", {"text": "old chat"}, ts="2020-01-01T00:00:00+00:00", kind="historical")
        got = a.read("conversation", "2026-03-04")
        assert [g["data"].get("role") for g in got] == ["user", "assistant"] and got[0]["data"]["text"] == "héllo"
        assert a.read("conversation", "2020-01-01")[0]["kind"] == "historical"
        assert len(seen) == 5 and a.verify() == []
        f = Path(d) / "x.txt"; f.write_text("artifact")
        assert a.put_file(f, sid) == a.put_file(f, sid)  # same content, one blob
        assert s.db.execute("SELECT COUNT(*) FROM archive_blobs").fetchone()[0] == 1
        with open(Path(d) / "archive" / "2026" / "03" / "04" / "tool_call.jsonl", "a") as fh: fh.write("{}\n")
        assert a.verify() == ["2026/03/04/tool_call.jsonl"]  # tamper/loss is detected
    print("archive ok")
