"""P2 live gate: the whole L0 + L1 chain on the real microphone, with the echo stand-in as the thinker: say "Hey FRED, ..." (or hold Left Ctrl + Left Alt and talk),
the clock decides when you are done, Whisper transcribes on the GPU, and the transcript is printed back as "ECHO: ...".

  python tests/live_clock.py [seconds=90]
Prints every clock state with its reason and timings (end of speech -> END_OF_TURN, END_OF_TURN -> transcript). With `stt_save_audio` on (default) each utterance and its
transcript are saved under <FRED_DATA>/voice_data/ (outside the repo), the live-mic data the voice ID enrolment and the turn-taking thresholds need.
Voice ID is used if somebody is enrolled; with nobody enrolled it passes everything (unknown, never "not you").
"""
import sys
import threading
import time

import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import events as ev
from audio.hotkey import Hotkey
from audio.mic import Mic
from audio.stt import Capture, Stt
from audio.vad import Vad
from audio.wakeword import WakeDetector
from clock.smart_turn import SmartTurn
from clock.turn_controller import TurnController
from clock.voice_id import VoiceId


def main():
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 90.0
    bus = ev.EventBus()
    t_end = {}
    bus.subscribe(lambda e: print(f"  {e.data['state']:13} {e.data.get('reason', '') or ''} {({k: v for k, v in e.data.items() if k not in ('state', 'reason')})}"), sources={"clock"})
    bus.subscribe(lambda e: print("  wake word", round(e.data.get("score", 0), 2)), kinds={"wake"}, sources={"audio"})
    mic = Mic(bus)
    WakeDetector(bus).attach(mic)
    vad = Vad(bus)
    vad.attach(mic)
    stt, voice, smart = Stt(bus), VoiceId(), SmartTurn()
    print(f"voice references enrolled: {voice.enrolled()}  (0 = voice gate passes everything)")
    smart.prob(np.zeros(16000, "int16"))                                               # load the model before the first turn
    stt.transcribe(np.ones(16000, "int16") * 1000, final=False)       # warm the GPU model before the first real turn
    cap = Capture(mic)

    def on_turn(audio, info):
        t0 = time.monotonic()

        def work():
            r = stt.transcribe(audio)
            print(f"ECHO: {r.text!r}   ({info['audio_s']} s audio, turn_prob {info['turn_prob']}, transcribed in {r.ms} ms, {(time.monotonic() - t0) * 1000:.0f} ms after the turn ended)")
        threading.Thread(target=work, daemon=True).start()

    tc = TurnController(bus, cap, smart, voice, mic=mic, vad=vad, on_turn=on_turn)
    hk = Hotkey(bus)
    if not mic.start():
        print("could not open the microphone")
        return 1
    hk.start()
    print(f"listening for {secs:.0f} s: say 'Hey FRED, <something>' or hold Left Ctrl + Left Alt and talk. Ctrl+C to stop.")
    try:
        time.sleep(secs)
    except KeyboardInterrupt:
        pass
    hk.stop()
    mic.stop()
    time.sleep(1.5)
    print("clock stats:", tc.stats())


if __name__ == "__main__":
    sys.exit(main())
