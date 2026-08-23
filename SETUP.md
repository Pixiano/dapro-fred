# F.R.E.D. Setup Guide

## Quick Start (Windows)

### 1. Install Dependencies (One-time setup)

Open PowerShell or Command Prompt in the Project_FRED directory and run:

```powershell
pip install -r requirements.txt
```

Or install manually:
```powershell
pip install llama-cpp-python faiss-cpu vosk pyaudio pyttsx3 sounddevice pycaw pyperclip pygetwindow pyautogui screen-brightness-control mss pillow winotify requests duckduckgo-search apscheduler sqlalchemy psutil comtypes pywin32
```

### 2. Get llama.cpp's binaries (needed for Vision)

llama-cpp-python's in-process multimodal handlers give wrong output on
FRED's current Vision model family (confirmed real binding bug, not a
config issue — see `Core/llm/vision_server.py`), so Vision runs through
llama.cpp's own `llama-server.exe` as a subprocess instead. These
binaries aren't in the repo (670MB, same reasoning as the model weights
below) — download them once:

1. Grab both zips from the llama.cpp release matching your CUDA version
   (this project used `b10509`, `win-cuda-13.3-x64` — pick the closest
   `win-cuda-13.x` asset if versions have moved on):
   - `https://github.com/ggml-org/llama.cpp/releases/download/b10509/llama-b10509-bin-win-cuda-13.3-x64.zip`
   - `https://github.com/ggml-org/llama.cpp/releases/download/b10509/cudart-llama-bin-win-cuda-13.3-x64.zip`
2. Extract both into the SAME folder: `C:\Users\Dhiraj Vatsal\llama.cpp\bin\`
   (the cudart zip provides the `cudart64_13.dll`/`cublas64_13.dll` etc.
   `llama-server.exe` needs at runtime). This exact path is hardcoded as
   `LLAMACPP_BIN_DIR` in `Core/config/settings.py` — must match.

### 3. Launch FRED

Three launchers at the repo root:

#### `FRED.bat` — the normal way to start FRED
- Starts the voice assistant; the HUD server comes up quietly in the
  background and stays closed until you click the tray icon
- Greets you immediately when started by hand (`--greet-now`); waits
  2 minutes when started at log-on via `install_startup.py`, so it
  isn't talking over the rest of Windows starting up

#### `FRED_POPUP.bat` — hold-to-talk popup
- Launches the hold-to-talk popup GUI directly (no tray-icon step)

#### `fred_cli.bat` — command line
- Terminal-based interface, runs `Core\main.py`
- Useful for scripting and voice testing without the HUD

### 4. First Run

When you launch FRED for the first time, it will:
1. Load the LLM models (takes 10-30 seconds)
2. Initialize the memory system
3. Start listening for commands

### 5. Try These Commands

**Voice/HUD (`FRED.bat`, `FRED_POPUP.bat`):**
- Say or type: "hello fred"
- "what's the weather in Mumbai?"
- "what's 847 * 23?"

**CLI (`fred_cli.bat`):**
- Type: `hello`
- Type: `voice` to switch to voice mode
- Type: `exit` to quit

## Troubleshooting

### "ModuleNotFoundError" when launching
**Fix:** Make sure all dependencies are installed:
```powershell
pip install -r requirements.txt
```

### HUD/popup window appears but doesn't respond
**Fix:** Close it and try again. First launch loads models which takes time.

### "Failed to initialize CUDA" or GPU errors
**Fix:** FRED will fall back to CPU mode. This is normal and works fine.

### Models take forever to load
**Fix:** First launch caches models. Subsequent launches are faster.

## File Structure

```
Project_FRED/
├── Core/                 # Main FRED codebase
│   ├── main.py          # CLI entry point
│   ├── ui/pill_app.py   # Hold-to-talk popup UI
│   ├── orchestrator/    # Command router / tool registry
│   ├── llm/             # LLM client (cloud-first, local fallback)
│   ├── memory/          # Memory system
│   ├── tools/           # FRED's abilities
│   ├── audio/           # STT/TTS
│   ├── config/          # Settings
│   └── ...
├── hud/                 # HUD server + tray UI
├── fred_popup.py         # Popup entry point (used by FRED.bat / FRED_POPUP.bat)
├── FRED.bat              # Normal launcher (voice + HUD tray)
├── FRED_POPUP.bat         # Hold-to-talk popup launcher
├── fred_cli.bat           # CLI launcher
├── requirements.txt       # Dependencies
└── README.md
```

## Development

### Run the popup UI directly (for debugging):
```powershell
python fred_popup.py
```

### Run CLI directly:
```powershell
cd Core
python main.py
```

### View HUD state changes:
Launch either version and watch the state indicator in the top-right corner cycle through IDLE → LISTENING → THINKING → SPEAKING.

## Notes

- Conversation and vision try a cloud API (Cerebras) first, falling
  back to local models (Qwen3-8B, etc.) if that fails — see the
  Privacy section in README.md for the current cascade and the
  sensitive-content local-only pin
- Models stored in: `Core/config/settings.py` (MODELS_DIR)
- Conversation history saved locally
- Memory persists across sessions
