# Core/audio/device_info.py

import sounddevice as sd


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
