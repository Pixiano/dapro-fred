"""The voice loop without the voice: microphone -> wake/VAD -> turn clock -> Whisper -> engine -> reply printed (TTS arrives in P4).

  python engine/live.py [seconds=120] [--memory] [--brain MODEL]
Say "Hey FRED, <request>" or hold Left Ctrl + Left Alt and talk. Each turn prints what was heard, FRED's reply and the trace line (route, tools, timings).
Sockets
  in : build(bus, mic, memory=False, brain=None) -> Runtime     wires Mic/Vad/Wake/Capture/Stt/SmartTurn/VoiceId/TurnController to an Engine
       Runtime.engine / .controller / .stt / .replies (every printed reply, for tests) / .close()
  out: bus events as before plus engine.turn / engine.tool / engine.say; `tts.start` / `tts.end` are emitted by P4 (barge-in is already wired in the controller)
A turn that Whisper hears as nothing (silence, noise) is dropped without calling the engine. Turns run on a worker thread: the clock keeps ticking while the engine thinks,
and the engine serialises turns itself.
Self-check: tests/replay_voice.py feeds a synthesized utterance through this wiring (no microphone).
"""
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

if __name__ == "__main__":
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
from engine import prompt
from engine.cli import trace
from engine.loop import Engine


@dataclass
class Runtime:
    engine: Engine
    controller: TurnController
    stt: Stt
    mic: Mic
    bus: ev.EventBus
    replies: list = field(default_factory=list)
    _closers: list = field(default_factory=list)

    def close(self):
        for fn in reversed(self._closers):
            try:
                fn()
            except Exception:
                pass


def build(bus, mic, memory: bool = False, brain: str | None = None, chat=None, embedder=None, stt=None, smart=None, wake: bool = True, out=print) -> Runtime:
    """`chat`/`embedder`/`stt`/`smart` can be injected (tests); by default the playground's Chat/Embedder, real Whisper and Smart Turn."""
    from models.client import Chat, Embedder
    from tools.builtin import build_registry
    c = config.get()
    chat, embedder = chat or Chat(brain or c.brain), embedder or Embedder()
    closers, recall = [], None
    if memory:
        from memory_bridge.feed import Feed
        from memory_bridge.link import open_memory
        from memory_bridge.turn_recall import MemoryRecall
        mem = open_memory()
        recall, feed = MemoryRecall(mem, embedder, chat, c.embedder), Feed(mem, bus)
        feed.start()
        closers += [mem.close, feed.stop]
    engine = Engine(chat, build_registry(recall=recall), embedder, prompt.build_system(), bus=bus, recall=recall, on_say=lambda t: out(f"FRED (interim): {t}"))
    if wake:
        WakeDetector(bus).attach(mic)
    vad = Vad(bus)
    vad.attach(mic)
    stt, smart, voice = stt or Stt(bus), smart or SmartTurn(), VoiceId()
    rt = Runtime(engine, None, stt, mic, bus, _closers=closers)

    def on_turn(audio, info):
        def work():
            heard = stt.transcribe(audio)
            if heard.silent or not heard.text.strip():
                return
            out(f"YOU: {heard.text}")
            r = engine.run(heard.text, source="voice")
            rt.replies.append(r)
            out(f"FRED: {r.text}")
            out(trace(r))
        threading.Thread(target=work, daemon=True).start()

    rt.controller = TurnController(bus, Capture(mic), smart, voice, mic=mic, vad=vad, on_turn=on_turn)
    return rt


def main(argv):
    from models.client import Chat, Embedder, Playground
    secs = next((float(a) for a in argv if a.replace(".", "").isdigit()), 120.0)
    brain = argv[argv.index("--brain") + 1] if "--brain" in argv else config.get().brain
    pg, loaded = Playground(), []
    if not pg.healthy():
        print("the playground is not running (python Mark_I/Initial/playground/server.py)")
        return 1
    for model, slot in ((brain, "chat"), (config.get().embedder, "embed")):
        if not (pg.status().get("slots") or {}).get(slot):
            print(f"loading {model} ...")
            pg.load(model, slot=slot)
            loaded.append(slot)
    bus = ev.EventBus()
    mic = Mic(bus)
    rt = build(bus, mic, memory="--memory" in argv, brain=brain)
    rt.stt.transcribe(__import__("numpy").ones(16000, "int16") * 1000, final=False)          # warm Whisper
    hk = Hotkey(bus)
    if not mic.start():
        print("could not open the microphone")
        return 1
    hk.start()
    print(f"listening for {secs:.0f} s: say 'Hey FRED, ...' or hold Left Ctrl + Left Alt. Ctrl+C to stop.")
    try:
        time.sleep(secs)
    except KeyboardInterrupt:
        pass
    hk.stop()
    mic.stop()
    time.sleep(1.5)
    rt.close()
    for s in loaded:
        pg.unload(s)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
