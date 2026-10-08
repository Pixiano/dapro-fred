"""Live gate for the edit_note skill: the 10 invented memory-file cases run through the real skill with the real 4B (playground chat slot) in a temp vault; the yes/no is answered by the test.
A case passes when the file ends exactly as expected after the yes, or (for the two must-ask cases) when the skill refuses and writes nothing.   python tests/live_edit_note.py [id,id,...]"""
import importlib.util
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
from edit_note_cases import C
from engine.gates import Confirmer
from models.client import Chat, Playground

HERE = Path(__file__).resolve().parents[1] / "skills" / "extra" / "vault" / "edit_note"


def main():
    config.set_data_dir(tempfile.mkdtemp())
    spec = importlib.util.spec_from_file_location("edit_note_run", HERE / "run.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    vault = Path(tempfile.mkdtemp())
    config.get().vault_dir = str(vault)
    pg, loaded = Playground(), False
    if not (pg.status().get("slots") or {}).get("chat"):
        pg.load(config.get().brain, slot="chat")
        loaded = True
    conf = Confirmer()

    class Reg:
        confirmer, brain = conf, Chat(config.get().brain, slot="chat", temperature=0, max_tokens=2000)
    m.bind({"registry": Reg()})
    only = sys.argv[1].split(",") if len(sys.argv) > 1 else None
    bad = 0
    try:
        for c in C:
            if c["cat"] != "memory" or (only and c["id"] not in only):
                continue
            f = vault / "note.md"
            f.write_text(c["text"], encoding="utf-8")
            t0, why = time.time(), ""
            try:
                q = m.run(file="note", instruction=c["instr"])
                r = conf.resolve("yes")
                m.run(**r[2])
                got = f.read_text(encoding="utf-8")
                ok = c["exp"] is not None and got.rstrip("\n") == c["exp"].rstrip("\n")
                why = "" if ok else "file differs: " + q
            except Exception as ex:
                ok = c["exp"] is None and f.read_text(encoding="utf-8") == c["text"] and getattr(ex, "kind", "") in ("bad_args", "failed")
                why = getattr(ex, "speakable", repr(ex))
            bad += not ok
            print(f"{'ok  ' if ok else 'FAIL'} {c['id']:4} {c['name']:24} {time.time() - t0:4.1f} s  {why[:110]}")
    finally:
        if loaded:
            pg.unload("chat")
    print(f"{bad} wrong")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
