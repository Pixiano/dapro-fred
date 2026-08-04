# Core/orchestrator/vault_router.py
#
# Semantic retrieval over every other file in the vault (60 of 63 .md at
# time of writing — everything except persona.md/profile.md/rules.md,
# which load directly and always, see personality/system_prompt.py).
# Same shape as orchestrator/tool_router.py: embed once, compare by
# cosine similarity. Unlike the tool router it no longer filters — the
# user asked for unrestricted read access, so the top-K nearest chunks
# come back on every turn regardless of score (VAULT_RETRIEVAL_FLOOR = 0.0).
#
# Chunked by ## section rather than whole-file, unlike the tool router.
# A tool's description is one coherent idea; a vault file like profile.md
# or machine.md covers several ("VRAM budget" and "Paths" have nothing to
# do with each other), and whole-file embedding would blur them into one
# vector that matches everything a little and nothing well. Section
# boundaries were checked against real files (persona.md, profile.md,
# rules.md, board-exams.md, active-priorities.md, machine.md,
# jobs/_TEMPLATE.md) before committing to this — every one uses ## / ---
# consistently.
#
# Cached to disk, keyed by content hash per file, because embedding is
# not free — the tool router's one-time 40-item build measured at ~4.8s,
# and the vault has more chunks than that. Re-embedding only changed
# files (not the whole vault) on every FRED launch matters given
# knowledge/jobs/root are near-static while people/projects/daily change
# regularly.

import hashlib
import json
import math
import re
from pathlib import Path

from config.settings import (
    VAULT_DIR,
    VAULT_HARDCODED_FILES,
    VAULT_EXCLUDED_FILES,
    VAULT_INDEXED_SUFFIXES,
    VAULT_INDEX_DIR,
    VAULT_RETRIEVAL_TOP_K,
    VAULT_RETRIEVAL_FLOOR,
)
from utils.vault_md import strip_frontmatter, extract_h1_title, split_sections

# orchestrator.vault_intent.should_check_vault() used to gate retrieve()
# below. It is deliberately NOT imported any more — the user asked for
# unrestricted read access, so every turn now queries the vault. The
# module is left intact and importable rather than deleted, so the gate
# can be put back by restoring this import and the one-line check in
# retrieve() if the added noise ever proves too costly.

CACHE_PATH = VAULT_INDEX_DIR / "chunks.json"


def _cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _iter_vault_files():
    """Every indexable file under VAULT_DIR except the hardcoded and
    excluded ones, as (relative_path_str, absolute_path)."""
    skip = set(VAULT_HARDCODED_FILES) | set(VAULT_EXCLUDED_FILES)
    if not VAULT_DIR.exists():
        return
    for path in sorted(VAULT_DIR.rglob("*")):
        if path.suffix.lower() not in VAULT_INDEXED_SUFFIXES:
            continue
        if path.name in skip or not path.is_file():
            continue
        yield str(path.relative_to(VAULT_DIR)).replace("\\", "/"), path


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# =========================================================
# CENTERING
# =========================================================
#
# Embedding spaces have "hubs": a handful of vectors sit near the centre
# of the distribution and therefore score highly against almost any
# query, carrying no discriminative information at all. This index had a
# textbook one. Measured over 16 queries (8 genuinely vault-relevant, 8
# pure chat), the chunk `projects/fred.md — What it does` was the #1 hit
# for 9 of them — including three relevant queries where it is simply
# wrong: it outranked reference/machine.md for "what are my machine
# specs" and active-priorities.md for "what are my current priorities".
#
# No threshold fixes this, which was checked before reaching for it:
# top-1 score, gap-to-6th, and gap-to-mean ALL overlap between relevant
# and chat queries (relevant top-1 0.578-0.712 vs chat 0.388-0.649), so
# any floor that rejects "tell me a fun fact" also rejects "who is
# suhani". The problem is not where the cutoff sits, it is that one
# vector is close to everything.
#
# Subtracting the corpus mean from every chunk AND from the query
# removes the shared component that makes everything look alike. On the
# same 16 queries this cost nothing and fixed real errors:
#   relevant recall@6      7/7  ->  7/7   (unchanged)
#   "machine specs" top-1  fred.md -> reference/machine.md
#   "priorities"    top-1  fred.md -> personal/goals.md — Priority order
#   fred.md as #1 on chat  6/6  ->  4/6
#
# Note this makes scores centered cosines, not raw ones: they are lower
# and CAN be negative, so VAULT_RETRIEVAL_FLOOR is interpreted against
# that scale (see the note there). Cheap — one subtraction per chunk at
# build, one per query.


