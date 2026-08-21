# Environment and Setup

## Platform

Windows only. No cross-platform abstraction anywhere in the codebase — direct `win32api`/`win32job`/`win32con` calls, `pythonw.exe` launchers, SAPI voices, Windows-specific hotkey hooks. A rebuild on another OS would need to redesign several subsystems (Job Objects for process cleanup, the native Win32 layered window UI, SAPI TTS in CLI mode) from scratch, not just port them.

Confirmed hardware for the reference machine this was built on/for:
- GPU: RTX 5060 Ti, 16310 MiB VRAM. Every VRAM budget number in `Core/config/settings.py` is calibrated against this exact card. This machine has "a documented history of hard access-violation crashes (0xc0000005)" from VRAM exhaustion — this is why so much of the LLM/vision design (single resident tier, cross-process coordination, idle unload) exists.
- Webcam: a desktop with **no built-in webcam**. Three camera indices are visible to OpenCV: index 0 = Canon EOS Webcam Utility (virtual), index 1 = iBall PHOCUS 40A (the only real hardware camera), index 2 = OBS Virtual Camera (virtual). `PRESENCE_CAMERA_INDEX = 1` in settings.py, confirmed live by capturing a frame from every index. If the camera setup changes, re-run that same per-index capture check rather than assuming the index still holds.
- Audio: no fixed device index assumption in code — `audio/device_info.py` enumerates devices at runtime (see `03_voice_pipeline.md`). Bluetooth output has a real, measured ramp-up/ramp-down artifact that TTS explicitly compensates for (`TTS_PREROLL_SEC`/`TTS_POSTROLL_SEC`).

## Launchers (repo root)

| File | What it does |
|---|---|
| `FRED.bat` | The normal way to start FRED. Runs `Core\venv\Scripts\pythonw.exe fred_popup.py --greet-now`. `pythonw.exe`, not `python.exe` — no console window, the tray icon is the UI. The HUD server comes up quietly in the background; the HUD *window* stays closed until the tray icon is clicked. `--greet-now` makes FRED speak a greeting within seconds of starting — used when started by hand. Compare: started at log-on via `install_startup.py` (no arguments), FRED waits **10 minutes** before greeting, specifically so it isn't talking over the rest of Windows starting up. |
| `FRED_POPUP.bat` | Launches the hold-to-talk popup GUI directly, no tray-icon step, no HUD. |
| `fred_cli.bat` | Runs `Core\main.py` — terminal text/voice interface, for scripting and voice testing without the HUD. |
| `install_startup.py` | Registers (or `--remove`, or `--status`) FRED to start automatically at Windows log-on. |
| `python fred_popup.py --mock` | Pill UI only, cycles through every visual state with synthetic audio, **loads no models**. The correct way to do any visual/UI work without paying model-load cost or needing real hardware. |

## The venv

