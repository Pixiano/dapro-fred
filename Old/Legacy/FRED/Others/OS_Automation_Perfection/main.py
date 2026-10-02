# main.py
import pickle
import re
from actions import open_browser, open_app, create_text_file, create_folder
from intents import intents

# --- Load trained model ---
with open("classifier.pkl", "rb") as f:
    data = pickle.load(f)
clf = data["model"]
vectorizer = data["vectorizer"]

# --- Intent → Action mapping ---
actions = {
    # Browsers
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

    # System Apps
    "open_app_notepad": lambda: open_app("notepad"),
    "open_app_calculator": lambda: open_app("calc"),
    "open_app_word": lambda: open_app("Word"),
    "open_app_excel": lambda: open_app("Excel"),
    "open_app_powerpoint": lambda: open_app("PowerPoint"),
    "open_app_paint": lambda: open_app("Paint"),
    "open_app_cmd": lambda: open_app("cmd"),
    "open_app_clock": lambda: open_app("Clock"),
    "open_app_settings": lambda: open_app("Settings"),

    # Games / Dev Software (.lnk shortcuts / UWP handled in actions.py)
    "open_app_steam": lambda: open_app("Steam"),
    "open_app_forza": lambda: open_app("Forza"),
    "open_app_lmstudio": lambda: open_app("LMStudio"),
    "open_app_unity": lambda: open_app("Unity"),
    "open_app_godot": lambda: open_app("Godot"),
    "open_app_davinci_resolve": lambda: open_app("DaVinci Resolve"),
    "open_app_lively": lambda: open_app("Lively"),
    "open_app_vscode": lambda: open_app("Visual Studio Code"),

    # File Operations
    "create_text_file": lambda fname, text: create_text_file(fname, text),
    "create_folder": lambda foldername: create_folder(foldername),

    # Casual Chat
    "chat": lambda: print("Hey FREDiie! I'm listening 😎")
}

# --- Parameter extraction for "create_text_file" ---
def extract_file_params(command: str):
    fname_match = re.search(r"called\s+(\w+)", command)
    content_match = re.search(r"with (.+)", command)
    filename = fname_match.group(1) + ".txt" if fname_match else "untitled.txt"
    content = content_match.group(1) if content_match else ""
    return filename, content

# --- Parameter extraction for "create_folder" ---
def extract_folder_params(command: str):
    fname_match = re.search(r"called\s+(\w+)", command)
    foldername = fname_match.group(1) if fname_match else "NewFolder"
    return foldername

# --- Main loop ---
def main():
    print("F.R.E.D is online! Type 'quit' to exit.")
    while True:
        user_input = input("Command (or 'quit'): ").strip()
        if user_input.lower() in ["quit", "exit"]:
            break

        # Predict intent
        try:
            X_vec = vectorizer.transform([user_input])
            intent = clf.predict(X_vec)[0]
            print(f"Detected intent: {intent}")
        except Exception as e:
            print(f"Error predicting intent: {e}")
            intent = "chat"

        # Execute mapped action
        try:
            if intent == "create_text_file":
                fname, text = extract_file_params(user_input)
                actions[intent](fname, text)
            elif intent == "create_folder":
                foldername = extract_folder_params(user_input)
                actions[intent](foldername)
            else:
                actions.get(intent, actions["chat"])()
        except Exception as e:
            print(f"Error executing intent '{intent}': {e}")

if __name__ == "__main__":
    main()