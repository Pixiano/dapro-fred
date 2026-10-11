"""The voice loop: microphone -> wake/VAD -> turn clock -> Whisper -> engine -> reply printed and spoken (Kokoro, audio/tts.py).

  python engine/live.py [seconds=120] [--memory] [--brain MODEL] [--silent] [--no-pill] [--no-hud (also no tray)] [--no-screen] [--no-presence]
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
import usage
from engine import prompt
from engine.cli import trace
from engine.loop import Engine
from engine.sir import is_echo, is_yes_no_question, sirify, strip_wake


@dataclass
class Runtime:
    engine: Engine
    controller: TurnController
    stt: Stt
    mic: Mic
    bus: ev.EventBus
    replies: list = field(default_factory=list)
    _closers: list = field(default_factory=list)
    news: object = None                         # news.service.News: main() starts it (the daily digest at 06:00 and 18:00)
    deadlines: object = None                    # proactive.deadlines.DeadlineWatch: main() builds and starts it (needs the screen); its answer() is checked first in handle()
    presence: object = None                     # senses.presence.Presence: main() starts it (--no-presence skips); Jobs pauses it during a night run
    jobs: object = None                         # memory_bridge.jobs.Jobs (memory on and a playground only): main() starts its check
    ask: object = None                          # memory_bridge.ask.AskDelivery (memory on only): offered every turn first, started by main()
    scheduler: object = None                    # proactive.scheduler.Scheduler: main() starts its tick, tests drive tick() themselves
    handle_text: object = None                  # handle_text(text): run a typed message exactly like a spoken turn

    def close(self):
        for fn in reversed(self._closers):
            try:
                fn()
            except Exception:
                pass


def build(bus, mic, memory: bool = False, brain: str | None = None, chat=None, embedder=None, stt=None, smart=None, wake: bool = True, out=print, registry=None, speaker=None, playground=None, talk=None, presence: bool = False) -> Runtime:
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
        recall, feed = MemoryRecall(mem, embedder, chat, c.embedder), Feed(mem, bus, lambda: engine.locked)
        feed.start()
        closers += [mem.close, feed.stop]
    def swap(locked):
        """Lockdown: the 4B brain steps out and the small talker is the only model while locked; the brain comes back on release (a few seconds, the unlock reply waits for it)."""
        if playground and talk:
            playground.unload("chat") if locked else playground.load(brain or c.brain, slot="chat")

    from proactive.scheduler import Scheduler
    sched = Scheduler(bus)

    def due(e):
        """A reminder or timer came due: say it (with the edge's "sir"); a missed one says so."""
        t = f"Reminder: {e.data['text']}." if e.data["text"] else "Your timer is up."
        t = sirify(("Sorry, I missed this one. " if e.data["late"] else "") + t)
        out(f"FRED (reminder): {t}")
        if not (speaker and c.tts_enabled):
            bus.emit("proactive", "caption", text=t)                               # a silent setup would otherwise lose the reminder: the pill shows it
        speaker and threading.Thread(target=speaker.speak, args=(t,), daemon=True).start()
    bus.subscribe(due, kinds=("due",), sources=("proactive",))

    def warn(e):
        """The scam watch found a lookalike page: say it (the toast and the pill caption already carry it for a silent room)."""
        t = sirify(e.data["text"])
        out(f"FRED (warning): {t}")
        speaker and e.data.get("level") == "high" and threading.Thread(target=speaker.speak, args=(t,), daemon=True).start()      # medium: toast and caption only
    bus.subscribe(warn, kinds=("warn",), sources=("proactive",))

    def say(t):
        t = sirify(t)
        out(f"FRED (interim): {t}")
        speaker and threading.Thread(target=speaker.speak, args=(t,), kwargs={"quiet": True}, daemon=True).start()

    engine = Engine(chat, registry or build_skills(recall, mic=mic, bus=bus, embed=embedder.embed, scheduler=sched), embedder, prompt.build_system(), bus=bus, recall=recall, on_say=say, talk=talk, lock_chat=talk, on_lock=swap)
    if wake:
        WakeDetector(bus).attach(mic)
    vad = Vad(bus)
    vad.attach(mic)
    stt, smart, voice = stt or Stt(bus), smart or SmartTurn(), VoiceId()
    rt = Runtime(engine, None, stt, mic, bus, _closers=closers, scheduler=sched)
    if presence:
        from senses.presence import Presence
        rt.presence = Presence(bus)
    if memory:
        from memory_bridge.ask import AskDelivery
        if playground:
            from memory_bridge.jobs import Jobs
            seen = {"p": None}
            bus.subscribe(lambda e: seen.update(p=e.data.get("present")), kinds=("state",), sources=("presence",))
            rt.jobs = Jobs(mem, playground, embedder.embed, present=lambda: seen["p"], pm=recall.model, bus=bus, presence=rt.presence)
        rt.ask = AskDelivery(bus, mem, lambda t: speaker and threading.Thread(target=speaker.speak, args=(sirify(t),), daemon=True).start())
    from news.service import News
    rt.news = News(chat, embedder.embed, mem if memory else None, bus, lambda t: speaker and threading.Thread(target=speaker.speak, args=(sirify(t),), daemon=True).start())
    if speaker:
        bus.subscribe(lambda e: e.data.get("state") == "BARGE_IN" and speaker.cancel(), kinds=("state",), sources=("clock",))

    spoken = []                                                                    # FRED's last spoken lines, for the echo filter

    def handle(text, source="voice"):
        """One turn from text (heard or typed): filler if slow, engine, print, speak."""
        out(f"YOU: {text}")
        dl = rt.deadlines and rt.deadlines.answer(text)                            # a yes/no to the screen-deadline question is not a new turn
        if dl:
            out(f"FRED: {sirify(dl)}")
            speaker and speaker.speak(sirify(dl))
            return dl
        a = source == "voice" and rt.news.answer(text)
        if a == "no":
            note = sirify("Okay. Just ask whenever you want the news.")
            out(f"FRED: {note}")
            speaker and speaker.speak(note)
            return note
        if a == "yes":
            text = "Read me the news."
        note = rt.ask and not engine.confirm.pending and rt.ask.take(text)       # an answer to the waiting question is not a new turn (a pending yes/no for a tool comes first)
        if note:
            out(f"FRED: {sirify(note)}")
            speaker and speaker.speak(sirify(note))
            return note
        fev = threading.Event()                                                    # the filler's own cancel: set the moment the reply is ready, so it never delays or follows the answer
        slow = speaker and threading.Timer(c.filler_after_s, lambda: speaker.speak(sirify(pick_filler()), cancel=fev, quiet=True))
        slow and slow.start()
        r = engine.run(text, source=source)
        slow and slow.cancel()
        fev.set()
        if r.via == "cancelled":
            out("(cancelled)")
            return ""
        rt.replies.append(r)
        shown = sirify(r.text)                                                     # the edge only: history keeps the model's own words
        out(f"FRED: {shown}")
        out(trace(r))
        spoken[:] = (spoken + [shown])[-4:]
        if speaker:
            rt.controller.expect_short = is_yes_no_question(shown)
            said = speaker.speak(shown)
            h = engine.history
            if " ".join(said.split()) != " ".join(shown.split()) and h and h[-1] == {"role": "assistant", "content": r.text}:
                if said:
                    h[-1] = {"role": "assistant", "content": said}                # follow-ups must not rely on words the user never heard
                else:
                    del h[-2:]                                                  # nothing heard: forget the turn (a placeholder reply was copied by the 4B model, 2026-10-08)
        return r.text

    def on_turn(audio, info):
        def work():
            heard = stt.transcribe(audio)
            if heard.silent or not heard.text.strip():
                return
            if is_echo(heard.text, spoken):
                return usage.save_clip("clock", audio, kind="ignored", reason="echo", text=heard.text)      # the mic heard FRED's own voice from the speakers
            handle(strip_wake(heard.text))
        threading.Thread(target=work, daemon=True).start()

    rt.handle_text = lambda text: handle(text, source="typed")                  # returns the reply text (the HUD console shows it)

    rt.controller = TurnController(bus, Capture(mic), smart, voice, mic=mic, vad=vad, on_turn=on_turn,
                                   on_ignored=lambda a, r: usage.save_clip("clock", a, kind="ignored", reason=r))
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
    stop, restart = threading.Event(), []
    usage.use_repo_data()                                                       # live runs keep their usage data in Project_FRED/Data
    bus = ev.EventBus()
    mic = Mic(bus)
    usage_log = usage.UsageLog(bus, mic)
    usage_log.start()
    speaker = None if "--silent" in argv else Speaker(bus)
    speaker and speaker.warm()
    rt = build(bus, mic, memory="--memory" in argv, brain=brain, speaker=speaker, playground=pg, presence="--no-presence" not in argv)
    rt.stt.transcribe(__import__("numpy").ones(16000, "int16") * 1000, final=False)          # warm Whisper
    if "--no-pill" not in argv:
        from ui.pill.controller import PillController
        from ui.pill.indicators import random_indicator
        from ui.pill.window import PillWindow
        pill = PillController(bus, PillWindow(random_indicator()), speaker=speaker, mic=mic, on_text=rt.handle_text, on_cancel=rt.engine.cancel, speaks=bool(speaker))
        pill.start()
        rt._closers.append(pill.stop)
        rt.ask and setattr(rt.ask, "show", pill.ask)
    if "--no-hud" not in argv:
        from ui.hud_feed import HudFeed
        feed = HudFeed(bus, speaker, on_command=rt.handle_text)
        feed.start()
        rt._closers.append(feed.stop)
        from ui.hud_launcher import HudLauncher
        from ui.tray import Tray
        hud = HudLauncher()
        hud.start_server()
        tray = Tray(hud.show, lambda: (restart.append(1), stop.set()), stop.set)           # left click = open the HUD
        tray.start()
        rt._closers += [tray.stop, hud.stop]
    from proactive.judge import Judge
    judge = Judge(bus, ev.EventLog(bus), Embedder().embed)                      # shadow mode: logs what it would do, speaks nothing (P6)
    judge.start()
    from proactive.alerts import SystemWatch
    alerts = SystemWatch(bus)
    alerts.start()
    rt._closers.append(alerts.stop)
    rt._closers.append(judge.stop)
    if "--no-screen" not in argv:
        from proactive.scamwatch import ScamWatch
        from senses.screen import ScreenSense
        from proactive.deadlines import DeadlineWatch
        from proactive.gate import Gate
        from proactive.screenread import Reader
        screen = ScreenSense(bus)
        reader = Reader(bus, screen)                                            # reads the foreground window about once a second and feeds the watchers
        watch = ScamWatch(bus, screen, reader=reader)
        rt.deadlines = DeadlineWatch(bus, reader, lambda t: speaker and threading.Thread(target=speaker.speak, args=(sirify(t),), daemon=True).start(), Gate(bus, fullscreen=reader.fullscreen), memory=rt.news.mem)
        screen.start()
        reader.start()
        watch.start()
        rt.deadlines.start()
        rt._closers += [rt.deadlines.stop, watch.stop, reader.stop, screen.stop]
    hk = Hotkey(bus)
    if not mic.start():
        print("could not open the microphone")
        return 1
    hk.start()
    rt.scheduler.start()
    if rt.presence:
        rt.presence.start()                                                      # always on; the night jobs pause it themselves
        rt._closers.append(rt.presence.stop)
    if rt.jobs:
        rt.jobs.start()
        rt._closers.append(rt.jobs.stop)
    if rt.ask:
        rt.ask.start()
        rt._closers.append(rt.ask.stop)
    rt.news.start()                                                              # the 06:00 and 18:00 news digest
    rt._closers.append(rt.news.stop)
    rt._closers.append(rt.scheduler.stop)
    print(f"listening for {secs:.0f} s: say 'Hey FRED, ...' or hold Left Ctrl + Left Alt. Ctrl+C to stop.")
    try:
        stop.wait(secs)
    except KeyboardInterrupt:
        pass
    hk.stop()
    mic.stop()
    time.sleep(1.5)
    rt.close()
    for s in loaded:
        pg.unload(s)
    restart and __import__("subprocess").Popen([sys.executable, *sys.argv])         # tray: Restart
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
