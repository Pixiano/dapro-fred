# Reported live 2026-08-12: a wake-triggered turn that heard nothing
# still "stayed listening" for several seconds — most of that was
# stop_and_transcribe() running a full Whisper pass over a buffer that
# never had any sound in it. The fix must skip straight to "" without
# ever touching the model, while still transcribing normally when
# there's real signal.

import threading

import numpy as np

from audio.stt_whisper import WhisperSTT


def _make_stt(blocks):
    stt = WhisperSTT.__new__(WhisperSTT)
    stt.samplerate = 16000
    stt.language = "en"
    stt._lock = threading.Lock()
    stt._stream_lock = threading.RLock()
    stt._stream = None
    stt._recording = True
    stt._blocks = blocks
    stt.model = "sentinel — must never be touched for pure silence"
    return stt


def test_pure_silence_skips_whisper_entirely(monkeypatch):
    silence = [np.zeros(16000, dtype=np.float32)]
    stt = _make_stt(silence)
    monkeypatch.setattr(stt, "ensure_loaded", lambda: (_ for _ in ()).throw(
        AssertionError("model should never be loaded for a silent buffer")
    ))

    assert stt.stop_and_transcribe() == ""


def test_real_signal_still_reaches_whisper(monkeypatch):
    loud = [np.full(16000, 0.5, dtype=np.float32)]
    stt = _make_stt(loud)

    called = []
    stt.ensure_loaded = lambda: (called.append(True), True)[1]

    class _Seg:
        text = "hello"

    class _Model:
        def transcribe(self, audio, **kw):
            return [_Seg()], None

    stt.model = _Model()

    assert stt.stop_and_transcribe() == "hello"
    assert called == [True]
