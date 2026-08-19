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

from audio import device_info, mute_state, phrase_cache
from config.settings import (
    KOKORO_MODEL_PATH,
    KOKORO_VOICES_PATH,
    KOKORO_VOICE,
    KOKORO_VOICE_BLEND,
    KOKORO_SPEED,
    TTS_PREROLL_SEC,
    TTS_POSTROLL_SEC,
)

# Blocks are the cancellation granularity: a write returns only once the
# device has taken the block, so this is also how quickly an interrupt
# can silence playback (~43 ms at 24 kHz).
PLAY_BLOCK = 1024

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

# Markdown the LLM emits but which must not be read aloud as characters.
_MD_NOISE = re.compile(r"[*_`#>]+")
_BULLET = re.compile(r"^\s*[-•]\s*", re.MULTILINE)

# A markdown link speaks its label, never the URL underneath — confirmed
# 2026-08-03: a task saved with the link as its own label (no separate
# name given) had Kokoro reading "h t t p s colon slash slash example
# dot com slash report" character by character.
_MD_LINK = re.compile(r"\[([^\]]*)\]\(https?://[^\s)]+\)")

# Whatever URL is still there afterward — the link's label having been
# the raw URL itself, or a bare URL never wrapped in markdown at all —
# collapses to just its first hostname label. "example" out of
# https://example.com/report, not the whole address read out loud.
_URL = re.compile(r"https?://(?:www\.)?([a-z0-9-]+)\.\S+", re.IGNORECASE)

# Fractions read as raw symbols instead of words — \frac{3}{4} has no
# backslash/brace in _MD_NOISE below, so it reached Kokoro completely
# untouched ("backslash frac open brace three..."). Runs first, before
# anything else gets a chance to mangle the backslash/braces.
_LATEX_FRAC = re.compile(r"\\frac\s*\{([^{}]+)\}\s*\{([^{}]+)\}")
# Bare digit fractions ("3/4") — not slash-anything, just digit/digit, so
# this can't collide with a URL path (letters, not digits, either side).
_SLASH_FRAC = re.compile(r"(?<!\d)(\d+)\s*/\s*(\d+)(?!\d)")

# Same class of bug as _LATEX_FRAC — other LaTeX/math notation Kokoro would
# otherwise read as raw symbols or drop silently. All run before _MD_NOISE
# strips stray '*'/'_' below, and before it since \sqrt{} has braces too.
_LATEX_SQRT = re.compile(r"\\sqrt\s*\{([^{}]+)\}")
_LATEX_SYMBOLS = [
    (re.compile(r"\\times|\\cdot"), " times "),
    (re.compile(r"\\pm"), " plus or minus "),
    (re.compile(r"\\leq"), " less than or equal to "),
    (re.compile(r"\\geq"), " greater than or equal to "),
    (re.compile(r"\\neq"), " not equal to "),
    (re.compile(r"\\approx"), " approximately "),
    (re.compile(r"\\infty"), " infinity "),
    (re.compile(r"\\pi"), " pi "),
]
# Exponents: x^2 -> "x squared", x^3 -> "x cubed", x^{10} -> "x to the power 10".
_EXPONENT = re.compile(r"(\w)\^\{?(-?\d+)\}?")
_EXPONENT_WORDS = {"2": "squared", "3": "cubed"}
# Bare digit multiplication ("3 * 4") — digit-bounded same as _SLASH_FRAC,
# so it can't collide with markdown *bold*/_italic_ around words.
_STAR_MULT = re.compile(r"(?<!\d)(\d+)\s*\*\s*(\d+)(?!\d)")


def _exponent_sub(match: "re.Match") -> str:
    base, exp = match.group(1), match.group(2)
    word = _EXPONENT_WORDS.get(exp, f"to the power {exp}")
    return f"{base} {word}"

# Bracket tags like list_scheduled()'s "[reminder_1785718306_1] Reminder:
# ..." — an internal job id meant for matching, not for a listener to
# hear as a string of digits. Runs after _MD_LINK, which has already
# consumed every real [label](url) pair, so anything still bracketed at
# this point is metadata, never content.
_BRACKET_TAG = re.compile(r"\[[^\]]*\]")

# Two attempts at the "quiet at the start, loud after ~0.2s" report were
# made on 2026-08-01 and BOTH REVERTED, recorded here so neither gets
# tried a third time:
#
#   1. Peak-normalising every chunk to a fixed target (0.95). Made things
#      actively worse — Kokoro's own output level was never the problem,
#      so all this did was amplify the already-audible part after the
#      ramp, widening the gap it was meant to close.
#   2. Replacing the silent preroll with a quiet 80Hz tone, on the theory
#      that a Bluetooth receiver's gain ramp only triggers on real signal
#      energy. No improvement to the quiet phase.
#
# The remaining behaviour (quiet opening, then a step up in level after a
# short gap, on Bluetooth output) is most likely the receiver's own
# hardware gain ramp — outside this process's control, and not something
# sample-level changes reached in either attempt. Left alone deliberately.


