"""Mark I memory: semantic recall over events. Standalone; wired into FRED at the integration step.

Sockets:
  - embed(text, is_query) -> list[float]: injected. Production passes the embedder
    (v1.2's MemoryManager._generate_embedding has this exact signature); tests pass a fake.
  - model: name of the embedder. Vectors are stored per (event, model), so swapping models never mixes
    vector spaces: change `model`, call sync(), done. All vectors of one model must share one dimension.
  - attach(): registers on MemoryStore.on_event, so every captured event gets a vector. If embedding
    fails the event is still saved (hooks are isolated) and sync() picks it up later, so the index
    can never silently drift from events the way v1.2's FAISS file vs jsonl did.
  - Backend is a FAISS flat (exact) inner-product index, rebuilt lazily from the vectors in SQLite. SQLite
    is the truth and FAISS only a cache, so the two can never drift (v1.2's FAISS file vs jsonl did).
    ponytail: flat = exact, fine to ~1M events; switch to an IVF/HNSW index in _index() beyond that.
  - search(): hybrid = vector recall + FTS5 keyword match, merged by reciprocal rank fusion, cold events
    excluded (see hygiene.py), and whatever it returns is marked recalled so it stays warm.
"""
import re
from datetime import datetime, timezone
from pathlib import Path

import faiss
import numpy as np

_HERE = Path(__file__).parent


class SemanticIndex:
    def __init__(self, store, embed, model="unset"):
        self.store, self.embed, self.model = store, embed, model
        with store.lock:
            for f in ("schema_hygiene.sql", "schema_embeddings.sql"):
                store.db.executescript((_HERE / f).read_text(encoding="utf-8"))
        self._m = None  # cached (model, ids, matrix)

    def attach(self):
        self.store.on_event.append(lambda ev: self.add(ev["id"], ev["content"]))

    def add(self, event_id, text):
        v = self._unit(self.embed(text, False))
        with self.store.lock:
            self.store.db.execute("INSERT OR REPLACE INTO embeddings VALUES(?,?,?)", (event_id, self.model, v.tobytes()))
            self.store.db.commit()
        self._m = None

    def sync(self) -> int:
        """Embed every event that has no vector for the current model. Returns how many."""
        with self.store.lock:
            rows = self.store.db.execute("SELECT id, content FROM events WHERE id NOT IN "
                                         "(SELECT event_id FROM embeddings WHERE model=?)", (self.model,)).fetchall()
        for r in rows:
            self.add(r["id"], r["content"])
        return len(rows)

    def clear(self):
        """Drop the current model's vectors (other models' vectors stay)."""
        with self.store.lock:
            self.store.db.execute("DELETE FROM embeddings WHERE model=?", (self.model,))
            self.store.db.commit()
        self._m = None

    def _index(self):
        if self._m is None or self._m[0] != self.model:
            with self.store.lock:
                rows = self.store.db.execute("SELECT event_id, vec FROM embeddings WHERE model=?", (self.model,)).fetchall()
            ix = None
            if rows:
                m = np.array([np.frombuffer(r[1], np.float32) for r in rows])
                ix = faiss.IndexFlatIP(m.shape[1])
                ix.add(m)
            self._m = (self.model, [r[0] for r in rows], ix)
        return self._m[1], self._m[2]

    def _events(self, ids):
        with self.store.lock:
            return {r["id"]: dict(r) for r in self.store.db.execute(
                f"SELECT * FROM events WHERE id IN ({','.join('?' * len(ids))})", ids)} if ids else {}

    def _cold(self):
        with self.store.lock:
            return {r[0] for r in self.store.db.execute("SELECT event_id FROM cold_events")}

    def recall(self, query, k=5, include_cold=False):
        """Top-k events by cosine similarity, best first, each with a `score`."""
        ids, ix = self._index()
        if not ids:
            return []
        cold = set() if include_cold else self._cold()
        D, I = ix.search(self._unit(self.embed(query, True))[None], min(len(ids), k + len(cold)))
        hits = [(ids[i], float(d)) for d, i in zip(D[0], I[0]) if i >= 0 and ids[i] not in cold][:k]
        evs = self._events([h[0] for h in hits])
        return [dict(evs[e], score=s) for e, s in hits]

    def keyword(self, query, k=5, include_cold=False):
        """FTS5 match on any word of the query, best (bm25) first."""
        words = re.findall(r"\w+", query)
        if not words:
            return []
        cold = set() if include_cold else self._cold()
        with self.store.lock:
            ids = [r[0] for r in self.store.db.execute(
                "SELECT rowid FROM events_fts WHERE events_fts MATCH ? ORDER BY rank LIMIT ?",
                (" OR ".join(f'"{w}"' for w in words), k + len(cold)))]
        evs = self._events([i for i in ids if i not in cold][:k])
        return [evs[i] for i in ids if i in evs]

    def search(self, query, k=5, include_cold=False, touch=True):
        """Hybrid: vector + keyword, reciprocal rank fusion (k=60). Returned events are marked recalled."""
        score, evs = {}, {}
        for hits in (self.recall(query, k * 3, include_cold), self.keyword(query, k * 3, include_cold)):
            for rank, ev in enumerate(hits):
                score[ev["id"]] = score.get(ev["id"], 0) + 1 / (60 + rank)
                evs[ev["id"]] = ev
        top = sorted(score, key=score.get, reverse=True)[:k]
        if touch:
            self.touch(top)
        return [dict(evs[i], score=score[i]) for i in top]

    def touch(self, event_ids):
        """Mark recalled now; a recalled cold event turns warm again."""
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.store.lock:
            for e in event_ids:
                self.store.db.execute("INSERT OR REPLACE INTO event_recalls VALUES(?,?)", (e, now))
                self.store.db.execute("DELETE FROM cold_events WHERE event_id=?", (e,))
            self.store.db.commit()

    @staticmethod
    def _unit(v):
        v = np.asarray(v, np.float32)
        return v / (np.linalg.norm(v) or 1.0)