`Core/venv/` — a dedicated virtualenv. This detail matters more than it looks: settings.py documents that this exact machine has **three different Python installs**, each with its own `llama_cpp` build — the venv here (CUDA, correct), a pyenv 3.10.11 install (CPU-only), and system Python 3.11 (CUDA-capable but missing FRED's other dependencies). Benchmarking or running FRED with the wrong interpreter silently degrades to CPU inference (measured: ~92s/turn on the wrong interpreter vs. ~4.3s/turn on the venv). **Always run/benchmark via `Core/venv/Scripts/python.exe` (or `pythonw.exe`).**

Install: `pip install -r requirements.txt` from the repo root. See `requirements.txt` for the full annotated dependency list — every entry has a comment explaining why it's there; notable ones:
- `llama-cpp-python` — local LLM inference (CUDA build required, see gpu_bootstrap note below).
- `vosk` + `pyaudio` + `pyttsx3` — CLI mode's STT/TTS pair.
- `faster-whisper` + `sounddevice` — GUI mode's STT (runs on CTranslate2, does its own CUDA/cuDNN discovery independent of torch — a CPU-only torch install says nothing about whether Whisper reaches the GPU; check the device line it prints at startup).
- `openwakeword` — the trained "Hey FRED" wake-word model runtime (training itself needs a separate, heavier dependency set, not listed in requirements.txt — see `Core/data/wakeword_training/`'s own notes).
- `kokoro-onnx` — GUI mode's TTS. Chosen over SAPI because it returns raw float32 PCM (SAPI/pyttsx3 only exposes word-boundary events), which is what lets the pill's speaking waveform react to real amplitude.
- `pystray` — tray icon; `pywebview`/WebView2 were tried and rejected for the pill UI because neither achieves true per-pixel desktop transparency (only a "frosted-glass" approximation) — hence the native Win32 layered window instead (see `08_ui_and_vision.md`).
- `cryptography` — AES for the vendored Haismart AC-control LAN protocol client. Note in requirements.txt: this was claimed to already be present transitively by the agent that vendored the Haismart code, but was confirmed missing live and added explicitly — a real, hard-won gotcha to remember (don't trust "it's already a transitive dependency" claims; verify by actually importing).
- `pypdf` — vault PDF indexing (two real personal PDFs sat unindexed in the vault before this was added, because the indexer only globbed `*.md`).
- `opencv-python` + `insightface` — presence detection (webcam capture + buffalo_l ArcFace face embeddings). Both install as pure-Python wheels on this machine, no compiler step required, confirmed 2026-08-21.

## Model files — where they live and how they're fetched

**None of the model weights are committed to git** (`.gitignore` excludes them — they are 1.5GB-11GB+ each, far past GitHub's 100MB/file limit). All are referenced by absolute path from `Core/config/settings.py`, which is the single source of truth for every model location. Read it directly rather than trusting any other doc.

| What | Path (from settings.py) | Source |
|---|---|---|
| `MODELS_DIR` | `C:\Users\Dhiraj Vatsal\.lmstudio\models` | LM Studio's own model folder — GGUF files are downloaded/managed through LM Studio, not fetched directly by FRED. |
| Standard/Backup tier | `MODELS_DIR/unsloth/Qwen3.5-4B-GGUF/Qwen3.5-4B-Q6_K.gguf` | LM Studio download (unsloth's GGUF quant). |
| Deep/Extreme tier | temporarily also pointed at the same Qwen3.5-4B file (see `02_llm_and_model_tiers.md` for why) | same |
| Vision tier weights + mmproj | `MODELS_DIR/unsloth/Qwen3.5-4B-GGUF/Qwen3.5-4B-Q6_K.gguf` + `mmproj-BF16.gguf` | same folder |
| Embedding model | `MODELS_DIR/Qwen/Qwen3-Embedding-4B-GGUF/Qwen3-Embedding-4B-Q4_K_M.gguf` | LM Studio download |
| `LLAMACPP_BIN_DIR` | `C:\Users\Dhiraj Vatsal\llama.cpp\bin` | llama.cpp's own **prebuilt release binaries** (`llama-server.exe` + CUDA runtime DLLs), used only for Vision inference — see `02_llm_and_model_tiers.md`. Not pip-installed, not in the repo (670MB). Fetch: `https://github.com/ggml-org/llama.cpp/releases/download/b10509/llama-b10509-bin-win-cuda-13.3-x64.zip` and the matching `cudart-llama-bin-win-cuda-13.3-x64.zip`, extract **both into the same folder** (the cudart zip provides `cudart64_13.dll`/`cublas64_13.dll` etc. that `llama-server.exe` needs at runtime). This exact path is hardcoded in settings.py — a rebuild must either match it or update the constant. |
| Vosk model (CLI STT) | `BASE_DIR/models/vosk-model-en-in-0.5` | Indian-English-tuned Vosk model, ~1.5GB, gitignored, not committed for the same GitHub size-limit reason. |
| Wake-word model | `BASE_DIR/models/wakeword/hey_fred.onnx` | A real trained openWakeWord ONNX model, trained from scratch (retrain via `Core/input/wakeword_train.py`). **Do not swap or redeploy this model without an explicit request** — flagged as a hard rule as of 2026-08-20. |
| Kokoro TTS | `BASE_DIR/models/kokoro/kokoro-v1.0.onnx` + `voices-v1.0.bin` | Not pip data — release downloads from `https://github.com/thewh1teagle/kokoro-onnx/releases` (model-v1.0), too large for git. |
| Whisper (GUI STT) | model id `"large-v3-turbo"` | Auto-downloaded to the HuggingFace cache on first use — not stored in the repo at all. |

## GPU / CUDA bootstrap gotcha

`Core/utils/gpu_bootstrap.py::ensure_cuda_dlls()` must run **before the first `import llama_cpp`** (it's called at the top of `Core/llm/llm_client.py`, before that import). It works around two real, confirmed Windows-specific problems, not speculative ones:
1. The pip-installed CUDA 12 runtime (`nvidia-cuda-runtime-cu12` etc.) doesn't put its DLL directories on `PATH` by default — fixed via `os.add_dll_directory()`.
2. `llama_cpp`'s own loader loads `llama.dll` with `winmode=ctypes.RTLD_GLOBAL`, under which the ggml backend DLLs (`ggml.dll`, `ggml-cuda.dll`, etc.) fail to resolve their sibling dependencies even though they're in the same folder — a real ctypes/Windows quirk, reproduced and confirmed independent of any `CUDA_PATH` conflict. Fix: preload the same DLLs first with the *default* winmode (in a fixed dependency order: `ggml-base.dll` → `ggml.dll` → `ggml-cpu.dll` → `ggml-cuda.dll` → `mtmd.dll` → `llama.dll`), so llama_cpp's later RTLD_GLOBAL load just reuses the already-loaded handles instead of repeating the load steps that fail.

If a rebuild sees `llama_cpp` import errors or CUDA-not-found on Windows despite a correct CUDA install, re-derive this exact fix — it's not obvious from any upstream documentation.

## Process cleanup gotcha (Windows Job Objects)

`Core/utils/process_group.py::contain_children()` is called once, as early as possible in FRED's startup, before anything spawns a child process. It creates a Windows Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` and assigns the current process to it. **Why this exists**: confirmed live 2026-08-19 — force-killing a stuck/duplicate FRED process (`Stop-Process -Force`) does **not** take its children with it on Windows. The screen watcher's `multiprocessing.Process` workers (`daemon=True`) only get cleaned up on a *clean* interpreter exit; daemon status means nothing to an external hard kill. Two orphaned watcher processes were found still running, one on stale settings, holding 15GB VRAM and 86% GPU utilization, with nothing left alive able to stop them.

The Job Object fixes this at the OS level: when the main process dies by *any* means (including a hard external kill with zero chance to run cleanup code), Windows kills every process still in the job — the main process and every descendant it ever spawned (`subprocess.Popen`, `multiprocessing.Process`, both ultimately `CreateProcess`), covering the screen watcher's workers, `hud/server.py`, `phone_api.py`, and `llama-server.exe` (vision) all in one place instead of three-plus separate per-subsystem fixes. A rebuild that skips this will reproduce the orphan-VRAM bug the first time a duplicate/stuck process gets force-killed.

## Vault (external, not in this repo)

FRED's personal-memory vault lives **outside** `Project_FRED` entirely, at a path hardcoded in `settings.py` as `VAULT_DIR` (`...\Projects\1_FRED_Memory\FRED`). It is deliberately not under this repo/git — "a memory vault outliving any one project is the point." A rebuild needs a vault directory at some path with at minimum `persona.md`, `profile.md`, `rules.md`, `active-priorities.md` (these four are read directly, not through retrieval — see `04a_orchestrator_core.md`) plus arbitrary other `.md`/`.pdf` files that get indexed for semantic retrieval. **Never quote real vault content anywhere** (commits, docs, tests) — this is a hard project rule, not a suggestion.

## First run / smoke test

Per `SETUP.md`: first launch loads the LLM (10-30s), initializes memory, and starts listening. Try "hello fred" (voice/HUD) or type `hello` (CLI). "ModuleNotFoundError" → re-run `pip install -r requirements.txt`. "Failed to initialize CUDA" → FRED falls back to CPU mode automatically (works, just slower — verify against the actual fallback behavior in `llm_client.py`, which does not have an explicit CPU-fallback branch beyond whatever `llama-cpp-python`/CUDA itself does when GPU init fails).

## Known drift between docs

`README.md` and `SETUP.md` are **not perfectly in sync with the code** — e.g. README states `THINKING_LENGTH_THRESHOLD` is 75 chars, but `Core/config/settings.py` currently has it at 175 (raised same-day, per that constant's own comment). This doc set treats `Core/config/settings.py` as ground truth for every numeric constant; always re-verify against it rather than trusting README/SETUP for exact values.
