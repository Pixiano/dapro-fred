"""Self-check for edit_note: an invented temp vault and a scripted brain (no model, no real notes). Covers the apply flow with the real Confirmer, the undo copy, each check, the vault boundary and stale edits."""
import importlib.util
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

import config
from engine.gates import Confirmer
from skills import Unavailable

HERE = Path(__file__).resolve().parent
PROFILE = "---\nname: Tavi Brennock\ncity: Dunmarrow\n---\n# Tavi Brennock\n\n## Facts\n- City: Dunmarrow\n- Job: Surveyor\n- Allergic to wool\n- Likes jazz\n"
CONTACTS = "# Contacts\n- Sam Okoro: 555-0101\n- Sam Pell: 555-0102\n- Zora Pryce: 555-0103\n"


def _selfcheck():
    spec = importlib.util.spec_from_file_location("edit_note_run", HERE / "run.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    config.set_data_dir(tempfile.mkdtemp())
    vault = Path(tempfile.mkdtemp())
    (vault / "profile.md").write_text(PROFILE, encoding="utf-8")
    (vault / "contacts.md").write_text(CONTACTS, encoding="utf-8")
    (vault / "sub").mkdir()
    (vault / "sub" / "profile old.md").write_text("x", encoding="utf-8")
    (vault.parent / "outside.md").write_text("secret", encoding="utf-8")
    try:
        m.bind({})
        raise AssertionError("bound without a vault")
    except Unavailable:
        pass
    config.get().vault_dir = str(vault)
    conf, script = Confirmer(), []

    class Brain:
        def complete(self, msgs, schema=None, think=None, **kw):
            assert schema and think is False and "INSTRUCTION:" in msgs[1]["content"]
            return type("R", (), {"json": lambda s: script.pop(0)})()

    class Reg:
        confirmer, brain = conf, Brain()
    m.bind({"registry": Reg()})

    def yes():
        r = conf.resolve("yes")
        assert r and r[0] == "run"
        return m.run(**r[2])

    def refused(**kw):
        try:
            m.run(**kw)
        except Exception as ex:
            return ex
        raise AssertionError("no refusal: " + str(kw))

    # an edit: summary first, nothing written until yes, undo copy, no blank-line damage on deletion
    script.append({"status": "edit", "question": "", "edits": [{"old": "- City: Dunmarrow", "new": "- City: Harrowgate"}]})
    q = m.run(file="profile", instruction="update my city to Harrowgate")
    assert q == 'In profile: change "- City: Dunmarrow" to "- City: Harrowgate". Apply it? Yes or no?', q
    assert (vault / "profile.md").read_text(encoding="utf-8") == PROFILE
    assert yes() == "Done, profile is updated."
    now = (vault / "profile.md").read_text(encoding="utf-8")
    assert now == PROFILE.replace("- City: Dunmarrow", "- City: Harrowgate")
    undo = list((config.data_dir() / "vault_undo").glob("profile.*.md"))
    assert len(undo) == 1 and undo[0].read_text(encoding="utf-8") == PROFILE
    assert not list(vault.glob(".*.tmp"))
    script.append({"status": "edit", "question": "", "edits": [{"old": "- Allergic to wool", "new": ""}]})
    m.run(file="profile.md", instruction="remove the wool allergy")
    yes()
    assert "wool" not in (vault / "profile.md").read_text(encoding="utf-8") and "\n\n- Job" not in (vault / "profile.md").read_text(encoding="utf-8")
    time.sleep(1.1)                                                                                              # a second undo copy needs a second timestamp
    # "no": nothing written, the pending edit is dropped
    script.append({"status": "edit", "question": "", "edits": [{"old": "- Likes jazz", "new": "- Likes soul"}]})
    m.run(file="profile", instruction="I like soul now")
    before = (vault / "profile.md").read_text(encoding="utf-8")
    r = conf.resolve("no")
    m.cancel(r[2])
    assert (vault / "profile.md").read_text(encoding="utf-8") == before and not m._PENDING
    assert m.run(apply="nope") == "That edit is gone, so nothing was changed."
    # the note changed between the question and the yes: nothing is written
    script.append({"status": "edit", "question": "", "edits": [{"old": "- Job: Surveyor", "new": "- Job: Architect"}]})
    m.run(file="profile", instruction="my job is architect")
    time.sleep(0.05)
    (vault / "profile.md").write_text(before + "- New line\n", encoding="utf-8")
    assert "changed in the meantime" in yes() and "Architect" not in (vault / "profile.md").read_text(encoding="utf-8")
    # two Sams: the check turns a guess into a question, nothing pending
    script.append({"status": "edit", "question": "", "edits": [{"old": "- Sam Okoro: 555-0101", "new": "- Sam Okoro: 555-0777"}]})
    ex = refused(file="contacts", instruction="update Sam's number to 555-0777")
    assert ex.kind == "bad_args" and "more than one entry" in ex.speakable and not m._PENDING, ex.speakable
    script.append({"status": "edit", "question": "", "edits": [{"old": "- Sam Okoro: 555-0101", "new": "- Sam Okoro: 555-0777"}]})
    m.run(file="contacts", instruction="update Sam Okoro's number to 555-0777")                                  # a name that tells them apart passes
    conf.resolve("no")
    # size, header and punctuation checks (pure)
    text = "---\nname: A\n---\n# Title\n- one\n- two\n- three\n"
    assert m.gate("add four", text, [{"old": "- three\n", "new": "- three\n- four\n"}], text.replace("- three\n", "- three\n- four\n")) is None
    assert "change" in m.gate("fix it", text, [{"old": "- one", "new": "- uno"}], "x\n" * 30)
    assert "heading" in m.gate("fix it", text, [{"old": "---\nname: A\n---\n", "new": "name: A\n"}], "name: A\n# Title\n- one\n- two\n- three\n")
    assert "punctuation" in m.gate("add: Ground levelled", text, [{"old": "- three\n", "new": "- three\n- Ground levelled.\n"}], text.replace("- three\n", "- three\n- Ground levelled.\n"))
    assert m.gate("rename Sam to Samuel everywhere", CONTACTS, [{"old": "Sam Okoro", "new": "Samuel Okoro"}], CONTACTS.replace("Sam Okoro", "Samuel Okoro")) is None
    # the model asks, or points at text that is not there
    script.append({"status": "ask", "question": "Which passport number?", "edits": []})
    assert refused(file="profile", instruction="update my passport").speakable == "Which passport number?"
    script.append({"status": "edit", "question": "", "edits": [{"old": "- Not in file", "new": "- x"}]})
    assert refused(file="profile", instruction="change it").kind == "failed"
    # files: ambiguous name, unknown name, and nothing outside the vault
    (vault / "profile two.md").write_text("y", encoding="utf-8")
    assert "Which note" in refused(file="prof", instruction="x").speakable
    for bad in ("nothing here", "../outside", "outside", "C:/Windows/win", "sub/../../outside"):
        assert refused(file=bad, instruction="x").kind in ("not_found", "bad_args"), bad
    assert refused(file="", instruction="x").kind == "bad_args" and refused(file="profile", instruction="").kind == "bad_args"
    assert (vault.parent / "outside.md").read_text() == "secret"
    print("edit_note check ok")


if __name__ == "__main__":
    _selfcheck()
