"""The pill, driven by the event bus: it shows what FRED is doing (listening, thinking, speaking), what was heard, the reply when FRED is silent, and warnings; three buttons act on it.

The window code (layered.py, render.py, indicators.py, window.py, input_popup.py) is the old build's pill, unchanged apart from the popup's question mode. This file replaces pill_app.py: no
turn logic lives here, only bus events in and a few callbacks out.

Sockets
  in : PillController(bus, window, speaker=None, mic=None, on_text=None, speaks=True, linger_s=1.0, on_cancel=None)
         window: ui.pill.window.PillWindow (any object with set_state/set_level/set_transcript/clear_transcript/show/hide works: tests pass a fake)
         bus events  clock.state ADDRESSED -> listening (shown; LISTEN alone is not, an utterance nobody addressed never shows the pill) | END_OF_TURN -> thinking | BARGE_IN -> listening | IGNORE while listening -> hidden
                     stt.transcript (final) -> what was heard, 2.5 s+ | tts.start -> speaking | tts.end -> idle, hidden after `linger_s`
                     engine.turn when speaks is False -> the reply as text for 6 s+, then hidden | proactive.warn -> its title as red warning text for 8 s (the toast carries the detail); while it shows it is not overwritten, and a turn in progress keeps its state
                     a turn that never answers (no tts.start/end) is hidden after 60 s; the longer a caption, the longer it stays (0.35 s per word, max 20 s)
         level: mic.level while listening, speaker.level while speaking, polled every 33 ms by one thread
         buttons: cancel -> speaker.cancel(), on_cancel() (engine.cancel: the turn in flight stops and says nothing) and hide | accept -> speaker.cancel() (stop talking, keep the exchange; the caption says "Stopped") | type -> a text box; Enter calls on_text(text) on its own thread
                  (the typed text is echoed as the caption, and an on_text failure shows "Something went wrong"); hovering a button names it and keeps the pill up (window.hover)
  out: start() / stop() (a thread creates the window and pumps its messages; window and popup are only ever touched from it, other threads post); ask(item, answer) shows an ask-queue question in the box, which takes keyboard focus (ui/pill/ask_popup.py)
       screens: the pill appears on the monitor holding the foreground window, scaled to its DPI; the box opens next to it on the same monitor (details in ui/pill/layered.py and window.py)
Self-check: python ui/pill/controller.py (fake window, no screen);  python ui/pill/controller.py --mock  cycles every state in a real window;  --png DIR [--scale 1.5]  renders each state to PNG files (at a forced DPI scale)
"""
import sys
import threading
import time
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import config
import events as ev
import log


THINK_MAX_S, LISTEN_MAX_S, WARN_S = 60.0, 60.0, 8.0          # watchdogs: a turn that never answers must not leave the pill up for ever


def _read_s(text, floor):
    return max(floor, min(20.0, len(text.split()) * 0.35))     # long captions need longer to read


