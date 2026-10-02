# Core/input/voice_activity.py
#
# Mic-level voice-activity detection, tapping wakeword.py's existing
# continuous InputStream instead of opening a second capture pipeline
# (per plan_perception_features_2026-08-25.md #2). webrtcvad was already
# installed and unused. wakeword.py's own callback already builds an
# int16 mono 16kHz block for openwakeword every 80ms (CHUNK=1280
# samples) -- feed_chunk() below re-chunks that into webrtcvad's fixed
# 20ms frames rather than opening a second sd.InputStream.
#
# Mirrors audio/media_state.py's shape: one small concern, one function
# the rest of the codebase actually cares about (is_voice_active()).
# NOT wired into any decision yet (presence, proactive gating) -- per
# the source plan doc, verification against real live audio (silence,
# ambient noise, actual speech) comes first.

import collections
import time

import numpy as np
import webrtcvad

# Same rate wakeword.py's SR/stt_whisper.py's STT_SAMPLE_RATE already
# use -- not cross-imported from wakeword.py to avoid a circular import
# (wakeword.py imports this module to feed it), same duplication
# precedent voice_id.py's own SAMPLE_RATE comment already established.
_SR = 16000
_FRAME_MS = 20  # one of webrtcvad's three supported frame sizes (10/20/30ms)
_FRAME_SAMPLES = _SR * _FRAME_MS // 1000  # 320 samples @ 16kHz

# "voice detected in the last N seconds" -- rolling window, not a single
# frame's verdict, same reasoning wakeword.py's own SILENCE_TIMEOUT_SECONDS
# uses for endpointing: a single missed/voiced frame shouldn't flip the
# answer.
ACTIVE_WINDOW_SECONDS = 2.0

# Mode 2 (of 0-3): webrtcvad's own "moderate" aggressiveness default --
# not measured against real audio yet, see module docstring.
_vad = webrtcvad.Vad(2)

_last_voice_at = 0.0
_leftover = np.array([], dtype=np.int16)  # samples not yet filling a full 20ms frame


def feed_chunk(int16_block: np.ndarray):
    """Call once per wakeword chunk -- the same int16 mono @ 16kHz block
    wakeword.py's _process_chunk already builds for openwakeword.
    Updates the rolling last-voice-detected timestamp on any voiced
    20ms frame within the chunk."""
    global _leftover, _last_voice_at

    samples = np.concatenate([_leftover, int16_block])
    n_frames = len(samples) // _FRAME_SAMPLES
    for i in range(n_frames):
        frame = samples[i * _FRAME_SAMPLES:(i + 1) * _FRAME_SAMPLES]
        if _vad.is_speech(frame.tobytes(), _SR):
            _last_voice_at = time.monotonic()
    _leftover = samples[n_frames * _FRAME_SAMPLES:]


def is_voice_active() -> bool:
    """True if voice was detected within the last ACTIVE_WINDOW_SECONDS."""
    return time.monotonic() - _last_voice_at < ACTIVE_WINDOW_SECONDS


if __name__ == "__main__":
    # Self-check: pure re-chunking/rolling-window logic against synthetic
    # PCM, no real mic needed -- same split as voice_id.py's own
    # __main__ self-check (real hardware validated by hand separately).
    rng = np.random.RandomState(0)

    silence = np.zeros(1280, dtype=np.int16)
    loud_tone = (rng.uniform(-1, 1, 1280) * 20000).astype(np.int16)

    assert is_voice_active() is False  # nothing fed yet

    feed_chunk(silence)
    assert is_voice_active() is False, "silence must never register as voice"

    # webrtcvad needs several frames of a real-speech-shaped signal to
    # trip -- a handful of loud chunks is enough to exercise the
    # re-chunking/leftover-carry path even if the synthetic noise itself
    # doesn't reliably read as "speech" to the model.
    for _ in range(5):
        feed_chunk(loud_tone)
    assert _leftover.size < _FRAME_SAMPLES, "leftover must never exceed one frame"

    print("voice_activity self-check: all passed")
