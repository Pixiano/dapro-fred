# Core/tools/haismart_tools.py
#
# Control a Haier AC through the "Haismart" app's ecosystem (Haier U+/uHome,
# also branded Haier SE-Asia) — entirely on the LAN, no cloud round-trip per
# command. The protocol itself (TCP :56800, AES-encrypted "uSS"/HRDP
# framing) is vendored, not reimplemented — see tools/haismart/vendor/ for
# why and its attribution/LICENSE. This file is the FRED-side glue: resolve
# the AC's current LAN address, read/write its state, and turn the results
# into the plain error strings FRED's other tools return on a bad state
# rather than raising (see phone_tools.py for the convention).
#
# One-time setup, not run here: Core/tools/haismart_setup.py signs in to the
# Haier account ONCE to fetch each AC's per-device localKey (the credential
# that then lets us talk to it directly, forever, without the cloud) and
# writes it to Core/data/haismart_devices.json. Nothing in this file ever
# touches the Haier account or the internet — every function below either
# LAN-broadcasts or opens a TCP socket straight to the AC.
#
# device_id + localKey together are the full authority to control the
# appliance from anywhere on the LAN. haismart_devices.json holds both, so
# it is gitignored exactly like phone_tokens.json — see .gitignore.

import asyncio
import json

from config.settings import DATA_DIR
from tools.haismart.vendor import haismart_hrdp as hrdp

DEVICES_PATH = DATA_DIR / "haismart_devices.json"

_NOT_SET_UP = (
    "The AC's local key isn't set up yet — run Core/tools/haismart_setup.py "
    "once with your Haier account to fetch it."
)

# LAN discovery (UDP :7083) is a ~3s broadcast — fine for one-time setup,
# too slow to pay on every voice command. So the persisted IP is tried
# first and re-discovery is only a fallback when that IP stops answering
# (DHCP handed it to someone else, the AC rebooted onto a new lease, ...).
# ponytail: one retry, not a background poller — if the AC moved AGAIN
# between the retry and the next command, that next command pays the same
# fallback cost again. Fine for a device that moves rarely.
_DISCOVER_TIMEOUT = 3.0
_OP_TIMEOUT = 6.0

# operationMode / windSpeed EPP values the vendored encoder already
# allowlists (haismart_hrdp.GRSETDAC_ENUMS) — mirrored here only so
# set_ac_mode/set_ac_fan_speed can validate the spoken word before opening
# a connection, not to duplicate the actual safety check (set_grsetdac_field
# re-validates regardless).
_MODES = hrdp.GRSETDAC_ENUMS["operationMode"]
_FAN_SPEEDS = hrdp.GRSETDAC_ENUMS["windSpeed"]


def _load_devices() -> list:
    if not DEVICES_PATH.exists():
        return []
    try:
        return json.loads(DEVICES_PATH.read_text(encoding="utf-8")).get("devices", [])
    except (json.JSONDecodeError, OSError):
        return []


def _save_host(device_id: str, host: str) -> None:
    """Persist a freshly-discovered IP so the next command skips the broadcast."""
    devices = _load_devices()
    for d in devices:
        if d.get("device_id") == device_id:
            d["host"] = host
    try:
        DEVICES_PATH.write_text(json.dumps({"devices": devices}, indent=2), encoding="utf-8")
    except OSError:
        pass  # best-effort cache; next call just re-discovers again


def _pick_device(name: str = ""):
    """
    The configured AC to act on, or (None, message).

    No active-device switching like phone_tools.use_phone — FRED only has
    one Haier AC set up as of 2026-08-20. `name` is accepted now so a
    second one added later doesn't need this function's callers touched,
    but with one device on file it's ignored and that device is used.
    """
    devices = _load_devices()
    if not devices:
        return None, _NOT_SET_UP

    if len(devices) == 1:
        return devices[0], ""

    if name:
        matches = [d for d in devices if d.get("name", "").lower() == name.strip().lower()]
        if matches:
            return matches[0], ""
        return None, f"I don't have an AC called {name}. I have: {', '.join(d.get('name', d['device_id']) for d in devices)}."

    return None, f"Which AC — {', '.join(d.get('name', d['device_id']) for d in devices)}?"


def _resolve_host(device: dict) -> str:
    """The AC's current LAN IP: the cached one if it still answers, else a fresh broadcast."""
    cached = device.get("host", "")
    if cached:
        info = hrdp.query(cached, timeout=1.5)
        if info is not None and info.device_id.upper() == device["device_id"].upper():
            return cached

    for info in hrdp.discover(timeout=_DISCOVER_TIMEOUT):
        if info.device_id.upper() == device["device_id"].upper():
            _save_host(device["device_id"], info.host)
            return info.host

    return ""


def _read_status_blob(device: dict, host: str):
    """Full-status blob for `device`, or None if nothing decodable came back."""
    raw = hrdp.read_status(host, device["device_id"], device["local_key"], timeout=_OP_TIMEOUT)
    for blob in raw:
        if hrdp.derive_status_layout(blob) is not None:
            return blob
    return None


