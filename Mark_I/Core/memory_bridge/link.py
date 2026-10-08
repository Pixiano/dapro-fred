"""Opens the memory pipeline (Mark_I/Initial/memory, frozen; Core itself lives in Mark_I/Core since the P8 cut-over) inside the Core process.

Sockets
  in : open_memory(root=None) -> manager.Memory      root defaults to <data_dir>/memory (personal data, outside the repo)
       memory_modules() -> the imported memory modules {name: module}, for the bridge pieces that need Graph / SemanticIndex / PersonalModel
  out: the memory package's own objects; nothing here wraps or changes them (the pipeline is frozen: bugs only)
The memory modules use flat imports (`from store import ...`), so their folder is appended to sys.path (appended, so Core's own module names always win; there is no clash today).
Self-check: python memory_bridge/link.py
File names here must not equal a memory module name (store, archive, recall, graph, semantic, manager...): a script run from this folder would shadow it.
"""
import importlib
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config

MEMORY_DIR = Path(__file__).resolve().parents[2] / "Initial" / "memory"


def memory_modules() -> dict:
    if str(MEMORY_DIR) not in sys.path:
        sys.path.append(str(MEMORY_DIR))
    return {n: importlib.import_module(n) for n in ("manager", "archive", "semantic", "graph", "personal_model", "recall")}


def open_memory(root=None):
    return memory_modules()["manager"].Memory(root or config.data_dir() / "memory")


def _selfcheck():
    import tempfile
    mods = memory_modules()
    assert {"manager", "archive", "semantic", "graph", "personal_model", "recall"} <= set(mods)
    with tempfile.TemporaryDirectory() as t:
        m = open_memory(t)
        sid = m.archive.new_session()
        m.archive.write("conversation", {"role": "user", "text": "hi"}, session=sid)
        assert m.archive.verify() == []
        m.close()
    assert mods["recall"].__file__.startswith(str(MEMORY_DIR))                    # the memory recall.py, not a Core module of the same name
    print("memory_bridge.link ok")


if __name__ == "__main__":
    _selfcheck()
