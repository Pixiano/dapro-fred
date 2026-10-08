"""The interaction clock (L1): decides whose turn it is. One tick every `clock_tick_s` (160 ms), one output state at a time.

States (bus events, source "clock", kind "state"): LISTEN, ADDRESSED, END_OF_TURN, BARGE_IN, BACKCHANNEL, IGNORE.
Sockets
  in : TurnController(bus, capture, smart_turn=None, voice=None, mic=None)
         bus events consumed: audio.vad {speaking, ts}, audio.wake, audio.hotkey {down}, tts.start / tts.end (from P4; until then call tts_active()),
         mic chunks (when `mic` is given the clock ticks itself from chunk timestamps, so a replay is deterministic); or call tick(now) yourself.
         capture: object with begin(preroll_s) / snapshot() / end() -> int16 audio  (audio.stt.Capture), started at speech start, ended at the turn's end
         vad (optional, audio.vad.Vad): its last_voice_ts lets barge-in measure real voiced time instead of time since onset (which includes the VAD hangover, so a 250 ms
         "mm-hm" would otherwise look like 450 ms of speech)
         smart_turn.prob(audio) -> float, voice.is_me(audio) -> bool | None (None = unknown, counts as a pass), headphones() -> bool via attribute `headphones`
  out: clock.state {state, reason, ...}; on_ignored(audio, reason) for each ignored utterance; on_turn(audio, info) callback for END_OF_TURN (the thinker, or the echo stand-in); stats()
Rules (all thresholds are settings):
  END_OF_TURN  VAD silence >= `eot_silence_ms` (200) AND Smart Turn >= `turn_prob_threshold` (0.5), OR silence >= `eot_hard_cap_s` (0.9; the self-check overrides it to 1.2), OR the hotkey chord released
               (while the chord is held only release ends the turn), OR the utterance hit `max_utterance_s`. A turn that nobody addressed (no hotkey, no wake word in the
               `wake_open_s` before or during it, not inside the `followup_s` window after FRED spoke) ends as IGNORE reason "not_addressed"; a voice that is not Vatsal's
               (voice ID False, not for hotkey turns) ends as IGNORE reason "not_vatsal". Voice unknown (None) passes: never reject on a missing enrolment.
  BARGE_IN     while FRED speaks: speech >= `barge_ms` (300) AND voice ID not False AND (headphones OR mic level >= `barge_level`): emitted once, the utterance then
               continues as a normal turn. Speech that ends before `barge_ms` while FRED speaks is a BACKCHANNEL ("mm-hm"): nothing is cancelled.
  LISTEN       an utterance started (not yet known to be for FRED: the UI must not react to it).
  ADDRESSED    once per utterance, when it is addressed (hotkey at once; wake word or follow-up window after `listen_show_s`): the pill and HUD show "listening" from here.
Echo: with headphones nothing is needed; on speakers the level gate stands in until voice ID is enrolled and the real echo floor is measured (P4).
Self-check: python clock/turn_controller.py (scripted VAD / Smart Turn / voice, simulated time).  Real audio: tests/offline_clock.py
"""
import sys
import threading
from pathlib import Path

import numpy as np

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import events as ev

LISTEN, END_OF_TURN, BARGE_IN, BACKCHANNEL, IGNORE, ADDRESSED = "LISTEN", "END_OF_TURN", "BARGE_IN", "BACKCHANNEL", "IGNORE", "ADDRESSED"