def _write_field(device: dict, host: str, field: str, epp_value: int):
    """Read-modify-write ONE grSetDAC field, seeded from the AC's own live status so every other
    setting is preserved. Returns (status_blob_or_None, error_message)."""

    def build(status_blob):
        if status_blob is None:
            raise RuntimeError("the AC didn't push a status baseline to seed the change")
        words = hrdp.grsetdac_baseline_from_status(status_blob)
        words = hrdp.set_grsetdac_field(words, field, epp_value)
        return hrdp.grsetdac_op_frame(words)

    try:
        # counter=1 every call, matching the upstream Home Assistant coordinator: each call opens
        # its own fresh session, and the counter is a per-SESSION sequence, not a persisted one.
        replies = asyncio.run(hrdp.async_send_op(
            host, device["device_id"], device["local_key"],
            counter=1, build_frame=build, timeout=_OP_TIMEOUT,
        ))
    except (OSError, RuntimeError, asyncio.TimeoutError, TimeoutError) as e:
        return None, str(e)

    for blob in replies:
        if hrdp.derive_status_layout(blob) is not None:
            return blob, ""
    return None, ""  # command sent; the AC just didn't echo a fresh status in time


def _describe_status(blob: bytes, type_id: str = None) -> dict:
    profile = hrdp.profile_for(type_id)
    return hrdp.parse_full_status(blob, profile)


# =========================================================
# TOOLS
# =========================================================


def get_ac_status(device: str = "") -> str:
    """Read the AC's current power, mode, temperature and fan speed."""
    dev, message = _pick_device(device)
    if not dev:
        return message

    host = _resolve_host(dev)
    if not host:
        return f"Can't find {dev.get('name', 'the AC')} on the network right now."

    blob = _read_status_blob(dev, host)
    if blob is None:
        return "The AC answered but sent nothing readable — the local key may have rotated; re-run the setup script."

    status = _describe_status(blob, dev.get("type_id"))
    on = status.get("onOffStatus")
    mode = status.get("operationMode")
    temp = status.get("targetTemperature")
    fan = status.get("windSpeed")
    room = status.get("indoorTemperature")

    parts = [f"{dev.get('name', 'The AC')} is " + ("on" if on else "off")]
    if on:
        if mode is not None:
            parts.append(f"mode {mode}")
        if temp is not None:
            parts.append(f"set to {temp}°C")
        if fan is not None:
            parts.append(f"fan {fan}")
    if room is not None:
        parts.append(f"room reads {room}°C")
    return ", ".join(parts) + "."


def set_ac_power(on: bool, device: str = "") -> str:
    """Turn the AC on or off."""
    dev, message = _pick_device(device)
    if not dev:
        return message

    host = _resolve_host(dev)
    if not host:
        return f"Can't find {dev.get('name', 'the AC')} on the network right now."

    _, err = _write_field(dev, host, "onOffStatus", 1 if on else 0)
    if err:
        return f"Couldn't reach the AC: {err}"
    return f"{dev.get('name', 'The AC')} is now " + ("on." if on else "off.")


def set_ac_temperature(celsius: int, device: str = "") -> str:
    """Set the AC's target temperature, in Celsius (16-30, the unit's own operating range)."""
    dev, message = _pick_device(device)
    if not dev:
        return message

    celsius = int(celsius)
    if not 16 <= celsius <= 30:
        return "AC target temperature has to be between 16 and 30°C."

    host = _resolve_host(dev)
    if not host:
        return f"Can't find {dev.get('name', 'the AC')} on the network right now."

    # Wire value is degC-16 — the vendored encoder's own convention (see GRSETDAC_FIELDS /
    # the examples/standalone_control.py this was modelled on).
    _, err = _write_field(dev, host, "targetTemperature", celsius - 16)
    if err:
        return f"Couldn't reach the AC: {err}"
    return f"{dev.get('name', 'The AC')} target set to {celsius}°C."


def set_ac_mode(mode: str, device: str = "") -> str:
    """Set the AC's mode: auto, cool, dry, heat, or fan_only."""
    dev, message = _pick_device(device)
    if not dev:
        return message

    key = (mode or "").strip().lower().replace(" ", "_")
    if key not in _MODES:
        return f"AC mode has to be one of: {', '.join(_MODES)}."

    host = _resolve_host(dev)
    if not host:
        return f"Can't find {dev.get('name', 'the AC')} on the network right now."

    _, err = _write_field(dev, host, "operationMode", _MODES[key])
    if err:
        return f"Couldn't reach the AC: {err}"
    return f"{dev.get('name', 'The AC')} mode set to {key}."


def set_ac_fan_speed(speed: str, device: str = "") -> str:
    """Set the AC's fan speed: high, medium, low, or auto."""
    dev, message = _pick_device(device)
    if not dev:
        return message

    key = (speed or "").strip().lower()
    if key not in _FAN_SPEEDS:
        return f"AC fan speed has to be one of: {', '.join(_FAN_SPEEDS)}."

    host = _resolve_host(dev)
    if not host:
        return f"Can't find {dev.get('name', 'the AC')} on the network right now."

    _, err = _write_field(dev, host, "windSpeed", _FAN_SPEEDS[key])
    if err:
        return f"Couldn't reach the AC: {err}"
    return f"{dev.get('name', 'The AC')} fan set to {key}."
