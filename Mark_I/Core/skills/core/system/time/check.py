"""Self-check for the time skill."""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from skills.testkit import skill


def _selfcheck():
    reg, problems = skill("time", {})
    r = reg.call("time")
    assert not problems and r.ok and r.data.startswith("It's ") and str(datetime.now().year) in r.data and reg.spec("time").say
    print("time check ok")


if __name__ == "__main__":
    _selfcheck()
