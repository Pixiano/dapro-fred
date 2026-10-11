"""P4 gate (no GPU, no audio device): the speaking side of engine/live.py with a scripted brain, fake Whisper and a fake speaker: a slow reply gets a filler first, a fast one does not,
a barge-in cancels the speech and the history keeps only the words spoken, interim lines are spoken.   python tests/test_voice_out.py"""
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import events as ev
from audio.mic import Mic
from engine import live
from models.fake import FakeChat, FakeEmbedder
from tools.builtin import build_registry


class Heard:
    silent, text = False, ""

    def __init__(self, text):
        self.text = text


class Stt:
    def __init__(self, texts): self.texts = list(texts)
    def transcribe(self, audio, final=True): return Heard(self.texts.pop(0))


class FakeSpeaker:
    def __init__(self): self.said, self.cancelled, self.block, self.quiet = [], 0, None, []
    def cancel(self): self.cancelled += 1
    def speak(self, text, cancel=None, quiet=False):
        self.said.append(text)
        quiet and self.quiet.append(text)
        if self.block and text == self.block:
            return "Half of"                                                          # a barge-in cut this reply off after its first words
        return text.strip()


def wait(cond, secs=5):
    t0 = time.time()
    while not cond() and time.time() - t0 < secs:
        time.sleep(0.02)
    assert cond(), "timed out"


