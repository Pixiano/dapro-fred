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
        # Load/create FAISS index
        # -----------------------------
        self.index = self._load_or_create_index()

        # -----------------------------
        # Cached memories
        # -----------------------------
        self.memories = self._load_memories()

    # =========================================================
    # PUBLIC METHODS
    # =========================================================

    def store(self, role: str, content: str):
        """
        Store memory persistently and index it.
        """

        if not content.strip():
            return

        embedding = self._generate_embedding(content)

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

        query_embedding = self._generate_embedding(query)

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
        Load existing FAISS index or create new one.
        """

        if self.index_file.exists():

            try:
                index = faiss.read_index(
                    str(self.index_file)
                )

                # Dimension safety check
                if index.d != self.embedding_dim:
                    raise ValueError(
                        "Embedding dimension mismatch."
                    )

                return index

            except Exception:
                pass

        return faiss.IndexFlatL2(
            self.embedding_dim
        )