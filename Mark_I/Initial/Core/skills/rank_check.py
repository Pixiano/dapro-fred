"""Ranking check: does every example phrase of every skill rank its own skill first? Catches look-alike skills (cpu_stats vs gpu_stats) before the brain sees them.

  python skills/rank_check.py                  self-check (a keyword embedder: a wrongly placed example is caught)
  python skills/rank_check.py --live [--margin]  real embedder through the playground (embed slot loaded, 127.0.0.1:8090); exit 1 if an example ranks another skill first;
                                                 --margin also lists examples whose own skill wins by less than 0.03
Sockets
  in : check(registry, embedder, margin=0.0) -> list of (skill, example, winner, own_score, winner_score) for every example that loses (or wins by < margin)
"""
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.classify import Router


def check(registry, embedder, margin: float = 0.0) -> list:
    rt, bad = Router(registry, embedder), []
    for n in registry.names():
        for ex in registry.spec(n).examples:
            ranked = rt.rank(ex)
            own = dict(ranked)[n]
            other = next((s for s in ranked if s[0] != n), (None, -1.0))
            if ranked[0][0] != n or own - other[1] < margin:
                bad.append((n, ex, ranked[0][0], round(own, 3), round(ranked[0][1], 3)))
    return bad


def _selfcheck():
    from tools.registry import Registry, ToolSpec

    class Words:                                                                   # one dimension per word: similarity = shared words
        V = ["cpu", "gpu", "hot", "load", "time", "clock", "ram", "free"]

        def embed(self, texts, is_query=False):
            return [[float(w in t.lower().split()) for w in self.V] for t in texts]
    r = Registry()
    r.register(ToolSpec("cpu_stats", lambda: "x", description="cpu load", examples=("cpu load", "is the cpu hot", "cpu free")))
    r.register(ToolSpec("gpu_stats", lambda: "x", description="gpu load", examples=("gpu load", "is the gpu hot", "cpu hot")))      # the last example belongs to the wrong skill
    bad = check(r, Words())
    assert [(b[0], b[1], b[2]) for b in bad] == [("gpu_stats", "cpu hot", "cpu_stats")], bad
    print("rank_check ok")


if __name__ == "__main__":
    if "--live" not in sys.argv:
        _selfcheck()
    else:
        from models.client import Embedder
        from skills.loader import load
        reg, problems = load(ctx={"recall": None})                                    # only the manifests matter here; bind() just stores the context
        bad = check(reg, Embedder(), 0.03 if "--margin" in sys.argv else 0.0)
        print(f"{len(reg.names())} skills, {sum(len(reg.spec(n).examples) for n in reg.names())} examples; {len(problems)} skipped add-ons")
        for b in bad:
            print(f"{b[0]}: {b[1]!r} -> {b[2]} (own {b[3]}, winner {b[4]})")
        sys.exit(1 if [b for b in bad if b[0] != b[2]] else 0)