if __name__ == "__main__":  # self-check: python semantic.py
    from store import MemoryStore
    words = ["cat", "dog", "car", "tax"]
    fake = lambda t, q: [float(w in t) for w in words]  # bag-of-words stand-in for the real embedder
    s = MemoryStore(":memory:")
    s.add_event("note", "old note about tax")      # before attach: needs sync()
    ix = SemanticIndex(s, fake, "fake-a"); ix.attach()
    s.add_event("note", "my cat and dog")
    s.add_event("note", "the car broke")
    assert ix.sync() == 1
    assert ix.recall("dog")[0]["content"] == "my cat and dog"
    assert ix.recall("tax")[0]["content"] == "old note about tax"
    ix.model = "fake-b"                            # model swap: empty until re-synced, no mixing
    assert ix.recall("dog") == [] and ix.sync() == 3
    ix.model = "fake-a"; ix.clear()  # clearing one model leaves the other
    assert ix.recall("dog") == [] and ix.sync() == 3
    # hybrid: the fake embedder knows no names, keyword search must still find them
    s.add_event("note", "Rina called about the visa")
    ix.model = "fake-b"; ix.sync()
    assert ix.search("visa Rina")[0]["content"] == "Rina called about the visa"
    ev = ix.search("visa")[0]
    s.db.execute("INSERT INTO cold_events VALUES(?,?)", (ev["id"], "now")); s.db.commit()
    assert all(e["id"] != ev["id"] for e in ix.search("visa Rina"))          # cold: hidden
    assert ix.search("visa Rina", include_cold=True)[0]["id"] == ev["id"]    # ...but findable, and warm again
    assert s.db.execute("SELECT COUNT(*) FROM cold_events").fetchone()[0] == 0
    print("semantic index ok")
