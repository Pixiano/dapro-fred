# TTS_POSTROLL_SEC's one non-trivial bit: the trailing silence write
# must happen on a normal, uninterrupted finish (so a Bluetooth link's
# downstream buffer has runway to actually play the last real words
# before stop()/close() tear the stream down), and must NOT happen on
# an interrupt (which should still cut off promptly, not linger).

import threading

import numpy as np

from audio import tts_kokoro
from audio.tts_kokoro import KokoroTTS
from config.settings import TTS_POSTROLL_SEC


def _make_tts(writes, on_write=None):
    tts = KokoroTTS.__new__(KokoroTTS)
    tts._lock = threading.Lock()
    tts.synth = lambda t: (np.zeros(4, dtype="float32"), 24000)

    class _Stream:
        def start(self): pass
        def stop(self): pass
        def close(self): pass
        def abort(self):
            writes.append("ABORT")

        def write(self, block):
            writes.append(np.array(block, copy=True))
            if on_write:
                on_write(len(writes))

    tts_kokoro.sd.OutputStream = lambda **kw: _Stream()
    tts_kokoro.phrase_cache.get = lambda chunk: None
    return tts


def test_postroll_written_on_natural_completion():
    writes = []
    tts = _make_tts(writes)

    KokoroTTS.speak(tts, iter(["A short reply that finishes normally."]))

    last = writes[-1]
    assert len(last) == int(24000 * TTS_POSTROLL_SEC)
    assert np.all(last == 0)


def test_postroll_skipped_on_interrupt():
    writes = []
    cancel = threading.Event()

    def _cancel_after_first_write(n_writes):
        if n_writes == 1:
            cancel.set()

    tts = _make_tts(writes, on_write=_cancel_after_first_write)

    KokoroTTS.speak(tts, iter(["A reply that gets interrupted."]), cancel=cancel)

    assert any(isinstance(w, str) and w == "ABORT" for w in writes)
    postroll_len = int(24000 * TTS_POSTROLL_SEC)
    assert not any(
        isinstance(w, np.ndarray) and len(w) == postroll_len and np.all(w == 0)
        for w in writes
    )
