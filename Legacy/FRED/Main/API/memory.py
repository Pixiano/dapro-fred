#memory.py 

import json
from datetime import datetime
from pathlib import Path
import numpy as np
import faiss
from sentence_transformers import SentenceTransformer

# --- Paths ---
MEMORY_DIR = Path("User_Memories")
MEMORY_DIR.mkdir(exist_ok=True)
FAISS_DIR = Path("Faiss_Index")
FAISS_DIR.mkdir(exist_ok=True)

# --- Session memory ---
conversation = []
session_embeddings = []
session_counter = 0

# --- Load SBERT ---
sbert_model = SentenceTransformer('all-MiniLM-L12-v2')
EMBED_DIM = sbert_model.get_sentence_embedding_dimension()
print(f"[INFO] SBERT embedding dimension: {EMBED_DIM}")

# --- Utility: current time ---
def get_current_time():
    """Return ISO-formatted current time."""
    return datetime.now().isoformat()

def get_current_time_info():
    """Return detailed current time info."""
    now = datetime.now()
    return {
        "year": now.year,
        "month": now.month,
        "day": now.day,
        "hour": now.hour,
        "minute": now.minute,
        "second": now.second
    }

# --- FAISS helpers ---
def get_faiss_file(username):
    return FAISS_DIR / f"{username}_index.faiss"

def load_faiss(username):
    faiss_file = get_faiss_file(username)
    if faiss_file.exists():
        try:
            index = faiss.read_index(str(faiss_file))
            if index.d != EMBED_DIM:
                print("[WARN] FAISS index dim mismatch, rebuilding.")
                index = faiss.IndexFlatIP(EMBED_DIM)
        except Exception as e:
            print(f"[WARN] Failed to load FAISS index ({e}), rebuilding.")
            index = faiss.IndexFlatIP(EMBED_DIM)
    else:
        index = faiss.IndexFlatIP(EMBED_DIM)
    return index

def save_faiss(username, index):
    faiss.write_index(index, str(get_faiss_file(username)))

# --- Memory file helpers ---
def get_memory_file(username):
    safe_name = "".join(c if c.isalnum() else "_" for c in username.lower())
    return MEMORY_DIR / f"{safe_name}.jsonl"

def append_memory(username, entry):
    """Append single entry safely to JSONL."""
    path = get_memory_file(username)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

def load_memory(username):
    """Load all entries for semantic search."""
    path = get_memory_file(username)
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]

# --- Embedding ---
def get_embedding(text):
    return sbert_model.encode(text).tolist()

# --- Remember ---
def remember(username, role, content, faiss_index=None):
    """Add message once per cycle with time info."""
    global session_counter
    embedding = get_embedding(content)
    timestamp = get_current_time()
    time_info = get_current_time_info()

    entry = {
        "role": role,
        "content": content,
        "timestamp": timestamp,
        "time_info": time_info,
        "embedding": embedding
    }

    # Prevent exact duplicate append
    if conversation and conversation[-1]["content"] == content:
        return  # skip redundant entry

    # Add to session
    conversation.append(entry)
    session_embeddings.append(embedding)
    append_memory(username, entry)

    # Add to FAISS
    if faiss_index is not None:
        vector = np.array([embedding], dtype="float32")
        faiss_index.add(vector)
        session_counter += 1
        if session_counter >= 5:
            save_faiss(username, faiss_index)
            session_counter = 0

# --- Recall ---
def recall(n=10):
    return conversation[-n:]

# --- Forget all ---
def forget_all(username):
    global conversation, session_embeddings, session_counter
    conversation.clear()
    session_embeddings.clear()
    session_counter = 0

    # Reset files
    get_memory_file(username).write_text("")
    faiss.write_index(faiss.IndexFlatIP(EMBED_DIM), str(get_faiss_file(username)))

# --- Search ---
def search_memory(username, query, top_n=5):
    memories = load_memory(username)
    if not memories:
        return []

    faiss_index = load_faiss(username)
    query_emb = get_embedding(query)
    query_vec = np.array([query_emb], dtype="float32")
    D, I = faiss_index.search(query_vec, top_n)

    results = []
    for idx in I[0]:
        if 0 <= idx < len(memories):
            results.append(memories[idx])
    return results