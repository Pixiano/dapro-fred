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
import hashlib, json, logging, re, shutil, uuid
from datetime import datetime, timezone
from pathlib import Path

from store import run_hooks, to_utc

_SCHEMA = Path(__file__).with_name("schema_archive.sql")
_STREAM = re.compile(r"^[a-z0-9_]{1,40}$")  # becomes a filename: no separators, no traversal


def parse_lines(text):
    """Records of a day file. Splits on the newline character ONLY (U+2028, U+2029 and U+0085 can sit inside text, and str.splitlines
    would cut a record there); a line that does not parse is skipped and counted. Returns (records, bad)."""
    out, bad = [], 0
    for line in text.split("\n"):
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                bad += 1
    return out, bad


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Archive:
    def __init__(self, root, store):
        self.root, self.store, self.on_write = Path(root), store, []
        self.lock = store.lock  # one lock for the shared connection
        self.root.mkdir(parents=True, exist_ok=True)
        store.db.executescript(_SCHEMA.read_text(encoding="utf-8"))

    def new_session(self) -> str:
        sid = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:6]
        self.write("session_state", {"event": "start"}, session=sid)
        return sid

    def write(self, stream, data, session=None, ts=None, source_id=None, kind="live") -> dict:
        if not _STREAM.match(stream):
            raise ValueError(f"bad stream name {stream!r}: use a-z, 0-9, _")
        ts = to_utc(ts) if ts else _now()   # the folder comes from the parsed UTC date, never from raw text
        rec = {"ts": ts, "stream": stream, "session": session, "kind": kind, "source_id": source_id, "data": data}
        day = ts[:10]
        path = self.root / day[:4] / day[5:7] / day[8:10] / f"{stream}.jsonl"
        line = json.dumps(rec, ensure_ascii=False, default=str) + "\n"
        with self.lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size:  # a torn last line (crash mid-write) must not swallow this record
                with open(path, "rb") as f:
                    f.seek(-1, 2)
                    if f.read(1) != b"\n":
                        line = "\n" + line
            with open(path, "a", encoding="utf-8", newline="\n") as f:
                f.write(line)
            self.store.db.execute(
                "INSERT INTO archive_files VALUES(?,?,?,1,?,?) ON CONFLICT(path) DO UPDATE SET "
                "records=records+1, last_ts=excluded.last_ts", (path.relative_to(self.root).as_posix(), stream, day, ts, ts))
            self.store.db.commit()
        run_hooks(self.on_write, rec)  # outside the lock; a failing hook never loses the record
        return rec

    def put_file(self, src, session=None, name=None) -> str:
        """Copy a file in by content hash (dedupes identical content) and log the reference. Returns sha256."""
        src = Path(src)
        h = hashlib.sha256()
        with open(src, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        sha = h.hexdigest()
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
        recs, bad = parse_lines(path.read_text(encoding="utf-8"))
        if bad:
            logging.getLogger("memory").warning("%s: %d unreadable line(s) skipped", path, bad)
        return recs

    def verify(self) -> list:
        """Return paths whose line count disagrees with the index or that hold an unreadable line (empty list = nothing lost or torn).
        It does not detect an edit inside a line."""
        bad = []
        with self.lock:
            rows = self.store.db.execute("SELECT path, records FROM archive_files").fetchall()
        for r in rows:
            p = self.root / r["path"]
            recs, torn = parse_lines(p.read_text(encoding="utf-8")) if p.exists() else ([], -1)
            if torn or len(recs) != r["records"]:
                bad.append(r["path"])
        return bad


if __name__ == "__main__":  # self-check: python archive.py
    logging.getLogger("memory").setLevel(logging.CRITICAL)  # the broken test hook logs on purpose
    import tempfile
    from store import MemoryStore
    with tempfile.TemporaryDirectory() as d:
        s = MemoryStore(":memory:")
        a = Archive(Path(d) / "archive", s)
        seen = []; a.on_write.append(lambda r: 1 / 0); a.on_write.append(seen.append)  # broken hook is isolated
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
        for bad in ("../evil", "a/b", "A", ""):
            try:
                a.write(bad, {}); raise SystemExit(f"accepted {bad!r}")
            except ValueError:
                pass
        # review fixes: unusual line separators inside text, a torn last line, hostile or naive timestamps
        a2 = Archive(Path(d) / "a2", MemoryStore(":memory:"))
        a2.write("conversation", {"text": "pasted\u2028text\u0085here"}, ts="2026-05-01T10:00:00+00:00")
        a2.write("conversation", {"text": "next"}, ts="2026-05-01T10:00:01+00:00")
        assert [r["data"]["text"] for r in a2.read("conversation", "2026-05-01")] == ["pasted\u2028text\u0085here", "next"] and a2.verify() == []
        f2 = Path(d) / "a2" / "2026" / "05" / "01" / "conversation.jsonl"
        with open(f2, "ab") as fh:
            fh.write(b'{"ts": "torn')                                                           # a crash in the middle of a write
        a2.write("conversation", {"text": "after the crash"}, ts="2026-05-01T10:00:02+00:00")
        assert [r["data"]["text"] for r in a2.read("conversation", "2026-05-01")] == ["pasted\u2028text\u0085here", "next", "after the crash"]
        assert a2.verify() == ["2026/05/01/conversation.jsonl"]                                # the torn piece is reported, nothing is swallowed
        for ts in ("../.X./Y..", "2026-1-5T10:00:00Z", "2026-05-01T10:00:00", "garbage"):
            try:
                a2.write("conversation", {}, ts=ts); raise SystemExit(f"accepted ts {ts!r}")
            except ValueError:
                pass
        a2.write("conversation", {"text": "ist"}, ts="2026-05-02T01:00:00+05:30")             # local time: filed under its UTC day
        assert a2.read("conversation", "2026-05-01")[-1]["data"]["text"] == "ist"
    print("archive ok")
