"""read_own_docs: FRED reads its own markdown documents from the local repo, so it can say what it is and how it was built. Read-only, and only these files:
Core/README.md, Core/HANDOFF.md, Core/PLAN.md, Core/Progress/*.md, Mark_I/Documentation/**/*.md (nothing else on the disk, whatever name is asked for).

Sockets
  in : run(doc="", page=1)   "" -> the list; "readme" -> Core/README.md; otherwise the one document whose path contains every word asked for
  out: PAGE_CHARS of text per page ("[page 2 of 5, say page 3 for more]"); several matches -> the list of them to choose from; no match -> bad_args with the list
GitHub is not used: the docs are read from the local checkout, which is the same text and works offline (the repo's own GitHub copy can be added later as a second source).
bind(ctx): none. The roots come from config.CORE_DIR.
"""
import re
from pathlib import Path

import config
from tools.registry import ToolError

CORE = Path(config.CORE_DIR)                                                        # not __file__: the self-check copies this skill to a temp folder
ROOT = CORE.parents[1]
PAGE_CHARS = 2500
MAX_LIST = 12


def _docs() -> list:
    out = [p for p in (CORE / "README.md", CORE / "HANDOFF.md", CORE / "PLAN.md") if p.is_file()]
    for base in (CORE / "Progress", ROOT / "Documentation"):
        out += sorted((p for p in base.rglob("*.md") if p.is_file()), key=lambda p: str(p)) if base.is_dir() else []
    return out


def _name(p: Path) -> str:
    return str(p.relative_to(ROOT)).replace("\\", "/")


def _listing(docs: list, head: str) -> str:
    rows = [_name(p) for p in docs]
    shown = rows[:MAX_LIST]
    more = f" ({len(rows) - MAX_LIST} more: name a few words from the title)" if len(rows) > MAX_LIST else ""
    return head + "\n" + "\n".join(shown) + more


def run(doc: str = "", page: int = 1) -> str:
    docs = _docs()
    q = re.sub(r"[^a-z0-9]+", " ", doc.lower()).split()
    if not q:
        return _listing(docs, "Documents I can read (start with the README):")
    if q == ["readme"] or q == ["read", "me"]:
        hits = [p for p in docs if p == CORE / "README.md"]
    else:
        hits = [p for p in docs if all(w in re.sub(r"[^a-z0-9]+", " ", _name(p).lower()) for w in q)]
    if not hits:
        raise ToolError("bad_args", "I don't have a document called that.", _listing(docs, f"no document matches {doc!r}; readable documents:"))
    if len(hits) > 1:
        return _listing(hits, f"{len(hits)} documents match; say which one:")
    text = hits[0].read_text(encoding="utf-8", errors="replace")
    pages = max(1, -(-len(text) // PAGE_CHARS))
    if page > pages:
        raise ToolError("bad_args", f"That document has only {pages} page{'s' if pages != 1 else ''}.", f"page {page} requested, {pages} exist")
    body = text[(page - 1) * PAGE_CHARS: page * PAGE_CHARS]
    return f"{_name(hits[0])} [page {page} of {pages}{', say page ' + str(page + 1) + ' for more' if page < pages else ''}]\n{body}"
