# Tests

Fast, offline regression tests. No model is loaded, nothing touches the
network, and the whole suite runs in well under a second — so there is
no reason not to run it before a commit.

```bash
cd Core
python -m pytest tests/ -q
```

## What's here

Every test in this folder pins a bug that **actually happened** and was
reproduced from a session log first. None of them are hypothetical, and
each one names its source log in a comment. That's the bar for adding
another: if you can't point at the failure, it doesn't belong here.

| File | Pins |
|---|---|
| `test_dispatcher_web_search.py` | Web searches that were really local file searches, and pronoun-led queries that lost their topic |
| `test_intent_cues.py` | Cue-word collisions: "project **copy**" reading the clipboard, "on **track**" changing the volume, "**find** spotify.exe" never offering file tools |
| `test_open_routing.py` | "Open dossier.pdf" opening a browser at `https://dossier.pdf` |
| `test_open_last_found.py` | "Open it" after a search having no referent |
| `test_relative_controls.py` | "Turn it up a bit" doing nothing |
| `test_speech_safety.py` | URLs and unbounded clipboard contents being read aloud |
| `test_tool_robustness.py` | STT punctuation (`"Spotify."`) and invented path arguments killing turns |
| `test_vault_tables.py` | A table's Target column being reported as the Current value |

## Which Python

**Use `Core/venv/Scripts/python.exe` for anything that loads a model.**

There are three Python installs on the development machine with three
different `llama-cpp-python` builds, and picking the wrong one produces
confidently wrong measurements:

| Interpreter | `llama_cpp` | FRED's other deps |
|---|---|---|
| `Core/venv` | CUDA, working | yes — **this is the one FRED runs** |
| pyenv 3.10.11 | CPU-only | yes |
| system Python 3.11 | CUDA, working | no |

This is not hypothetical: a benchmark accidentally run under pyenv
reported ~92s for a turn that the venv completes in ~4.3s, and the
resulting "the build is CPU-only" conclusion was written into several
source comments before it was caught.

The tests in this folder don't load a model, so they run fine under any
of them — `conftest.py` only puts `Core/` on `sys.path`. The warning
matters for benchmarks and for anything importing `llama_cpp`.
