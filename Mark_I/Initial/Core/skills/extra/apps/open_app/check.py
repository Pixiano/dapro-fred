"""Self-check for open_app with invented apps and a fake launcher (nothing real is scanned for names, nothing is opened)."""
import importlib.util
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

import config
from engine.gates import Confirmer

HERE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location("open_app_run", HERE / "run.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


APPS = [{"name": n, "kind": "lnk", "target": f"C:/x/{n}.lnk"} for n in ("Zorbit Music", "Quill Notes", "Visual Pad Code", "Visual Pad Studio", "Orbit Browser", "Frog Terminal", "Pixel Draw", "Calc Pro")] \
    + [{"name": "Chatterly", "kind": "uwp", "target": "Chatterly.App!App"}]


def _selfcheck():
    m = _load()
    config.set_data_dir(tempfile.mkdtemp())

    # scan: shortcuts and Store apps, junk dropped, one per name
    with tempfile.TemporaryDirectory() as d:
        for n in ("Zorbit Music.lnk", "Uninstall Zorbit.lnk", "Zorbit Readme.lnk", "Quill Notes.lnk"):
            (Path(d) / n).write_text("", encoding="utf-8")
        (Path(d) / "Sub").mkdir()
        (Path(d) / "Sub" / "Orbit Browser.lnk").write_text("", encoding="utf-8")
        got = m.scan([d], uwp=[{"Name": "Chatterly", "AppID": "Chatterly.App!App"}, {"Name": "Quill Notes", "AppID": "dup!x"}, {"Name": "Chatterly Help", "AppID": "h!x"}, {"Name": "", "AppID": "z"}])
    assert [(e["name"], e["kind"]) for e in got] == [("Chatterly", "uwp"), ("Orbit Browser", "lnk"), ("Quill Notes", "lnk"), ("Zorbit Music", "lnk")], got

    # matching decisions
    dec = lambda q, usage=None, sims=None: m.decide(m.rank(q, APPS, usage, sims))
    assert dec("zorbit music") == ("open", APPS[0]) and dec("open Zorbit") == ("open", APPS[0]) and dec("the orbit browser")[0] == "open"
    assert dec("chatterly")[0] == "open" and dec("frog terminal")[0] == "open"
    assert dec("the browser") == ("open", APPS[4]) and dec("terminal") == ("open", APPS[5]), (dec("the browser"), dec("terminal"))        # generic words through the alias table
    assert dec("visual pad")[0] == "pick" and {e["name"] for e in dec("visual pad")[1]} == {"Visual Pad Code", "Visual Pad Studio"}           # two close ones: asks, never guesses
    assert dec("visual pad", usage={"Visual Pad Code": 40})[0] in ("open", "pick")
    assert dec("quil nots")[0] in ("ask", "open") and dec("quil nots")[1]["name"] == "Quill Notes"                                           # a typo or mishearing
    assert dec("vpc") == ("open", APPS[2]) or dec("vpc")[0] == "pick"                                                                   # initials; "vp code" below is unambiguous
    assert dec("vp code") == ("open", APPS[2]) and dec("vp studio") == ("open", APPS[3])
    assert dec("photoshop") == ("none",) and dec("xyzzy plugh") == ("none",)
    assert dec("music player", sims={"Zorbit Music": 0.9})[1]["name"] == "Zorbit Music"                                                       # meaning from the embedder alone
    assert m.rank("pixel", APPS, {"Pixel Draw": 3})[0][1]["name"] == "Pixel Draw"

    # the skill: confident open, ask then yes, pick, none, bad input, File Explorer, usage counted
    opened = []
    m.LAUNCH = lambda e: opened.append(e["name"])
    m._index_path().write_text('{"at": %s, "apps": %s}' % (__import__("time").time(), __import__("json").dumps(APPS)), encoding="utf-8")

    class Reg:
        confirmer = Confirmer()
    m.bind({"registry": Reg()})
    assert m.run("open zorbit music") == "Opening Zorbit Music." and opened == ["Zorbit Music"] and m._usage() == {"Zorbit Music": 1}
    r = m.run("quil nots")
    assert r == "I'm not sure which app you mean. Did you mean Quill Notes? Yes or no?" and opened == ["Zorbit Music"], r
    verdict = Reg.confirmer.resolve("yes")
    assert verdict[:2] == ("run", "open_app") and verdict[2] == {"app": "Quill Notes", "exact": True}, verdict
    assert m.run(**verdict[2]) == "Opening Quill Notes." and opened[-1] == "Quill Notes"
    assert m.run("visual pad") == "Which one: Visual Pad Code or Visual Pad Studio? Say the name." and len(opened) == 2
    assert m.run("photoshop") == "I can't find an app called photoshop." and len(opened) == 2
    assert m.run("my files") == "Opening File Explorer." and opened[-1] == "File Explorer"
    assert m.run("Gone App", exact=True) == "I can't find Gone App any more."
    for bad in ("", "  ", "the"):
        try:
            m.run(bad)
            raise AssertionError(bad)
        except Exception as ex:
            assert getattr(ex, "kind", "") == "bad_args", (bad, ex)

    # an empty or failed scan never wipes a good index, and a missing one is rebuilt
    m.scan = lambda *a, **k: []
    before = m._index_path().read_text(encoding="utf-8")
    assert m.reindex() == 0 and m._index_path().read_text(encoding="utf-8") == before
    m.LAUNCH = None
    print("open_app check ok")


if __name__ == "__main__":
    _selfcheck()
