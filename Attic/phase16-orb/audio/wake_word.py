# Core/audio/wake_word.py

import re
import threading

import numpy as np
import sounddevice as sd
import webrtcvad

from audio.stt import STTManager
from config.settings import WAKE_PHRASES, STT_SAMPLE_RATE


class WakeWordListener:
    """
    Passive wake-word detection for F.R.E.D., gated by cheap VAD
    (webrtcvad) instead of running the full Vosk recognizer continuously.

    Flow: a low-cost VAD stream runs always-on, watching for voice
    activity near the mic. Only once real speech is detected does this
    hand off to the existing Vosk phrase-matching logic
    (STTManager.listen_once) for a single bounded utterance, checking
    whether a configured wake phrase was said. If not, it goes back to
    VAD-only listening rather than continuing to run Vosk. This is what
    makes "always listening" compatible with a low idle CPU budget —
    Vosk only runs in short bursts triggered by real speech, not
    continuously (see the Phase 16 overlay plan's efficiency budget).

    Not a trained acoustic wake-word model (openWakeWord has no
    pretrained "Fred" phrase, and training a custom one is a separate,
    larger undertaking — deferred as a future follow-up). This still
    listens for short utterances and checks if any configured phrase
    appears in what was said; loose by design, trading precision for
    "always responsive."

    Responsibilities:
    - Continuously (but cheaply) watch for speech via VAD
    - On speech, check a short utterance against WAKE_PHRASES
    - Block until a wake phrase is heard, then return control to the caller
    """

    FRAME_MS = 30  # webrtcvad only accepts 10/20/30ms frames
    VAD_AGGRESSIVENESS = 2  # 0 (least aggressive/most sensitive) - 3 (most aggressive)
    SPEECH_FRAMES_TO_TRIGGER = 3  # consecutive voiced frames before handing off to Vosk
    POST_TRIGGER_LISTEN_TIMEOUT = 10  # seconds, passed straight to STTManager.listen_once

    def __init__(self, stt: STTManager = None):

        # Accepts an existing STTManager so the Vosk model isn't loaded
        # twice when the caller already has one for command capture.
        self.stt = stt or STTManager()
        self.phrases = [p.lower().strip() for p in WAKE_PHRASES]

        self.samplerate = STT_SAMPLE_RATE
        self.frame_size = int(self.samplerate * self.FRAME_MS / 1000)
        self.vad = webrtcvad.Vad(self.VAD_AGGRESSIVENESS)

    def listen_for_wake_word(self):
        """
        Blocks until a wake phrase is heard. Internally loops: cheap VAD
        gating until speech is detected, then a bounded Vosk phrase
        check, repeating if that utterance didn't contain a wake phrase.
        """

        while True:

            self._wait_for_speech()

            heard = self.stt.listen_once(timeout=self.POST_TRIGGER_LISTEN_TIMEOUT)

            if self._matches_wake_phrase(heard):
                return

    # =========================================================
    # VAD GATE
    # =========================================================

    def _wait_for_speech(self):
        """
        Blocks until webrtcvad detects SPEECH_FRAMES_TO_TRIGGER
        consecutive voiced frames from the microphone. Sub-1% CPU —
        this is the always-on component, not the Vosk phrase check.
        """

        triggered = threading.Event()
        consecutive = {"count": 0}

        def callback(indata, frames, time_info, status):

            if status:
                print(f"[WakeWord VAD] {status}")

            if triggered.is_set():
                return

            frame_bytes = indata.astype(np.int16).tobytes()

            try:
                is_speech = self.vad.is_speech(frame_bytes, self.samplerate)
            except Exception:
                is_speech = False

            if is_speech:
                consecutive["count"] += 1
                if consecutive["count"] >= self.SPEECH_FRAMES_TO_TRIGGER:
                    triggered.set()
            else:
                consecutive["count"] = 0

        with sd.InputStream(
            samplerate=self.samplerate,
            channels=1,
            dtype="int16",
            blocksize=self.frame_size,
            callback=callback,
        ):
            triggered.wait()

    # =========================================================
    # PHRASE MATCHING (unchanged from the original implementation)
    # =========================================================

    def _matches_wake_phrase(self, text: str) -> bool:

        if not text:
            return False

        text = text.lower().strip()
        words = re.findall(r"\b\w+\b", text)

        for phrase in self.phrases:
            if " " in phrase:
                if phrase in text:
                    return True
            elif phrase in words:
                return True

        return False
