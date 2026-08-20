# Core/llm/vision_server.py
#
# Local Vision inference via llama.cpp's OWN llama-server.exe binary,
# not llama-cpp-python. Confirmed 2026-08-20: llama-cpp-python 0.3.31's
# in-process multimodal handlers (Gemma4ChatHandler/MTMDChatHandler/
# Qwen25VLChatHandler) give wrong output on Qwen3.5-architecture vision
# models — Bonsai-27B and Qwen3.5-4B both tested, both wrong, both print
# the same "find_slot: non-consecutive token position" warning. Raw
# llama.cpp (llama-mtmd-cli.exe, llama-server.exe, release b10509) gets
# the identical model+mmproj exactly right — same warning, correct
# output — confirming the bug lives in llama-cpp-python's binding layer,
# not llama.cpp itself. This module runs llama-server.exe as a
# subprocess and talks to its OpenAI-compatible HTTP API instead.
#
# Lifecycle: lazy-started on first describe_image() call, left resident
# after that (matches llm_client._get_model()'s own "load once, keep
# warm" pattern). Whichever process calls ensure_running() first spawns
# it — safe from either FRED's main process or screen_watcher.py's
# separate child process, since readiness is checked over HTTP, not
# local state. The spawning process's Windows Job Object membership
# (utils/process_group.py, assigned once at FRED startup and inherited
# transitively by every process FRED creates, including grandchildren)
# means llama-server.exe dies when FRED's main process exits or is hard-
# killed, regardless of which process actually spawned it — a
# screen_watcher child being terminate()'d by watcher_manager.touch()
# does NOT take the server down, since that child never held the Job
# handle FRED's main process opened.
#
# Deliberately NOT atexit-registered: whichever process happens to spawn
# it first (e.g. a one-off screen_watcher capture) would then kill it
# the moment THAT process exits, even though the server should outlive
# it — confirmed live 2026-08-20, a short-lived test script's own exit
# was silently killing the server between calls. The Job Object above is
# the real cleanup path, correctly scoped to FRED's main process
# regardless of who spawned this. shutdown() stays available for an
# explicit, intentional stop (e.g. from FRED's own real shutdown
# sequence), just never auto-registered here.

import json
import subprocess
import time
import urllib.error
import urllib.request

from config.settings import (
    CONTEXT_WINDOW,
    CONTEXT_WINDOW_BY_TIER,
    LLAMACPP_BIN_DIR,
    MMPROJ_PATH_BY_TIER,
    MODEL_TIERS,
    VISION_SERVER_PORT,
)

_BASE_URL = f"http://127.0.0.1:{VISION_SERVER_PORT}"
_LOG_PATH = LLAMACPP_BIN_DIR.parent / "vision_server.log"

_process = None


def _is_healthy(timeout: float = 1.0) -> bool:
    try:
        urllib.request.urlopen(f"{_BASE_URL}/health", timeout=timeout)
        return True
    except Exception:
        return False


def ensure_running(startup_timeout: float = 30.0) -> bool:
    """
    Idempotent: no-ops if llama-server.exe is already answering (checked
    over HTTP, so this is safe to call from any process). Otherwise
    spawns it and blocks until it reports healthy or startup_timeout
    elapses. Returns whether it's usable right now.
    """
    global _process

    if _is_healthy():
        return True

    if _process is None or _process.poll() is not None:
        exe = LLAMACPP_BIN_DIR / "llama-server.exe"
        model_path = MODEL_TIERS["Vision"]
        mmproj_path = MMPROJ_PATH_BY_TIER["Vision"]
        n_ctx = CONTEXT_WINDOW_BY_TIER.get("Vision", CONTEXT_WINDOW)

        log_file = open(_LOG_PATH, "a", encoding="utf-8")
        _process = subprocess.Popen(
            [
                str(exe),
                "-m", str(model_path),
                "--mmproj", str(mmproj_path),
                "-ngl", "99",
                "-c", str(n_ctx),
                "--port", str(VISION_SERVER_PORT),
            ],
            cwd=str(LLAMACPP_BIN_DIR),
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )

    deadline = time.monotonic() + startup_timeout
    while time.monotonic() < deadline:
        if _is_healthy():
            return True
        time.sleep(0.5)
    return False


def describe_image(image_data_uri: str, prompt: str, max_tokens: int = 200,
                    timeout: float = 60.0) -> str:
    """
    Same call shape as the old in-process describe_image() local
    fallback: one-shot image + prompt, plain string back.
    chat_template_kwargs.enable_thinking=False is llama-server's own
    equivalent of TIER_TEMPLATE_KWARGS — confirmed live 2026-08-20,
    without it the model's <think> block eats the whole max_tokens
    budget before ever answering.
    """
    if not ensure_running():
        raise RuntimeError("vision server failed to start")

    payload = {
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": image_data_uri}},
                {"type": "text", "text": prompt},
            ],
        }],
        "max_tokens": max_tokens,
        "temperature": 0.2,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    req = urllib.request.Request(
        f"{_BASE_URL}/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"] or ""


def shutdown():
    """App exit — graceful stop. The Job Object (process_group.py) is
    the backstop for a hard kill; this is the clean-exit path."""
    global _process
    if _process is not None and _process.poll() is None:
        _process.terminate()
    _process = None
