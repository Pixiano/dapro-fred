# Two of the four "blocking bugs" recorded in the vault's
# active-priorities.md since 2026-07-29:
#   - FAISS index and JSONL desync silently on any load exception ->
#     wrong memories returned, no rebuild path
#   - IndexFlatL2 on unnormalized embeddings -> distance isn't cosine
#     similarity
#
# __init__ loads a real embedding model from disk, which is slow and
# couples every test to that file existing — these build a MemoryManager
# via __new__ instead, setting only what the methods under test need,
# with a deterministic fake embedder standing in for the real one.

import numpy as np
import faiss

from memory.memory_manager import MemoryManager


def _make_manager(tmp_path, memories=None, dim=8):
    mgr = MemoryManager.__new__(MemoryManager)
    mgr.username = "test_user"
    mgr.memory_dir = tmp_path
    mgr.index_dir = tmp_path
    mgr.memory_file = tmp_path / "test_user.jsonl"
    mgr.index_file = tmp_path / "test_user.faiss"
    mgr.embedding_dim = dim
    mgr.memories = memories or []

    # Deterministic, content-dependent fake embedding — different text
    # must produce different vectors for a rebuild's rankings to mean
    # anything in these tests.
    def fake_embed(text):
        rng = np.random.RandomState(abs(hash(text)) % (2**31))
        return rng.rand(dim).tolist()

    mgr._generate_embedding = fake_embed
    return mgr


def test_normalize_produces_unit_vectors():
    mgr = MemoryManager.__new__(MemoryManager)
    normalized = mgr._normalize([3.0, 4.0])  # 3-4-5 triangle
    assert abs(np.linalg.norm(normalized) - 1.0) < 1e-6


def test_normalize_handles_zero_vector_without_dividing_by_zero():
    mgr = MemoryManager.__new__(MemoryManager)
    result = mgr._normalize([0.0, 0.0, 0.0])
    assert result == [0.0, 0.0, 0.0]


def test_rebuild_index_recovers_from_a_count_mismatch(tmp_path):
    """
    The confirmed desync: memories has entries the index doesn't know
    about (or vice versa). retrieve_relevant must notice and repair it
    rather than silently returning misaligned results.
    """
    mgr = _make_manager(
        tmp_path,
        memories=[
            {"role": "user", "content": "I like hiking", "timestamp": "t1"},
            {"role": "user", "content": "My favourite food is pasta", "timestamp": "t2"},
        ],
    )
    # Simulate the bug directly: an index that disagrees with memories.
    mgr.index = faiss.IndexFlatIP(mgr.embedding_dim)

    assert mgr.index.ntotal != len(mgr.memories)

    results = mgr.retrieve_relevant("hiking", top_k=2)

    assert mgr.index.ntotal == len(mgr.memories)
    assert len(results) == 2


def test_rebuild_index_is_a_noop_on_no_memories(tmp_path):
    mgr = _make_manager(tmp_path, memories=[])
    mgr._rebuild_index()
    assert mgr.index.ntotal == 0


def test_store_and_retrieve_round_trip(tmp_path):
    mgr = _make_manager(tmp_path, memories=[])
    mgr.index = faiss.IndexFlatIP(mgr.embedding_dim)

    mgr.store("user", "I own a golden retriever")
    mgr.store("user", "I'm learning to play guitar")

    assert mgr.index.ntotal == 2
    assert len(mgr.memories) == 2

    results = mgr.retrieve_relevant("dog", top_k=1)
    assert len(results) == 1
    assert results[0]["content"] in (
        "I own a golden retriever", "I'm learning to play guitar",
    )


def test_fresh_index_is_inner_product_not_l2(tmp_path):
    """IndexFlatL2 measured Euclidean distance on raw vectors, which
    isn't cosine similarity. Confirms the type actually changed."""
    mgr = _make_manager(tmp_path, memories=[])
    mgr.index_file = tmp_path / "does_not_exist.faiss"
    index = mgr._load_or_create_index()
    assert isinstance(index, faiss.IndexFlatIP)
