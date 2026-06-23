#fred.py (the main cmd handler before server was made)

from llm import ask_fred
from memory import (
    remember, recall, search_memory, forget_all,
    load_memory, load_faiss, save_faiss, EMBED_DIM, get_current_time_info
)
from stt import STT
from tts import speak
from actions import open_browser, open_app, create_text_file, create_folder
import pickle
import re
import numpy as np
import faiss

# === Configuration ===
USE_STT = True
USE_TTS = True


# === Load OS Command Classifier ===
with open("classifier.pkl", "rb") as f:
    data = pickle.load(f)
clf = data["model"]
vectorizer = data["vectorizer"]


# === Extract parameters from natural language ===
def extract_file_params(command: str):
    fname_match = re.search(r"called\s+([^\s]+)", command)
    content_match = re.search(r"with (.+)", command)
    filename = fname_match.group(1) + ".txt" if fname_match else "untitled.txt"
    content = content_match.group(1) if content_match else ""
    return filename, content


def extract_folder_params(command: str):
    fname_match = re.search(r"called\s+([^\s]+)", command)
    foldername = fname_match.group(1) if fname_match else "NewFolder"
    return foldername


# === Mapped OS Actions ===
actions = {
    "open_browser_youtube": lambda: open_browser("https://youtube.com"),
    "open_browser_google": lambda: open_browser("https://google.com"),
    "open_browser_chrome": lambda: open_app("Google Chrome"),
    "open_browser_edge": lambda: open_app("Microsoft Edge"),
    "open_browser_gmail": lambda: open_browser("https://mail.google.com"),
    "open_browser_chatgpt": lambda: open_browser("https://chat.openai.com"),
    "open_browser_discord": lambda: open_browser("https://discord.com/app"),
    "open_browser_spotify": lambda: open_browser("https://open.spotify.com/collection/tracks"),
    "open_browser_amazon": lambda: open_browser("https://amazon.in"),
    "open_browser_allen": lambda: open_browser("https://allen.in"),
    "open_app_notepad": lambda: open_app("notepad"),
    "open_app_calculator": lambda: open_app("calc"),
    "open_app_word": lambda: open_app("Word"),
    "open_app_excel": lambda: open_app("Excel"),
    "open_app_powerpoint": lambda: open_app("PowerPoint"),
    "open_app_paint": lambda: open_app("Paint"),
    "open_app_cmd": lambda: open_app("cmd"),
    "open_app_clock": lambda: open_app("Clock"),
    "open_app_settings": lambda: open_app("Settings"),
    "open_app_steam": lambda: open_app("Steam"),
    "open_app_forza": lambda: open_app("Forza"),
    "open_app_lmstudio": lambda: open_app("LMStudio"),
    "open_app_unity": lambda: open_app("Unity"),
    "open_app_godot": lambda: open_app("Godot"),
    "open_app_davinci_resolve": lambda: open_app("DaVinci Resolve"),
    "open_app_lively": lambda: open_app("Lively"),
    "open_app_vscode": lambda: open_app("Visual Studio Code"),
    "create_text_file": lambda fname, text: create_text_file(fname, text),
    "create_folder": lambda foldername: create_folder(foldername),
    "chat": lambda: print("Hey! I'm listening!😎"),
}


# === Intent Parsing and Execution ===
def parse_command(user_input):
    X_vec = vectorizer.transform([user_input])
    intent = clf.predict(X_vec)[0]

    try:
        if intent in ["create_text_file", "create_folder"] or intent.startswith("open_"):
            if intent == "create_text_file":
                fname, text = extract_file_params(user_input)
                actions[intent](fname, text)
                return True, f"File '{fname}' created."
            elif intent == "create_folder":
                foldername = extract_folder_params(user_input)
                actions[intent](foldername)
                return True, f"Folder '{foldername}' created."
            else:
                actions[intent]()
                return True, f"{intent} executed."

        elif intent == "chat":
            return False, None
        else:
            return False, None
    except Exception as e:
        return False, f"Error executing intent: {e}"


