# Core/tools/smart_search.py
#
# Bug #4's real fix. search_files (machine_tools.py) does one
# deterministic substring pass over every file under a directory —
# fine when the filename itself is known, useless for "find my health
# logs" where the actual filename has to be inferred from folder
# structure and naming, not string-matched. This walks the tree the
# way a person would: look at what's here, decide which subfolder is
# worth opening next, repeat.
#
# Deliberately its own small reasoning loop rather than a trip through
# the orchestrator's normal one-shot tool-calling round: this needs
# the model to reason across several sequential steps BEFORE the
# outer tool loop would ever see a final answer, so the stepping has
# to happen inside this function, not at the orchestrator level.
#
# Runs on the RESIDENT tier (whatever is already loaded — normally
# Standard), NOT Deep. Originally written for Deep on the assumption
# that "which folder should I open" wanted the stronger model. Two
# facts killed that, both established 2026-08-02:
#
#   1. This llama-cpp-python build is CPU-only
#      (llama_supports_gpu_offload() is False), so a 14B model runs on
#      the CPU. Observed live: one find_file_smart call pinned the
#      machine at 99% CPU long enough for Vatsal to notice and ask.
#   2. llm_client._get_model now keeps only ONE tier resident, so
#      asking for Deep evicts Standard, runs, and forces Standard to
#      reload for the very next turn — a reload either side of every
#      smart search.
#
# The per-step judgment is a short multiple-choice pick over a listed
# set of names, not open-ended reasoning, which is well within the
# resident model. Steps cut 6 -> 4 for the same reason: each step is a
# full round trip on CPU, and four levels reaches essentially anything
# in a normal user tree.

import os
import re
from pathlib import Path

from tools import found_cache

MAX_STEPS = 4

# One line ("ENTER: Documents") is all that's wanted. Uncapped, a
# thinking-on model can spend hundreds of tokens reasoning per step,
# multiplied by MAX_STEPS, on CPU.
_STEP_MAX_TOKENS = 24

_SYSTEM = (
    "You are navigating a folder tree to find a file matching a "
    "description. You will be shown the current folder's subfolders "
    "and files, and the thing being searched for. Reply with EXACTLY "
    "ONE line, one of:\n"
    "ENTER: <subfolder name>   - look inside that subfolder\n"
    "FOUND: <file name>        - a listed file clearly matches\n"
    "NONE                      - nothing here looks promising\n"
    "Only name a subfolder or file that is actually listed below. "
    "Never invent a name that isn't shown."
)


def _list_entries(path: Path):
    try:
        entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except OSError:
        return [], []
    return (
        [e.name for e in entries if e.is_dir()],
        [e.name for e in entries if e.is_file()],
    )


def _parse_step(text: str):
    first_line = (text or "").strip().splitlines()[0].strip() if (text or "").strip() else ""

    m = re.match(r"ENTER:\s*(.+)", first_line, re.IGNORECASE)
    if m:
        return "enter", m.group(1).strip()

    m = re.match(r"FOUND:\s*(.+)", first_line, re.IGNORECASE)
    if m:
        return "found", m.group(1).strip()

    return "none", None


def find_file_smart(description: str, directory: str = "", llm=None) -> str:
    """
    Agentic search: `description` is natural language ("my health
    logs"), not a filename substring. Caches its final answer through
    found_cache under the same shape search_files uses, keyed with a
    "smart:" prefix so the two search modes never collide on the same
    key for a differently-scoped result.
    """

    # Small models invent plausible-looking paths for this argument
    # rather than leaving it blank — confirmed: "find spotify.exe"
    # produced directory="C:\Users\%username%\Desktop", a literal
    # unexpanded env var that exists nowhere, and the turn died on
    # "Directory not found" instead of just searching. Expand env vars
    # (so %USERPROFILE%-style guesses resolve), then fall back to home
    # rather than failing the turn on a guessed path.
    base = Path(os.path.expandvars(directory)).expanduser() if directory else Path.home()
    if not base.exists():
        print(f"[smart_search] {base} doesn't exist — falling back to {Path.home()}")
        base = Path.home()

    cache_key = f"smart:{description.strip().lower()}"
    cached = found_cache.get(cache_key, str(base))
    if cached is not None:
        names = ", ".join(f"{Path(p).name} (in {Path(p).parent.name})" for p in cached)
        return f"Found: {names}"

    if llm is None:
        return "Smart search isn't available right now (no model handle)."

    current = base
    steps_taken = 0

    for _ in range(MAX_STEPS):
        steps_taken += 1
        dirs, files = _list_entries(current)

        listing = (
            f"Subfolders: {', '.join(dirs) if dirs else '(none)'}\n"
            f"Files: {', '.join(files) if files else '(none)'}"
        )
        prompt = [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": f"Looking for: {description}\n\n{listing}"},
        ]

        try:
            # No tier= — use whatever is already resident (see the
            # module header on why this must not ask for Deep).
            reply = llm.generate(prompt, max_tokens=_STEP_MAX_TOKENS)
        except Exception as e:
            return f"Search failed: {e}"

        action, target = _parse_step(reply)

        if action == "found" and target in files:
            result_path = current / target
            found_cache.put(cache_key, str(base), [str(result_path)])
            return f"Found: {target} (in {current.name})"

        if action == "enter" and target in dirs:
            current = current / target
            continue

        break

    return (
        f"Couldn't find a file matching '{description}' after checking "
        f"{steps_taken} folder(s)."
    )
