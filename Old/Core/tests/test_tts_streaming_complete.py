# Long replies were spoken only up to their first sentence boundary:
# the streaming producer located each flushed chunk inside its raw
# buffer with find(), but the chunk had already been stripped of
# markdown, so find() missed and the rest of the buffer was discarded.

import threading

from audio import tts_kokoro
from audio.tts_kokoro import KokoroTTS, split_for_speech


def test_chunks_are_exact_slices():
    text = "**Bold** first sentence here. - a bullet point follows. [tag] and a tail."
    for chunk in split_for_speech(text):
        assert chunk in text


def _speak_capture(pieces):
    """Run speak()'s producer/consumer for real, with synthesis and the
    audio device stubbed out, and return what it tried to say."""
    said = []
    tts = KokoroTTS.__new__(KokoroTTS)
    tts._lock = threading.Lock()
    tts.synth = lambda t: (said.append(t), (__import__("numpy").zeros(4, dtype="float32"), 24000))[1]

    class _Stream:
        def start(self): pass
        def write(self, block): pass
        def stop(self): pass
        def close(self): pass
        def abort(self): pass

    tts_kokoro.sd.OutputStream = lambda **kw: _Stream()
    tts_kokoro.phrase_cache.get = lambda chunk: None
    KokoroTTS.speak(tts, iter(pieces))
    return " ".join(said)


def test_whole_markdown_reply_is_spoken():
    # Both sentences arrive in one piece, so a single flush must speak
    # the first and keep the second. The **bold** inside the first is
    # what used to make the producer lose track of its buffer.
    pieces = [
        "Here is **the** first sentence, and it runs long enough to flush on its own. "
        "And the final sentence that used to vanish, arriving in the same "
        "piece as the one before it so one flush had to carry both.",
    ]
    said = _speak_capture(pieces)
    assert "first sentence" in said
    assert "final sentence that used to vanish" in said


if __name__ == "__main__":
    test_chunks_are_exact_slices()
    test_whole_markdown_reply_is_spoken()
    print("ok")
