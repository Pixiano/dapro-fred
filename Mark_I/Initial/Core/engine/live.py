"""The voice loop: microphone -> wake/VAD -> turn clock -> Whisper -> engine -> reply printed and spoken (Kokoro, audio/tts.py).

  python engine/live.py [seconds=120] [--memory] [--brain MODEL] [--silent] [--no-pill] [--no-hud]
Say "Hey FRED, <request>" or hold Left Ctrl + Left Alt and talk. Each turn prints what was heard, FRED's reply and the trace line (route, tools, timings).
Sockets
  in : build(bus, mic, memory=False, brain=None) -> Runtime     wires Mic/Vad/Wake/Capture/Stt/SmartTurn/VoiceId/TurnController to an Engine
       Runtime.engine / .controller / .stt / .replies (every printed reply, for tests) / .close()
  out: bus events as before plus engine.turn / engine.tool / engine.say; `tts.start` / `tts.end` come from the Speaker. `build(..., speaker=Speaker)` adds speech: a reply slower than filler_after_s gets a cached filler first, interim lines (engine.say) are spoken,
  the pill (ui/pill/controller.py) shows state and text and takes typed messages (Runtime.handle_text), lockdown swaps the 4B brain for the talker model (playground=...), a barge-in cancels the speech, and the history keeps only the words that were actually spoken; no speaker = text only
main() first makes the process per-monitor-v2 DPI aware (ui/pill/layered.py) so the pill is sharp on scaled displays; screen capture stays at native resolution.
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
from audio.tts import Speaker, pick_filler
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
    handle_text: object = None                  # handle_text(text): run a typed message exactly like a spoken turn

    def close(self):
        for fn in reversed(self._closers):
            try:
                fn()
            except Exception:
                pass


def build(bus, mic, memory: bool = False, brain: str | None = None, chat=None, embedder=None, stt=None, smart=None, wake: bool = True, out=print, registry=None, speaker=None, playground=None, talk=None) -> Runtime:
    """`chat`/`embedder`/`stt`/`smart` can be injected (tests); by default the playground's Chat/Embedder, real Whisper and Smart Turn."""
    from models.client import Chat, Embedder
    from skills.loader import build as build_skills
    c = config.get()
    chat, embedder = chat or Chat(brain or c.brain), embedder or Embedder()
    talk = talk or (Chat(c.talker, slot="talk") if c.talker and playground else None)
    closers, recall = [], None
    if memory:
        from memory_bridge.feed import Feed
        from memory_bridge.link import open_memory
        from memory_bridge.turn_recall import MemoryRecall
        mem = open_memory()
        recall, feed = MemoryRecall(mem, embedder, chat, c.embedder), Feed(mem, bus)
        feed.start()
        closers += [mem.close, feed.stop]
    def swap(locked):
        """Lockdown: the 4B brain steps out and the small talker is the only model while locked; the brain comes back on release (a few seconds, the unlock reply waits for it)."""
        if playground and talk:
            playground.unload("chat") if locked else playground.load(brain or c.brain, slot="chat")

    def say(t):
        out(f"FRED (interim): {t}")
        speaker and threading.Thread(target=speaker.speak, args=(t,), daemon=True).start()

    engine = Engine(chat, registry or build_skills(recall, mic=mic, bus=bus, embed=embedder.embed), embedder, prompt.build_system(), bus=bus, recall=recall, on_say=say, talk=talk, lock_chat=talk, on_lock=swap)
    if wake:
        WakeDetector(bus).attach(mic)
    vad = Vad(bus)
    vad.attach(mic)
    stt, smart, voice = stt or Stt(bus), smart or SmartTurn(), VoiceId()
    rt = Runtime(engine, None, stt, mic, bus, _closers=closers)
    if speaker:
        bus.subscribe(lambda e: e.data.get("state") == "BARGE_IN" and speaker.cancel(), kinds=("state",), sources=("clock",))

    def handle(text, source="voice"):
        """One turn from text (heard or typed): filler if slow, engine, print, speak."""
        out(f"YOU: {text}")
        slow = speaker and threading.Timer(c.filler_after_s, lambda: speaker.speak(pick_filler()))
        slow and slow.start()
        r = engine.run(text, source=source)
        slow and slow.cancel()
        if r.via == "cancelled":
            out("(cancelled)")
            return ""
        rt.replies.append(r)
        out(f"FRED: {r.text}")
        out(trace(r))
        if speaker:
            said = speaker.speak(r.text)
            h = engine.history
            if said != r.text.strip() and h and h[-1] == {"role": "assistant", "content": r.text}:
                h[-1] = {"role": "assistant", "content": said or "(interrupted)"}               # follow-ups must not rely on words the user never heard
        return r.text

    def on_turn(audio, info):
        def work():
            heard = stt.transcribe(audio)
            if not (heard.silent or not heard.text.strip()):
                handle(heard.text)
        threading.Thread(target=work, daemon=True).start()

    rt.handle_text = lambda text: handle(text, source="typed")                  # returns the reply text (the HUD console shows it)

    rt.controller = TurnController(bus, Capture(mic), smart, voice, mic=mic, vad=vad, on_turn=on_turn)
    return rt


def main(argv):
    from models.client import Chat, Embedder, Playground
    from ui.pill.layered import enable_dpi_awareness
    enable_dpi_awareness()                      # first, before any window or screen capture exists: sharp pill on scaled displays (mss would set it anyway, but later)
    secs = next((float(a) for a in argv if a.replace(".", "").isdigit()), 120.0)
    brain = argv[argv.index("--brain") + 1] if "--brain" in argv else config.get().brain
    pg, loaded = Playground(), []
    if not pg.healthy():
        print("the playground is not running (python Mark_I/Initial/playground/server.py)")
        return 1
    for model, slot in ((brain, "chat"), (config.get().embedder, "embed"), *((config.get().talker, "talk"),) * bool(config.get().talker)):
        if not (pg.status().get("slots") or {}).get(slot):
            print(f"loading {model} ...")
            pg.load(model, slot=slot)
            loaded.append(slot)
    bus = ev.EventBus()
    mic = Mic(bus)
    speaker = None if "--silent" in argv else Speaker(bus)
    speaker and speaker.warm()
    rt = build(bus, mic, memory="--memory" in argv, brain=brain, speaker=speaker, playground=pg)
    rt.stt.transcribe(__import__("numpy").ones(16000, "int16") * 1000, final=False)          # warm Whisper
    if "--no-pill" not in argv:
        from ui.pill.controller import PillController
        from ui.pill.indicators import random_indicator
        from ui.pill.window import PillWindow
        pill = PillController(bus, PillWindow(random_indicator()), speaker=speaker, mic=mic, on_text=rt.handle_text, on_cancel=rt.engine.cancel, speaks=bool(speaker))
        pill.start()
        rt._closers.append(pill.stop)
    if "--no-hud" not in argv:
        from ui.hud_feed import HudFeed
        feed = HudFeed(bus, speaker, on_command=rt.handle_text)
        feed.start()
        rt._closers.append(feed.stop)
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
