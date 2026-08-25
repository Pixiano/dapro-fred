# Core/audio/media_state.py
#
# Is anything actually playing audio right now, other than FRED itself?
# orchestrator/headphone_watch.py's own priority signal, added
# 2026-08-25 — Vatsal's own reasoning: "who uses speakers in family",
# so if media's playing at all, headphones is the right output without
# needing the camera to confirm anyone's wearing them.
#
# pycaw session peak-metering (confirmed live 2026-08-25): each
# process with an open audio stream gets its own AudioUtilities
# session, and IAudioMeterInformation.GetPeakValue() on that session's
# _ctl gives its current output level, 0.0 when silent. Playing a tone
# from this very process and enumerating sessions confirmed pycaw
# attributes it to THIS process's own pid — so excluding os.getpid()
# reliably excludes FRED's own TTS output (including the "Switched to
# headphones, sir" announcement itself) and nothing else needed for
# that, no separate flag to thread through the TTS call.

import os

from pycaw.pycaw import AudioUtilities, IAudioMeterInformation

# Real background noise floor measured at 0.0 on an idle session; a
# real playing session measured 0.3-0.9 in the same live check. Well
# below the lowest real reading, well above true silence.
_PEAK_THRESHOLD = 0.02


def is_media_playing() -> bool:
    my_pid = os.getpid()
    for session in AudioUtilities.GetAllSessions():
        proc = session.Process
        if proc is None or proc.pid == my_pid:
            continue
        try:
            peak = session._ctl.QueryInterface(IAudioMeterInformation).GetPeakValue()
        except Exception:
            continue  # a session can vanish between enumeration and query — skip, not fatal
        if peak > _PEAK_THRESHOLD:
            return True
    return False


if __name__ == "__main__":
    # Live self-check, not Core/tests/ — needs a real audio session to
    # mean anything (see headphone_watch.py's own __main__-less
    # convention for hardware-dependent modules; this one at least has
    # one to run by hand). Play something, then run this file.
    print(f"is_media_playing(): {is_media_playing()}")
