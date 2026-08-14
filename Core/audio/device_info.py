# Core/audio/device_info.py

import json
from pathlib import Path

import sounddevice as sd

from config.settings import DATA_DIR

# The HUD server runs as its own OS process (see hud/server.py), so its
# own sd.default.device is independent of FRED's — it can't just query
# sd.default.device to know what FRED currently has selected. This file
# is the same voice-line bus utils/voice_line.py already uses for other
# cross-process state, so the HUD's device dropdown can show FRED's
# actual current selection instead of its own process's OS default.
_BUS_DIR = Path.home() / "voice-line"
_SELECTION_PATH = _BUS_DIR / "audio_devices.json"

# Remembered choice, by NAME not index — PortAudio indices shift between
# runs as devices connect/disconnect (a Bluetooth headset reappearing
# doesn't get the same number twice), so an index saved today is not a
# reliable pointer to the same device tomorrow. Same atomic write-then-
# replace shape as lockdown_state.py's persisted state.
_PREF_PATH = DATA_DIR / "audio_device_prefs.json"


def _save_preference():
    try:
        input_idx, output_idx = sd.default.device
        pref = {
            "input_name": sd.query_devices(input_idx)["name"] if input_idx is not None else None,
            "output_name": sd.query_devices(output_idx)["name"] if output_idx is not None else None,
        }
        _PREF_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _PREF_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(pref), encoding="utf-8")
        tmp.replace(_PREF_PATH)
    except Exception:
        pass


def _publish_selection():
    try:
        _BUS_DIR.mkdir(parents=True, exist_ok=True)
        input_, output = sd.default.device
        _SELECTION_PATH.write_text(
            json.dumps({"input": input_, "output": output}), encoding="utf-8"
        )
    except OSError:
        pass


def _wasapi_index():
    """
    PortAudio lists every physical device once per host API (MME,
    DirectSound, WASAPI, WDM-KS) — confirmed 2026-08-04: on a 2-mic/
    2-speaker machine that's ~35 entries for 4 real devices. WASAPI is
    the one that actually matches what Windows itself calls the
    default device, so filtering to it alone removes the duplicates
    instead of trying to de-dupe by name (name collisions across real
    distinct devices, e.g. two "Headphones ()", make name-matching
    unreliable — see WDM-KS's unnamed Realtek entries above).
    Returns None if WASAPI isn't present (older Windows / no driver),
    in which case callers fall back to the unfiltered list rather than
    showing nothing.
    """
    for i, api in enumerate(sd.query_hostapis()):
        if api["name"] == "Windows WASAPI":
            return i
    return None


def _is_wasapi(device_index) -> bool:
    if device_index is None:
        return False
    try:
        return sd.query_devices(device_index)["hostapi"] == _wasapi_index()
    except Exception:
        return False


def output_extra_settings():
    """
    Pass as OutputStream(..., extra_settings=...). Confirmed 2026-08-04:
    picking a WASAPI device (the mic/speaker dropdown only ever offers
    WASAPI ones — see _wasapi_index) then opening a stream at Kokoro's
    fixed synth rate raised "Invalid sample rate [PaErrorCode -9997]".
    MME/DirectSound silently resample; WASAPI validates the rate
    against the device's own native list unless told to let Windows'
    audio engine convert for it — auto_convert=True is that ask.
    Returns None off WASAPI, where no such setting exists.
    """
    if _is_wasapi(sd.default.device[1]):
        return sd.WasapiSettings(auto_convert=True)
    return None


def input_extra_settings():
    """Same as output_extra_settings, for the microphone side."""
    if _is_wasapi(sd.default.device[0]):
        return sd.WasapiSettings(auto_convert=True)
    return None


def _devices_for(channel_key: str) -> list:
    wasapi = _wasapi_index()
    devices = list(enumerate(sd.query_devices()))
    if wasapi is not None:
        devices = [(i, d) for i, d in devices if d["hostapi"] == wasapi]
    return [{"index": i, "name": d["name"]} for i, d in devices if d[channel_key] > 0]


def list_input_devices() -> list:
    """[{index, name}] for every real microphone, one entry each — for
    the HUD's microphone dropdown."""
    return _devices_for("max_input_channels")


def list_output_devices() -> list:
    """[{index, name}] for every real speaker, one entry each — for the
    HUD's speaker dropdown."""
    return _devices_for("max_output_channels")


