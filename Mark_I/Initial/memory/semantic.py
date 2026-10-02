"""Mark I memory, Phase 2: semantic recall over events.

Sockets:
  - embed(text, is_query) -> list[float]: injected. Production passes the embedder
    (v1.2's MemoryManager._generate_embedding has this exact signature); tests pass a fake.
  - attach(): registers on MemoryStore.on_event, so every captured event gets a vector.
  - sync(): embeds any event missing a vector (backfill / model swap after clear()); the index
    can never drift from events the way v1.2's FAISS file vs jsonl did.
  - Backend is a numpy matrix, rebuilt lazily. ponytail: brute-force cosine, fine to ~100k events;
    swap the _matrix/recall pair for FAISS if it grows past that.
"""
import numpy as np

_SCHEMA = __import__("pathlib").Path(__file__).with_name("schema_embeddings.sql")


class SemanticIndex:
    def __init__(self, store, embed, model="unset"):
        self.store, self.embed, self.model = store, embed, model
        store.db.executescript(_SCHEMA.read_text(encoding="utf-8"))
        self._m = None  # (ids, matrix) cache

    def attach(self):
        self.store.on_event.append(lambda ev: self.add(ev["id"], ev["content"]))

    def add(self, event_id, text):
        v = self._unit(self.embed(text, False))
        self.store.db.execute("INSERT OR REPLACE INTO embeddings VALUES(?,?,?)", (event_id, self.model, v.tobytes()))
        self.store.db.commit()
        self._m = None

    def sync(self) -> int:
        rows = self.store.db.execute("SELECT id, content FROM events WHERE id NOT IN "
                                     "(SELECT event_id FROM embeddings)").fetchall()
        for r in rows:
            self.add(r["id"], r["content"])
        return len(rows)

    def clear(self):
        self.store.db.execute("DELETE FROM embeddings")
        self.store.db.commit()
        self._m = None

    def recall(self, query, k=5):
        """Top-k events by cosine similarity, best first."""
        if self._m is None:
            rows = self.store.db.execute("SELECT event_id, vec FROM embeddings").fetchall()
            self._m = ([r[0] for r in rows], np.array([np.frombuffer(r[1], np.float32) for r in rows]))
        ids, m = self._m
        if not ids:
            return []
        scores = m @ self._unit(self.embed(query, True))
        out = []
        for i in np.argsort(-scores)[:k]:
            ev = dict(self.store.db.execute("SELECT * FROM events WHERE id=?", (ids[i],)).fetchone())
            ev["score"] = float(scores[i])
            out.append(ev)
        return out

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
    ix = SemanticIndex(s, fake); ix.attach()
    s.add_event("note", "my cat and dog")
    s.add_event("note", "the car broke")
    assert ix.sync() == 1
    assert ix.recall("dog")[0]["content"] == "my cat and dog"
    assert ix.recall("tax")[0]["content"] == "old note about tax"
    ix.clear(); assert ix.recall("dog") == [] and ix.sync() == 3
    print("semantic index ok")