class TurnController:
    def __init__(self, bus: ev.EventBus, capture, smart_turn=None, voice=None, mic=None, on_turn=None, headphones: bool = True, vad=None, on_ignored=None):
        self.bus, self.capture, self.smart, self.voice, self.on_turn, self.headphones, self._vad_src = bus, capture, smart_turn, voice, on_turn, headphones, vad
        self.on_ignored = on_ignored                                  # (audio, reason) for every utterance the clock ignores: usage data
        self._lock = threading.RLock()
        self._now = 0.0
        self._last_tick = -1e9
        self.reset()
        self._unsubs = [bus.subscribe(self._on_event, sources={"audio", "tts"})]
        if mic is not None:
            self._unsubs.append(mic.subscribe(self._on_chunk))
        self.counts = {"turns": 0, "ignored": 0, "barge_in": 0, "backchannel": 0}

    def close(self):
        for u in self._unsubs:
            u()

    def reset(self):
        self.active = False            # an utterance is being collected
        self.speaking = False          # VAD says speech right now
        self.t_start = self.t_voice = 0.0
        self.hotkey = False
        self.hotkey_turn = False
        self._announced = False
        self.wake_ts = -1e9
        self.tts_on = False
        self.open_until = -1e9
        self.barged = False
        self.level = 0.0

    # ---- inputs -------------------------------------------------------
    def _on_event(self, e):
        k = (e.source, e.kind)
        d = e.data
        with self._lock:
            if k == ("audio", "vad"):
                self.vad(bool(d["speaking"]), d.get("ts", self._now))
            elif k == ("audio", "wake"):
                self.wake_ts = self._now
            elif k == ("audio", "hotkey"):
                self.hotkey_event(bool(d["down"]), self._now)
            elif k == ("tts", "start"):
                self.tts_on = True
            elif k == ("tts", "end"):
                self.tts_on = False
                self.open_until = self._now + config.get().followup_s

    def _on_chunk(self, chunk, ts):
        with self._lock:
            self._now = ts
            n = len(chunk)
            self.level = 0.7 * self.level + 0.3 * float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2))) if n else self.level
            if ts - self._last_tick >= config.get().clock_tick_s - 1e-6:
                self._last_tick = ts
                self.tick(ts)

    def vad(self, speaking: bool, ts: float):
        c = config.get()
        if speaking:
            self.speaking = True
            self.t_voice = ts
            if not self.active:
                self.active, self.t_start, self.barged, self._announced = True, ts, False, False
                self.hotkey_turn = self.hotkey
                self.capture.begin(0.5)
                self._emit(LISTEN, reason="speech_start")
        else:
            self.speaking = False
            self.t_voice = ts - c.vad_hangover_ms / 1000.0       # the VAD reports the end after its hangover: the last voiced audio was that much earlier
            if self.active and self.tts_on and not self.barged and self.t_voice - self.t_start < c.barge_ms / 1000.0:
                self._finish_silently(BACKCHANNEL, "short_speech_while_fred_speaks", "backchannel")

    def hotkey_event(self, down: bool, now: float):
        self.hotkey = down
        if down and not self.active:
            self.hotkey_turn = True
        if not down and self.active and self.hotkey_turn:
            self._end(now, "chord_release", None)

    def tick(self, now: float):
        with self._lock:
            self._now = max(self._now, now)
            if not self.active:
                return
            c = config.get()
            if not self._announced and (self.hotkey_turn or now - self.t_start >= c.listen_show_s) and self._addressed(now):
                self._announced = True
                self._emit(ADDRESSED)
            if self.speaking:
                voiced = (self._vad_src.last_voice_ts if self._vad_src is not None else now) - self.t_start
                if self.tts_on and not self.barged and voiced >= c.barge_ms / 1000.0:
                    self._maybe_barge(now)
                if now - self.t_start >= c.max_utterance_s:
                    self._end(now, "max_length", None)
                return
            if self.hotkey_turn and self.hotkey:                 # chord held: only its release ends the turn
                return
            silence = now - self.t_voice
            if silence < c.eot_silence_ms / 1000.0:
                return
            p = None
            if self.smart is not None:
                try:
                    p = self.smart.prob(self.capture.snapshot())
                except Exception as ex:                          # a broken model must not wedge the clock: the silence rules still end the turn
                    p = None
                    self.bus.emit("clock", "error", error=repr(ex))
                if p is not None and p >= c.turn_prob_threshold:
                    return self._end(now, "silence+smart_turn", p)
            if silence >= c.eot_hard_cap_s:
                self._end(now, "hard_cap", p)

    # ---- decisions ----------------------------------------------------
    def _maybe_barge(self, now: float):
        c = config.get()
        if not (self.headphones or self.level >= c.barge_level):
            return
        if self.voice is not None and c.voice_id_enabled and self.voice.is_me(self.capture.snapshot()) is not True:
            return                                                  # not Vatsal, or too short to tell yet: FRED keeps talking (asked again every tick)
        self.barged = True
        self.counts["barge_in"] += 1
        self._emit(BARGE_IN, speech_ms=int((now - self.t_start) * 1000))

    def _addressed(self, now: float) -> bool:
        c = config.get()
        last = max(self.t_voice, self._vad_src.last_voice_ts if self._vad_src is not None else 0.0)
        followup = now <= self.open_until and last - self.t_start >= c.followup_min_s           # noise right after FRED spoke is not an answer
        return self.hotkey_turn or self.wake_ts >= self.t_start - c.wake_open_s or followup

    def _end(self, now: float, reason: str, p):
        audio = self.capture.end()
        info = {"reason": reason, "audio_s": round(len(audio) / config.get().sample_rate, 2), "turn_prob": None if p is None else round(float(p), 3)}
        hk, addressed = self.hotkey_turn, self._addressed(now)
        self.active = self.speaking = False
        self.hotkey_turn = False
        if not addressed:
            return self._ignored("not_addressed", audio)
        if self.voice is not None and not hk and self.voice.is_me(audio) is False:
            return self._ignored("not_vatsal", audio)
        self.counts["turns"] += 1
        self._emit(END_OF_TURN, **info)
        if self.on_turn:
            self.on_turn(audio, info)

    def _ignored(self, reason: str, audio=None):
        self.counts["ignored"] += 1
        if self.on_ignored and audio is not None:
            self.on_ignored(audio, reason)
        self._emit(IGNORE, reason=reason)

    def _finish_silently(self, state: str, reason: str, count: str):
        self.capture.end()
        self.active = False
        self.counts[count] += 1
        self._emit(state, reason=reason)

    def _emit(self, state: str, **data):
        self.bus.emit("clock", "state", state=state, **data)

    def stats(self) -> dict:
        return dict(self.counts)