def clean_for_speech(text: str) -> str:
    text = _LATEX_FRAC.sub(r"\1 over \2", text)
    text = _SLASH_FRAC.sub(r"\1 over \2", text)
    text = _LATEX_SQRT.sub(r"square root of \1", text)
    for pattern, replacement in _LATEX_SYMBOLS:
        text = pattern.sub(replacement, text)
    text = _EXPONENT.sub(_exponent_sub, text)
    text = _STAR_MULT.sub(r"\1 times \2", text)
    text = _MD_LINK.sub(r"\1", text)
    text = _URL.sub(r"\1", text)
    text = _BRACKET_TAG.sub("", text)
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

    Splits the text as given — cleaning happens at synthesis time
    (emit() below), not here. It used to happen here, and that silently
    ate long replies: the streaming producer locates the chunk it just
    flushed inside its raw buffer, and a chunk stripped of markdown is
    not findable in text that still has it, so the whole remaining
    buffer was dropped. Any reply containing a bullet, a link or a bold
    span was cut off at its first sentence boundary.
    """
    text = re.sub(r"[ \t]+", " ", text).strip()
    if not text:
        return []

    # Chunks are exact slices of `text` (the merge below extends a span
    # rather than re-joining strings), so the streaming producer can
    # locate the chunk it flushed and keep the remainder of its buffer.
    bounds = [0]
    for m in _SENTENCE_SPLIT.finditer(text):
        bounds.append(m.end())
    bounds.append(len(text))

    merged = []
    spans = []
    for i in range(len(bounds) - 1):
        start, end = bounds[i], bounds[i + 1]
        if not text[start:end].strip():
            continue
        if spans and (spans[-1][1] - spans[-1][0]) < min_chunk:
            spans[-1] = (spans[-1][0], end)
        else:
            spans.append((start, end))
    merged = [text[a:b].strip() for a, b in spans]

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
        self._model_path = str(model_path or KOKORO_MODEL_PATH)
        self._voices_path = str(voices_path or KOKORO_VOICES_PATH)
        self._voice_name = voice or KOKORO_VOICE
        self.speed = speed or KOKORO_SPEED

        # Deferred, not built here — see _ensure_model(). Kokoro runs
        # CPUExecutionProvider only (verified: kokoro_onnx hardcodes it,
        # and this environment's onnxruntime has no GPU provider at all),
        # so "loading" it costs RAM, not VRAM, unlike the LLM/Whisper
        # unload pair in model_lifecycle.py. Still worth deferring: a
        # session whose every spoken phrase is a phrase_cache hit (see
        # audio/phrase_cache.py) never needs Kokoro's ~340MB resident at
        # all, and construction time comes off FRED's boot instead.
        self.kokoro = None
        self.voice = None  # resolved (possibly blended) voice; set on load
        self._lock = threading.Lock()
        self._load_lock = threading.Lock()

    # =========================================================
    # LAZY LOAD / UNLOAD — same shape as LLMClient / WhisperSTT
    # =========================================================

    def is_loaded(self) -> bool:
        return self.kokoro is not None

    def ensure_loaded(self) -> bool:
        """Load ahead of use. Safe to call from any thread — real
        construction is guarded so two callers can't double-load."""
        try:
            self._ensure_model()
            return True
        except Exception as e:
            print(f"[KokoroTTS] preload failed: {e}")
            return False

    def unload(self) -> bool:
        """Drop the model. Frees ordinary RAM only (~340MB) — see the
        note in __init__ on why this is not a VRAM reclaim."""
        with self._load_lock:
            if self.kokoro is None:
                return False
            self.kokoro = None
            self.voice = None
        import gc
        gc.collect()
        return True

    def _ensure_model(self):
        if self.kokoro is not None:
            return
        with self._load_lock:
            if self.kokoro is not None:  # lost the race, already loaded
                return
            from kokoro_onnx import Kokoro
            model = Kokoro(self._model_path, self._voices_path)
            self.kokoro = model
            self.voice = self._resolve_voice(self._voice_name)

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
        self._ensure_model()
        return sorted(self.kokoro.get_voices())

    # =========================================================
    # SYNTHESIS
    # =========================================================

    def synth(self, text: str):
        """
        One chunk -> (float32 samples, sample_rate). Loads the model on
        first real use if it isn't resident.

        Not guarded by self._lock — that lock's job is serialising whole
        speak() calls, and it's held for a speak() call's entire
        duration by a different thread than the one running this (the
        producer thread), so taking it here would deadlock. This means
        the phrase-cache warm-up thread (pill_app._warm_phrase_cache)
        can call synth() concurrently with an in-flight speak()'s own
        synth() calls. Left unsynchronised on the assumption that
        onnxruntime's InferenceSession.run() is safe for concurrent
        calls from multiple threads, which is a documented ORT design
        goal — unlike llama.cpp's raw C bindings, which turned out NOT
        to have that guarantee (see orchestrator.py's producer.join()
        fix). Not independently verified here; flagged rather than
        silently assumed.
        """
        self._ensure_model()
        return self.kokoro.create(
            text, voice=self.voice, speed=self.speed, lang="en-us"
        )

    # =========================================================
    # STREAMING PLAYBACK
    # =========================================================

    def speak(self, text, on_level=None, on_first_audio=None, cancel=None) -> str:
        """
        Synthesise and play `text`, prefetching the next chunk while the
        current one plays. Blocks until done or cancelled.

        `text` may be a finished string, or an iterable of pieces from a
        streaming generator — in which case speech starts as soon as the
        first complete sentence has arrived, rather than waiting for the
        model to finish. That is the difference between a couple of
        seconds of dead air per turn and none.

        Returns the text that was *actually spoken* — not the input, when
        cancelled partway. The caller should record that rather than the
        full reply: storing words the user never heard makes follow-up
        turns incoherent.
        """
        if isinstance(text, str):
            source = iter([text])
        else:
            source = iter(text)

        cancel = cancel or threading.Event()

        # Muted: drain the reply text without synthesising or playing any
        # audio at all — no model call, no output stream. Conversation
        # state still needs the full text (see the docstring's note on
        # returning what was "spoken"), so the join happens here rather
        # than skipping the whole reply.
        if mute_state.is_muted():
            pieces = []
            for piece in source:
                if cancel.is_set():
                    break
                pieces.append(piece or "")
            return "".join(pieces).strip()

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

        def emit(chunk):
            """Synthesise one chunk and hand it to the player — or skip
            synthesis entirely on a phrase_cache hit. Fillers and tool
            captions are a closed, ~50-phrase vocabulary spoken on
            nearly every turn; checking a cache dict is orders of
            magnitude cheaper than re-running Kokoro on the same "On
            it." for the thousandth time, and a full cache hit means
            self.kokoro never has to be loaded at all this turn."""
            speakable = clean_for_speech(chunk)
            if not speakable:
                return
            cached = phrase_cache.get(speakable)
            if cached is not None:
                samples, sr = cached
            else:
                try:
                    samples, sr = self.synth(speakable)
                except Exception as e:
                    print(f"[KokoroTTS] synth failed for {speakable!r}: {e}")
                    return
            while not stopping():
                try:
                    pending.put((chunk, samples, sr), timeout=0.1)
                    return
                except queue.Full:
                    continue

        def producer():
            # Accumulate incoming text and flush whole sentences as they
            # complete. A partial sentence is never synthesised — Kokoro's
            # prosody depends on seeing the full clause, and half a
            # sentence spoken then corrected sounds worse than waiting.
            buffer = ""
            first = True

            for piece in source:
                if stopping():
                    break
                # Same whitespace normalisation split_for_speech applies,
                # so the chunks it returns are findable slices of this.
                buffer = re.sub(r"[ \t]+", " ", buffer + (piece or "")).lstrip()

                while not stopping():
                    ready = split_for_speech(
                        buffer,
                        first_chunk_max=90 if first else 10_000,
                    )
                    # Only flush when there is provably a completed
                    # sentence, i.e. more than one chunk, or the buffer
                    # already ends on terminal punctuation.
                    complete = len(ready) > 1 or buffer.rstrip().endswith((".", "!", "?"))
                    if not (ready and complete):
                        break
                    head = ready[0]
                    emit(head)
                    first = False
                    index = buffer.find(head)
                    if index < 0:
                        # Shouldn't happen now that chunks are exact
                        # slices — but dropping the buffer here is how
                        # long replies used to go unspoken, so bail out
                        # of the flush loop and keep the text instead.
                        break
                    buffer = buffer[index + len(head):]

            # Whatever is left is the tail of the reply — speak it even
            # without terminal punctuation, or the last words are lost.
            if buffer.strip() and not stopping():
                for chunk in split_for_speech(buffer):
                    if stopping():
                        break
                    emit(chunk)
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
            natural_end = False
            sr = None
            try:
                while not cancel.is_set():
                    try:
                        item = pending.get(timeout=0.2)
                    except queue.Empty:
                        continue
                    if item is None:
                        natural_end = True
                        break

                    chunk, samples, sr = item

                    if stream is None:
                        stream = sd.OutputStream(
                            samplerate=sr, channels=1, dtype="float32",
                            extra_settings=device_info.output_extra_settings(),
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
                        elif natural_end and sr and TTS_POSTROLL_SEC > 0:
                            # See TTS_POSTROLL_SEC: stop() only waits for
                            # PortAudio's own buffer, not a Bluetooth
                            # link's downstream latency — give the device
                            # inaudible tail to still be playing so the
                            # real last words are already out by the time
                            # stop()+close() tear the stream down. Skipped
                            # on an interrupt — that should still cut off
                            # promptly, not linger.
                            stream.write(
                                np.zeros(int(sr * TTS_POSTROLL_SEC), dtype=np.float32)
                            )
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
