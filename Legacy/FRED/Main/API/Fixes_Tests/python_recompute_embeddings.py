# recompute_embeddings.py
import json
from pathlib import Path
import numpy as np
import faiss
from sentence_transformers import SentenceTransformer
from datetime import datetime

MEMORY_FILE = Path("memory.json")
FAISS_INDEX_FILE = Path("memory.index")

# --- Load SBERT middle model ---
sbert_model = SentenceTransformer('all-MiniLM-L12-v2')
EMBED_DIM = sbert_model.get_sentence_embedding_dimension()
print(f"[INFO] SBERT embedding dimension detected: {EMBED_DIM}")

# --- Load old memories ---
if MEMORY_FILE.exists():
    with open(MEMORY_FILE, "r", encoding="utf-8") as f:
        memories = json.load(f)
else:
    memories = []
    print("No memories to recompute. Exiting.")
    exit()

# --- Recompute embeddings ---
for entry in memories:
    content = entry.get("content", "")
    emb = sbert_model.encode(content).tolist()
    entry["embedding"] = emb

print(f"[INFO] Recomputed embeddings for {len(memories)} entries.")

# --- Save updated memory ---
with open(MEMORY_FILE, "w", encoding="utf-8") as f:
    json.dump(memories, f, indent=2)
print(f"[INFO] Saved updated memory.json")

# --- Rebuild FAISS index ---
faiss_index = faiss.IndexFlatIP(EMBED_DIM)
vectors = [np.array(m["embedding"], dtype="float32") for m in memories if "embedding" in m]
if vectors:
    faiss_index.add(np.stack(vectors))
    faiss.write_index(faiss_index, str(FAISS_INDEX_FILE))
    print(f"[INFO] Rebuilt FAISS index with {len(vectors)} vectors.")
else:
    print("[WARN] No vectors to add to FAISS index.")