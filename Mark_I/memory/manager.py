"""Mark I memory manager: the one object the rest of FRED will hold.

Sockets: Memory(root) is the only config (put it outside the git repo, it is personal data).
Later layers attach to .store / .archive via their hooks (on_event, on_write); this file only composes.
Usable as a context manager.
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
        self.store.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


if __name__ == "__main__":  # self-check: python manager.py
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        with Memory(d) as m:
            sid = m.archive.new_session()
            m.archive.write("conversation", {"text": "hi"}, session=sid)
            m.store.add_event("note", "hi", source_id=m.store.add_source("archive", sid))
        with Memory(d) as m:  # reopen: data persists
            assert m.store.db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
            assert m.store.db.execute("SELECT COUNT(*) FROM archive_files").fetchone()[0] == 2
            assert m.archive.verify() == []
    print("manager ok")
