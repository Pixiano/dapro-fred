# server.py

import os
import json
import threading
import time
import re
from flask import Flask, render_template, request
from flask_cors import CORS
from flask_socketio import SocketIO, emit
from llm import ask_fred
from tts import speak
from sentence_transformers import SentenceTransformer, util

# ---------------- Flask setup ----------------
app = Flask(__name__, template_folder="templates")
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_FILE = os.path.join(BASE_DIR, "credentials.json")
MEMORY_DIR = os.path.join(BASE_DIR, "User_Memories")
os.makedirs(MEMORY_DIR, exist_ok=True)

# ---------------- Default credentials ----------------
DEFAULT_CREDENTIALS = {
    "Vatsal": "DaPro",
    "Abram": "Abram",
    "Suhani": "Suhani",
    "Shreyansh": "Shreyansh",
    "RedBhaiya": "RedBhaiya",
    "Cherie": "Cherie",
    "Jiyana": "Jiyana",
    "Anvay": "Anvay",
    "Vatsal Da Pro": "DaPro",
    "Devansh": "Devansh",
    "Pranati": "Pranati",
    "Saloni": "Saloni",
    "Vihaan": "Vihaan",
    "Kayam": "Kayam",
    "Diya": "Diya",
    "Sara": "Sara",
    "Amayr": "Amayr",
    "Aarav": "Aarav",
    "Aliza": "Aliza",
    "Aleeza": "Aleeza",
    "Charu": "Charu",
    "Kirti": "Kirti",
    "Dhiraj": "Dhiraj",
    "Sunitika": "Sunitika",
    "Abhishek": "Abhishek",
    "Abishek": "Abishek",
    "Shweta": "Shweta",
    "Monica": "Monica",
    "Guest": "Yast",
    "Neha": "Neha",
    "Shauryamann": "Shauryamann",
    "Monika": "Monika"
}

# ---------------- Load credentials ----------------
if not os.path.exists(CREDENTIALS_FILE):
    with open(CREDENTIALS_FILE, "w", encoding="utf-8") as f:
        json.dump(DEFAULT_CREDENTIALS, f, indent=2)
    CREDENTIALS = DEFAULT_CREDENTIALS
else:
    with open(CREDENTIALS_FILE, "r", encoding="utf-8") as f:
        CREDENTIALS = json.load(f)

# ---------------- Embedding model ----------------
model = SentenceTransformer("all-MiniLM-L6-v2")

# ---------------- Helper functions ----------------
def get_user_memory_file(username):
    username = username.strip().lower()
    safe_name = re.sub(r"[^a-z0-9_\- ]", "_", username).replace(" ", "_")
    return os.path.join(MEMORY_DIR, f"{safe_name}.jsonl")

def load_user_memory(username):
    path = get_user_memory_file(username)
    if not os.path.exists(path):
        return []
    entries = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            entries.append(json.loads(line.strip()))
    return entries

def append_user_memory(username, memory_entry):
    path = get_user_memory_file(username)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(memory_entry, ensure_ascii=False) + "\n")

def semantic_search(query, memory_entries, top_n=5):
    if not memory_entries:
        return []
    corpus = [m["content"] for m in memory_entries]
    corpus_embeddings = [m.get("embedding") for m in memory_entries]
    if None in corpus_embeddings:
        corpus_embeddings = model.encode(corpus, convert_to_tensor=True)
        for i, m in enumerate(memory_entries):
            m["embedding"] = corpus_embeddings[i].tolist()
    query_embedding = model.encode(query, convert_to_tensor=True)
    scores = util.cos_sim(query_embedding, corpus_embeddings)[0]
    top_results = sorted(
        [(i, float(scores[i])) for i in range(len(scores))],
        key=lambda x: x[1], reverse=True
    )[:top_n]
    return [memory_entries[i] for i, _ in top_results]

# ---------------- Web routes ----------------
@app.route("/")
def index():
    return render_template("index_real.html")

# ---------------- Socket.IO ----------------
sid_to_username = {}

@socketio.on("verify_user")
def handle_verify(data):
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    sid = request.sid

    if username in CREDENTIALS and CREDENTIALS[username] == password:
        sid_to_username[sid] = username
        if username.lower() in ["vatsal", "vatsal da pro"]:
            message = "✅ Verified! Welcome God!"
        elif username.lower() == "suhani":
            message = f"✅ Verified! Welcome, {username}"
        else:
            message = f"✅ Verified! Welcome, {username}"

        emit("verify_response", {"success": True, "name": username, "message": message}, room=sid)

        def reveal_chat():
            time.sleep(2)
            socketio.emit("show_chat", {"name": username}, room=sid)
        socketio.start_background_task(reveal_chat)
        print(f"DEBUG: {username} verified, SID={sid}")

    else:
        emit("verify_response", {"success": False, "message": "❌ Invalid name or password!"}, room=sid)
        print(f"DEBUG: Failed login attempt for {username}, SID={sid}")

@socketio.on("message")
def handle_message(data):
    sid = request.sid
    username = sid_to_username.get(sid)
    if not username:
        emit("bot_reply", {"reply": "Unauthorized. Please login first."}, room=sid)
        print(f"DEBUG: Unauthorized message attempt, SID={sid}")
        return

    user_input = data.get("message", "").strip()
    if not user_input:
        return

    memory_entries = load_user_memory(username)
    relevant = semantic_search(user_input, memory_entries, top_n=5)
    if relevant:
        memory_text = "\n".join([f"{m['role']}: {m['content']}" for m in relevant])
        prompt = f"Past relevant memory:\n{memory_text}\nUser asked: {user_input}"
    else:
        prompt = user_input

    # ---------------- Generate response ----------------
    try:
        recent_memory = memory_entries[-10:] if memory_entries else []
        reply = ask_fred(prompt, recent_memory)
        backend_used = "LM Studio" if "🖥️" in reply else "Mistral API"
        print(f"DEBUG: Reply ready | User={username} | Backend={backend_used} | Content={reply[:80]}...")
    except Exception as e:
        reply = f"[ERROR] F.R.E.D. malfunctioned: {e}"
        backend_used = "Error"
        print(f"DEBUG: Reply generation failed for {username}: {e}")

    # ---------------- Memory entries ----------------
    user_entry = {"role": "user", "content": user_input, "embedding": model.encode(user_input).tolist()}
    assistant_entry = {"role": "assistant", "content": reply, "embedding": model.encode(reply).tolist()}
    append_user_memory(username, user_entry)
    append_user_memory(username, assistant_entry)

    # ---------------- Emit reply ----------------
    reply_tagged = f"[{backend_used}] {reply}"
    emit("bot_reply", {"reply": reply_tagged}, room=sid)
    print(f"DEBUG: Reply emitted to {username} (SID={sid})")

    # ---------------- Dual Voice Output ----------------
    # 1️⃣ Server-side TTS playback
    threading.Thread(target=speak, args=(reply,)).start()

    # 2️⃣ Client-side (browser) TTS playback
    socketio.emit("tts_text", {"text": reply}, room=sid)

@socketio.on("exit_command")
def handle_exit(data):
    emit("close_chat", {"text": "close_chat"}, broadcast=True)
    print("🚨 Server shutting down by exit command!")
    threading.Thread(target=lambda: os._exit(0)).start()

# ---------------- Run Server ----------------
if __name__ == "__main__":
    print("🚀 F.R.E.D. Server running on http://localhost:5000")
    socketio.run(app, host="0.0.0.0", port=5000)