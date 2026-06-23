#stt.py

import sounddevice as sd
import numpy as np
from vosk import Model, KaldiRecognizer
import json
import queue
import threading
import os
import time

class STT:
    def __init__(self, model_path="models/vosk-model-en-in-0.5", samplerate=16000, batch_size=5):
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"Model not found at {model_path}. Please download and unzip it.")

        print(f"[STT] Loading model from: {model_path}")
        self.model = Model(model_path)
        self.samplerate = samplerate
        self.recognizer = KaldiRecognizer(self.model, samplerate)
        self.q = queue.Queue()
        self.running = False
        self.batch_size = batch_size
        self._buffer = []

    def list_mics(self):
        print("[STT] Available audio input devices:")
        for i, dev in enumerate(sd.query_devices()):
            if dev['max_input_channels'] > 0:
                print(f"  {i}: {dev['name']} (Input channels: {dev['max_input_channels']})")

    def _callback(self, indata, frames, time_info, status):
        if status:
            print(f"[STT] Stream status: {status}")
        self._buffer.append(indata.astype(np.int16))
        if len(self._buffer) >= self.batch_size:
            data_bytes = np.concatenate(self._buffer).tobytes()
            self.q.put(data_bytes)
            self._buffer = []

    def listen_loop(self, device=None):
        """Continuously listens and prints recognized text with partial results."""
        self.running = True
        print("[STT] Starting listening loop...")
        try:
            with sd.InputStream(samplerate=self.samplerate,
                                channels=1,
                                dtype='int16',
                                device=device,
                                callback=self._callback):
                print("[STT] Listening... speak into your mic.")
                while self.running:
                    try:
                        data = self.q.get(timeout=1)
                    except queue.Empty:
                        continue
                    if self.recognizer.AcceptWaveform(data):
                        result = json.loads(self.recognizer.Result())
                        text = result.get("text", "")
                        if text.strip():
                            print(f"[STT] Recognized: {text}")
                    else:
                        partial = json.loads(self.recognizer.PartialResult()).get("partial", "")
                        if partial.strip():
                            print(f"[STT] Partial: {partial}", end="\r")
        except KeyboardInterrupt:
            print("\n[STT] Stopped by user.")
        except Exception as e:
            print(f"[STT] Error: {e}")

    def listen_once(self, device=None, timeout=10):
        """Listen once for a single phrase with batching and timeout."""
        self.running = True
        result_text = ""
        start_time = time.time()
        try:
            with sd.InputStream(samplerate=self.samplerate,
                                channels=1,
                                dtype='int16',
                                device=device,
                                callback=self._callback):
                while self.running:
                    if timeout and (time.time() - start_time) > timeout:
                        break
                    try:
                        data = self.q.get(timeout=0.5)
                    except queue.Empty:
                        continue
                    if self.recognizer.AcceptWaveform(data):
                        result = json.loads(self.recognizer.Result())
                        result_text = result.get("text", "")
                        break
        except Exception as e:
            print(f"[STT] Error: {e}")
        return result_text

if __name__ == "__main__":
    stt = STT()
    stt.list_mics()
    stt.listen_loop()