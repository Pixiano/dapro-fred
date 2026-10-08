"""Finding one personal-model item from what the user said (shared by the forget and correct skills).

Sockets
  in : find_item(model, ref) -> dict   model = the memory's PersonalModel; ref = an item number ("3", "#3") or a few words from its text
  out: the one active item as a dict; no match -> ToolError(not_found); several -> ToolError(bad_args) naming them, so the caller asks which (never a guess)
Self-check: python memory_bridge/items.py
"""
import re
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.registry import ToolError


_STOP = {"that", "the", "you", "your", "about", "remember", "forget", "memory", "what", "with", "has", "have", "does", "and", "for"}


def find_item(model, ref) -> dict:
    m = str(ref).strip().lower().lstrip("#")
    rows = model.q("SELECT * FROM model_items WHERE status='active'")
    hits = [r for r in rows if str(r["id"]) == m] or [r for r in rows if m and m in r["text"].lower()]
    if not hits:                                                                           # spoken phrasing rarely repeats the stored words exactly: fall back to shared words
        want = {w for w in re.findall(r"[a-z0-9]+", m) if len(w) > 2 and w not in _STOP}
        scored = sorted(((len(want & set(re.findall(r"[a-z0-9]+", r["text"].lower()))) / len(want), r) for r in rows), key=lambda x: -x[0]) if want else []
        if scored and scored[0][0] >= 0.5:
            hits = [r for s, r in scored if s == scored[0][0]]
    if not hits:
        raise ToolError("not_found", "I don't have a memory like that.", f"no active item matches {ref!r}; recall with no query lists them")
    if len(hits) > 1:
        raise ToolError("bad_args", "More than one memory matches. Which one?", "ambiguous: " + "; ".join(f"#{r['id']} {r['text'][:60]}" for r in hits))
    return dict(hits[0])


def _selfcheck():
    class M:
        def __init__(self, rows):
            self.rows = rows

        def q(self, sql, args=()):
            return self.rows
    m = M([{"id": 1, "text": "likes spicy food"}, {"id": 2, "text": "likes dark mode"}, {"id": 12, "text": "wakes at six"}])
    assert find_item(m, "#2")["text"] == "likes dark mode" and find_item(m, "spicy")["id"] == 1 and find_item(m, "12")["id"] == 12      # an exact id beats a text match
    assert find_item(m, "that I like spicy food")["id"] == 1 and find_item(m, "the one about dark mode")["id"] == 2             # spoken wording, not the stored words
    for ref, kind in (("zebra", "not_found"), ("likes", "bad_args"), ("", "not_found")):
        try:
            find_item(m, ref)
            raise AssertionError(ref)
        except ToolError as ex:
            assert ex.kind == kind, (ref, ex.kind)
    print("items ok")


if __name__ == "__main__":
    _selfcheck()
