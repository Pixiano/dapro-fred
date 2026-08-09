# Core/vision/screen_watcher.py
#
# Background screen-content awareness, built after a specific correction
# to the original idea: "kill the process on hotkey press" has to mean a
# genuine OS process, not a thread.
#
# llama.cpp's create_chat_completion() is a single blocking C call with
# no cooperative cancellation point — this codebase already hit the
# failure mode of two inference calls racing on one model instance
# (documented in orchestrator/pill_app.py: "Fatal Python error: Aborted"
# from two concurrent llama_decode calls). A background analysis task
# sharing the main process's model would either have to be strictly
# serialized with real conversation turns (defeating the point — it
# would block your actual request) or risk that exact crash. A separate
# OS process sidesteps both: it can be terminate()'d instantly and
# safely from the main process, because it's a different memory space
# and a different CUDA context entirely. Nothing it does can touch the
# real conversation model.
#
# This module is the CHILD process's entire world. It is launched via
# multiprocessing.Process(target=run) by vision/watcher_manager.py (the
# main-process side) and knows nothing about the hotkey, the pill UI, or
# the orchestrator — it only screenshots, describes, and writes a result
# for someone else to read later.

import base64
import io
import json
import time

from config.settings import (
    LLM_STATUS_PATH,
    SCREEN_WATCHER_INTERVAL_SECONDS,
)

_DESCRIBE_PROMPT = (
    "Describe what's on this screen. Name the application and the "
    "general activity — e.g. 'VS Code, editing a Python file' or "
    "'Chrome, watching a YouTube video'. If there's an error message, "
    "code, a specific number, or any other text that looks like what "
    "the user is actually looking at, quote it exactly rather than "
    "summarizing it — that's usually the point of asking. Don't "
    "speculate about anything not clearly visible."
)


def _main_process_has_a_model_loaded() -> bool:
    """
    Cross-process VRAM coordination — see settings.py's SCREEN WATCHER
    section for the real numbers this is protecting against (the main
    LLM's hour-long idle-unload means it's very often still resident
    during this watcher's much shorter idle window).

    Fails safe: any problem reading the status file is treated as
    "assume something is loaded, skip this cycle" — a missed screenshot
    costs nothing, a VRAM collision on this machine has crashed it
    before.
    """
    try:
        if not LLM_STATUS_PATH.exists():
            return False
        data = json.loads(LLM_STATUS_PATH.read_text(encoding="utf-8"))
        return bool(data.get("loaded"))
    except (OSError, json.JSONDecodeError):
        return True


def _capture_screenshot_data_uri() -> str:
    """
    Screenshot straight to an in-memory PNG, base64-encoded — the image
    bytes never touch disk at any point. This is the one part of the
    original proposal (proposal 2's screenshot/analyze idea) where the
    privacy stakes are highest: a screen can show email, chat, banking,
    anything. Only the resulting short TEXT description is ever
    persisted (see screen_context.py) — the image itself lives only in
    this process's memory for the few seconds it takes to describe it.
    """
    from mss import mss
    from PIL import Image

    with mss() as sct:
        # Primary monitor only — sct.monitors[0] is "all monitors
        # combined", which is unnecessary detail for a one-sentence
        # description and a larger image to encode/describe for no
        # benefit.
        raw = sct.grab(sct.monitors[1])

    img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
    # Downscaled — a vision model describing "which app, what activity"
    # doesn't need full resolution, and a smaller image means less to
    # encode and a faster describe call.
    img.thumbnail((1024, 1024))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _run_one_cycle(llm) -> bool:
    """One screenshot + describe + write. Returns True if it ran."""
    if _main_process_has_a_model_loaded():
        return False

    image_uri = _capture_screenshot_data_uri()
    # 200 (describe_image's default) is enough for a one-line summary
    # but not a quoted error/traceback, which the prompt above now asks
    # for verbatim — bumped so a real quote doesn't get cut mid-message.
    description = llm.describe_image(image_uri, _DESCRIBE_PROMPT, max_tokens=500)

    from vision import screen_context
    screen_context.write(description)
    return True


def run_once():
    """
    One-shot variant of run() for on-demand capture (see
    watcher_manager.capture_now(), called by whats_on_screen() when its
    cache is stale) — loads the model, does exactly one cycle, exits.
    No sleep loop, and the model isn't kept resident afterward.

    Reuses _run_one_cycle's own _main_process_has_a_model_loaded()
    check, so an on-demand capture is exactly as fail-safe against a
    VRAM collision as the idle-triggered loop is — it silently skips
    (writes nothing) rather than racing the main process's model.
    """
    from llm.llm_client import LLMClient

    llm = LLMClient(report_status=False)
    try:
        _run_one_cycle(llm)
    except Exception as e:
        print(f"[screen_watcher] on-demand capture failed: {e}")


def run():
    """
    The child process's entry point — blocks forever (until the parent
    terminate()s this process) in a screenshot/describe/sleep loop.

    Deliberately loads the Vision model ONCE and keeps it resident for
    the process's lifetime rather than reloading every cycle — the
    watcher is expected to run for several minutes at a stretch between
    hotkey presses, and repeated ~4s model loads every
    SCREEN_WATCHER_INTERVAL_SECONDS would be pure waste. When the whole
    process is killed, that residency ends with it — there is nothing
    to explicitly unload.
    """
    # Imported here, not at module level: this is the CHILD process's
    # own separate import of llm_client (and therefore its own separate
    # CUDA context), and it should only happen once this process has
    # actually started, not when the parent process merely imports this
    # module to hand it to multiprocessing.Process.
    from llm.llm_client import LLMClient

    llm = LLMClient(report_status=False)

    while True:
        try:
            _run_one_cycle(llm)
        except Exception as e:
            print(f"[screen_watcher] cycle failed: {e}")
        time.sleep(SCREEN_WATCHER_INTERVAL_SECONDS)


if __name__ == "__main__":
    run()