class PillController:
    def __init__(self, bus, window, speaker=None, mic=None, on_text=None, speaks=True, linger_s=1.0, popup=None, on_cancel=None):
        self.bus, self.win, self.speaker, self.mic, self.on_text, self.speaks, self.linger_s = bus, window, speaker, mic, on_text, speaks, linger_s
        self.cancel_turn = on_cancel                                           # on_cancel(): stop the turn in flight (engine.cancel), not only the speech
        self.state, self._hide_at, self._stop, self._thread, self.popup = "idle", 0.0, threading.Event(), None, popup
        self._warn_until, self._lk = 0.0, threading.RLock()                    # bus threads, the tick thread and the button callbacks all decide state: one lock (the window calls under it only post, never block)
        self._unsub = bus.subscribe(self._on, sources=("clock", "stt", "tts", "engine", "proactive"))
        window.on_cancel, window.on_accept, window.on_type = self._cancel, self._accept, self._type

    # ---- bus -> pill --------------------------------------------------------------------------------------------------------------
    def _set(self, state):
        self.state = state
        self.win.set_state(state)

    def _show(self, state, hold=0.0):
        self._hide_at = 0.0
        self._set(state)
        self.win.show()
        hold and self._later_hide(hold)

    def _later_hide(self, secs):
        now = time.time()
        self._hide_at = now + max(secs, self._warn_until - now)               # a warning on screen outlasts whatever else wants the pill gone

    def _say(self, text, ttl):
        if time.time() >= self._warn_until:                                   # never overwrite a warning
            self.win.set_transcript(text, ttl=ttl)

    def _on(self, e):
        k, d = (e.source, e.kind), e.data
        try:
            with self._lk:
                self._handle(k, d)
        except Exception as ex:
            log.session("pill_error", error=type(ex).__name__)

    def _handle(self, k, d):
        if k == ("clock", "state"):
            s = d.get("state")
            if s in ("ADDRESSED", "BARGE_IN"):
                self._show("listening", LISTEN_MAX_S)
            elif s == "END_OF_TURN":
                self._show("thinking", THINK_MAX_S)
            elif s == "IGNORE" and self.state == "listening":
                self._set("idle")
                self._later_hide(0.3)
        elif k == ("stt", "transcript") and d.get("final") and d.get("text", "").strip():
            text = d["text"].strip()
            self._say(text, _read_s(text, 2.5))
        elif k == ("tts", "start") and not d.get("quiet"):
            self._show("speaking")
        elif k == ("tts", "end") and not d.get("quiet") and self.state == "speaking":      # a late end after a barge-in must not hide the pill mid-sentence
            self._set("idle")
            self._later_hide(self.linger_s)
        elif k == ("engine", "turn") and d.get("origin") != "proactive":
            if not self.speaks:
                text = d.get("reply", "")
                ttl = _read_s(text, 6.0) if text.strip() else 0.3
                self._say(text, ttl)
                self._show("idle", ttl)
            elif self.state == "thinking":
                self._later_hide(5.0)                                          # answered but nothing spoken (empty reply, tts down): tts.start cancels this
        elif k == ("proactive", "caption"):
            ttl = _read_s(d.get("text", ""), 6.0)
            self._say(d.get("text", ""), ttl)
            self._show("idle", ttl)
        elif k == ("proactive", "warn"):
            self._warn_until = time.time() + WARN_S
            self.win.set_transcript(d.get("title", "Careful"), ttl=WARN_S, warn=True)
            if self.state in ("idle", "alert"):                                # an active turn keeps its own state; the warning rides on it
                self._show("alert", WARN_S)

    # ---- window loop --------------------------------------------------------------------------------------------------------------
    def _tick(self):
        while not self._stop.wait(0.033):
            src = self.speaker if self.state == "speaking" else self.mic if self.state == "listening" else None
            self.win.set_level(getattr(src, "level", 0.0) if src else 0.0)
            with self._lk:
                if self._hide_at and time.time() >= self._hide_at and not getattr(self.win, "hover", None):     # hovering a button keeps it up
                    self._hide_at = 0.0
                    self._set("idle")
                    self.win.hide()
                    self.win.clear_transcript()

    def start(self):
        if self._thread:
            return
        self._stop.clear()
        threading.Thread(target=self._tick, daemon=True, name="pill-tick").start()
        ready = threading.Event()

        def ui():
            try:
                self.win.create()
                from ui.pill.input_popup import TypeInputPopup
                self.popup = self.popup or TypeInputPopup(on_submit=self._submit)
                getattr(self.popup, "prepare", lambda: None)()                 # the box's window is created here, on the pumping thread
                self.win.run(on_ready=ready.set)
            except Exception as ex:
                log.session("pill_error", error=type(ex).__name__)
                ready.set()
        self._thread = threading.Thread(target=ui, daemon=True, name="pill-ui")
        self._thread.start()
        ready.wait(5)

    def stop(self):
        self._stop.set()
        self._unsub()
        self.popup and self.popup.destroy()
        self.win.destroy() if hasattr(self.win, "destroy") else None
        self._thread = None

    # ---- buttons ------------------------------------------------------------------------------------------------------------------
    def _cancel(self):
        with self._lk:
            self.speaker and self.speaker.cancel()
            self.cancel_turn and self.cancel_turn()
            self._warn_until = self._hide_at = 0.0
            self.win.clear_transcript()
            self._set("idle")
            self.win.hide()

    def _accept(self):
        with self._lk:
            speaking = self.state == "speaking"
            self.speaker and self.speaker.cancel()
            self._say("Stopped" if speaking else "Nothing to stop", 1.5)         # a click always answers
            self.state in ("idle", "alert") and self._later_hide(1.5)

    def _box_xy(self):
        from ui.pill import render as R
        from ui.pill.input_popup import popup_geometry
        k = getattr(self.win, "scale", 1.0)
        w, h = popup_geometry(k)[:2]
        return self.win.x + (R.canvas_size(k)[0] - w) // 2, self.win.y - h - round(10 * k)      # the popup clamps this to its monitor's work area

    def _type(self):
        self.popup and self.popup.show(*self._box_xy())

    def _submit(self, text):
        with self._lk:
            self.speaker and self.speaker.cancel()                              # a typed message interrupts anything still being said
            self.cancel_turn and self.cancel_turn()                             # and a turn still thinking (the engine runs the new one after it stops)
            self.win.set_transcript(text, ttl=_read_s(text, 2.5))               # echo it, so the user sees what was sent
            self._show("thinking", THINK_MAX_S)
        self.on_text and threading.Thread(target=self._run_text, args=(text,), daemon=True).start()

    def _run_text(self, text):
        try:
            self.on_text(text)
        except Exception as ex:
            log.session("pill_error", error=type(ex).__name__)
            with self._lk:
                self.win.set_transcript("Something went wrong", ttl=4.0)
                self._show("idle", 4.0)

    def ask(self, item, answer, done=None):
        if not self.popup:
            return                                                              # not started yet
        from ui.pill.ask_popup import AskPopup
        AskPopup(self.popup, self._box_xy, answer, done).show(item)


