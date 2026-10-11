"""The news clock: builds an edition when its hour has come (06:00, 18:00) and offers it the first time Vatsal is seen after that.

Sockets
  in : News(chat, embed, memory, bus, speak, build=digest.build).start() / .stop()
       chat = the 4B (tool-less use only), embed = Embedder.embed, memory = manager.Memory or None, speak(text) = say a line (None = silent)
       reads presence.state events on the bus; checks digest.due() every `tick_s`
  out: digest day files; the spoken offer "Good morning, sir. The 06:00 news is ready, 8 stories. Shall I read it?" once per edition, when present
Self-check: python news/service.py
"""
import json
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import log
from news import digest


class News:
    def __init__(self, chat, embed, memory, bus, speak=None, build=digest.build, tick_s: float = 60.0):
        self.chat, self.embed, self.mem, self.bus, self.speak, self._build, self.tick_s = chat, embed, memory, bus, speak, build, tick_s
        self.present, self.offer_until = None, 0.0
        self._file = config.data_dir() / "news_offered.json"
        try:
            self.offered = json.loads(self._file.read_text(encoding="utf-8"))          # {edition: time it was offered}: survives a restart
        except (OSError, ValueError):
            self.offered = {}
        self._stop = threading.Event()
        self._t = None

    def _on_presence(self, e):
        self.present = e.data.get("present")
        if self.present:
            self.offer()

    def offer(self):
        ed = digest.latest()
        if not ed or not ed["items"] or not self.speak or digest.is_read(ed["built"]):
            return False
        if time.time() - self.offered.get(ed["built"], 0.0) < config.get().news_offer_hold_s:
            return False
        self.offered[ed["built"]] = time.time()
        try:
            self._file.write_text(json.dumps(self.offered), encoding="utf-8")
        except OSError:
            pass
        greet = "Good morning" if int(ed["at"][:2]) < 12 else "Good evening"
        early = digest.earlier_unread(ed["built"])
        tail = f" The {early['at']} one is still unread: I'll read this first, then ask if you want that too." if early else ""
        self.offer_until = time.time() + 40
        self.speak(f"{greet}, sir. The {ed['at']} news is ready, {len(ed['items'])} stories.{tail} Shall I read it?")
        return True

    def answer(self, text: str):
        """The spoken offer is not in the model's history, so a bare 'Yes, please read it' went to the wrong tool (read_own_docs, 2026-10-11): within 40 s of the offer a yes is turned into 'Read me the news.' here."""
        if time.time() > self.offer_until:
            return None
        t = re.sub(r"[^a-z ]", "", text.lower()).strip()
        if re.match(r"^(yes|yeah|yep|yup|sure|ok|okay|please|go ahead|read it|do it|why not)\b", t):
            self.offer_until = 0.0
            return "yes"
        if re.match(r"^(no|nope|not now|later|not yet|skip)\b", t):
            self.offer_until = 0.0
            return "no"
        return None

    def tick(self):
        if digest.due():
            try:
                self._build(self.chat, self.embed, self.mem)
            except Exception as ex:
                log.session("news_build_failed", error=repr(ex))
                self._stop.wait(600)                                   # try again in 10 minutes, not every tick
                return
            if self.present:
                self.offer()

    def _loop(self):
        while not self._stop.is_set():
            self.tick()
            self._stop.wait(self.tick_s)

    def start(self):
        self.bus.subscribe(self._on_presence, kinds=("state",), sources=("presence",))
        self._t = threading.Thread(target=self._loop, daemon=True, name="news")
        self._t.start()

    def stop(self):
        self._stop.set()


def _selfcheck():
    import os
    import tempfile
    import events as ev
    with tempfile.TemporaryDirectory() as d:
        os.environ["FRED_DATA"] = d
        config._data = None
        config.reload()
        said, built = [], []
        now = datetime.now(config.IST)

        def fake_build(chat, embed, mem):
            built.append(1)
        bus = ev.EventBus()
        n = News(None, None, None, bus, said.append, fake_build)
        config.get().news_hours = str(now.hour)
        n.tick()
        assert built == [1]                                            # the slot is due and nothing is built yet
        digest._day(now).parent.mkdir(parents=True, exist_ok=True)
        digest._day(now).write_text('{"editions": [{"at": "%02d:00", "built": "b1", "items": [{"n": 1}]}], "seen": []}' % now.hour, encoding="utf-8")
        assert digest.due() is None
        n.start()
        bus.emit("presence", "state", present=True)
        for _ in range(50):
            if said:
                break
            threading.Event().wait(0.05)
        assert len(said) == 1 and "1 stories" in said[0] and "Shall I read it?" in said[0], said
        bus.emit("presence", "state", present=True)
        threading.Event().wait(0.2)
        assert len(said) == 1                                          # offered once per edition
        n.offer_until = time.time() + 40
        assert n.answer("Yes, please read it.") == "yes" and n.answer("yes") is None        # one answer per offer
        n.offer_until = time.time() + 40
        assert n.answer("No, not now") == "no" and n.answer("Novel idea") is None and n.answer("What's the weather") is None
        n.offer_until = time.time() - 1
        assert n.answer("yes") is None                                                          # the offer has expired
        n2 = News(None, None, None, bus, speak=said.append, build=fake_build)           # a restart: the offer is remembered
        assert n2.offer() is False and len(said) == 1
        n2.offered = {k: v - config.get().news_offer_hold_s - 1 for k, v in n2.offered.items()}
        assert n2.offer() is True and len(said) == 2                                     # held for 2 h, then offered again while unread
        digest.mark_read("b1", now)
        n2.offered = {}
        assert n2.offer() is False                                                          # never once it was read
        n.stop()
    print("news service ok")


if __name__ == "__main__":
    _selfcheck()
