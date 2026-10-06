"""Face reference store: the embeddings a live face will later be compared against (no matching here yet).

Two tiers, 13 : 7 (Vatsal 2026-10-06):
  static   `faces_static_cap` (13), deliberate shots, never changed by the running system, only by an explicit enroll / import call.
  dynamic  `faces_dynamic_cap` (7), a FIFO window of recent confident matches (glasses, hair, light drift); the oldest is dropped when full.
Sockets
  in : FaceStore(path=None)  (default <FRED_DATA>/face_refs.json, outside the repo: embeddings are biometric data, never logged, never in an event, never committed)
       add_static(vec) / add_dynamic(vec, ts=None) / import_old(old_json_path) / counts() / vectors() -> (static, dynamic) lists of unit-length float32 arrays
  out: the JSON file {"v":1,"static":[{"e":[512 floats],"ts":..}],"dynamic":[..]}; import_old keeps only the 20 imported references (the unused old ones are dropped, Vatsal's call).
  add_static refuses past the cap (raises ValueError) rather than evicting a deliberate shot. Vectors are L2-normalised on the way in (cosine similarity = dot product later).
Import rule for the old FRED file (tiers base / hard / dynamic, 512-d ArcFace): static = the 13 most DIVERSE of base+hard (farthest-point sampling, so 13 different looks
rather than 13 near-duplicates); dynamic = the 7 most recent dynamic entries by timestamp.
Self-check: python senses/face_store.py (random vectors, temp dir)
"""
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config


def _unit(v) -> np.ndarray:
    a = np.asarray(v, dtype=np.float32)
    n = float(np.linalg.norm(a))
    if a.ndim != 1 or n == 0:
        raise ValueError("embedding must be a non-zero 1-D vector")
    return a / n


def diverse(vecs: list[np.ndarray], k: int) -> list[int]:
    """Indices of k vectors chosen by farthest-point sampling (start from the one closest to the mean, then repeatedly the one least similar to those already chosen)."""
    if len(vecs) <= k:
        return list(range(len(vecs)))
    m = np.stack(vecs)
    chosen = [int(np.argmax(m @ _unit(m.mean(axis=0))))]
    best = m @ m[chosen[0]]
    while len(chosen) < k:
        nxt = int(np.argmin(best))
        chosen.append(nxt)
        best = np.maximum(best, m @ m[nxt])
    return chosen


class FaceStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else config.data_dir() / "face_refs.json"
        self._d = {"v": 1, "static": [], "dynamic": []}
        if self.path.exists():
            self._d = json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._d), encoding="utf-8")
        tmp.replace(self.path)

    def counts(self) -> dict:
        return {"static": len(self._d["static"]), "dynamic": len(self._d["dynamic"])}

    def vectors(self):
        return [_unit(x["e"]) for x in self._d["static"]], [_unit(x["e"]) for x in self._d["dynamic"]]

    def add_static(self, vec, ts=None):
        if len(self._d["static"]) >= config.get().faces_static_cap:
            raise ValueError("static tier is full; remove one deliberately before adding")
        self._d["static"].append({"e": _unit(vec).tolist(), "ts": ts or datetime.now().isoformat()})
        self._save()

    def add_dynamic(self, vec, ts=None):
        d = self._d["dynamic"]
        d.append({"e": _unit(vec).tolist(), "ts": ts or datetime.now().isoformat()})
        del d[:max(0, len(d) - config.get().faces_dynamic_cap)]
        self._save()

    def import_old(self, old_path) -> dict:
        old = json.loads(Path(old_path).read_text(encoding="utf-8"))["embeddings"]
        c = config.get()
        stat = [e for e in old if e.get("kind") in ("base", "hard")]
        dyn = sorted([e for e in old if e.get("kind") == "dynamic"], key=lambda e: e.get("ts", ""))[-c.faces_dynamic_cap:]
        vs = [_unit(e["embedding"]) for e in stat]
        keep = [stat[i] for i in diverse(vs, c.faces_static_cap)]
        self._d = {"v": 1,
                   "static": [{"e": _unit(e["embedding"]).tolist(), "ts": e.get("ts", "")} for e in keep],
                   "dynamic": [{"e": _unit(e["embedding"]).tolist(), "ts": e.get("ts", "")} for e in dyn]}
        self._save()
        return {**self.counts(), "old_static": len(stat), "old_dynamic": len([e for e in old if e.get("kind") == "dynamic"])}


def _selfcheck():
    rng = np.random.default_rng(3)
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        s = FaceStore(d / "refs.json")
        for i in range(13):
            s.add_static(rng.normal(size=512))
        try:
            s.add_static(rng.normal(size=512))
            raise AssertionError("static cap not enforced")
        except ValueError:
            pass
        for i in range(10):
            s.add_dynamic(rng.normal(size=512), ts=f"2026-10-0{i}")
        assert s.counts() == {"static": 13, "dynamic": 7}
        st, dy = FaceStore(d / "refs.json").vectors()                  # survives a reload, unit length, newest dynamic kept
        assert len(st) == 13 and len(dy) == 7 and abs(float(np.linalg.norm(st[0])) - 1) < 1e-5
        assert [x["ts"] for x in s._d["dynamic"]] == [f"2026-10-0{i}" for i in range(3, 10)]
        # import: 20 base (two tight clusters of near-duplicates + spread), 30 dynamic
        base_c = rng.normal(size=(2, 512))
        base = [base_c[i % 2] + 0.05 * rng.normal(size=512) for i in range(14)] + [rng.normal(size=512) for _ in range(6)]
        old = {"embeddings": [{"embedding": b.tolist(), "kind": "base", "ts": "2026-08-01"} for b in base]
               + [{"embedding": rng.normal(size=512).tolist(), "kind": "dynamic", "ts": f"2026-09-{i + 1:02d}"} for i in range(30)]}
        (d / "old.json").write_text(json.dumps(old), encoding="utf-8")
        s2 = FaceStore(d / "new" / "refs.json")
        r = s2.import_old(d / "old.json")
        assert r == {"static": 13, "dynamic": 7, "old_static": 20, "old_dynamic": 30}, r
        assert not (d / "new" / "face_refs_old_import.json").exists()
        assert [x["ts"] for x in s2._d["dynamic"]] == [f"2026-09-{i:02d}" for i in range(24, 31)]     # the 7 most recent
        # diversity: all 6 spread vectors are kept, and not all 14 near-duplicates are
        kept = np.stack(s2.vectors()[0])
        spread = np.stack([_unit(b) for b in base[14:]])
        assert (spread @ kept.T).max(axis=1).min() > 0.999, "a spread-out look was dropped"
        assert len(diverse([_unit(b) for b in base], 13)) == 13
    print("face_store ok")


if __name__ == "__main__":
    if "--import-old" in sys.argv:
        print(FaceStore().import_old(sys.argv[sys.argv.index("--import-old") + 1]))
    else:
        _selfcheck()
