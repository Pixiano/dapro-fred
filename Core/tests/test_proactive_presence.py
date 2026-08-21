"""check_presence — thin scheduler wrapper around presence.poll_once(),
mirroring check_vip_messages/check_recent_calls: a camera hiccup or a
transient vision-model failure must never crash the scheduler."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from input import presence
from orchestrator import proactive_checks as pc

_real_poll_once = presence.poll_once


def _raise():
    raise RuntimeError("camera exploded")


try:
    presence.poll_once = _raise
    pc.check_presence()  # must not raise
finally:
    presence.poll_once = _real_poll_once

print("ok")
