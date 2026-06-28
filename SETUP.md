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

### 2. Launch FRED

Two options on your **Desktop**:

#### GUI Version (Graphical Interface)
- **Double-click**: `FRED (GUI).bat`
- Dark-themed window with conversation display
- Easy to use, no terminal needed
- Shows state indicator (IDLE/LISTENING/THINKING/SPEAKING)

#### CLI Version (Command Line)
- **Double-click**: `FRED CLI.bat`
- Terminal-based interface
- Great for scripting and voice testing

### 3. First Run

When you launch FRED for the first time, it will:
1. Load the LLM models (takes 10-30 seconds)
2. Initialize the memory system
3. Start listening for commands

### 4. Try These Commands

**GUI:**
- Type: "hello fred"
- Type: "what's the weather in Mumbai?"
- Type: "what's 847 * 23?"

**CLI:**
- Type: `hello`
- Type: `voice` to switch to voice mode
- Type: `exit` to quit

## Troubleshooting

### "ModuleNotFoundError" when launching GUI
**Fix:** Make sure all dependencies are installed:
```powershell
pip install -r requirements.txt
```

### GUI window appears but doesn't respond
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
│   ├── ui/gui_app.py    # GUI interface
│   ├── orchestrator/    # Command router
│   ├── llm/            # LLM client (local models)
│   ├── memory/         # Memory system
│   ├── tools/          # FRED's abilities
│   ├── audio/          # STT/TTS
│   ├── config/         # Settings
│   └── ...
├── fred_gui.py         # GUI launcher (Python)
├── FRED_GUI.bat        # GUI launcher (Batch)
├── FRED CLI.bat        # CLI launcher
├── requirements.txt    # Dependencies
└── README.md
```

## Development

### Run GUI directly (for debugging):
```powershell
cd Core
python ui/gui_app.py
```

### Run CLI directly:
```powershell
cd Core
python main.py
```

### View HUD state changes:
Launch either version and watch the state indicator in the top-right corner cycle through IDLE → LISTENING → THINKING → SPEAKING.

## Notes

- All models run locally (offline after first download)
- No data sent to cloud except deliberate web search/weather lookups
- Models stored in: `Core/config/settings.py` (MODELS_DIR)
- Conversation history saved locally
- Memory persists across sessions