class FakeWindow:
    def __init__(self):
        self.calls, self.x, self.y = [], 100, 800
        self.on_cancel = self.on_accept = self.on_type = None

    def __getattr__(self, name):
        if name in ("set_state", "set_level", "set_transcript", "clear_transcript", "show", "hide", "create", "run", "destroy"):
            return lambda *a, **k: self.calls.append((name, *a, *k.values()))
        raise AttributeError(name)


def _screens_selfcheck():
    """DPI scale, monitor choice (fake layouts, negative origins), scaled geometry and hit testing: no screen needed."""
    from ui.pill import layered as L
    from ui.pill import render as R
    from ui.pill.indicators import BarsIndicator
    from ui.pill.window import PillWindow
    assert [L.scale_for_dpi(d) for d in (96, 120, 144, 192, 0, 72)] == [1.0, 1.25, 1.5, 2.0, 1.0, 1.0]
    M = L.Monitor
    left, main, above = M((-1920, 0, 0, 1080), (-1920, 0, 0, 1040), 96, False), M((0, 0, 2560, 1440), (0, 0, 2560, 1400), 192, True), M((0, -1080, 1920, 0), (0, -1080, 1920, 0), 96, False)
    mons = [main, left, above]
    assert L.pick_monitor(mons, (-1500, 100, -500, 700), (100, 100)) is left                    # the foreground window wins over the cursor
    assert L.pick_monitor(mons, (-100, 100, 900, 700), None) is main                           # straddling two monitors: the larger overlap
    assert L.pick_monitor(mons, (-32000, -32000, -31840, -31970), (-5, 5)) is left            # a minimised window overlaps nothing: the cursor decides
    assert L.pick_monitor(mons, None, (500, -500)) is above and L.pick_monitor(mons, None, (-1, 5)) is left        # no window: the cursor
    assert L.pick_monitor(mons, None, (9999, 9999)) is main and L.pick_monitor([left, main], None, None) is main   # nothing: the primary
    assert L.pick_monitor([], None, None) is None
    mon = L.current_monitor()
    assert mon.work[2] > mon.work[0] and mon.dpi >= 72                                          # the real machine answers sanely
    # placement: bottom-centre of any work area, signed
    for k in (1.0, 1.5, 2.0):
        cw, ch = R.canvas_size(k)
        for work in ((0, 0, 1920, 1040), (-1920, 0, 0, 1040), (0, -1080, 1920, -40)):
            x, y = R.canvas_origin_for_bottom_centre(work[2], work[3], work_left=work[0], scale=k)
            assert work[0] <= x and x + cw <= work[2] and work[1] <= y and abs((x + cw / 2) - (work[0] + work[2]) / 2) <= 1
            assert y + ch <= work[3] + R.TEXT_H * k                                              # canvas bottom within the margin (the shadow pad is below the capsule)
    assert R.canvas_origin_for_bottom_centre(1920, 1040) == (650, 1040 - 18 - 66 - 56)           # 100%: the original placement
    # geometry and hit testing at 100/150/200 %: the clickable zone is the drawn button
    w = PillWindow(BarsIndicator())
    for k in (1.0, 1.5, 2.0):
        w.scale, w.visible = k, False
        assert R.canvas_size(k) == (round(R.CANVAS_W * k), round(R.CANVAS_H * k))
        for (bx, by), name in zip(R.button_centres(k), ("cancel", "accept", "type")):
            assert w._hit_button(bx, by) == name and w._hit_button(bx + (R.BTN_D / 2 + 2) * k, by) == name
            assert w._hit_button(bx + (R.BTN_D / 2 + 5) * k, by) in (None, "accept", "type") and w._hit_button(bx, by + (R.BTN_D / 2 + 5) * k) is None
        assert w._hit_button(0, 0) is None
        frame = R.render_pill("listening", 0.4, 0.5, BarsIndicator(), transcript="hello there", warn=True, scale=k)
        assert frame.size == R.canvas_size(k)
        ix, iy, iw, ih = R.indicator_box(k)
        assert 0 <= ix and ix + iw <= frame.size[0] and iy + ih <= frame.size[1]
    assert R.indicator_box() == (233, R.PILL_Y + 6, 114, R.PILL_H - 12) and R.indicator_box(2.0)[2:] == (228, 68)       # 100% = the original layout
    # the type box sits above the pill at any scale, centred on it
    w2 = FakeWindow()
    w2.x, w2.y, w2.scale = -1500, 400, 2.0
    pc = PillController(ev.EventBus(), w2)
    bx, by = pc._box_xy()
    assert bx == -1500 + (R.canvas_size(2.0)[0] - 640) // 2 and by == 400 - 148 - 20
    pc.stop()
    assert isinstance(L.enable_dpi_awareness(), str)                                           # callable twice, never raises


