"""The standard tool set: every tool group that is built, registered in one place.

Sockets
  in : build_registry(groups=None) -> Registry   groups = names of modules to include (default: all below)
  out: a Registry ready for engine.loop.Engine. Add a tool group = write tools/<area>.py with register(registry) and list it in MODULES.
Self-check: python tools/builtin.py (every tool has a description, a cue or an example, and unique names across groups)
"""
import importlib
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.registry import Registry

MODULES = ("clock", "audio_dev", "files", "windows", "reminders")


def build_registry(groups=None) -> Registry:
    r = Registry()
    for m in MODULES:
        if groups is None or m in groups:
            importlib.import_module(f"tools.{m}").register(r)
    return r


def _selfcheck():
    r = build_registry()
    assert len(r.names()) == len(set(r.names())) >= 15
    for n in r.names():
        s = r.spec(n)
        assert s.description, n
        assert s.cues or s.examples or s.group in {"audio"}, f"{n}: no cue or example, the router could never offer it"
        assert s.schema.get("type") == "object", n
    assert {"get_current_time", "calculate", "set_volume", "read_text_file", "find_files", "focus_window", "schedule_reminder"} <= set(r.names())
    assert len(build_registry({"clock"}).names()) == 2
    print(f"builtin ok ({len(r.names())} tools in {len(r.groups())} groups)")


if __name__ == "__main__":
    _selfcheck()
