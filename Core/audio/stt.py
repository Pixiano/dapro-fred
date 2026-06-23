# Core/audio/stt.py

import queue
import json

import numpy as np
import sounddevice as sd

from vosk import Model, KaldiRecognizer

from config.settings import STT_MODEL_PATH, STT_SAMPLE_RATE


class STTManager:
    """
    Speech-to-text system for F.R.E.D. — fully offline via Vosk.

    Responsibilities:
    - Capture microphone input
    - Convert speech into text
    - Handle live voice listening
    """

    def __init__(
        self,
        model_path: str = None,
        samplerate: int = None
    ):

        model_path = str(model_path or STT_MODEL_PATH)
        samplerate = samplerate or STT_SAMPLE_RATE

        self.samplerate = samplerate

        self.audio_queue = queue.Queue()

        # -----------------------------
        # Load Vosk model
        # -----------------------------
        self.model = Model(model_path)

        self.recognizer = KaldiRecognizer(
            self.model,
            samplerate
        )

    # =========================================================
    # AUDIO CALLBACK
    # =========================================================

    def _audio_callback(
        self,
        indata,
        frames,
        time,
        status
    ):

        if status:
            print(
                f"[STT WARNING] {status}"
            )

        audio_data = (
            indata
            .astype(np.int16)
            .tobytes()
        )

        self.audio_queue.put(audio_data)

    # =========================================================
    # SINGLE LISTEN
    # =========================================================

    def listen_once(
        self,
        timeout: int = 10
    ) -> str:
        """
        Listen for a single spoken phrase.
        """

        result_text = ""

        try:

            with sd.InputStream(
                samplerate=self.samplerate,
                channels=1,
                dtype="int16",
                callback=self._audio_callback
            ):

                while True:

                    data = self.audio_queue.get()

                    if self.recognizer.AcceptWaveform(data):

                        result = json.loads(
                            self.recognizer.Result()
                        )

                        result_text = (
                            result.get("text", "")
                            .strip()
                        )

                        break

        except Exception as e:

            print(
                f"[STT ERROR] {str(e)}"
            )

        return result_text

    # =========================================================
    # LIST MICROPHONES
    # =========================================================

    def list_microphones(self):
        """
        Display available microphone devices.
        """

        devices = sd.query_devices()

        print("\nAvailable microphones:\n")

        for idx, device in enumerate(devices):

            if device["max_input_channels"] > 0:

                print(
                    f"{idx}: "
                    f"{device['name']}"
                )