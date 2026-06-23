# fred_lan_demo.py
import os
import sys
import threading
import webbrowser
import json
import subprocess
from flask import Flask, render_template
from flask_cors import CORS
from flask_socketio import SocketIO, emit

# --- Ensure Python can find fred.py ---
sys.path.append(r"C:\Users\Admin\Project_FRED\FRED\Main\API")
from fred import ask_fred, remember, recall, search_memory, parse_command, speak, USE_TTS

# --- Flask + SocketIO setup ---
TEMPLATE_DIR = r"C:\Users\Admin\Project_FRED\FRED\Others\LAN_Demo\templates"
app = Flask(__name__, template_folder=TEMPLATE_DIR)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

PORT = 5000
HOST_IP = "0.0.0.0"
LAN_IP = "192.168.0.106"  # replace with your LAN IP

MEMORY_FILE = r"C:\Users\Admin\Project_FRED\FRED\Main\API\semantic_memory.json"

# --- Auth config ---
AUTHORIZED_USERS = ["Vatsali", "Suhani", "Jiyana", "Anvay", "Vatsal Da Pro", "Devansh", "Pranati", "Saloni", "Vihaan", "Dhiraj"]
OS_ALLOWED_USERS = ["Vatsal", "Vatsal Da Pro"]

# --- Memory helpers ---
def persistent_remember(role, content, embedding=None):
    print(f"[DEBUG] Saving memory: role={role}, content={content[:60]}...")
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            mem = json.load(f)
    except FileNotFoundError:
        mem = []

    mem.append({
        "role": role,
        "content": content,
        "embedding": embedding,
        "timestamp": None
    })

    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(mem, f, indent=2)

def persistent_recall(n=10):
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            mem = json.load(f)
    except FileNotFoundError:
        mem = []
    print(f"[DEBUG] Recalling last {n} memories (found {len(mem)})")
    return mem[-n:]

# --- Enhanced search wrapper ---
def search_memory_enhanced(keyword):
    print(f"[DEBUG] Searching memory for keyword: {keyword}")
    results = search_memory(keyword)
    keyword_lower = keyword.lower()
    filtered = [r for r in results if keyword_lower in r['content'].lower()]
    print(f"[DEBUG] Found {len(filtered)} matching memory entries")
    return filtered

# --- OS command runner ---
def run_os_command(command, username):
    print(f"[DEBUG] Running OS command: {command}")
    if username not in OS_ALLOWED_USERS:
        return "⚠️ OS commands disabled for your account."
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True)
        return result.stdout if result.stdout else "✅ Command executed."
    except Exception as e:
        return f"❌ Error executing command: {e}"

# --- Routes ---
@app.route("/")
def index():
    print("[DEBUG] Serving index.html")
    return render_template("index.html")

# --- SocketIO message handler ---
@socketio.on("user_message")
def handle_message(data):
    username = data.get("name", "Unknown").strip()
    user_input = data.get("user_input", "").strip()
    print(f"\n[DEBUG] Username={username}, Input={user_input}")

    if username not in AUTHORIZED_USERS:
        emit("fred_reply", {"text": "[ACCESS DENIED] You are not authorized.", "username": username})
        print("[DEBUG] Unauthorized user blocked.")
        return

    if not user_input:
        emit("fred_reply", {"text": "[FRED] Please type something.", "username": username})
        print("[DEBUG] Empty input ignored.")
        return

    persistent_remember("user", user_input)
    context = persistent_recall(10)

    MEMORY_TRIGGERS = ["remember", "recall", "first chat", "our chat", "memory", "do you know", "my name"]
    semantic_results = []
    if any(word in user_input.lower() for word in MEMORY_TRIGGERS):
        semantic_results = search_memory_enhanced(user_input)
        for mem in semantic_results:
            if mem not in context:
                context.append(mem)

    if semantic_results:
        memory_text = "\n".join([f"{m['role']}: {m['content']}" for m in semantic_results])
        user_input_with_memory = f"Past relevant memory:\n{memory_text}\nUser asked: {user_input}"
    else:
        user_input_with_memory = user_input

    # --- Ask FRED in a background thread ---
    def process_fred_reply():
        print("[DEBUG] Asking FRED for reply...")
        try:
            fred_reply = ask_fred(user_input_with_memory, context)
            if not fred_reply:
                fred_reply = "[ERROR] No reply received from FRED."
        except Exception as e:
            fred_reply = f"[ERROR] {str(e)}"

        # --- Check if FRED's output is an OS command ---
        OS_KEYWORDS = ["dir", "ls", "rm", "mkdir", "ping", "tasklist", "shutdown", "start", "del", "echo"]
        output_first_word = fred_reply.strip().split(" ")[0].lower()
        if output_first_word in OS_KEYWORDS and username not in OS_ALLOWED_USERS:
            fred_reply = "⚠️ OS commands are disabled for your account."
            print("[DEBUG] FRED output was an OS command, access denied.")

        persistent_remember("assistant", fred_reply)

        # --- Emit reply ---
        emit("fred_reply", {"text": fred_reply, "username": username}, broadcast=False)
        print(f"[DEBUG] Emitted reply to client: {fred_reply[:80]}...")

        # --- TTS ---
        if USE_TTS:
            threading.Thread(target=lambda: speak(fred_reply)).start()

    threading.Thread(target=process_fred_reply).start()

# --- Start server ---
def run_server():
    print("🚀 F.R.E.D. LAN demo starting...")

    def open_browser_later():
        import time
        time.sleep(1)
        webbrowser.open(f"http://{LAN_IP}:{PORT}")

    threading.Thread(target=open_browser_later).start()
    socketio.run(app, host=HOST_IP, port=PORT, debug=True, use_reloader=False, allow_unsafe_werkzeug=True)

if __name__ == "__main__":
    run_server()