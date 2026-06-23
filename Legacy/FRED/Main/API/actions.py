# actions.py
from pathlib import Path
import os
import webbrowser
import subprocess

# Folder containing your .lnk shortcuts
SHORTCUTS_FOLDER = Path(r"C:\Users\Admin\Project_FRED\FRED\OS_Automation_Perfection\Shortcuts")

# --- Browser ---
def open_browser(url):
    """Open a URL in the default browser."""
    webbrowser.open(url)

# --- Apps / Games ---
def open_app(app_name):
    """
    Open an application.
    1. Try .lnk shortcut in SHORTCUTS_FOLDER first.
    2. Fallback to system PATH for normal apps (detached).
    3. Special handling for UWP apps (Clock, Settings, Forza, etc.).
    """
    # --- Check .lnk shortcut ---
    shortcut_path = SHORTCUTS_FOLDER / f"{app_name}.lnk"
    if shortcut_path.exists():
        try:
            os.startfile(shortcut_path)
            return
        except Exception as e:
            print(f"Failed to open {app_name} via shortcut: {e}")

    # --- UWP / special apps ---
    UWP_APPS = {
        "Clock": "ms-clock:",
        "Settings": "ms-settings:",
        "Forza": "forza:"  # placeholder if Forza is UWP
    }
    if app_name in UWP_APPS:
        try:
            subprocess.run(f"start {UWP_APPS[app_name]}", shell=True)
            return
        except Exception as e:
            print(f"Failed to open {app_name} via UWP protocol: {e}")

    # --- Fallback to PATH executable (detached) ---
    try:
        # Use 'start "" <app>' to detach and not block Python
        subprocess.Popen(f'start "" "{app_name}"', shell=True)
    except Exception as e:
        print(f"Failed to open {app_name} from system PATH: {e}")

# --- File Operations ---
def create_text_file(filename, content=""):
    """Create a text file with optional content."""
    path = Path(filename)
    path.write_text(content)
    print(f"File '{filename}' created with content: {content}")

def create_folder(foldername):
    """Create a folder with the given name."""
    path = Path(foldername)
    path.mkdir(exist_ok=True)
    print(f"Folder '{foldername}' created.")