def set_input_device(index: int) -> str:
    """
    Switch FRED's microphone. sd.default.device is process-global, so
    this is picked up by the next STTManager.listen_once() call — audio
    streams read the default at creation time, not once at import.
    """
    _, output = sd.default.device
    name = sd.query_devices(int(index))["name"]
    sd.default.device = (int(index), output)
    _publish_selection()
    _save_preference()
    return f"Microphone set to {name}"


def set_output_device(index: int) -> str:
    """Switch FRED's speaker output — see set_input_device."""
    input_, _ = sd.default.device
    name = sd.query_devices(int(index))["name"]
    sd.default.device = (input_, int(index))
    _publish_selection()
    _save_preference()
    return f"Speaker set to {name}"


def apply_saved_devices() -> None:
    """
    Call once at startup, before anything opens an audio stream. Looks
    up last session's remembered mic/speaker by name among devices
    present right now; a device that's gone (unplugged, a Bluetooth
    headset not yet reconnected) is silently skipped rather than
    forced, which leaves sd.default.device on PortAudio's own resolved
    default for that side — exactly "switch to whatever's there."
    """
    try:
        pref = json.loads(_PREF_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return

    by_name = {"input": {}, "output": {}}
    for d in _devices_for("max_input_channels"):
        by_name["input"][d["name"]] = d["index"]
    for d in _devices_for("max_output_channels"):
        by_name["output"][d["name"]] = d["index"]

    input_idx, output_idx = sd.default.device
    input_name = pref.get("input_name")
    output_name = pref.get("output_name")
    if input_name in by_name["input"]:
        input_idx = by_name["input"][input_name]
    if output_name in by_name["output"]:
        output_idx = by_name["output"][output_name]

    sd.default.device = (input_idx, output_idx)
    _publish_selection()


if __name__ == "__main__":
    # Self-check, not Core/tests/ (regression-only, see its README).
    # Mocks sounddevice entirely — never touches real hardware.
    import tempfile
    from pathlib import Path
    from unittest.mock import patch

    _FAKE_DEVICES = [
        {"name": "USB Mic", "hostapi": 0, "max_input_channels": 1, "max_output_channels": 0},
        {"name": "Built-in Speakers", "hostapi": 0, "max_input_channels": 0, "max_output_channels": 2},
    ]
    _FAKE_HOSTAPIS = [{"name": "Windows WASAPI"}]

    with tempfile.TemporaryDirectory() as tmp, \
         patch.object(sd, "query_devices", lambda i=None: _FAKE_DEVICES[i] if i is not None else _FAKE_DEVICES), \
         patch.object(sd, "query_hostapis", lambda: _FAKE_HOSTAPIS):

        _PREF_PATH = Path(tmp) / "audio_device_prefs.json"

        # Device present -> gets picked up.
        sd.default.device = (None, None)
        _PREF_PATH.write_text(json.dumps({"input_name": "USB Mic", "output_name": "Built-in Speakers"}))
        apply_saved_devices()
        assert list(sd.default.device) == [0, 1], sd.default.device

        # Saved name no longer present -> that side left alone (falls
        # back to whatever was already the default), not forced to None.
        sd.default.device = (5, 5)
        _PREF_PATH.write_text(json.dumps({"input_name": "Gone Mic", "output_name": "Built-in Speakers"}))
        apply_saved_devices()
        assert list(sd.default.device) == [5, 1], sd.default.device

        # set_input_device/set_output_device round-trip through the save.
        set_input_device(0)
        saved = json.loads(_PREF_PATH.read_text())
        assert saved["input_name"] == "USB Mic", saved

    print("device_info self-check: all passed")


def list_audio_devices() -> str:
    """Every mic and speaker by index and name — for set_input_device /
    set_output_device to target, and for a spoken 'what devices do I have'."""
    mics = "\n".join(f"  {d['index']}: {d['name']}" for d in list_input_devices())
    speakers = "\n".join(f"  {d['index']}: {d['name']}" for d in list_output_devices())
    return f"Microphones:\n{mics}\n\nSpeakers:\n{speakers}"


def describe_audio_devices() -> str:
    """
    One line naming the current default mic and speakers, so it's
    obvious at a glance which devices FRED will actually use.
    """

    try:
        input_idx, output_idx = sd.default.device
        mic = sd.query_devices(input_idx)["name"] if input_idx is not None else "system default"
        speakers = sd.query_devices(output_idx)["name"] if output_idx is not None else "system default"
    except Exception as e:
        return f"Audio devices: unavailable ({e})"

    return f"Mic: {mic} | Speakers: {speakers}"