# === Main FRED Functionality ===
def main():
    print("F.R.E.D. is online. Type 'exit' to quit.")
    print("Commands: /recall, /search <query>, /forget, /listen")

    if USE_TTS:
        speak("FRED is online. Hello, Vutsal!")

    username = "default_user"
    faiss_index = load_faiss(username)

    # --- Load memory once (no rewriting!) ---
    past_memories = load_memory(username)
    if past_memories:
        print(f"[INFO] Loaded {len(past_memories)} previous memory entries.")
        vectors = [np.array(m["embedding"], dtype="float32") for m in past_memories if "embedding" in m]
        if vectors:
            faiss_index.add(np.vstack(vectors))
            save_faiss(username, faiss_index)

    # --- Setup STT ---
    stt = STT() if USE_STT else None
    if stt:
        stt.list_mics()
        print("[FRED] STT enabled. Use /listen to speak.")

    MEMORY_TRIGGERS = ["remember", "first chat", "our chat", "memory", "do you know", "my name"]
    session_counter = 0

    while True:
        user_input = input("You: ").strip()
        if not user_input:
            continue
        if user_input.lower() == "exit":
            break

        # --- Speech-to-Text ---
        if user_input.lower().startswith("/listen"):
            if not stt:
                print("[FRED] STT not enabled.")
                continue
            print("[FRED] Listening... Speak now (timeout 10s).")
            spoken_text = stt.listen_once(timeout=10).strip()
            if spoken_text:
                print(f"You (voice): {spoken_text}")
                user_input = spoken_text
            else:
                print("[FRED] No speech detected.")
                continue

        # --- Memory Commands ---
        if user_input.lower().startswith("/recall"):
            try:
                n = int(user_input.split()[1])
            except:
                n = 10
            for entry in recall(n):
                print(f"[{entry['role']} at {entry.get('timestamp', 'unknown')}] {entry['content']}")
            continue

        if user_input.lower().startswith("/search"):
            if len(user_input.split()) < 2:
                print("Usage: /search <query>")
                continue
            keyword = user_input.split(" ", 1)[1]
            results = search_memory(username, keyword, top_n=5)
            for r in results:
                print(f"[{r['role']} at {r['timestamp']}] {r['content']}")
            continue

        if user_input.lower().startswith("/forget"):
            forget_all(username)
            faiss_index = faiss.IndexFlatIP(EMBED_DIM)
            save_faiss(username, faiss_index)
            print("🧹 Memory wiped.")
            continue

        # --- OS Commands ---
        is_cmd, os_response = parse_command(user_input)
        if is_cmd:
            print(f"FRED (OS): {os_response}")
            if USE_TTS:
                speak(os_response)
            continue

        # --- Chat Processing ---
        remember(username, "user", user_input, faiss_index=faiss_index)
        session_counter += 1

        # --- Memory Recall (semantic + recency) ---
        semantic_results = search_memory(username, user_input, top_n=5) \
            if any(word in user_input.lower() for word in MEMORY_TRIGGERS) else []
        context = recall(10)
        seen_hashes = set(hash(m["content"]) for m in context)
        for mem in semantic_results:
            if hash(mem["content"]) not in seen_hashes:
                context.append(mem)
                seen_hashes.add(hash(mem["content"]))

        # --- Time Awareness ---
        current_time_info = get_current_time_info()
        time_text = (
            f"The current local time is {current_time_info['hour']:02d}:"
            f"{current_time_info['minute']:02d}, "
            f"{current_time_info['day']:02d}/"
            f"{current_time_info['month']:02d}/"
            f"{current_time_info['year']}."
        )

        # --- Context Assembly ---
        context_text = "\n".join([f"{m['role'].upper()}: {m['content']}" for m in context[-10:]])
        prompt = f"{context_text}\n\n{time_text}\n\nUSER: {user_input}\nFRED:"

        try:
            fred_reply = ask_fred(prompt)
        except Exception as e:
            fred_reply = f"[Error: {str(e)}]"

        print(f"FRED: {fred_reply}")
        remember(username, "assistant", fred_reply, faiss_index=faiss_index)

        session_counter += 1
        if session_counter >= 5:
            save_faiss(username, faiss_index)
            session_counter = 0

        if USE_TTS:
            speak(fred_reply)


# === Run ===
if __name__ == "__main__":
    main()