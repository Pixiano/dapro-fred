# Core/audio/mute_state.py
#
# FRED's own "mute" flag — silences only the TTS output (tts_kokoro.py
# checks this), not the system volume. Confirmed 2026-08-04: the mute
# button previously called machine_tools.mute(), which flipped the
# Windows default-speaker endpoint via pycaw — that silenced everything
# on the PC, not just FRED, which is not what a mute button on FRED's
# own HUD should do.
#
# A bare module-level bool, not a class: there is exactly one FRED
# process and exactly one mute state: no per-instance reason to wrap it.

_muted = False


def set_muted(value: bool) -> None:
    global _muted
    _muted = bool(value)


def is_muted() -> bool:
    return _muted