def _mean_vector(vectors):
    if not vectors:
        return None
    dim = len(vectors[0])
    n = len(vectors)
    return [sum(v[i] for v in vectors) / n for i in range(dim)]


def _center(entries):
    """entries -> same entries with the corpus mean subtracted."""
    mean = _mean_vector([v for _l, _t, v in entries])
    if mean is None:
        return entries
    return [
        (label, text, [x - m for x, m in zip(vec, mean)])
        for label, text, vec in entries
    ]


def _chunk_pdf(rel_path: str, path: Path):
    """
    (label, embed_text, display_text) per PAGE of a PDF.

    Per page rather than per document on purpose: a PDF has no "##"
    headings for split_sections() to work with, so the whole file would
    otherwise become one unbounded chunk — and the embedding model runs
    at n_ctx=4096 (see memory_manager.py), so a long document would be
    silently truncated at embed time and retrieve badly for anything
    past the cut. Pages are the only structural boundary a PDF reliably
    has.

    Returns [] and warns rather than raising: a malformed or
    image-only PDF must not take down the whole vault index, which is
    built lazily on the first turn of a session.
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        print(f"[vault] pypdf not installed — skipping {rel_path}")
        return []

    try:
        reader = PdfReader(str(path))
    except Exception as e:
        print(f"[vault] couldn't read {rel_path}: {e}")
        return []

    chunks = []
    for number, page in enumerate(reader.pages, start=1):
        try:
            text = (page.extract_text() or "").strip()
        except Exception as e:
            print(f"[vault] {rel_path} page {number} failed to extract: {e}")
            continue
        if not text:
            # Scanned/image-only page — nothing to embed, and a chunk of
            # empty text would just be noise near every query.
            continue
        label = f"{rel_path} — p{number}"
        chunks.append((label, f"{rel_path} — page {number}\n{text}", text))

    return chunks


def _chunk_file(rel_path: str, path: Path):
    """(section_label, embed_text, display_text) for every chunk in one file."""
    if path.suffix.lower() == ".pdf":
        return _chunk_pdf(rel_path, path)

    raw = path.read_text(encoding="utf-8")
    body = strip_frontmatter(raw)
    title = extract_h1_title(body) or rel_path

    chunks = []
    for heading, text in split_sections(body):
        label = f"{rel_path} — {heading}" if heading != "(whole file)" else rel_path
        embed_text = f"{title} — {heading}\n{text}" if heading not in ("(whole file)", "(intro)") else f"{title}\n{text}"
        chunks.append((label, embed_text, text))
    return chunks


class VaultRouter:
    """
    Nearest-neighbour retrieval over vault knowledge chunks.

    Same lazy-build, fail-open shape as SemanticToolRouter: build() is
    idempotent and safe to call repeatedly, and any failure (vault
    missing, embedder unavailable) degrades to "no vault context this
    turn" rather than breaking the conversation.
    """

    def __init__(self, embed_fn):
        self.embed = embed_fn
        self._entries = []  # [(label, display_text, CENTERED vector)]
        self._mean = None   # corpus mean, subtracted from every query too
        self._ready = False
        self._failed = False

    # =========================================================
    # BUILD (cached, per-file hash invalidation)
    # =========================================================

    def build(self, force: bool = False) -> bool:
        if self._ready and not force:
            return True
        if self._failed and not force:
            return False

        try:
            cache = self._load_cache()
            updated_cache = {}
            entries = []
            changed = 0
            seen_files = set()

            for rel_path, path in _iter_vault_files():
                seen_files.add(rel_path)
                file_hash = _file_hash(path)
                cached = cache.get(rel_path)

                if cached and cached.get("hash") == file_hash:
                    updated_cache[rel_path] = cached
                    for c in cached["chunks"]:
                        entries.append((c["label"], c["text"], c["vector"]))
                    continue

                changed += 1
                chunk_records = []
                for label, embed_text, display_text in _chunk_file(rel_path, path):
                    try:
                        vector = self.embed(embed_text)
                    except Exception as e:
                        print(f"[vault_router] embed failed for {label!r}: {e}")
                        continue
                    chunk_records.append(
                        {"label": label, "text": display_text, "vector": vector}
                    )
                    entries.append((label, display_text, vector))

                updated_cache[rel_path] = {"hash": file_hash, "chunks": chunk_records}

            dropped = set(cache.keys()) - seen_files
            if changed or dropped:
                self._save_cache(updated_cache)
                print(
                    f"[vault_router] indexed {len(entries)} chunks from "
                    f"{len(seen_files)} files ({changed} re-embedded, "
                    f"{len(dropped)} dropped)"
                )
            else:
                print(f"[vault_router] {len(entries)} chunks loaded from cache, all current")

            self._entries = _center(entries)
            self._mean = _mean_vector([v for _l, _t, v in entries])
            self._ready = True
            return True

        except Exception as e:
            print(f"[vault_router] build failed: {e}")
            self._failed = True
            self._entries = []
            return False

    def _load_cache(self) -> dict:
        if not CACHE_PATH.exists():
            return {}
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            print(f"[vault_router] cache unreadable ({e}) — rebuilding from scratch")
            return {}

    def _save_cache(self, cache: dict):
        VAULT_INDEX_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cache), encoding="utf-8")
        tmp.replace(CACHE_PATH)

    # =========================================================
    # RETRIEVE
    # =========================================================

    def retrieve(self, query: str, top_k: int = None, floor: float = None):
        """
        Returns [(label, display_text, score)] for chunks above `floor`,
        best first, capped at `top_k`. Empty list only if the query is
        blank, the router isn't built, or the vault is empty — all "no
        vault context this turn," not errors.

        No cue gate and (by default) no floor: with VAULT_RETRIEVAL_FLOOR
        at 0.0 every non-blank turn gets the top_k nearest chunks, however
        unrelated. That is the intended behaviour — unrestricted read
        access — not an oversight.
        """
        if not query.strip():
            return []
        if not self.build():
            return []
        if not self._entries:
            return []

        try:
            # is_query=True — asymmetric instruction convention, see
            # memory_manager.py's _generate_embedding. The vault chunks
            # embedded in build() are documents; this is the query being
            # matched against them.
            q_vector = self.embed(query, is_query=True)
        except Exception as e:
            print(f"[vault_router] query embed failed: {e}")
            return []

        top_k = VAULT_RETRIEVAL_TOP_K if top_k is None else top_k
        floor = VAULT_RETRIEVAL_FLOOR if floor is None else floor

        # The query must be centered by the same corpus mean the chunks
        # were, or the two live in different spaces and the comparison is
        # meaningless. self._entries already holds centered vectors.
        if self._mean is not None:
            q_vector = [x - m for x, m in zip(q_vector, self._mean)]

        scored = [
            (label, text, _cosine(q_vector, vec))
            for label, text, vec in self._entries
        ]
        scored.sort(key=lambda t: t[2], reverse=True)

        return [(l, t, s) for l, t, s in scored[:top_k] if s >= floor]
