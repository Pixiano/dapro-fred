"""Self-check for email_draft: fake Gmail transport, fake Notepad launcher, the real Confirmer and cancel hook. Nothing is sent or opened."""
import base64
import importlib.util
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

import config
from engine.gates import Confirmer
from tools import gmail

HERE = Path(__file__).resolve().parent


def _selfcheck():
    spec = importlib.util.spec_from_file_location("email_draft_run", HERE / "run.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    config.set_data_dir(tempfile.mkdtemp())
    sent, opened = [], []

    def hdrs(frm, to):
        return {"payload": {"headers": [{"name": "From", "value": frm}, {"name": "To", "value": to}]}}

    def fake(method, path, params, body):
        if method == "POST":
            import email; sent.append(email.message_from_bytes(base64.urlsafe_b64decode(body["raw"])))
            return {"id": "s1"}
        if path == "messages":
            q = params["q"].lower()
            if "dad" in q:
                return {"messages": [{"id": "1"}, {"id": "2"}, {"id": "3"}]}
            if "sam" in q:
                return {"messages": [{"id": "4"}, {"id": "5"}]}
            return {"messages": []}
        return {"1": hdrs("Dad <dad@example.com>", "me@example.com"), "2": hdrs("me@example.com", "Dad <dad@example.com>"), "3": hdrs("Dad <dad@example.com>", "me@example.com"),
                "4": hdrs("Sam A <sam.a@example.com>", "me@example.com"), "5": hdrs("Sam B <sam.b@example.com>", "me@example.com")}[path.split("/")[1]]
    gmail.TRANSPORT = fake
    m.LAUNCH = lambda p: opened.append(p)
    conf = Confirmer()

    class Reg:
        confirmer = conf
    m.bind({"registry": Reg()})

    for bad, word in (({}, "who it's to"), ({"to": "Dad", "subject": "Hi"}, "what it should say")):
        try:
            m.run(**bad)
            raise AssertionError(bad)
        except Exception as ex:
            assert ex.kind == "bad_args" and word in ex.speakable, ex.speakable
    for who, word in (("Nobody", "can't find"), ("Sam", "Which one")):
        try:
            m.run(to=who, subject="s", body="b")
            raise AssertionError(who)
        except Exception as ex:
            assert word in ex.speakable, ex.speakable

    q = m.run(to="Dad", subject="Dinner", body="I'll be late tonight.")
    assert q == "Draft to dad@example.com is open in Notepad. Send it? Yes or no?" and len(opened) == 1 and not sent
    f = Path(opened[0])
    assert f.read_text().startswith("To: dad@example.com\nSubject: Dinner\n\nI'll be late tonight.")
    f.write_text(f.read_text().replace("late tonight", "late tonight, start without me"))                 # an edit made in Notepad
    r = conf.resolve("yes")
    assert r[0] == "run" and r[1] == "email_draft"
    assert m.run(**r[2]) == "Sent to dad@example.com." and "start without me" in sent[0].get_payload(decode=True).decode() and sent[0]["Subject"] == "Dinner" and not f.exists()
    try:
        m.run(**r[2])                                                                                         # sent once; a replay is not honored
        raise AssertionError
    except Exception as ex:
        assert ex.kind == "bad_args" and len(sent) == 1

    m.run(to="a@b.com", subject="x", body="y")                                                              # "no" removes the file
    f2 = Path(opened[1])
    r = conf.resolve("no")
    assert r[0] == "cancel" and f2.exists()
    m.cancel(r[2])
    assert not f2.exists() and len(sent) == 1

    m.run(to="a@b.com", subject="x", body="y")                                                              # a path trick cannot leave the drafts folder
    g = Path(opened[2])
    g.write_text("To: not an address\nSubject: x\n\nbody")
    try:
        m.run(send_draft=str(g))
        raise AssertionError
    except Exception as ex:
        assert ex.kind == "bad_args" and len(sent) == 1
    try:
        m.run(send_draft="../../etc/passwd")                                                                # not issued: ignored, so it is an incomplete draft request
        raise AssertionError
    except Exception as ex:
        assert ex.kind == "bad_args" and len(sent) == 1
    gmail.TRANSPORT = None
    print("email_draft check ok")


if __name__ == "__main__":
    _selfcheck()