def _selfcheck():
    _screens_selfcheck()
    bus, w = ev.EventBus(), FakeWindow()

    class Sp:
        level, cancelled = 0.6, 0
        def cancel(self): Sp.cancelled += 1
    sp = Sp()
    p = PillController(bus, w, speaker=sp, linger_s=0.05)
    p._stop.clear()
    threading.Thread(target=p._tick, daemon=True).start()
    def until(cond, secs=3.0):                                                                       # poll, so a loaded machine cannot make a timing assert flaky
        t0 = time.time()
        while not cond() and time.time() - t0 < secs:
            time.sleep(0.02)
        assert cond()
    bus.emit("clock", "state", state="LISTEN")
    assert p.state == "idle" and ("show",) not in w.calls                                              # heard something, not yet addressed: nothing on screen
    bus.emit("clock", "state", state="ADDRESSED")
    assert w.calls[-2:] == [("set_state", "listening"), ("show",)]
    bus.emit("stt", "transcript", text="what time is it", final=True)
    assert ("set_transcript", "what time is it", 2.5) in w.calls
    bus.emit("stt", "transcript", text="partial", final=False)
    assert not any(c[:2] == ("set_transcript", "partial") for c in w.calls)
    bus.emit("clock", "state", state="END_OF_TURN")
    assert p.state == "thinking"
    bus.emit("tts", "start")
    until(lambda: ("set_level", 0.6) in w.calls)
    assert p.state == "speaking"                                  # the level follows the speaker while speaking
    bus.emit("tts", "end")
    until(lambda: w.calls.count(("hide",)) == 1)
    assert p.state == "idle"                                       # idle, then hidden after the linger
    shows, hides = w.calls.count(("show",)), w.calls.count(("hide",))
    bus.emit("clock", "state", state="LISTEN")
    bus.emit("clock", "state", state="IGNORE")
    time.sleep(0.2)
    assert w.calls.count(("show",)) == shows and w.calls.count(("hide",)) == hides and p.state == "idle"          # an ignored utterance leaves nothing on screen
    bus.emit("clock", "state", state="ADDRESSED")
    bus.emit("tts", "end")
    bus.emit("clock", "state", state="END_OF_TURN")
    time.sleep(0.5)                                                                                  # longer than the pending hide would have taken
    assert p.state == "thinking" and w.calls.count(("hide",)) == hides                                   # a new turn cancels a pending hide
    bus.emit("proactive", "warn", title="Possible phishing page", level="high")
    assert ("set_transcript", "Possible phishing page", 8.0, True) in w.calls                       # a warning is flagged so it draws red
    assert p.state == "thinking"                                                                     # ... and does not hijack a turn in progress
    bus.emit("stt", "transcript", text="later words", final=True)
    assert not any(c[:2] == ("set_transcript", "later words") for c in w.calls)                      # a warning is not overwritten by captions
    bus.emit("tts", "start")
    bus.emit("tts", "end")
    assert p._hide_at - time.time() > 5                                                              # speech ending does not hide a warning early
    n = Sp.cancelled
    stopped = []
    p.cancel_turn = lambda: stopped.append(1)
    w.on_cancel()
    assert stopped == [1] and Sp.cancelled == n + 1 and p.state == "idle"                          # cancel also stops the turn in flight
    w.on_accept()
    assert Sp.cancelled == n + 2 and ("set_transcript", "Nothing to stop", 1.5) in w.calls          # a click always answers
    bus.emit("tts", "start")
    w.on_accept()
    assert ("set_transcript", "Stopped", 1.5) in w.calls
    bus.emit("tts", "end")
    n = Sp.cancelled - 2
    got = []
    p.on_text = got.append
    p._submit("hello there")
    until(lambda: got == ["hello there"])
    assert Sp.cancelled == n + 3 and p.state == "thinking"               # typed text interrupts speech and becomes a turn
    assert ("set_transcript", "hello there", 2.5) in w.calls             # ... and is echoed
    p.on_text = lambda t: 1 / 0
    p._submit("boom")
    until(lambda: ("set_transcript", "Something went wrong", 4.0) in w.calls and p.state == "idle")   # a failing turn does not leave the pill thinking
    # a turn that never answers is hidden by the watchdog
    bus.emit("clock", "state", state="END_OF_TURN")
    assert 50 < p._hide_at - time.time() <= THINK_MAX_S
    hides = w.calls.count(("hide",))
    p._hide_at = time.time() + 0.05
    until(lambda: w.calls.count(("hide",)) == hides + 1 and p.state == "idle")
    # a hovered button keeps the pill up
    w.hover = "type"
    bus.emit("tts", "start")
    bus.emit("tts", "end")
    p._hide_at = time.time() + 0.05
    time.sleep(0.3)
    assert w.calls.count(("hide",)) == hides + 1
    del w.hover
    until(lambda: w.calls.count(("hide",)) == hides + 2)
    assert _read_s("one two", 2.5) == 2.5 and _read_s("word " * 30, 6.0) == 10.5 and _read_s("word " * 200, 6.0) == 20.0
    # silent mode: the reply is shown as text
    w2 = FakeWindow()
    p2 = PillController(bus, w2, speaks=False)
    bus.emit("engine", "turn", text="x", reply="It is half past three.", via="chat", origin="voice")
    assert ("set_transcript", "It is half past three.", 6.0) in w2.calls
    bus.emit("engine", "turn", text="x", reply="proactive line", via="chat", origin="proactive")
    assert not any(c[:2] == ("set_transcript", "proactive line") for c in w2.calls)
    p._stop.set()
    p.stop()
    p2.stop()
    print("pill controller ok")


