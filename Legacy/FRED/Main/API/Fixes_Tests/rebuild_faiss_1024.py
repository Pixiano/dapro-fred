# rebuild_faiss.py

# rebuild_faiss.py
import json
from pathlib import Path
import numpy as np
import faiss
from llm import get_embedding

MEMORY_FILE = Path("memory.json")
FAISS_INDEX_FILE = Path("memory.index")

# Load memories
if not MEMORY_FILE.exists():
    print("[ERROR] memory.json not found!")
    exit()

with open(MEMORY_FILE, "r", encoding="utf-8") as f:
    memories = json.load(f)

if not memories:
    print("[INFO] No memories to rebuild.")
    exit()

# Detect embedding dimension dynamically
sample_emb = get_embedding("test")
EMBED_DIM = len(sample_emb)
print(f"[INFO] Detected embedding dimension: {EMBED_DIM}")

# Create fresh FAISS index
faiss_index = faiss.IndexFlatIP(EMBED_DIM)
added = 0

for m in memories:
    content = m.get("content", "")
    emb = get_embedding(content)
    if emb and len(emb) == EMBED_DIM:
        vector = np.array([emb], dtype="float32")
        faiss_index.add(vector)
        added += 1
    else:
        print(f"[WARN] Skipped memory (invalid embedding): {content[:30]}...")

# Save new FAISS index
faiss.write_index(faiss_index, str(FAISS_INDEX_FILE))
print(f"[INFO] Rebuilt FAISS index with {added} entries.")