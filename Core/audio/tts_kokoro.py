# Core/audio/tts_kokoro.py
#
# Kokoro TTS for GUI mode, with sentence-level streaming.
#
# Two things this fixes that the SAPI path (audio/tts.py) could not:
#
#   1. Dead air. The old flow was: generate the entire reply, then start
#      speaking. On a local model that's seconds of silence every turn.
#      Here each sentence is synthesised and played while the next is
#      still being synthesised, so speech starts almost immediately.
#
#   2. A fake speaking animation. pyttsx3/SAPI exposes word-boundary
#      events but never raw audio, so the old orb pulsed on word timing
#      rather than amplitude. Kokoro returns float32 samples, so
#      `on_level` below carries the RMS of the block actually being
#      played — the waveform reacts to real speech.
#
# Cancellation is the subtle part. During a reply there are up to three
# things in flight: the LLM still generating, a queue of pending
# sentences, and one audio block on the device. Order matters when
# tearing that down — kill the audio first (that's the silence the user
# perceives), then the queue, then the generator. This module owns the
# first two; the caller owns the generator and passes the same
# `cancel` event in.

import queue
import re
import threading

import numpy as np
import sounddevice as sd

from config.settings import (
    KOKORO_MODEL_PATH,
    KOKORO_VOICES_PATH,
    KOKORO_VOICE,
    KOKORO_VOICE_BLEND,
    KOKORO_SPEED,
    TTS_PREROLL_SEC,
)

# Blocks are the cancellation granularity: a write returns only once the
# device has taken the block, so this is also how quickly an interrupt
# can silence playback (~43 ms at 24 kHz).
PLAY_BLOCK = 1024

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

# Markdown the LLM emits but which must not be read aloud as characters.
_MD_NOISE = re.compile(r"[*_`#>]+")
_BULLET = re.compile(r"^\s*[-•]\s*", re.MULTILINE)


