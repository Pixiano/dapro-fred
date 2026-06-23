# Core/audio/tts.py

import threading

import pyttsx3

from config.settings import TTS_VOICE


class TTSManager:
    """
    Text-to-speech system for F.R.E.D. — fully offline via the
    system's built-in SAPI voices (pyttsx3). No network, no API.

    Responsibilities:
    - Convert text into speech
    - Handle asynchronous playback
    - Prevent overlapping voice output
    """

    def __init__(self):

        self.voice = TTS_VOICE

        self.lock = threading.Lock()

    # =========================================================
    # PUBLIC SPEAK METHOD
    # =========================================================

    def speak(
        self,
        text: str
    ):
        """
        Non-blocking speech output.
        """

        if not text.strip():
            return

        thread = threading.Thread(
            target=self._speak_internal,
            args=(text,),
            daemon=True
        )

        thread.start()

    # =========================================================
    # INTERNAL SPEECH LOGIC
    # =========================================================

    def _speak_internal(
        self,
        text: str
    ):

        with self.lock:

            try:

                engine = pyttsx3.init()

                self._apply_voice(engine)

                engine.say(text)
                engine.runAndWait()
                engine.stop()

            except Exception as e:

                print(
                    f"[TTS ERROR] {str(e)}"
                )

    # =========================================================
    # VOICE SELECTION
    # =========================================================

    def _apply_voice(self, engine):
        """
        Selects a matching installed SAPI voice by name fragment,
        falling back to the engine default if no match is found.
        """

        if not self.voice:
            return

        for voice in engine.getProperty("voices"):
            if self.voice.lower() in voice.name.lower():
                engine.setProperty("voice", voice.id)
                return
