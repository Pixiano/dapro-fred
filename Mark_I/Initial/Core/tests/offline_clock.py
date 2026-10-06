"""P2 gate harness: the interaction clock on labelled, Kokoro-spoken recordings, through the real Mic -> Vad -> Capture -> TurnController path (real Smart Turn).

  python tests/offline_clock.py
Spoken material is invented (Kokoro, 4 voices). Cases and labels:
  complete   one sentence, then silence                       expected: exactly 1 turn, ending after the speech; metric = wait from the last word to END_OF_TURN
  pause      clause A, a pause of 0.4 / 0.7 / 1.0 s, clause B  expected: ONE turn containing both; a "false cutoff" is an END_OF_TURN before clause B started
  backchannel  a 0.15-0.25 s interjection while FRED is speaking     expected: BACKCHANNEL, nothing cancelled
  barge-in   a 1 s interruption while FRED is speaking          expected: BARGE_IN
  unaddressed speech with no wake word / hotkey                 expected: IGNORE not_addressed
Compared end-of-turn rules: A = VAD silence only (200 ms), B = + Smart Turn 0.5 (the planned rule), C = hard cap only (1.2 s).
Reports counts, rates and waits; no personal audio is involved.
"""
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import events as ev
from audio.mic import Mic
from audio.stt import Capture
from audio.vad import Vad
from audio.wav import to16k
from clock.smart_turn import SmartTurn
from clock.turn_controller import TurnController

KOKORO = Path(__file__).resolve().parents[4] / "Old" / "Core" / "models" / "kokoro"
VOICES = ["am_michael", "af_bella", "bf_emma", "am_adam"]
PAIRS = [("Set a timer for", "five minutes."), ("What's on my agenda", "for tomorrow afternoon?"), ("Turn the volume down", "to thirty percent."),
         ("Open the budget spreadsheet", "and read me the first three rows."), ("Remind me to call the dentist", "on Thursday morning.")]
SR = 16000


class Always:
    def prob(self, a):
        return 1.0


def trim(a):
    nz = np.where(np.abs(a) > 400)[0]
    return a[nz[0]:nz[-1]]


_rng = np.random.default_rng(11)


def sil(s):
    """Silence with a room noise floor (std 25 int16 = about -62 dBFS). Digital zeros are not what a mic hears, and Smart Turn reads them as 'the end' (0.63 -> 0.98 within 0.2 s of zeros)."""
    return (_rng.normal(size=int(SR * s)) * 25).astype(np.int16)


def build(parts):
    """parts: list of (kind, array|seconds). Returns (audio, marks): marks = speech start / end times (s) of each spoken part."""
    x, marks, t = [], [], 0.0
    for kind, v in parts:
        a = v if kind == "speech" else sil(v)
        if kind == "speech":
            marks.append((t, t + len(a) / SR))
        x.append(a)
        t += len(a) / SR
    return np.concatenate(x), marks


def run(audio, mode, wake=True, tts=False, tts_at=None):
    bus, log = ev.EventBus(), []
    mic = Mic(bus, device="")
    vad = Vad(bus)
    vad.attach(mic)                                    # subscribed before the clock: VAD events land first within a chunk
    cap = Capture(mic)
    smart = {"A": Always(), "B": SMART, "C": None}[mode]
    tc = TurnController(bus, cap, smart, None, mic=mic, vad=vad, on_turn=lambda a, i: log.append(("turn", tc._now - T0, i)))
    bus.subscribe(lambda e: log.append((e.data["state"], tc._now - T0, e.data)), sources={"clock"})
    if tts:
        bus.emit("tts", "start")
    T0 = 1000.0
    lead = sil(0.5)
    tc._now = T0                                       # replay time starts at T0 (live, the clock follows the mic's timestamps)
    if wake:
        bus.emit("audio", "wake")
    mic.feed_array(np.concatenate([lead, audio, sil(2.5)]), ts=T0)
    tc.close()
    return [(k, round(t - 0.5, 2), d) for k, t, d in log if k != "turn"]


