"""Feed the memory archive from the Core event bus: every conversation turn, tool call, transcript, presence change and error becomes one archive record.

Sockets
  in : Feed(memory, bus).start() -> session id     subscribes to the bus; .stop() unsubscribes and writes the session end
       memory = manager.Memory (memory_bridge.link.open_memory)
  out: Archive.write(stream, data, session, ts) with these streams:
         conversation  {role: user|assistant, text, origin|via}   one user line and one assistant line per engine.turn (what episodic extraction reads)
         tool_call     {name, args, ok, error_kind, ms, result}   one per engine.tool
         speech        {text, audio_s, ms}                        every FINAL stt.transcript (text only; the audio lives in voice_data)
         perception    {kind: presence|screen, ...}               presence.state changes; on-demand screen descriptions
         error         {source, kind, error}                      any *.error / camera.fault / engine fault turn
         system        {event: start|stop}                        session boundaries
Rules: the archive is append-only and complete; a write that fails is logged and never reaches the producer (the bus isolates subscribers anyway). Noise (VAD, wake scores,
clock states, screen changes) is NOT archived: it is the event log's ring (events.EventLog), not memory. Timestamps are the event's own, as UTC.
Self-check: python memory_bridge/feed.py
"""
import sys
from datetime import datetime
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import log


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, config.IST).isoformat()


class Feed:
    def __init__(self, memory, bus, locked=lambda: False):
        self.memory, self.bus, self.session, self._unsub, self.locked = memory, bus, None, None, locked

    def start(self) -> str:
        self.session = self.memory.archive.new_session()
        self._write("system", {"event": "start"})
        self._unsub = self.bus.subscribe(self._on)
        return self.session

    def stop(self):
        if self._unsub:
            self._unsub()
            self._unsub = None
            self._write("system", {"event": "stop"})

    def _write(self, stream: str, data: dict, ts: float | None = None):
        try:
            self.memory.archive.write(stream, data, session=self.session, ts=_iso(ts) if ts else None)
        except Exception as ex:
            log.session("feed_error", stream=stream, error=repr(ex))

    def _on(self, e):
        d, k, s = e.data, e.kind, e.source
        if self.locked() and (s, k) in (("stt", "transcript"), ("engine", "tool"), ("screen", "described")):
            return                                                                  # lockdown: a spoken passphrase, tool results and the screen are not archived
        if (s, k) == ("engine", "turn"):
            self._write("conversation", {"role": "user", "text": d["text"], "origin": d.get("origin")}, e.ts - d.get("ms", 0) / 1000)
            self._write("conversation", {"role": "assistant", "text": d["reply"], "via": d.get("via"), "tools": d.get("calls") or []}, e.ts)
            if d.get("via") == "fault":
                self._write("error", {"source": s, "kind": "fault_turn", "error": d["reply"]}, e.ts)
        elif (s, k) == ("engine", "tool"):
            self._write("tool_call", {x: d.get(x) for x in ("name", "args", "ok", "error_kind", "ms", "result")}, e.ts)
        elif (s, k) == ("stt", "transcript") and d.get("final") and d.get("text"):
            self._write("speech", {"text": d["text"], "audio_s": d.get("audio_s"), "ms": d.get("ms")}, e.ts)
        elif (s, k) == ("presence", "state"):
            self._write("perception", {"kind": "presence", **{x: v for x, v in d.items() if x in ("state", "present", "since", "reason")}}, e.ts)
        elif (s, k) == ("screen", "described") and d.get("text"):
            self._write("perception", {"kind": "screen", "text": d["text"]}, e.ts)
        elif k in ("error", "fault", "save_error", "hotkey_error", "wake_error", "glance_error"):
            self._write("error", {"source": s, "kind": k, "error": str(d.get("error") or d)[:300]}, e.ts)


def _selfcheck():
    import tempfile
    import events as ev
    from memory_bridge.link import open_memory
    with tempfile.TemporaryDirectory() as t:
        m, bus = open_memory(t), ev.EventBus()
        f = Feed(m, bus)
        sid = f.start()
        bus.emit("engine", "turn", text="what time is it", reply="It's ten.", via="say", calls=["get_current_time"], ms=900, origin="voice")
        bus.emit("engine", "tool", name="get_current_time", args={}, ok=True, error_kind=None, ms=4, result="It's ten.")
        bus.emit("stt", "transcript", text="what time is it", final=True, audio_s=1.2, ms=300)
        bus.emit("stt", "transcript", text="what ti", final=False, audio_s=0.5, ms=100)             # a partial is not archived
        bus.emit("presence", "state", state="absent")
        bus.emit("camera", "fault", error="black frame")
        bus.emit("engine", "turn", text="x", reply="I hit a problem.", via="fault", calls=[], ms=10, origin="cli")
        bus.emit("audio", "vad", voiced=True)                                                         # noise: not archived
        bus.emit("clock", "state", state="LISTEN")
        f.stop()
        bus.emit("engine", "turn", text="after stop", reply="ignored", via="chat", calls=[], ms=1)    # unsubscribed
        day = datetime.now(config.IST).strftime("%Y-%m-%d")
        rd = lambda s: m.archive.read(s, day)
        conv = rd("conversation")
        assert [(r["data"]["role"], r["data"]["text"]) for r in conv][:2] == [("user", "what time is it"), ("assistant", "It's ten.")] and len(conv) == 4
        assert conv[0]["ts"] < conv[1]["ts"] and all(r["session"] == sid for r in conv)
        assert rd("tool_call")[0]["data"]["name"] == "get_current_time" and len(rd("speech")) == 1 and rd("perception")[0]["data"]["state"] == "absent"
        assert [r["data"]["kind"] for r in rd("error")] == ["fault", "fault_turn"]
        assert [r["data"]["event"] for r in rd("system")] == ["start", "stop"] and m.archive.verify() == []
        m.close()
    print("feed ok")


if __name__ == "__main__":
    _selfcheck()
