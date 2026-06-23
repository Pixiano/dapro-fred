# rebuild_faiss.py
import json
from pathlib import Path
import numpy as np
import faiss

# --- File paths ---
MEMORY_FILE = Path("memory.json")
FAISS_INDEX_FILE = Path("memory.index")

# --- Step 1: Load memory ---
if MEMORY_FILE.exists():
    with open(MEMORY_FILE, "r", encoding="utf-8") as f:
        memories = json.load(f)
    print(f"[INFO] Loaded {len(memories)} memory entries from {MEMORY_FILE}.")
else:
    print(f"[WARN] {MEMORY_FILE} not found. Nothing to rebuild.")
    memories = []

# --- Step 2: Detect embedding dimension ---
if memories and "embedding" in memories[0]:
    embed_dim = len(memories[0]['embedding'])
    print(f"[INFO] Detected embedding dimension: {embed_dim}")
else:
    embed_dim = 384  # fallback default
    print(f"[WARN] No embeddings found. Using default dim: {embed_dim}")

# --- Step 3: Create new FAISS index ---
faiss_index = faiss.IndexFlatIP(embed_dim)

# --- Step 4: Add all embeddings ---
if memories:
    vectors = np.array([m['embedding'] for m in memories], dtype='float32')
    faiss_index.add(vectors)
    print(f"[INFO] Added {vectors.shape[0]} vectors to FAISS index.")
else:
    print("[INFO] No vectors to add to FAISS index.")

# --- Step 5: Save FAISS index ---
faiss.write_index(faiss_index, str(FAISS_INDEX_FILE))
print(f"[SUCCESS] FAISS index rebuilt and saved to {FAISS_INDEX_FILE}")

# --- Optional: sanity check ---
if memories:
    D, I = faiss_index.search(vectors[:1], 5)
    print(f"[INFO] Top 5 closest memory indices to first entry: {I[0]}")