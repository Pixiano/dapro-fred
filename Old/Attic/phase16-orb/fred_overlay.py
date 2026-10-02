# fred_overlay.py
# FRED GUI Application Launcher — always-on-top Siri-style overlay.
# Run this instead of Core/main.py to launch FRED in GUI (overlay) mode.

import sys
import os

app_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Core")
sys.path.insert(0, app_dir)

from ui.overlay_app import main as run_overlay

if __name__ == "__main__":
    run_overlay()
