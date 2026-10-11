"""Self-check for screen_watch: the flag file flips the pause switch; temp data dir."""
import importlib.util
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

import config
from proactive import gate

HERE = Path(__file__).resolve().parent


def _selfcheck():
    config.set_data_dir(tempfile.mkdtemp())
    spec = importlib.util.spec_from_file_location("screen_watch_run", HERE / "run.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert not gate.paused() and "reading your screen for deadlines" in m.run("status")
    assert "stopped" in m.run("pause") and gate.paused() and "not reading" in m.run("status")
    assert "again" in m.run("resume") and not gate.paused()
    try:
        m.run("explode")
        raise AssertionError("bad action accepted")
    except m.ToolError:
        pass
    print("screen_watch ok")


if __name__ == "__main__":
    _selfcheck()
