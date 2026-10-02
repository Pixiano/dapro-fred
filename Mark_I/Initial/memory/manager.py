"""Mark I memory manager: the one object the rest of FRED will hold.

Sockets: Memory(root) is the only config (put it outside the git repo, it is personal data).
Later layers attach to .store / .archive via their hooks (on_event, on_write); this file only composes.
"""
from pathlib import Path

from archive import Archive
from store import MemoryStore


class Memory:
    def __init__(self, root):
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        self.store = MemoryStore(root / "memory.sqlite")
        self.archive = Archive(root / "archive", self.store)

    def close(self):
        self.store.db.close()


if __name__ == "__main__":  # self-check: python manager.py
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        m = Memory(d)
        sid = m.archive.new_session()
        m.archive.write("conversation", {"text": "hi"}, session=sid)
        m.store.add_event("note", "hi", source_id=m.store.add_source("archive", sid))
        m.close()
        m2 = Memory(d)  # reopen: data persists
        assert len(m2.store.events_on(m2.archive.read("session_state", __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).strftime("%Y-%m-%d"))[0]["ts"][:10])) == 1
        assert m2.archive.verify() == []
        m2.close()
    print("manager ok")
