# Core/tools/system_tools.py

import webbrowser
import subprocess
from pathlib import Path
from datetime import datetime


# =========================================================
# BROWSER TOOLS
# =========================================================

def open_website(url: str) -> str:
    """
    Open a website in the default browser.
    """

    webbrowser.open(url)

    return f"Opened {url}"


# =========================================================
# APPLICATION TOOLS
# =========================================================

def launch_application(app_name: str) -> str:
    """
    Launch a desktop application.
    """

    try:

        subprocess.Popen(app_name)

        return f"Launched {app_name}"

    except Exception as e:

        return (
            f"Failed to launch {app_name}: {str(e)}"
        )


# =========================================================
# FILE TOOLS
# =========================================================

def create_text_file(
    filename: str,
    content: str = ""
) -> str:
    """
    Create a text file.
    """

    path = Path(filename)

    if not path.suffix:
        path = path.with_suffix(".txt")

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

    return (
        f"Created file: {path.resolve()}"
    )


def create_folder(folder_name: str) -> str:
    """
    Create a folder.
    """

    path = Path(folder_name)

    path.mkdir(
        parents=True,
        exist_ok=True
    )

    return (
        f"Created folder: {path.resolve()}"
    )


# =========================================================
# SYSTEM INFO TOOLS
# =========================================================

def get_current_time() -> str:
    """
    Get local system time.
    """

    now = datetime.now()

    return f"It's {now.strftime('%H:%M:%S')} on {now.strftime('%Y-%m-%d')}."