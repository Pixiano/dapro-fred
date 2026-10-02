"""check_recent_calls — persisted seen-set behaviour mirroring
whatsapp_tools.check_vip_messages, but keyed on the single max call
`date` rather than a full stamp set (see phone_tools.CALL_SEEN_PATH) —
AND gated on the exact same VIP tier data whatsapp_tools already keeps
(whatsapp_tools._read_tiers / tier_of), not a second parallel tier
file. Fake adb output reuses the real row shape captured for
test_call_log.py (2026-08-20), not invented data."""
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tools.phone_tools as pt
import tools.whatsapp_tools as wt

# Mom is VIP, Papa is merely trusted (so his calls dedup-advance the
# watermark but never get announced), matching how tier_of is keyed on
# the lowercased NAME, not the number.
_TIERS = ("strict", {"mom": "vip", "papa": "trusted"}, {})

_ROWS_BEFORE = (
    "Row: 0 number=+919967635204, name=Mom, type=2, date=1764080748927, duration=0\n"
    "Row: 1 number=+919167611149, name=Papa, type=2, date=1764419932873, duration=0\n"
)

# Two more calls, later than anything in _ROWS_BEFORE: one from Mom
# (VIP -> announced), one from a number not in the contact book with
# no provider name either (unresolvable -> never VIP, never announced).
_ROWS_AFTER = _ROWS_BEFORE + (
    "Row: 2 number=+919998612855, name=, type=3, date=1765442102928, duration=0\n"
    "Row: 3 number=+919967635204, name=Mom, type=3, date=1765442200000, duration=0\n"
)


def _adb_returning(output):
    return lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=output, stderr="")


_real_adb = pt._adb
_real_device_ready = pt._device_ready
_real_read_contacts = pt._read_contacts
_real_resolve = pt._resolve
_real_seen_path = pt.CALL_SEEN_PATH
_real_read_tiers = wt._read_tiers

pt._device_ready = lambda: True
pt._read_contacts = lambda: {"Mom": "+919967635204", "Papa": "+919167611149"}
pt._resolve = lambda: "TESTSERIAL"
wt._read_tiers = lambda serial: _TIERS

with tempfile.TemporaryDirectory() as tmp:
    pt.CALL_SEEN_PATH = Path(tmp) / "call_log_seen.json"

    try:
        # --- phone unreachable: silent, not an error ---
        pt._device_ready = lambda: False
        assert pt.check_recent_calls() == ""
        pt._device_ready = lambda: True

        # --- first-ever run: seeds the watermark, announces nothing,
        # even though the log already has a VIP (Mom) call sitting in
        # it — matches check_vip_messages' own first-run behaviour with
        # an empty seen-set (see check_recent_calls docstring). ---
        pt._adb = _adb_returning(_ROWS_BEFORE)
        assert not pt.CALL_SEEN_PATH.exists()
        first = pt.check_recent_calls()
        assert first == "", first
        assert pt.CALL_SEEN_PATH.exists()

        # --- nothing new since: still silent ---
        assert pt.check_recent_calls() == ""

        # --- two new calls after the watermark: an unresolvable
        # unknown-number call (never VIP, dropped) and a VIP call from
        # Mom (announced) ---
        pt._adb = _adb_returning(_ROWS_AFTER)
        announced = pt.check_recent_calls()
        assert announced.startswith("Mom called"), announced
        assert "unknown" not in announced, announced

        # --- state persists across two separate calls (simulating two
        # FRED runs): the call just announced is not announced again ---
        assert pt.check_recent_calls() == ""

        # --- a call AT the seen watermark (not after it) must not be
        # announced — confirms the boundary is a strict >, not >= ---
        boundary_row = (
            "Row: 0 number=+919967635204, name=Mom, type=1, "
            "date=1765442200000, duration=10\n"  # same date as the last one seen
        )
        pt._adb = _adb_returning(boundary_row)
        assert pt.check_recent_calls() == ""

        # --- a trusted (non-VIP) caller after the watermark still
        # advances state but is never announced ---
        trusted_only = (
            "Row: 0 number=+919167611149, name=Papa, type=2, "
            "date=1765442300000, duration=5\n"
        )
        pt._adb = _adb_returning(trusted_only)
        assert pt.check_recent_calls() == ""
        # and doesn't linger to be (mis-)announced on the next check either
        assert pt.check_recent_calls() == ""

        # --- no calls in the log at all: silent, no crash on max() of
        # an empty sequence ---
        pt._adb = lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="", stderr="")
        assert pt.check_recent_calls() == ""

        # --- adb refuses the query: silent, not an error ---
        pt._adb = lambda *a, **k: subprocess.CompletedProcess(a, 1, stdout="", stderr="denied")
        assert pt.check_recent_calls() == ""

    finally:
        pt._adb = _real_adb
        pt._device_ready = _real_device_ready
        pt._read_contacts = _real_read_contacts
        pt._resolve = _real_resolve
        pt.CALL_SEEN_PATH = _real_seen_path
        wt._read_tiers = _real_read_tiers

print("ok")
