"""Mark I memory: verbose decision log. Everything a model was asked, answered, did, and every time code overruled it.

One table in the private memory database (never the repo). Kinds:
  prompt         the full prompt/system+map a model was given
  reply          the raw model reply
  action         a tool-loop action: tool, args, result
  override       code refused or changed what the model wanted (write gate, evidence rule, personal-model gate, refused tool...)
  model_failure  unusable output; nothing was changed
Logging never raises: a broken log must not break memory. Read with show(); it is in the sqlite backup like everything else.
ponytail: unbounded growth (full text kept on purpose while the small models are still being judged); prune by age once trusted.
"""
import json
import logging
from datetime import datetime, timezone

log = logging.getLogger("memory")
_SQL = """CREATE TABLE IF NOT EXISTS decision_log (id INTEGER PRIMARY KEY, at TEXT NOT NULL, agent TEXT NOT NULL, run TEXT,
  kind TEXT NOT NULL, data TEXT NOT NULL)"""


def note(store, agent, run, kind, **data):
    try:
        with store.lock:
            store.db.execute(_SQL)
            store.db.execute("INSERT INTO decision_log(at,agent,run,kind,data) VALUES(?,?,?,?,?)",
                             (datetime.now(timezone.utc).isoformat(timespec="seconds"), agent, str(run), kind, json.dumps(data, default=str, ensure_ascii=False)))
            store.db.commit()
    except Exception:
        log.exception("decision log write failed")


def traced(store, agent, run, llm):
    """Wraps a prompt-style llm(prompt)->str: logs the prompt, the reply, or the failure (which is re-raised)."""
    def call(prompt):
        note(store, agent, run, "prompt", text=prompt)
        try:
            reply = llm(prompt)
        except Exception as ex:
            note(store, agent, run, "model_failure", error=repr(ex))
            raise
        note(store, agent, run, "reply", text=reply)
        return reply
    return call


def show(store, agent=None, run=None, kind=None, limit=200):
    with store.lock:
        store.db.execute(_SQL)
        sql, args = "SELECT * FROM decision_log WHERE 1=1", []
        for col, v in (("agent", agent), ("run", run), ("kind", kind)):
            if v is not None:
                sql += f" AND {col}=?"
                args.append(str(v))
        rows = [dict(r) for r in store.db.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit))]
    return [dict(r, data=json.loads(r["data"])) for r in rows[::-1]]


if __name__ == "__main__":  # self-check: python decisionlog.py
    import tempfile
    from manager import Memory
    from weekly import Week
    logging.getLogger("memory").setLevel(logging.CRITICAL)
    with tempfile.TemporaryDirectory() as d, Memory(d) as m:
        s = m.store
        assert traced(s, "a", "r1", lambda p: "ok")("hello") == "ok"
        try:
            traced(s, "a", "r1", lambda p: 1 / 0)("hello")
            raise SystemExit("failure swallowed")
        except ZeroDivisionError:
            pass
        assert [r["kind"] for r in show(s, agent="a")] == ["prompt", "reply", "prompt", "model_failure"] and show(s, kind="reply")[0]["data"]["text"] == "ok"
        replies = iter(['{"tool": "nope", "args": {}}', "not json", '{"tool": "finish", "args": {"digest": "quiet", "force": true}}'])
        from semantic import SemanticIndex
        ix = SemanticIndex(s, lambda t, q: [1.0, 0.0], "fake")
        ix.attach()
        s.add_event("note", "something happened", importance=0.5, ts="2026-03-05T10:00:00+00:00")
        w = Week(m, "2026-03-08", ix)
        assert w.loop(lambda msgs: next(replies)) == ("done", "quiet")
        kinds = [r["kind"] for r in show(s, agent="Week")]
        assert kinds.count("prompt") == 1 and kinds.count("reply") == 3 and kinds.count("override") == 1 and kinds.count("model_failure") == 1 and kinds.count("action") == 1
        assert "unknown tool" in json.dumps(show(s, kind="override")[0]["data"])
        s.db.execute("DROP TABLE decision_log")                                  # a broken log never breaks memory
        s.db.execute("CREATE VIEW decision_log AS SELECT 1 AS x"); s.db.commit()
        note(s, "a", "r", "reply", text="x")
    print("decision log ok")