def _selfcheck():
    c = config.get()
    c.vad_hangover_ms, c.eot_silence_ms, c.eot_hard_cap_s, c.barge_ms, c.followup_s, c.wake_open_s = 200, 200, 1.2, 300, 8.0, 8.0
    bus, states, turns = ev.EventBus(), [], []
    addr = []
    bus.subscribe(lambda e: (addr if e.data["state"] == ADDRESSED else states).append((e.data["state"], e.data.get("reason"))), sources={"clock"})

    class Cap:
        def __init__(self):
            self.n = 0
        def begin(self, p):
            self.n = 16000
        def snapshot(self):
            return np.ones(self.n, np.int16)
        def end(self):
            a, self.n = self.snapshot(), 0
            return a

    class Smart:
        p = 0.9
        def prob(self, a):
            return self.p

    class Voice:
        me = True
        def is_me(self, a):
            return self.me

    cap, sm, vo = Cap(), Smart(), Voice()
    t = TurnController(bus, cap, sm, vo, on_turn=lambda a, i: turns.append(i))

    def say(t0, t1, **kw):                                       # speech from t0 to t1 (VAD end arrives t1 + 0.2 s later)
        t.vad(True, t0)
        t.tick(t0 + 0.16)
        t.vad(False, t1 + 0.2)

    def run(t_from, t_to):                                       # ticks every 160 ms
        x = t_from
        while x <= t_to:
            t.tick(x)
            x += 0.16

    # 1. addressed by wake word, Smart Turn says done -> END_OF_TURN right after the 200 ms of silence
    t._now = 10.0
    t.wake_ts = 10.0
    say(10.0, 11.5)
    run(11.7, 12.4)
    assert states[-1][0] == END_OF_TURN and turns[-1]["reason"] == "silence+smart_turn", states
    # 2. mid-sentence pause: Smart Turn says not done -> keeps waiting, then the 1.2 s hard cap ends it
    states.clear(); turns.clear()
    t.wake_ts = 20.0
    sm.p = 0.1
    say(20.0, 21.0)
    run(21.2, 22.0)
    assert states == [(LISTEN, "speech_start")] and not turns, states        # 0.8 s of silence, still waiting
    run(22.0, 22.6)
    assert turns and turns[-1]["reason"] == "hard_cap", (states, turns)
    # 3. speech resumes before the end: same utterance continues
    states.clear(); turns.clear(); sm.p = 0.1
    t.wake_ts = 30.0
    say(30.0, 31.0)
    run(31.2, 31.6)
    t.vad(True, 31.8)
    t.vad(False, 33.0)
    sm.p = 0.9
    run(33.2, 33.6)
    assert [s for s, _ in states].count(LISTEN) == 1 and len(turns) == 1, states
    # 4. nobody addressed it (no wake, no hotkey, no follow-up window) -> IGNORE
    states.clear(); turns.clear(); addr.clear()
    say(100.0, 101.0)
    run(101.2, 101.6)
    assert states[-1] == (IGNORE, "not_addressed") and not turns and not addr          # an ignored utterance never announces itself (the pill stays hidden)
    # 5. wake word 3 s BEFORE the speech starts still addresses it
    states.clear(); turns.clear(); addr.clear()
    t.wake_ts = 197.0
    say(200.0, 201.0)
    run(201.2, 201.6)
    assert states[-1][0] == END_OF_TURN and len(addr) == 1                                # addressed: announced once
    # 6. not Vatsal's voice -> IGNORE not_vatsal; unknown voice (None) passes
    states.clear(); turns.clear()
    t.wake_ts = 300.0
    vo.me = False
    say(300.0, 301.0)
    run(301.2, 301.6)
    assert states[-1] == (IGNORE, "not_vatsal") and not turns
    states.clear()
    vo.me = None
    t.wake_ts = 310.0
    say(310.0, 311.0)
    run(311.2, 311.6)
    assert states[-1][0] == END_OF_TURN
    vo.me = True
    # 7. hotkey: no early end during a pause while held, ends on release, no wake word needed, voice check skipped
    states.clear(); turns.clear()
    vo.me = False
    t.hotkey_event(True, 400.0)
    say(400.5, 401.0)
    run(401.2, 403.0)
    assert not turns, states                                          # chord held: even 2 s of silence does not end it
    t.hotkey_event(False, 403.1)
    assert turns and turns[-1]["reason"] == "chord_release", states
    vo.me = True
    # 8. FRED speaking: a 150 ms "mm-hm" is a BACKCHANNEL; 400 ms of speech is a BARGE_IN (once), then it continues as a turn
    states.clear(); turns.clear()
    bus.emit("tts", "start")
    t.vad(True, 500.0)
    t.tick(500.1)
    t.vad(False, 500.35)                                              # 350 ms between onset and the VAD end event (incl. 200 ms hangover) -> 150 ms speech
    assert states[-1] == (BACKCHANNEL, "short_speech_while_fred_speaks") and not turns
    states.clear()
    t.vad(True, 510.0)
    t.tick(510.16)
    assert BARGE_IN not in [s for s, _ in states]
    t.tick(510.32)
    t.tick(510.48)
    assert [s for s, _ in states].count(BARGE_IN) == 1, states
    bus.emit("tts", "end")
    t.vad(False, 511.5)
    t.wake_ts = 511.0
    run(511.7, 512.2)
    assert turns and states[-1][0] == END_OF_TURN
    # 9. speakers (no headphones): quiet mic level blocks a barge-in, enough level allows it
    states.clear(); t.headphones, t.level = False, 100.0
    bus.emit("tts", "start")
    t.vad(True, 600.0)
    t.tick(600.4)
    assert BARGE_IN not in [s for s, _ in states]
    t.level = 5000.0
    t.tick(600.56)
    assert BARGE_IN in [s for s, _ in states]
    t.active = t.speaking = False
    bus.emit("tts", "end")
    # 9b. voice ID on: an unknown or foreign voice never cuts FRED off; Vatsal's does
    config.get().voice_id_enabled, t.headphones, keep = True, True, vo.me
    for who, cuts, t0 in ((None, False, 620.0), (False, False, 630.0), (True, True, 640.0)):
        vo.me = who
        states.clear()
        bus.emit("tts", "start")
        t.vad(True, t0)
        t.tick(t0 + 0.7)
        assert (BARGE_IN in [s for s, _ in states]) == cuts, (who, states)
        t.active = t.speaking = False
        bus.emit("tts", "end")
    vo.me = keep
    # 10. follow-up window: speech within 8 s after FRED spoke needs no wake word
    states.clear(); turns.clear()
    t._now = 700.0
    bus.emit("tts", "start"); bus.emit("tts", "end")
    say(703.0, 704.0)
    run(704.2, 704.6)
    assert states[-1][0] == END_OF_TURN, states
    states.clear()
    say(720.0, 721.0)                                                 # window long over
    run(721.2, 721.6)
    assert states[-1] == (IGNORE, "not_addressed")
    # 11. a failing Smart Turn model must not wedge the clock: the hard cap still ends the turn
    states.clear(); turns.clear()
    sm.prob = lambda a: 1 / 0
    t.wake_ts = 800.0
    say(800.0, 801.0)
    run(801.2, 802.4)
    assert turns and turns[-1]["reason"] == "hard_cap", (states, turns)
    config.reload()
    print("turn_controller ok")


if __name__ == "__main__":
    _selfcheck()
