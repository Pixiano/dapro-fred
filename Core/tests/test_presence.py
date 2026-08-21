# Core/tests/test_presence.py
#
# NOT CI-safe: needs a real camera at PRESENCE_CAMERA_INDEX and a real
# enrolled face (run scripts/enroll_face.py first). Guarded behind
# __main__ rather than plain test_*() functions — this needs input()
# for the "step away" pause, which would hang pytest collection if it
# ran as module-level code. Run by hand: python tests/test_presence.py

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from input import presence


def main():
    presence.poll_once()
    assert presence.is_present() is True, "sit in frame, run again"
    print("present: OK,", presence.last_seen())

    input("step away from the camera, then press enter")
    presence.poll_once()
    assert presence.is_present() is False, "still detected present"
    print("absent: OK")

    assert not any(Path("Core/data").glob("*presence*frame*")), \
        "a raw frame was persisted, should never happen"
    print("no persisted frames: OK")


if __name__ == "__main__":
    main()
