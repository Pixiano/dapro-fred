# Core/audio/wake_word.py

import re

from audio.stt import STTManager
from config.settings import WAKE_PHRASES


class WakeWordListener:
    """
    Passive wake-word detection for F.R.E.D. — fully offline via
    Vosk transcription + text matching against WAKE_PHRASES.

    Not a trained acoustic wake-word model (openWakeWord only ships
    fixed pretrained phrases) — this listens for short utterances and
    checks if any configured phrase appears in what was said. Loose
    by design: short/common words in WAKE_PHRASES make this trigger
    easily, trading precision for "always responsive."

    Responsibilities:
    - Continuously listen to the microphone
    - Detect any configured wake phrase
    - Block until heard, then return control to the caller
    """

    def __init__(self, stt: STTManager = None):

        # Accepts an existing STTManager so the Vosk model isn't
        # loaded twice when the caller already has one for command
        # capture.
        self.stt = stt or STTManager()
        self.phrases = [p.lower().strip() for p in WAKE_PHRASES]

    def listen_for_wake_word(self):
        """
        Blocks until any wake phrase is heard.
        """

        while True:
            heard = self.stt.listen_once()

            if self._matches_wake_phrase(heard):
                return

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
