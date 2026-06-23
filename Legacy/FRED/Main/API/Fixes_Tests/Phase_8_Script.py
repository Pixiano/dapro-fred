import os
import webbrowser
import pyautogui
import pygetwindow as gw
import psutil
import time

# --- 1. Open a website ---
print("Opening Python.org in default browser...")
webbrowser.open("https://www.python.org")
time.sleep(3)

# --- 2. Launch an application (Windows example: Notepad) ---
app_name = "notepad.exe"
if not any(p.name().lower() == app_name for p in psutil.process_iter()):
    print(f"Launching {app_name}...")
    os.startfile(app_name)
else:
    print(f"{app_name} is already running.")

time.sleep(2)

# --- 3. Bring app to front ---
try:
    win = gw.getWindowsWithTitle("Untitled - Notepad")[0]
    win.activate()
    print("Notepad is now active.")
except IndexError:
    print("Could not find Notepad window.")

# --- 4. Type something ---
pyautogui.typewrite("Hello F.R.E.D. OS automation test!", interval=0.05)

print("Test complete!")