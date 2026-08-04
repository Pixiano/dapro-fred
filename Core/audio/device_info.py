# Core/audio/device_info.py

import json
from pathlib import Path

import sounddevice as sd

# The HUD server runs as its own OS process (see hud/server.py), so its
# own sd.default.device is independent of FRED's — it can't just query
# sd.default.device to know what FRED currently has selected. This file
# is the same voice-line bus utils/voice_line.py already uses for other
# cross-process state, so the HUD's device dropdown can show FRED's
# actual current selection instead of its own process's OS default.
_BUS_DIR = Path.home() / "voice-line"
_SELECTION_PATH = _BUS_DIR / "audio_devices.json"


def _publish_selection():
    try:
        _BUS_DIR.mkdir(parents=True, exist_ok=True)
        input_, output = sd.default.device
        _SELECTION_PATH.write_text(
            json.dumps({"input": input_, "output": output}), encoding="utf-8"
        )
    except OSError:
        pass


def list_input_devices() -> list:
    """[{index, name}] for every device with at least one input channel —
    for the HUD's microphone dropdown."""
    return [
        {"index": i, "name": d["name"]}
        for i, d in enumerate(sd.query_devices())
        if d["max_input_channels"] > 0
    ]


def list_output_devices() -> list:
    """[{index, name}] for every device with at least one output channel —
    for the HUD's speaker dropdown."""
    return [
        {"index": i, "name": d["name"]}
        for i, d in enumerate(sd.query_devices())
        if d["max_output_channels"] > 0
    ]


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
    return f"Microphone set to {name}"


def set_output_device(index: int) -> str:
    """Switch FRED's speaker output — see set_input_device."""
    input_, _ = sd.default.device
    name = sd.query_devices(int(index))["name"]
    sd.default.device = (input_, int(index))
    _publish_selection()
    return f"Speaker set to {name}"


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
