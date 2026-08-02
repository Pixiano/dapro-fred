# Core/memory/memory_manager.py

import json
from pathlib import Path
from datetime import datetime

import faiss
import numpy as np

from utils.gpu_bootstrap import ensure_cuda_dlls

ensure_cuda_dlls()

from llama_cpp import Llama

from config.settings import EMBEDDING_MODEL_PATH, MEMORY_DIR, INDEX_DIR


class MemoryManager:
    """
    Long-term semantic memory system for F.R.E.D.

    Responsibilities:
    - Store memories persistently
    - Generate embeddings
    - Perform semantic retrieval
    - Maintain FAISS vector index
    """

    def __init__(self, username: str = "default_user"):

        self.username = username

        # -----------------------------
        # Directories
        # -----------------------------
        # Absolute, from settings — NOT relative to the working directory.
        # These used to be Path("memory_data") / Path("memory_indexes"),
        # which resolved against the CWD, so FRED kept a different memory
        # depending on how it was started: one under Core/ for the CLI
        # (run from Core/) and another at the repo root for the GUI
        # (launched from there). Neither could see the other, so the popup
        # appeared to have no history at all while 254 entries sat in the
        # CLI's store. Both are preserved under data/memory_archive/.
        self.memory_dir = Path(MEMORY_DIR)
        self.memory_dir.mkdir(parents=True, exist_ok=True)

        self.index_dir = Path(INDEX_DIR)
        self.index_dir.mkdir(parents=True, exist_ok=True)

        # -----------------------------
        # Files
        # -----------------------------
        self.memory_file = self.memory_dir / f"{username}.jsonl"
        self.index_file = self.index_dir / f"{username}.faiss"

        # -----------------------------
        # Embedding model (local, via llama.cpp)
        # -----------------------------
        if not EMBEDDING_MODEL_PATH.exists():
            raise FileNotFoundError(
                f"Embedding model not found: {EMBEDDING_MODEL_PATH}"
            )

        # n_ctx explicit rather than left at llama-cpp-python's default of
        # 512 — this model is called up to 3x per turn (memory retrieval,
        # tool routing, vault routing) via three separate call sites that
        # all share this one instance, and repeated turns in quick
        # succession (rapid hotkey presses) produced native access
        # violations inside llama_cpp's decode() (see data/logs/crash.log)
        # consistent with that 512-token context being run past its
        # limit. 4096 is comfortably above any single embedded text here
        # (tool descriptions, vault chunks, memory entries) and the model
        # trained on 32768, so there's no accuracy tradeoff either way.
        self.embedding_model = Llama(
            model_path=str(EMBEDDING_MODEL_PATH),
            embedding=True,
            n_ctx=4096,
            verbose=False,
        )

        self.embedding_dim = len(
            self._generate_embedding("dimension probe")
        )

        # -----------------------------
        # Cached memories — loaded BEFORE the index now, since a load
        # failure's rebuild path (_load_or_create_index) needs
        # self.memories to already exist.
        # -----------------------------
        self.memories = self._load_memories()

        # -----------------------------
        # Load/create FAISS index
        # -----------------------------
        self.index = self._load_or_create_index()

    # =========================================================
    # PUBLIC METHODS
    # =========================================================

    def store(self, role: str, content: str):
        """
        Store memory persistently and index it.
        """

        if not content.strip():
            return

        embedding = self._normalize(self._generate_embedding(content))

        entry = {
            "role": role,
            "content": content.strip(),
            "timestamp": datetime.now().isoformat()
        }

        # Save memory in RAM
        self.memories.append(entry)

        # Save to disk
        with open(self.memory_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        # Add to FAISS
        vector = np.array([embedding], dtype=np.float32)
        self.index.add(vector)

        # Persist index
        faiss.write_index(
            self.index,
            str(self.index_file)
        )

    def retrieve_relevant(
        self,
        query: str,
        top_k: int = 5
    ) -> list:
        """
        Retrieve semantically relevant memories.
        """

        if not self.memories:
            return []

        # self.index and self.memories can only be trusted together if
        # they hold the same number of entries — see _load_or_create_index
        # for the confirmed way they used to drift apart silently.
        if self.index.ntotal != len(self.memories):
            self._rebuild_index()

        query_embedding = self._normalize(self._generate_embedding(query))

        query_vector = np.array(
            [query_embedding],
            dtype=np.float32
        )

        distances, indices = self.index.search(
            query_vector,
            min(top_k, len(self.memories))
        )

        results = []

        for idx in indices[0]:
            if 0 <= idx < len(self.memories):
                results.append(self.memories[idx])

        return results

    # =========================================================
    # INTERNAL METHODS
    # =========================================================

    def _generate_embedding(self, text: str):
        """
        Generate embedding vector via llama.cpp, in-process.
        """

        result = self.embedding_model.create_embedding(text)

        return result["data"][0]["embedding"]

    @staticmethod
    def _normalize(vector) -> list:
        """
        Unit-length the embedding before it goes anywhere near FAISS.

        Confirmed bug (one of four listed as open in the vault's
        active-priorities.md since 2026-07-29): the index was
        IndexFlatL2 on raw, unnormalized vectors, so "distance" was
        Euclidean distance, not cosine similarity — meaningfully
        different rankings for embeddings whose magnitude varies with
        text length, which most embedding models' do. Normalizing here
        and switching the index to IndexFlatIP (below) makes inner
        product BE cosine similarity, which is what "semantically
        relevant" is actually supposed to mean.
        """
        arr = np.asarray(vector, dtype=np.float32)
        norm = np.linalg.norm(arr)
        if norm < 1e-8:
            return arr.tolist()
        return (arr / norm).tolist()

    def _rebuild_index(self):
        """
        Re-embed every stored memory and rebuild the FAISS index from
        scratch.

        Confirmed bug (the second of the four listed in
        active-priorities.md): _load_or_create_index caught ANY
        exception from a bad/mismatched index file and silently
        substituted an empty one, while self.memories still loaded
        every entry from the JSONL. The next store() call then added a
        vector at position 0 of the new empty index while memories
        already had N entries — so index position 0 pointed at
        memories[N], not memories[0], and every retrieval after that
        returned entries next to the ones actually meant. There was no
        rebuild path, so the desync was permanent until the process
        was manually deleted.

        This is the rebuild path. Called automatically the moment
        retrieve_relevant notices the counts disagree, and directly by
        _load_or_create_index on a load failure, so an empty-but-wrong
        index is never left in place silently again.
        """
        if not self.memories:
            self.index = faiss.IndexFlatIP(self.embedding_dim)
            return

        vectors = [
            self._normalize(self._generate_embedding(m["content"]))
            for m in self.memories
        ]
        index = faiss.IndexFlatIP(self.embedding_dim)
        index.add(np.array(vectors, dtype=np.float32))
        self.index = index

        try:
            faiss.write_index(self.index, str(self.index_file))
        except OSError as e:
            print(f"[MemoryManager] rebuilt index but couldn't persist it: {e}")

    def _load_memories(self) -> list:
        """
        Load memory entries from disk.
        """

        if not self.memory_file.exists():
            return []

        memories = []

        with open(self.memory_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()

                if not line:
                    continue

                try:
                    memories.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

        return memories

    def _load_or_create_index(self):
        """
        Load an existing FAISS index, or build one from the memories
        already loaded into self.memories.

        A load failure or count mismatch used to fall through to a
        silently empty IndexFlatL2 — see _rebuild_index's docstring for
        the desync that produced. It now re-embeds and rebuilds instead,
        so the index and self.memories can never disagree on count
        after this returns.

        IndexFlatIP (inner product), not IndexFlatL2 (Euclidean
        distance) — see _normalize for why raw L2 distance on
        unnormalized vectors wasn't actually measuring similarity.
        """

        if self.index_file.exists():
            try:
                index = faiss.read_index(str(self.index_file))

                if index.d != self.embedding_dim:
                    raise ValueError("Embedding dimension mismatch.")
                if index.ntotal != len(self.memories):
                    raise ValueError(
                        f"Index has {index.ntotal} vectors but "
                        f"{len(self.memories)} memories are on disk."
                    )

                return index

            except Exception as e:
                print(f"[MemoryManager] index unusable ({e}) — rebuilding from memories")
                self._rebuild_index()
                return self.index

        if self.memories:
            self._rebuild_index()
            return self.index

        return faiss.IndexFlatIP(self.embedding_dim)