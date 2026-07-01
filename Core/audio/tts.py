# Core/audio/tts.py

import threading
from typing import Callable, Optional

import pythoncom
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
        text: str,
        on_word: Optional[Callable[[], None]] = None,
        on_end: Optional[Callable[[], None]] = None,
    ):
        """
        Non-blocking speech output.

        on_word/on_end are optional hooks for callers that want to react
        to playback (the overlay UI's speaking-state pulse animation) —
        both default to None so the CLI path is unaffected.
        """

        if not text.strip():
            return

        thread = threading.Thread(
            target=self._speak_internal,
            args=(text, on_word, on_end),
            daemon=True
        )

        thread.start()

    # =========================================================
    # INTERNAL SPEECH LOGIC
    # =========================================================

    def _speak_internal(
        self,
        text: str,
        on_word: Optional[Callable[[], None]] = None,
        on_end: Optional[Callable[[], None]] = None,
    ):

        with self.lock:

            # pyttsx3's SAPI backend is COM-based, and this always
            # runs on a freshly spawned thread — explicitly init COM
            # here rather than relying on it happening implicitly,
            # which silently fails ("CoInitialize has not been
            # called") when speak() is triggered from contexts like
            # the background scheduler.
            pythoncom.CoInitialize()

            try:

                engine = pyttsx3.init()

                self._apply_voice(engine)

                if on_word:
                    # SAPI reports word *boundaries*, not amplitude —
                    # pyttsx3 never exposes raw PCM for this backend, so
                    # this drives a per-word pulse rather than true
                    # waveform-reactive animation. Verified against the
                    # configured TTS_VOICE before relying on it — some
                    # SAPI voices don't emit these events reliably.
                    engine.connect(
                        "started-word",
                        lambda name, location, length: on_word()
                    )

                engine.say(text)
                engine.runAndWait()
                engine.stop()

            except Exception as e:

                print(
                    f"[TTS ERROR] {str(e)}"
                )

            finally:

                if on_end:
                    try:
                        on_end()
                    except Exception:
                        pass

                pythoncom.CoUninitialize()

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
