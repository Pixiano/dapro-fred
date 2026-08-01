# Core/tools/found_cache.py
#
# Suggestion #2 from the 2026-08-01 feedback session: a small persistent
# "already found this" index, so search_files doesn't re-walk the same
# directory tree for a query it's already resolved. Same shape as
# audio/phrase_cache.py (normalize a key, cache the resolved result,
# never raise on a bad entry — a cache problem just means "search
# again") and the same content-hash-invalidation spirit as
# orchestrator/vault_router.py, adapted for a filesystem search instead
# of an embedding index.
#
# Unlike phrase_cache's fixed ~50-phrase vocabulary, this cache's keys
# are open-ended, so staleness (a file since moved, renamed, or
# deleted) is a real risk a closed vocabulary never has. Handled by
# verifying every cached path still exists before trusting a hit — an
# os.path.exists() check per path is a handful of syscalls, not a
# directory walk, so verification stays cheap even though it isn't
# free. A hit where any path fails verification is discarded entirely
# and treated as a miss, rather than silently returned half-stale.

import json
from pathlib import Path

from config.settings import DATA_DIR

CACHE_PATH = DATA_DIR / "found_files.json"


def _key(query: str, directory: str) -> str:
    return f"{directory.strip().lower()}\x00{query.strip().lower()}"


def _load() -> dict:
    if not CACHE_PATH.exists():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"[found_cache] cache unreadable ({e}) — starting fresh")
        return {}


def _save(cache: dict):
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cache), encoding="utf-8")
    tmp.replace(CACHE_PATH)


def get(query: str, directory: str):
    """
    Returns a list of full path strings if this exact (directory,
    query) pair was searched before AND every cached path still
    exists, else None. A partially-stale hit is dropped rather than
    returned trimmed, since "some of these moved" is a signal the
    whole entry may be out of date, not just one path.
    """
    cache = _load()
    entry = cache.get(_key(query, directory))
    if entry is None:
        return None

    paths = entry.get("paths", [])
    if not all(Path(p).exists() for p in paths):
        return None

    return paths


def put(query: str, directory: str, paths: list):
    cache = _load()
    cache[_key(query, directory)] = {"paths": paths}
    _save(cache)
