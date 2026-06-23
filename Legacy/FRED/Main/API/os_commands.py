# os_commands.py

import os
import subprocess
import webbrowser
from pathlib import Path
import re

# ----------------- Action Functions -----------------
def open_app(app_name):
    """Open a desktop app"""
    try:
        # Windows example: assumes app_name is in PATH or start menu
        subprocess.Popen(app_name)
        return f"✅ FRED Opened {app_name}"
    except Exception as e:
        return f"❌ FRED failed to open {app_name}: {e}"

def open_browser(url):
    """Open URL in default browser"""
    try:
        webbrowser.open(url)
        return f"✅ Opened browser at {url}"
    except Exception as e:
        return f"❌ Failed to open browser: {e}"

def create_text_file(file_name, content=""):
    """Create a text file with optional content"""
    try:
        file_path = Path(file_name).with_suffix(".txt")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"✅ Created file {file_path.resolve()}"
    except Exception as e:
        return f"❌ Failed to create file: {e}"

# ----------------- Command Parsing -----------------
def parse_command(user_input):
    """
    Detects if input is an OS command.
    Returns (is_command, response)
    """
    text = user_input.lower()

    # --- Open browser shortcuts ---
    if "open youtube" in text:
        return True, open_browser("https://www.youtube.com")
    if "open google" in text:
        return True, open_browser("https://www.google.com")
    if "open spotify" in text:
        return True, open_browser("https://open.spotify.com/collection/tracks")
    if "open discord" in text:
        return True, open_browser("https://discord.com")
    if "open chatgpt" in text:
        return True, open_browser("https://chatgpt.com")
    if "open allen" in text:
        return True, open_browser("https://allen.in")
    if "open gmail" in text or "open email" in text:
        return True, open_browser("https://mail.google.com/mail/u/0/#inbox")
    
    # --- Open generic app ---
    if text.startswith("open "):
        app = user_input[5:].strip()  # take the rest as app name
        return True, open_app(app)
    
    # --- Create text file with optional content ---
    if "create text file" in text:
        # Flexible regex: allows 'with', 'as', 'containing' for content
        match = re.search(
            r'create text file (?:called )?(\w+)(?: (?:with|as|containing) (.+))?',
            text,
            re.IGNORECASE
        )
        if match:
            file_name = match.group(1)
            content = match.group(2) if match.group(2) else ""
            return True, create_text_file(file_name, content)
        else:
            return True, "❌ FRED couldn't detect file name."
    
    # Not an OS command
    return False, None