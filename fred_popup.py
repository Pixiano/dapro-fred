# fred_popup.py
#
# GUI-mode entry point: the hold-to-talk popup.
#
# Hold LEFT Ctrl+Alt, speak, release. Quit from the tray icon.
# CLI mode is still `python Core/main.py` and is unaffected by this.
#
#   python fred_popup.py            full app
#   python fred_popup.py --mock     window only: cycles every state with
#                                   synthetic audio levels, no LLM, no
#                                   mic, no model loading. Use this for
#                                   any visual work on the pill — booting
#                                   the whole stack to look at an
#                                   animation is a waste of a minute.

import argparse
import atexit
import faulthandler
import math
import os
import sys
import time
from pathlib import Path

# Core/ is the import root (config.settings, audio.*, ui.*), matching how
# Core/main.py is run.
CORE_DIR = Path(__file__).resolve().parent / "Core"
sys.path.insert(0, str(CORE_DIR))


def _enable_crash_dump():
    """
    Dump every thread's Python stack on a fatal fault.

    This process links three native runtimes that can each die below the
    interpreter — llama.cpp/CUDA, CTranslate2/CUDA, and PortAudio — and a
    crash in any of them exits with a bare Windows access violation
    (0xc0000005) and no Python traceback at all. faulthandler is the only
    thing that says *which* thread and which call was responsible.
    """
    log_dir = CORE_DIR / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "crash.log"
    # Kept open for the process lifetime on purpose: faulthandler writes
    # to the raw fd from a signal context, so it must not be a file that
    # Python might have closed or buffered by then.
    handle = open(path, "a", buffering=1, encoding="utf-8")
    handle.write(f"\n===== session start pid={os.getpid()} =====\n")
    faulthandler.enable(file=handle, all_threads=True)
    atexit.register(handle.close)
    return path


def run_mock(indicator_name=None, dwell=4.0):
    import threading

    from ui.pill.indicators import ALL_INDICATORS, random_indicator
    from ui.pill.window import PillWindow

    if indicator_name:
        match = [c for c in ALL_INDICATORS if c.name == indicator_name]
        if not match:
            names = ", ".join(c.name for c in ALL_INDICATORS)
            raise SystemExit(f"unknown indicator {indicator_name!r} — try: {names}")
        indicator = match[0]()
    else:
        indicator = random_indicator()

    window = PillWindow(indicator)
    window.create()

    states = ["idle", "listening", "thinking", "speaking", "working"]

    def driver():
        window.show()
        t0 = time.time()
        while True:
            elapsed = time.time() - t0
            state = states[int(elapsed / dwell) % len(states)]
            window.set_state(state)
            # Synthetic amplitude for the audio-reactive states, shaped
            # to look like speech rather than a sine — bursts and gaps.
            if state in ("listening", "speaking"):
                t = elapsed * 6.0
                env = max(0.0, math.sin(t * 0.7)) ** 2
                window.set_level(env * (0.55 + 0.45 * abs(math.sin(t * 3.1))))
            else:
                window.set_level(0.0)
            if state == "thinking":
                window.set_transcript("mock: what is the weather tomorrow", ttl=dwell)
            time.sleep(0.03)

    threading.Thread(target=driver, daemon=True).start()
    print(f"[mock] indicator={indicator.name} — cycling {states}, Ctrl+C to quit")
    window.run()


def main():
    parser = argparse.ArgumentParser(description="FRED hold-to-talk popup")
    parser.add_argument(
        "--mock", action="store_true",
        help="window only: cycle states with synthetic audio, no LLM/mic",
    )
    parser.add_argument(
        "--indicator", default=None,
        help="force an indicator style in mock mode (bars | ribbon)",
    )
    parser.add_argument(
        "--greet-now", action="store_true",
        help="greet within seconds instead of the log-on delay; set by the "
             "desktop launcher, where the greeting confirms FRED started",
    )
    args = parser.parse_args()

    crash_log = _enable_crash_dump()
    print(f"[fred_popup] crash traces -> {crash_log}")

    # Before anything spawns a child (screen_watcher's multiprocessing
    # workers, hud/server.py, phone_api.py): a hard kill of THIS process
    # (Stop-Process -Force, a crash, Task Manager) otherwise orphans them
    # — confirmed live, two such orphans found still running on stale
    # settings, one holding 15GB VRAM with nothing left alive to stop it.
    # See utils/process_group.py for the mechanism.
    from utils.process_group import contain_children
    contain_children()

    if args.mock:
        run_mock(args.indicator)
        return

    from utils import event_log
    session_log = event_log.start_session()
    print(f"[fred_popup] session log -> {session_log}")
    event_log.log("system", note="crash log path", path=str(crash_log))

    # Vault session block: one per calendar day, auto-created here so
    # logging doesn't wait on an explicit "recap" request. Empty string
    # if today's already exists (a relaunch, not a new day) — nothing
    # new to announce in that case.
    from tools.session_summary import start_daily_session
    session_announce = start_daily_session()

    # Assert the expensive-to-be-wrong-about assumptions before the UI
    # comes up — see utils/health_check.py for the two failures that
    # went unnoticed for weeks because nothing checked them. Runs in
    # milliseconds and never raises; a hard failure is reported and
    # startup continues, because a half-working FRED still beats one
    # that refuses to start.
    from utils import health_check
    results = health_check.run()
    for failure in health_check.failures(results):
        print(f"[fred_popup] HEALTH FAILURE — {failure}")

    # Keep the paired phone's screen from auto-locking mid-task (adb UI
    # automation — set_alarm, the Haismart/HTTP-Shortcuts setup scripts,
    # camera capture — needs the screen on and unlocked to work at all).
    # Reverted the instant FRED actually exits, not left on permanently:
    # "stayon usb" only while FRED is running, normal behavior otherwise.
    # Best-effort — a disconnected phone or missing adb must never block
    # startup. Does NOT cover a hard kill (Stop-Process, crash): the
    # revert below never runs then, same accepted gap as every other
    # "runs on clean exit only" cleanup in this codebase.
    def _set_phone_stayon(value: str):
        try:
            from tools.phone_tools import _adb, _device_ready
            if _device_ready():
                _adb("shell", "svc", "power", "stayon", value, timeout=5)
        except Exception:
            pass

    _set_phone_stayon("usb")
    try:
        from ui.pill_app import main as app_main
        app_main(greet_now=args.greet_now, session_announce=session_announce)
    finally:
        _set_phone_stayon("false")


if __name__ == "__main__":
    main()
