"""get_call_log — row parsing, contacts-over-provider-name precedence,
missed-only filtering, and the not-connected/no-calls messages.

Fake adb output below is a real capture (2026-08-20, `adb -s
O3PRIS25DB005413 shell content query --uri content://call_log/calls
--projection number:name:type:date:duration`, numbers changed), not a
guess at the row shape."""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tools.phone_tools as pt

_REAL_ROWS = (
    "Row: 0 number=9967635204, name=, type=2, date=1764080748927, duration=0\n"
    "Row: 1 number=+919967635204, name=, type=3, date=1764087702823, duration=0\n"
    "Row: 2 number=+919167611149, name=Papa, type=2, date=1764419932873, duration=0\n"
    "Row: 3 number=+917411951669, name=, type=1, date=1764485025185, duration=46\n"
    "Row: 4 number=+919998612855, name=, type=3, date=1765442102928, duration=0\n"
)


def _fake_adb_ok(*args, **kwargs):
    return subprocess.CompletedProcess(args, 0, stdout=_REAL_ROWS, stderr="")


# --- device not reachable: same "phone isn't connected" family of message
# as call_phone/hang_up/sync_contacts, not a bare error. ---
_real_device_ready = pt._device_ready
pt._device_ready = lambda: False
try:
    assert "isn't connected" in pt.get_call_log()
finally:
    pt._device_ready = _real_device_ready


# --- happy path against a real captured shape ---
_real_adb = pt._adb
_real_read_contacts = pt._read_contacts
pt._device_ready = lambda: True
pt._adb = _fake_adb_ok
# Mom is on file; Papa is not, so the provider's own name= must be used
# as the fallback for Papa and the raw number for the two blanks.
pt._read_contacts = lambda: {"Mom": "+919967635204"}
try:
    out = pt.get_call_log(limit=10)
    assert out.startswith("5 calls:"), out
    assert "Mom (missed) yesterday" not in out  # sanity: not asserting exact date wording
    assert "Mom (outgoing)" in out or "Mom (missed)" in out, out
    assert "Papa (outgoing)" in out, out
    assert "Unknown number (+917411951669) (incoming)" in out, out

    missed = pt.get_call_log(missed_only=True)
    assert missed.startswith("2 missed calls:"), missed
    assert "Papa" not in missed  # Papa's row here is outgoing, not missed
    assert "Mom" in missed
    assert "Unknown number (+919998612855)" in missed, missed

    limited = pt.get_call_log(limit=1, missed_only=True)
    assert limited.startswith("1 missed call:"), limited  # singular, not "1 missed calls"
finally:
    pt._adb = _real_adb
    pt._read_contacts = _real_read_contacts
    pt._device_ready = _real_device_ready


# --- no calls at all: query succeeds but returns nothing usable ---
pt._device_ready = lambda: True
pt._adb = lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="", stderr="")
try:
    assert pt.get_call_log() == "No calls in the log."
    assert pt.get_call_log(missed_only=True) == "No missed calls."
finally:
    pt._adb = _real_adb
    pt._device_ready = _real_device_ready


# --- adb itself refuses the query ---
pt._device_ready = lambda: True
pt._adb = lambda *a, **k: subprocess.CompletedProcess(a, 1, stdout="", stderr="denied")
try:
    assert "refused" in pt.get_call_log()
finally:
    pt._adb = _real_adb
    pt._device_ready = _real_device_ready

print("ok")