def clean_for_speech(text: str) -> str:
    text = _BULLET.sub("", text)
    text = _MD_NOISE.sub("", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def split_for_speech(text: str, first_chunk_max: int = 90, min_chunk: int = 45):
    """
    Split into speakable chunks, front-loaded for latency.

    The first chunk is deliberately kept short so audio starts sooner;
    later chunks are allowed to run long because longer spans give the
    model more context and therefore better prosody.

    Abbreviations fall out for free: splitting requires whitespace after
    the period, so "3.5" never splits, and "Dr. Smith" splits into a
    3-character fragment that the merge pass below immediately reattaches.
    """
    text = clean_for_speech(text)
    if not text:
        return []

    raw = [s for s in _SENTENCE_SPLIT.split(text) if s.strip()]

    merged = []
    for part in raw:
        if merged and len(merged[-1]) < min_chunk:
            merged[-1] = f"{merged[-1]} {part}".strip()
        else:
            merged.append(part.strip())

    if not merged:
        return []

    # Break the opening chunk at a clause boundary if it's long, so
    # time-to-first-audio doesn't depend on the first sentence's length.
    head = merged[0]
    if len(head) > first_chunk_max:
        cut = head.rfind(", ", 0, first_chunk_max)
        if cut > min_chunk:
            merged[0] = head[: cut + 1]
            merged.insert(1, head[cut + 2 :])

    return merged


class KokoroTTS:
    """
    Local neural TTS. speak() blocks for the duration of playback, so
    call it from a worker thread, never from the window's message loop.
    """

    def __init__(self, model_path=None, voices_path=None, voice=None, speed=None):
        from kokoro_onnx import Kokoro

        model_path = str(model_path or KOKORO_MODEL_PATH)
        voices_path = str(voices_path or KOKORO_VOICES_PATH)

        self.kokoro = Kokoro(model_path, voices_path)
        self.speed = speed or KOKORO_SPEED
        self.voice = self._resolve_voice(voice or KOKORO_VOICE)
        self._lock = threading.Lock()

    def _resolve_voice(self, name):
        """
        Returns either a voice name or a blended style tensor.

        Blending is the supported route to a voice that isn't in the
        stock list: voicepacks are plain embedding tensors, so a weighted
        sum of two is itself a valid voice. There is no published
        finetuning pipeline for this model, so this is as far as custom
        voices go without changing TTS engine entirely.
        """
        if not KOKORO_VOICE_BLEND:
            return name

        other, weight = KOKORO_VOICE_BLEND
        try:
            base = self.kokoro.get_voice_style(name)
            mix = self.kokoro.get_voice_style(other)
            w = float(np.clip(weight, 0.0, 1.0))
            return base * (1.0 - w) + mix * w
        except Exception as e:
            print(f"[KokoroTTS] voice blend failed ({e}) — using '{name}'")
            return name

    def available_voices(self):
        return sorted(self.kokoro.get_voices())

    # =========================================================
    # SYNTHESIS
    # =========================================================

    def synth(self, text: str):
        """One chunk -> (float32 samples, sample_rate)."""
        return self.kokoro.create(
            text, voice=self.voice, speed=self.speed, lang="en-us"
        )

    # =========================================================
    # STREAMING PLAYBACK
    # =========================================================

    def speak(self, text, on_level=None, on_first_audio=None, cancel=None) -> str:
        """
        Synthesise and play `text` chunk by chunk, prefetching the next
        chunk while the current one plays. Blocks until done or cancelled.

        Returns the text that was *actually spoken* — which is not the
        input when cancelled partway. The caller should record that
        rather than the full reply: storing words the user never heard
        makes follow-up turns incoherent.
        """
        chunks = split_for_speech(text)
        if not chunks:
            return ""

        cancel = cancel or threading.Event()
        spoken = []

        # Separate from `cancel` on purpose. `cancel` belongs to the
        # caller and means "the user interrupted"; this one only means
        # "playback is over, producer may stop". Setting the caller's
        # event on normal completion would destroy its ability to tell
        # a finished reply from an interrupted one.
        finished = threading.Event()

        def stopping():
            return cancel.is_set() or finished.is_set()

        # maxsize bounds how far ahead synthesis may run — one chunk of
        # lookahead is enough to hide synthesis latency, and keeping it
        # small means a cancel discards little wasted work.
        pending = queue.Queue(maxsize=1)

        def producer():
            for chunk in chunks:
                if stopping():
                    break
                try:
                    samples, sr = self.synth(chunk)
                except Exception as e:
                    print(f"[KokoroTTS] synth failed for {chunk!r}: {e}")
                    continue
                while not stopping():
                    try:
                        pending.put((chunk, samples, sr), timeout=0.1)
                        break
                    except queue.Full:
                        continue
            # Sentinel so the consumer doesn't wait on a dead producer.
            while not stopping():
                try:
                    pending.put(None, timeout=0.1)
                    break
                except queue.Full:
                    continue

        with self._lock:
            producer_thread = threading.Thread(target=producer, daemon=True)
            producer_thread.start()

            stream = None
            started = False
            try:
                while not cancel.is_set():
                    try:
                        item = pending.get(timeout=0.2)
                    except queue.Empty:
                        continue
                    if item is None:
                        break

                    chunk, samples, sr = item

                    if stream is None:
                        stream = sd.OutputStream(
                            samplerate=sr, channels=1, dtype="float32"
                        )
                        stream.start()
                        # Let a Bluetooth link do its wake-up ramp against
                        # silence rather than against the first words of
                        # the reply — see TTS_PREROLL_SEC in settings for
                        # the measurements behind this.
                        if TTS_PREROLL_SEC > 0:
                            stream.write(
                                np.zeros(int(sr * TTS_PREROLL_SEC), dtype=np.float32)
                            )

                    if not started:
                        started = True
                        if on_first_audio:
                            try:
                                on_first_audio()
                            except Exception:
                                pass

                    interrupted = False
                    for i in range(0, len(samples), PLAY_BLOCK):
                        if cancel.is_set():
                            interrupted = True
                            break
                        block = samples[i : i + PLAY_BLOCK]
                        # write() blocks until the device accepts the
                        # block, which keeps the reported level roughly
                        # in step with what's actually audible.
                        stream.write(np.ascontiguousarray(block))
                        if on_level:
                            rms = float(np.sqrt(np.mean(block * block)))
                            try:
                                on_level(min(1.0, rms * 4.0))
                            except Exception:
                                pass

                    if interrupted:
                        break
                    spoken.append(chunk)

            finally:
                # Audio device first — this is the part the listener
                # actually perceives as "it stopped".
                if stream is not None:
                    try:
                        if cancel.is_set():
                            stream.abort()
                        stream.stop()
                        stream.close()
                    except Exception as e:
                        print(f"[KokoroTTS] stream teardown: {e}")
                finished.set()  # unblocks the producer if it's still going
                if on_level:
                    try:
                        on_level(0.0)
                    except Exception:
                        pass

        return " ".join(spoken).strip()