def main():
    config.set_data_dir(tempfile.mkdtemp())
    config.get().filler_after_s = 0.3
    config.get().sir_rate = 0.0                                                                # the "sir" edge is tested at the end; the other asserts compare exact words
    bus, lines, sp = ev.EventBus(), [], FakeSpeaker()
    slow = lambda m, t: (time.sleep(0.8), "Slow answer here.")[1]
    chat = FakeChat([slow, "Quick one.", "Half of this long reply was never heard by anyone."])
    rt = live.build(bus, Mic(bus, device=""), chat=chat, embedder=FakeEmbedder(), stt=Stt(["first", "second", "third"]), smart=object(), wake=False, out=lines.append, registry=build_registry(), speaker=sp)
    turn = lambda: rt.controller.on_turn(np.zeros(16000, np.int16), {})
    turn()
    wait(lambda: "Slow answer here." in sp.said)
    from audio.tts import FILLERS
    assert sp.said[0] in FILLERS and sp.said[-1] == "Slow answer here.", sp.said              # slow: filler first, then the reply
    n = len(sp.said)
    turn()
    wait(lambda: sp.said[-1] == "Quick one.")
    time.sleep(0.5)
    assert len(sp.said) == n + 1, sp.said[n:]                                                  # fast: no filler
    sp.block = "Half of this long reply was never heard by anyone."
    turn()
    wait(lambda: sp.said[-1] == sp.block)
    time.sleep(0.2)
    assert rt.engine.history[-1] == {"role": "assistant", "content": "Half of"}, rt.engine.history[-1]   # only the spoken words are remembered
    bus.emit("clock", "state", state="BARGE_IN")
    assert sp.cancelled == 1
    bus.emit("clock", "state", state="LISTEN")
    assert sp.cancelled == 1
    rt.engine._say("Digging deeper.")
    wait(lambda: "Digging deeper." in sp.said)                                                 # interim lines are spoken too
    chat.script.append("Typed answer.")                                                        # a typed message runs the same turn as a spoken one
    rt.handle_text("typed hello")
    assert sp.said[-1] == "Typed answer." and any(l == "YOU: typed hello" for l in lines)
    # cancel: a turn still thinking stops, says nothing, is not remembered and the engine is free for the next one
    gate = threading.Event()

    def stuck(m, t):
        gate.wait(3)
        return "Too late."
    rt3 = live.build(bus, Mic(bus, device=""), chat=FakeChat([stuck, "After cancel.", "Third."]), embedder=FakeEmbedder(), stt=Stt([]), smart=object(), wake=False, out=lines.append, registry=build_registry(), speaker=sp)
    got = []
    th = threading.Thread(target=lambda: got.append(rt3.handle_text("long question")), daemon=True)
    th.start()
    time.sleep(0.2)
    n = len(sp.said)
    rt3.engine.cancel()
    gate.set()
    th.join(5)
    assert got == [""] and len(sp.said) == n and rt3.engine.history == [] and "(cancelled)" in lines, (got, rt3.engine.history)
    assert rt3.handle_text("next one") == "After cancel."                                      # the flag does not leak into the next turn
    rt3.engine.cancel()
    third = rt3.handle_text("hello there my friend")
    assert third == "Third.", third                                                # a cancel pressed while idle does not poison the next turn
    # the "sir" edge: spoken and printed with it, remembered without it, fillers and the interim line rules
    config.get().sir_rate = 1.0
    chat.script.append("Plain reply.")
    rt.handle_text("tell me something")
    assert sp.said[-1] == "Plain reply, sir." and "FRED: Plain reply, sir." in lines and rt.engine.history[-1]["content"] == "Plain reply."
    rt.engine._say("Digging deeper.")
    wait(lambda: sp.said[-1] == "Digging deeper, sir.")
    from audio.tts import FILLERS
    assert all("sir" not in f for f in FILLERS)
    # a reminder coming due is spoken (with the edge's "sir"), a missed one says so, a plain timer has its own line
    rt.bus.emit("proactive", "due", text="check the oven", what="reminder", late=False)
    wait(lambda: sp.said[-1] == "Reminder: check the oven, sir.")
    config.get().tts_enabled = False
    caps = []
    rt.bus.subscribe(lambda e: caps.append(e.data["text"]), kinds=("caption",), sources=("proactive",))
    rt.bus.emit("proactive", "due", text="silent one", what="reminder", late=False)
    assert caps and set(caps) == {"Reminder: silent one, sir."}, caps                       # no speech: the pill gets it
    config.get().tts_enabled = True
    rt.bus.emit("proactive", "due", text="", what="timer", late=True)
    wait(lambda: sp.said[-1] == "Sorry, I missed this one. Your timer is up, sir.")
    # a scam warning is spoken
    rt.bus.emit("proactive", "warn", scam="phishing", level="high", domain="x", brand="y", reasons=[], title="t", text="Careful, this page looks fake.")
    wait(lambda: sp.said[-1] == "Careful, this page looks fake, sir.")
    # the ask queue: an answer to the waiting question is consumed (not a turn); anything else goes on to the engine
    n = len(rt.engine.history)
    rt.ask = type("A", (), {"take": lambda s, t: "Noted." if t == "same person" else None})()
    rt.handle_text("same person")
    assert sp.said[-1] == "Noted, sir." and len(rt.engine.history) == n
    config.get().sir_rate = 0.0
    # lockdown swap: engage unloads the brain and the talker answers; release loads the brain back
    class Pg:
        calls = []
        def unload(self, slot): Pg.calls.append(("unload", slot))
        def load(self, model, slot, vision=False): Pg.calls.append(("load", slot, vision))
    talker = FakeChat(["Talker here."])
    rt2 = live.build(bus, Mic(bus, device=""), chat=FakeChat(), embedder=FakeEmbedder(), stt=Stt([]), smart=object(), wake=False, out=lines.append, registry=build_registry(), playground=Pg(), talk=talker)
    rt2.engine.run("lockdown", source="voice")
    assert Pg.calls == [("unload", "chat")] and rt2.engine.locked and rt2.engine.lock_chat is talker, Pg.calls
    rt2.engine._set_lockdown(False)
    assert Pg.calls[-1] == ("load", "chat", config.get().vision_shared and config.get().describe_model == config.get().brain)      # the brain comes back with its image projector when it also describes
    print("voice out ok")


if __name__ == "__main__":
    main()
