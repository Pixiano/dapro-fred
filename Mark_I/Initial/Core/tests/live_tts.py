"""Live TTS gate with the real Kokoro and NO audio device (a fake stream swallows the samples): time to first audio, real-time factor, and cancel latency.   python tests/live_tts.py"""
import sys
import threading
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from audio.tts import FILLERS, Speaker


class Sink:
    def __init__(self, rate):
        self.rate, self.n, self.first, self.t_cancel, self.t_stop = rate, 0, None, None, None

    def start(self): pass
    def write(self, a):
        self.n += len(a)
        self.first = self.first or time.time()
        time.sleep(len(a) / self.rate)                                       # real time: a real device blocks the same way
    def abort(self): self.t_stop = time.time()
    def stop(self): pass
    def close(self): pass


def main():
    config.get().tts_preroll_s = config.get().tts_postroll_s = 0
    sinks = []
    sp = Speaker(stream=lambda r: sinks.append(Sink(r)) or sinks[-1])
    t0 = time.time()
    sp.warm()
    print(f"load + warm {len(FILLERS)} fillers: {time.time() - t0:.1f} s")
    text = "Your next meeting is at three thirty with the design team. It is about the launch plan, and you have two unread emails from today."
    t0 = time.time()
    spoken = sp.speak(text)
    s = sinks[-1]
    secs = s.n / s.rate
    print(f"first audio after {s.first - t0:.2f} s; {secs:.1f} s of speech in {time.time() - t0:.1f} s total; spoken all: {spoken == text}")
    t0 = time.time()
    sp.speak("On it.")
    print(f"cached filler: first audio after {sinks[-1].first - t0:.3f} s")
    ev = threading.Event()
    t1 = []
    threading.Timer(1.0, lambda: (t1.append(time.time()), ev.set())).start()
    sp.speak(text + " " + text, cancel=ev)
    s = sinks[-1]
    lat = (s.t_stop - t1[0]) * 1000
    print(f"cancel latency: {lat:.0f} ms")
    return 0 if lat < 200 else 1


if __name__ == "__main__":
    sys.exit(main())
