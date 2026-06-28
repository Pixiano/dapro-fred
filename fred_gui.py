# fred_gui.py
# FRED GUI Application Launcher
# Run this to launch FRED as a graphical application (not CLI)

import sys
import os

# Handle both bundled EXE and development environments
if getattr(sys, 'frozen', False):
    # Running as bundled EXE
    app_dir = sys._MEIPASS
else:
    # Running as script
    app_dir = os.path.join(os.path.dirname(__file__), 'Core')
    sys.path.insert(0, app_dir)

# Now do the imports
from ui.gui_app import run_gui

if __name__ == "__main__":
    run_gui()
