"""The FRED reindex for skills: every skill that keeps an index (installed apps, files) rebuilds it.

Sockets
  in : reindex_all(registry=None) -> {skill: count | "error: ..."}   registry defaults to the real skill folders; one skill failing never stops the others
       python skills/reindex.py now                                   the same from a terminal; prints one line per skill
  out: the P7 nightly job calls reindex_all() with the other maintenance; each skill also refreshes itself when its index is missing or stale, so this only keeps them warm.
Self-check: python skills/reindex.py
"""
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def reindex_all(registry=None) -> dict:
    import log
    if registry is None:
        from skills.loader import build
        registry = build()
    out = {}
    for n in registry.names():
        s = registry.spec(n)
        if s and s.reindex:
            try:
                out[n] = s.reindex()
            except Exception as ex:
                out[n] = f"error: {ex!r}"
    log.session("skills_reindexed", result=out)
    return out


def _selfcheck():
    import tempfile
    import config
    from tools.registry import Registry, ToolSpec
    config.set_data_dir(tempfile.mkdtemp())
    r = Registry()
    r.register(ToolSpec("plain", lambda: "x", description="no index", group="g"))
    r.register(ToolSpec("good", lambda: "x", description="index", group="g", reindex=lambda: 7))
    r.register(ToolSpec("bad", lambda: "x", description="index", group="g", reindex=lambda: 1 / 0))
    got = reindex_all(r)
    assert got["good"] == 7 and got["bad"].startswith("error: ZeroDivisionError") and "plain" not in got, got
    print("reindex ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["now"]:
        for name, n in reindex_all().items():
            print(f"{name}: {n}")
    else:
        _selfcheck()
