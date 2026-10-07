"""recall: search FRED's memory, list what it knows, or read back recent conversation. Read-only.

Sockets
  in : run(query="", days=0)   days > 0 -> the last turns from the archive (1 = today, 2 = since yesterday); else query -> the memory's judged search (always runs, even if the words do not look
                               like a past reference); neither -> the personal model as "#id [category] text" lines
  out: text for the brain to word; "nothing found" is said plainly (never an empty string); an unsure name comes back as the memory's own question ("Do you mean X?"), and the
       engine's next yes/no resolves it
  bind(ctx): needs ctx["recall"] = memory_bridge.turn_recall.MemoryRecall
"""
from datetime import datetime, timedelta, timezone

from skills import Unavailable

_rc = None


def bind(ctx):
    global _rc
    if "recall" not in ctx:
        raise Unavailable("memory is off")
    _rc = ctx["recall"]


def _recent(days: int) -> str:
    lines = []
    for k in range(days - 1, -1, -1):
        day = (datetime.now(timezone.utc) - timedelta(days=k)).strftime("%Y-%m-%d")
        lines += [f"[{day}] {'You' if r['data'].get('role') == 'user' else 'FRED'}: {r['data'].get('text', '')[:200]}" for r in _rc.memory.archive.read("conversation", day)]
    return "\n".join(lines[-24:]) or "We haven't talked in that time."


def run(query: str = "", days: int = 0) -> str:
    if days:
        return _recent(days)
    if not query.strip():
        return _rc.model.show()
    r = _rc.context(query, force=True)
    return r.ask or r.direct or r.block or "I don't remember anything about that."
