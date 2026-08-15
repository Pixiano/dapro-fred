# Core/tools/self_docs.py
#
# Backlog #13 (MVP Plan v1.0-v1.1): indexed access to FRED's OWN
# documentation, so "do you have a calculator tool", "what can you
# actually do" and "how does the phone thing work" are answered from
# real indexed text instead of the model guessing from whatever is in
# its conversation context. The plan is explicit that this is a
# practical substitute for self-awareness, not a claim to be it — this
# is a read-only lookup over five checked-in files, nothing more.
#
# Not the vault. The vault (VAULT_DIR, outside this repo) is Vatsal's
# personal memory and already has its own always-on retriever; this set
# is FRED's project docs and is read only when he is asked about
# himself. See the DOCS_FILES comment in config/settings.py for why the
# two corpora stay separate.
#
# Reuses VaultRouter rather than retrieving a second way: it already
# does chunking, per-file content-hash cache invalidation, corpus
# centering and top-K, all of which were tuned against real measured
# failures (see its header). It takes the file list and cache path as
# arguments as of 2026-08-15 precisely so this module can hand it a
# different corpus.
#
# Complements describe_self (tools/system_tools.py), which answers
# "how many tools / which model" from live runtime state. That one
# knows what is loaded right now; this one knows what any of it is FOR.

from config.settings import (
    DOCS_DIR,
    DOCS_FILES,
    DOCS_INDEX_PATH,
    DOCS_RETRIEVAL_TOP_K,
)

# Each retrieved section is capped before it reaches the model. Four
# sections of the roadmap uncapped is several thousand words, and this
# comes back as a tool result the model must read in full and then
# summarise into one spoken answer — the same reasoning as
# VAULT_CHUNK_INJECT_CHARS, at a tighter cap because a doc section
# front-loads its point where a vault table does not.
_EXCERPT_CHARS = 900

_router = None  # built once per process, on first question


def iter_doc_files():
    """(rel_path, absolute_path) for every DOCS_FILES entry that exists.

    Same shape as vault_router._iter_vault_files so VaultRouter can take
    either. Missing files are skipped silently rather than raising: a
    renamed doc must degrade to "one fewer document indexed", not to a
    tool that fails on every question.
    """
    for name in DOCS_FILES:
        path = DOCS_DIR / name
        if path.is_file():
            yield name, path


def _get_router(embed_fn):
    global _router
    if _router is None:
        from orchestrator.vault_router import VaultRouter

        _router = VaultRouter(
            embed_fn, files_fn=iter_doc_files, cache_path=DOCS_INDEX_PATH
        )
    return _router


def ask_about_myself(question: str, embed_fn=None) -> str:
    """
    Look up FRED's own documentation and return the most relevant
    sections verbatim, for the model to answer from.

    Returns excerpts rather than a finished sentence on purpose: the
    failure this exists to fix is FRED inventing capabilities, and a
    quoted section with its filename is what makes an answer checkable
    (and what makes "the docs don't say" possible at all).
    """
    if not (question or "").strip():
        return "Ask me something specific about myself and I'll check my docs."

    if embed_fn is None:
        return "My documentation index isn't available right now."

    hits = _get_router(embed_fn).retrieve(question, top_k=DOCS_RETRIEVAL_TOP_K)
    if not hits:
        return (
            "I couldn't find anything in my own documentation about that. "
            "Say so rather than guessing."
        )

    parts = [
        f"From {label}:\n{text[:_EXCERPT_CHARS]}"
        for label, text, _score in hits
    ]

    return (
        "My own documentation says the following. Answer from this text "
        "only, and say the docs don't cover it if they don't:\n\n"
        + "\n\n---\n\n".join(parts)
    )


if __name__ == "__main__":
    # ASCII only in print() — these reach a cp1252 console under
    # pythonw (see utils/hud_manager.py).
    names = [rel for rel, _path in iter_doc_files()]
    assert names, "no doc files resolved - check DOCS_DIR/DOCS_FILES"
    assert "PHONE.md" in names, "phone docs must be indexed (2026-08-15 work)"
    assert "README.md" in names

    # No embedder here on purpose: that would load a model just to prove
    # a lookup path. The retrieval itself is covered by
    # tests/test_self_docs.py with a deterministic stand-in embedder.
    assert "isn't available" in ask_about_myself("what can you do")
    assert "specific" in ask_about_myself("   ")

    print(f"[self_docs] OK - {len(names)} documents resolved: {', '.join(names)}")
