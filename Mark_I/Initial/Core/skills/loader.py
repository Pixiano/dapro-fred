"""Skill loader: every tool is a folder; add a folder to add a tool, delete it to remove it.

Layout   skills/core/<group>/<skill>/   built-in basics (a broken one is an error: FRED must not start without them)
         skills/extra/<group>/<skill>/  add-ons (a broken one is skipped and reported, the rest load)
         a folder whose name starts with "_" is switched off
Folder   manifest.json  {"name", "description", "args": <JSON schema object>, "examples": [3+ phrasings], "not_for": [...], "flags": {"destructive","say","reads_untrusted","sends"}, "timeout_s"}
         run.py         run(**args) -> str   (raise tools.registry.ToolError for expected failures)
         check.py       optional self-check, picked up by tests/run_all.py when it defines _selfcheck
Sockets
  in : load(root=None, registry=None, include_extra=True) -> (Registry, problems)   root defaults to this folder; problems = list of "path: reason" for skipped extras
  out: a Registry whose ToolSpec.group is the group folder name, ready for engine.loop.Engine. Routing uses the examples through engine.classify.Router (embedding only: skills carry no regex cues).
Ranking: description + examples are embedded; `not_for` is only shown to the model. skills/rank_check.py asks the real embedder whether every example ranks its own skill first.
Self-check: python skills/loader.py (temporary skill folders: good, broken core, broken extra, switched off, duplicate)
"""
import importlib.util
import json
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.registry import Registry, ToolSpec

HERE = Path(__file__).resolve().parent
FLAGS = {"destructive", "say", "reads_untrusted", "sends"}


def _read(folder: Path) -> ToolSpec:
    m = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    for k, t in (("name", str), ("description", str), ("args", dict), ("examples", list)):
        if not isinstance(m.get(k), t) or not m[k]:
            if not (k == "args" and m.get(k) == {}):
                raise ValueError(f"manifest needs a non-empty {k}")
    if m["name"] != folder.name:
        raise ValueError(f"name {m['name']!r} must match the folder name {folder.name!r}")
    if len(m["examples"]) < 3 or not all(isinstance(e, str) and e.strip() for e in m["examples"]):
        raise ValueError("at least 3 example phrasings are needed (they are what the ranking uses)")
    bad = set(m.get("flags", {})) - FLAGS
    if bad:
        raise ValueError(f"unknown flags {sorted(bad)}")
    schema = {"type": "object", **m["args"]} if "type" not in m["args"] else m["args"]
    spec = importlib.util.spec_from_file_location(f"skill_{folder.parent.name}_{folder.name}", folder / "run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not callable(getattr(mod, "run", None)):
        raise ValueError("run.py must define run(**args)")
    return ToolSpec(m["name"], mod.run, schema, m["description"], group=folder.parent.name, timeout_s=m.get("timeout_s"), examples=tuple(m["examples"]),
                    not_for=tuple(m.get("not_for", ())), **{k: bool(v) for k, v in m.get("flags", {}).items()})


def load(root=None, registry=None, include_extra: bool = True):
    root, reg, problems = Path(root) if root else HERE, registry or Registry(), []
    for tier in ("core", "extra") if include_extra else ("core",):
        for manifest in sorted((root / tier).glob("*/*/manifest.json")):
            folder = manifest.parent
            if folder.name.startswith("_") or folder.parent.name.startswith("_"):
                continue
            try:
                reg.register(_read(folder))
            except Exception as ex:
                if tier == "core":
                    raise RuntimeError(f"core skill {folder.relative_to(root)} is broken: {ex}") from ex
                problems.append(f"{folder.relative_to(root)}: {ex}")
    return reg, problems


def _selfcheck():
    import tempfile

    def make(root, tier, group, name, run="def run(**a):\n    return 'x'\n", **over):
        d = Path(root) / tier / group / name
        d.mkdir(parents=True)
        m = {"name": name, "description": f"does {name}", "args": {"properties": {}}, "examples": ["a", "b", "c"], **over}
        (d / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
        (d / "run.py").write_text(run, encoding="utf-8")

    with tempfile.TemporaryDirectory() as t:
        make(t, "core", "sys", "cpu", not_for=["GPU"], flags={"say": True})
        make(t, "extra", "misc", "ok")
        make(t, "extra", "misc", "_off")
        make(t, "extra", "misc", "noexamples", examples=["only one"])
        make(t, "extra", "misc", "norun", run="x = 1\n")
        make(t, "extra", "misc", "flagged", flags={"explode": True})
        r, problems = load(t)
        assert sorted(r.names()) == ["cpu", "ok"] and r.spec("cpu").group == "sys" and r.spec("cpu").say and r.call("cpu").data == "x"
        assert len(problems) == 3 and all("noexamples" in p or "norun" in p or "flagged" in p for p in problems), problems
        assert "Not for: GPU." in r.schemas(["cpu"])[0]["function"]["description"] and "GPU" not in r.spec("cpu").description        # shown to the model, never embedded
        assert load(t, include_extra=False)[0].names() == ["cpu"]
        make(t, "core", "sys", "broken", examples=[])
        try:
            load(t)
            raise AssertionError("a broken core skill must stop start-up")
        except RuntimeError as ex:
            assert "broken" in str(ex)
    with tempfile.TemporaryDirectory() as t:
        make(t, "core", "a", "same")
        make(t, "core", "b", "same")
        try:
            load(t)
            raise AssertionError
        except RuntimeError as ex:
            assert "already registered" in str(ex)                                        # two folders, one name: refused
    print("loader ok")


if __name__ == "__main__":
    _selfcheck()
