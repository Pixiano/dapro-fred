#python test_semantic.py

from sentence_transformers import SentenceTransformer
import faiss
import numpy as np

# 1. Load embedding model
print("[INFO] Loading embedding model...")
model = SentenceTransformer("all-MiniLM-L6-v2")  # small + fast

# 2. Example sentences
sentences = [
    "Hello, I am F.R.E.D.",
    "I am your AI assistant.",
    "You like calisthenics and building Jarvis.",
    "The sky is blue today.",
    "Python is great for AI projects."
]

# 3. Create embeddings
print("[INFO] Encoding sentences...")
embeddings = model.encode(sentences, convert_to_numpy=True)

# 4. Setup FAISS index (L2 = cosine similarity proxy)
d = embeddings.shape[1]  # dimension (should be 384)
index = faiss.IndexFlatL2(d)
index.add(embeddings)

print(f"[INFO] Added {len(sentences)} sentences to FAISS index.")

# 5. Test query
query = "Tell me about my AI assistant."
print(f"\n[QUERY] {query}")
query_emb = model.encode([query], convert_to_numpy=True)

# 6. Search
k = 2  # top results
D, I = index.search(query_emb, k)

print("\n[RESULTS]")
for rank, (dist, idx) in enumerate(zip(D[0], I[0]), start=1):
    print(f"{rank}. '{sentences[idx]}' (distance={dist:.4f})")