def main():
    global SMART
    from kokoro_onnx import Kokoro
    k = Kokoro(str(KOKORO / "kokoro-v1.0.onnx"), str(KOKORO / "voices-v1.0.bin"))
    SMART = SmartTurn()
    SMART.prob(sil(1))
    speak = lambda text, v: trim(to16k(*k.create(text, voice=v, speed=1.0, lang="en-us")[:2]))
    cl = {(a, v): speak(a, v) for a, _ in PAIRS for v in VOICES}
    cl.update({(b, v): speak(b, v) for _, b in PAIRS for v in VOICES})
    full = {(a + " " + b, v): speak(a + " " + b, v) for a, b in PAIRS for v in VOICES}
    modes = ["A", "B", "C"]
    print(f"{len(PAIRS) * len(VOICES)} sentences x {len(modes)} rules; Kokoro voices {VOICES}\n")

    print("complete sentences: turns found (expect 1) and wait from last word to END_OF_TURN")
    for m in modes:
        waits, wrong = [], 0
        for (s, v), a in full.items():
            audio, marks = build([("speech", a)])
            ev_ = run(audio, m)
            ends = [t for kind, t, d in ev_ if kind == "END_OF_TURN"]
            if len(ends) != 1:
                wrong += 1
            else:
                waits.append(ends[0] - marks[-1][1])
        print(f"  rule {m}: wrong turn count {wrong}/{len(full)}  wait median {np.median(waits):.2f}s  p90 {np.percentile(waits, 90):.2f}s  max {max(waits):.2f}s")

    def split(x):
        """Cut one continuously spoken sentence at its quietest 60 ms between 35% and 65%: a pause inside the sentence, mid-sentence intonation on both sides."""
        w, lo, hi = int(0.06 * SR), int(len(x) * 0.35), int(len(x) * 0.65)
        e = np.convolve(np.abs(x[lo:hi].astype(np.float32)), np.ones(w) / w, mode="valid")
        c = lo + int(np.argmin(e)) + w // 2
        return x[:c], x[c:]

    variants = (("one sentence cut at a word gap (natural mid-sentence prosody)", lambda a, b, v: split(full[(a + " " + b, v)])),
                ("two clauses each spoken alone (the first has final intonation)", lambda a, b, v: (cl[(a, v)], cl[(b, v)])))
    for label, mk in variants:
        print(f"\nmid-sentence pause, {label}: false cutoffs = END_OF_TURN before the rest started")
        for g in (0.4, 0.7, 1.0):
            row = []
            for m in modes:
                fc = tot = 0
                for a, b in PAIRS:
                    for v in VOICES:
                        p1, p2 = mk(a, b, v)
                        audio, marks = build([("speech", p1), ("sil", g), ("speech", p2)])
                        ev_ = run(audio, m)
                        ends = [t for kind, t, d in ev_ if kind == "END_OF_TURN"]
                        tot += 1
                        fc += bool(ends) and ends[0] < marks[1][0]
                row.append(f"{m}: {fc}/{tot} ({fc / tot:.0%})")
            print(f"  pause {g:.1f}s   " + "   ".join(row))

    print("\nwhile FRED speaks (rule B):")
    bc = br = 0
    v = VOICES[0]
    for kind, secs, want in (("backchannel", 0.15, "BACKCHANNEL"), ("backchannel", 0.2, "BACKCHANNEL"), ("backchannel", 0.25, "BACKCHANNEL"), ("interrupt", 1.0, "BARGE_IN")):
        hit = 0
        for a, _ in PAIRS:
            seg = full[(a + ' ' + [b for x, b in PAIRS if x == a][0], v)][:int(SR * secs)]
            audio, _m = build([("speech", seg)])
            ev_ = run(audio, "B", tts=True)
            hit += any(kk == want for kk, _, _ in ev_)
        print(f"  {kind:11} {secs:.2f}s speech -> {want}: {hit}/{len(PAIRS)}")
    ign = 0
    for a, b in PAIRS:
        audio, _m = build([("speech", full[(a + ' ' + b, v)])])
        ev_ = run(audio, "B", wake=False)
        ign += any(kk == "IGNORE" and d.get("reason") == "not_addressed" for kk, _, d in ev_)
    print(f"\nno wake word / hotkey -> IGNORE not_addressed: {ign}/{len(PAIRS)}")


if __name__ == "__main__":
    main()
