# fred_gui.py
# FRED GUI Application Launcher
# Run this to launch FRED as a graphical application (not CLI)

import sys
import os

# Add Core directory to path so imports work
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'Core'))

from ui.gui_app import run_gui

if __name__ == "__main__":
    run_gui()
