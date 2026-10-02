"""haismart_tools — argument validation, the "not set up yet" message, host
resolution/re-discovery, and grSetDAC command-formatting (the right field
name + EPP value reaches the vendored encoder), all against a fake
haismart_hrdp module standing in for the real TCP/protocol layer — same
spirit as test_call_log.py faking `_adb` rather than touching a real
device. The vendored protocol's own byte-level codec is upstream's to
test; this only checks the glue in haismart_tools.py."""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tools.haismart_tools as ht

_DEVICE = {
    "name": "Bedroom AC",
    "device_id": "A1B2C3D4E5F6",
    "local_key": "00112233445566778899aabbccddeeff",
    "type_id": "AAC1UKZ01",
    "host": "192.168.1.50",
}

_STATUS_BLOB = b"FAKE-STATUS-BLOB"


def _info(device_id: str, host: str):
    return SimpleNamespace(device_id=device_id, host=host)


class _FakeHrdp:
    """Stands in for tools.haismart.vendor.haismart_hrdp. Records every
    set_grsetdac_field call so tests can check the command actually built,
    without touching real AES/wire framing."""

    def __init__(self):
        self.set_field_calls = []
        self.discover_hosts = []      # what discover() returns this test
        self.query_ok = True          # whether the cached host still answers

    def query(self, host, timeout=1.5):
        if self.query_ok:
            return _info(_DEVICE["device_id"], host)
        return None

    def discover(self, timeout=3.0):
        return list(self.discover_hosts)

    def read_status(self, host, device_id, local_key, timeout=6.0):
        return [_STATUS_BLOB]

    def derive_status_layout(self, blob):
        return object() if blob == _STATUS_BLOB else None

    def profile_for(self, type_id):
        return "profile:" + str(type_id)

    def parse_full_status(self, blob, profile):
        return {
            "onOffStatus": 1, "operationMode": "cool",
            "targetTemperature": 24, "windSpeed": "auto", "indoorTemperature": 26,
        }

    def grsetdac_baseline_from_status(self, blob):
        return b"baseline"

    def set_grsetdac_field(self, words, name, epp_value, **kw):
        self.set_field_calls.append((name, epp_value))
        return words

    def grsetdac_op_frame(self, words):
        return b"frame:" + words

    async def async_send_op(self, host, device_id, local_key, *, counter, build_frame, timeout=6.0):
        build_frame(_STATUS_BLOB)   # exercises the real read-modify-write path above
        return [_STATUS_BLOB]

    GRSETDAC_ENUMS = {
        "operationMode": {"auto": 0, "cool": 1, "dry": 2, "heat": 4, "fan_only": 6},
        "windSpeed": {"high": 1, "medium": 2, "low": 3, "auto": 5},
    }


_real_hrdp = ht.hrdp
_real_load = ht._load_devices
_real_save = ht._save_host


def _patch(devices=(_DEVICE,), fake=None):
    fake = fake or _FakeHrdp()
    ht.hrdp = fake
    ht._load_devices = lambda: list(devices)
    ht._save_host = lambda device_id, host: None
    return fake


def _unpatch():
    ht.hrdp = _real_hrdp
    ht._load_devices = _real_load
    ht._save_host = _real_save


# --- not set up yet: no devices on file -------------------------------
_patch(devices=())
try:
    assert "run" in ht.get_ac_status().lower() and "setup" in ht.get_ac_status().lower()
    assert "setup" in ht.set_ac_power(True).lower()
    assert "setup" in ht.set_ac_temperature(22).lower()
    assert "setup" in ht.set_ac_mode("cool").lower()
    assert "setup" in ht.set_ac_fan_speed("low").lower()
finally:
    _unpatch()


# --- argument validation happens before any network call --------------
fake = _patch()
try:
    assert "16 and 30" in ht.set_ac_temperature(40)
    assert "16 and 30" in ht.set_ac_temperature(5)
    assert fake.set_field_calls == []   # never got as far as building a command

    out = ht.set_ac_mode("blazing")
    assert "auto" in out and "cool" in out  # lists the valid modes
    assert fake.set_field_calls == []

    out = ht.set_ac_fan_speed("ludicrous")
    assert "high" in out and "low" in out
    assert fake.set_field_calls == []
finally:
    _unpatch()


# --- host resolution: cached host still answers -> no broadcast needed
fake = _patch()
try:
    out = ht.set_ac_power(True)
    assert "on" in out.lower()
    assert fake.set_field_calls == [("onOffStatus", 1)]
finally:
    _unpatch()


# --- host resolution: cached host gone stale -> falls back to discover()
fake = _patch()
fake.query_ok = False
fake.discover_hosts = [_info(_DEVICE["device_id"], "192.168.1.77")]
try:
    out = ht.set_ac_power(False)
    assert "off" in out.lower(), out
    assert fake.set_field_calls == [("onOffStatus", 0)]
finally:
    _unpatch()


# --- host resolution: not found at all (cache dead, broadcast empty) --
fake = _patch()
fake.query_ok = False
fake.discover_hosts = []
try:
    out = ht.set_ac_power(True)
    assert "can't find" in out.lower(), out
    assert fake.set_field_calls == []
finally:
    _unpatch()


# --- command formatting: temperature is degC-16 on the wire -----------
fake = _patch()
try:
    ht.set_ac_temperature(24)
    assert fake.set_field_calls == [("targetTemperature", 24 - 16)]
finally:
    _unpatch()


# --- command formatting: mode/fan words map to the right EPP values ---
fake = _patch()
try:
    ht.set_ac_mode("Fan_Only")   # case/spacing-insensitive
    assert fake.set_field_calls == [("operationMode", 6)]
finally:
    _unpatch()

fake = _patch()
try:
    ht.set_ac_fan_speed("HIGH")
    assert fake.set_field_calls == [("windSpeed", 1)]
finally:
    _unpatch()


# --- read path: status describes power/mode/temp/fan ------------------
fake = _patch()
try:
    out = ht.get_ac_status()
    assert "on" in out.lower()
    assert "cool" in out
    assert "24" in out
finally:
    _unpatch()


# --- multiple devices on file: ambiguous without a name ----------------
device2 = dict(_DEVICE, name="Living Room AC", device_id="112233445566")
fake = _patch(devices=(_DEVICE, device2))
try:
    out = ht.get_ac_status()
    assert "Bedroom AC" in out and "Living Room AC" in out, out
    assert fake.set_field_calls == []
finally:
    _unpatch()

print("ok")