def _mock():
    from ui.pill.indicators import random_indicator
    from ui.pill.window import PillWindow
    bus = ev.EventBus()
    w = PillWindow(random_indicator())
    p = PillController(bus, w, linger_s=0.5)
    p.start()
    for state, text in (("LISTEN", None), ("END_OF_TURN", "turn the volume up a bit"), ("tts", None)):
        if state == "tts":
            bus.emit("tts", "start")
        else:
            bus.emit("clock", "state", state=state)
        text and bus.emit("stt", "transcript", text=text, final=True)
        time.sleep(1.6)
    bus.emit("tts", "end")
    time.sleep(2)
    print("mock cycled listening, thinking, speaking, idle; hidden:", not w.visible)
    p.stop()


def _png(out, scale=1.0):
    from ui.pill import render as R
    from ui.pill.indicators import BarsIndicator, RibbonIndicator
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    for ind in (BarsIndicator(), RibbonIndicator()):
        for st in ("idle", "listening", "thinking", "speaking", "working"):
            R.render_pill(st, 1.3, 0.6, ind, transcript="what is on my calendar today", scale=scale).save(out / f"{ind.name}_{st}.png")
    print("wrote", len(list(out.glob("*.png"))), "frames to", out)


if __name__ == "__main__":
    if "--mock" in sys.argv:
        _mock()
    elif "--png" in sys.argv:
        _png(sys.argv[sys.argv.index("--png") + 1], float(sys.argv[sys.argv.index("--scale") + 1]) if "--scale" in sys.argv else 1.0)
    else:
        _selfcheck()
