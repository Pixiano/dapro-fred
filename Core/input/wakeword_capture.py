# Core/input/wakeword_capture.py
#
# Live-usage dataset for wake-word retraining, built passively during
# normal use instead of a dedicated recording session (see
# wakeword_train.py's REAL_POSITIVE_DIR / room_recording — same idea,
# collected for free from real activations rather than a scripted
# read-through). Every FIRED wake-word event gets its trigger phrase
# plus whatever followed saved as one clip, tagged with the two things
# asked for, 2026-08-13: whether it fired, and whether real speech
# followed at all — independent of whether the turn was later
# cancelled, so a "said something, then hit the FRED button on a bad
# transcript" attempt still logs correctly as spoke_after=True.

import json
import threading
import time

import numpy as np
import soundfile as sf

from config.settings import DATA_DIR
from audio.stt_whisper import _SILENCE_PEAK_FLOOR

CAPTURE_DIR = DATA_DIR / "wakeword_training" / "live_captures"
MANIFEST_PATH = CAPTURE_DIR / "manifest.jsonl"
SR = 16000

# Silent seam between the trigger clip and whatever followed — audible
# on playback as a clean split, and keeps the two segments from being
# misread as one continuous utterance if this ever feeds training data.
_GAP_SECONDS = 0.3

_lock = threading.Lock()


def save(trigger_audio, followup_audio, cancelled: bool, transcript: str,
         wake_score: float, wake_gain: float):
    """
    trigger_audio: WakewordListener.last_trigger_audio — the ~2.5s
    pre-trigger buffer snapshotted at fire time (may be shorter right
    after startup, before the buffer's full). None only if the
    listener never captured anything, which shouldn't happen for a
    real fire.

    followup_audio: WhisperSTT.last_audio after the turn ended, however
    it ended (natural silence-timeout, or a FRED-button cancel — both
    now populate this, see stt_whisper.py). None if nothing was
    recorded at all.

    Never raises into the caller — this is a logging side-effect of a
    real conversation turn, not allowed to take it down.
    """
    try:
        parts = []
        if trigger_audio is not None and len(trigger_audio):
            parts.append(trigger_audio)

        followup_seconds = 0.0
        spoke_after = False
        if followup_audio is not None and len(followup_audio):
            parts.append(np.zeros(int(SR * _GAP_SECONDS), dtype=np.float32))
            parts.append(followup_audio)
            followup_seconds = len(followup_audio) / SR
            spoke_after = bool(np.max(np.abs(followup_audio)) >= _SILENCE_PEAK_FLOOR)

        if not parts:
            return  # nothing at all to save — shouldn't happen for a real fire

        combined = np.concatenate(parts).astype(np.float32)
        trigger_seconds = len(trigger_audio) / SR if trigger_audio is not None else 0.0

        CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
        filename = f"capture_{time.strftime('%Y%m%d_%H%M%S')}_{int(time.time() * 1000) % 1000:03d}.wav"
        sf.write(CAPTURE_DIR / filename, combined, SR)

        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "file": filename,
            "trigger_seconds": round(trigger_seconds, 2),
            "followup_seconds": round(followup_seconds, 2),
            "total_seconds": round(len(combined) / SR, 2),
            "spoke_after": spoke_after,
            "cancelled": cancelled,
            "transcript": transcript,
            "wake_score": round(wake_score, 3),
            "wake_gain": round(wake_gain, 2),
        }
        with _lock:
            with open(MANIFEST_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[wakeword_capture] save failed: {e}")
