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
    args = parser.parse_args()

    crash_log = _enable_crash_dump()
    print(f"[fred_popup] crash traces -> {crash_log}")

    if args.mock:
        run_mock(args.indicator)
        return

    from utils import event_log
    session_log = event_log.start_session()
    print(f"[fred_popup] session log -> {session_log}")
    event_log.log("system", note="crash log path", path=str(crash_log))

    from ui.pill_app import main as app_main
    app_main()


if __name__ == "__main__":
    main()
