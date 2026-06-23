# Core/audio/wake_word.py

import numpy as np
import sounddevice as sd
from openwakeword.model import Model

from config.settings import WAKE_WORD, WAKE_WORD_THRESHOLD, STT_SAMPLE_RATE

_CHUNK_SAMPLES = 1280  # 80ms at 16kHz, openwakeword's expected frame size


class WakeWordListener:
    """
    Passive wake-word detection for F.R.E.D. — fully offline via
    openWakeWord (ONNX models run locally, no network).

    Responsibilities:
    - Continuously listen to the microphone
    - Detect the configured wake word ("hey jarvis" by default)
    - Block until heard, then return control to the caller
    """

    def __init__(self):

        self.model = Model(wakeword_models=[WAKE_WORD])
        self.samplerate = STT_SAMPLE_RATE
        self.threshold = WAKE_WORD_THRESHOLD

    def listen_for_wake_word(self):
        """
        Blocks until the wake word is detected.
        """

        with sd.InputStream(
            samplerate=self.samplerate,
            channels=1,
            dtype="int16",
            blocksize=_CHUNK_SAMPLES,
        ) as stream:

            while True:
                audio_chunk, _ = stream.read(_CHUNK_SAMPLES)
                audio_chunk = audio_chunk.flatten().astype(np.int16)

                predictions = self.model.predict(audio_chunk)

                for model_name, score in predictions.items():
                    if score >= self.threshold:
                        self.model.reset()
                        return
