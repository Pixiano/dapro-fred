# Core/tools/haismart_setup.py
#
# ONE-TIME setup for Haier AC control (see tools/haismart_tools.py). Run
# this by hand, once, whenever an AC is added or its localKey needs
# re-fetching (Haier rotates it server-side occasionally — haismart_tools
# will say so if reads stop decrypting).
#
#   cd Core && python tools/haismart_setup.py
#
# What it does, in order:
#   1. Prompts for the Haier account email/phone, password (hidden input —
#      never a CLI argument, so it never lands in shell history) and the
#      account's registration country code.
#   2. Signs in to Haier's cloud ONCE and lists the account's appliances.
#   3. Fetches each appliance's localKey over the cloud MQTT gateway — the
#      one credential a LAN-only client needs and can get no other way.
#   4. Broadcasts on the LAN (UDP :7083) to find each appliance's current
#      IP, then opens a real local TCP connection and confirms the
#      fetched key actually decrypts its status — so this doesn't report
#      success on a key that doesn't work.
#   5. Writes device_id + localKey + host + product code to
#      Core/data/haismart_devices.json (gitignored — see .gitignore, same
#      handling as phone_tokens.json).
#
# After this, tools/haismart_tools.py never touches the Haier account or
# the internet again — every control call goes straight to the AC's LAN
# IP. See tools/haismart/vendor/__init__.py for where the cloud-login and
# protocol code actually came from.
#
# NOTE for whoever runs this: it needs a real Haier account with the AC
# already paired in the Haismart/Haier U+/uHome app, and the AC reachable
# on the same LAN this script runs on. Nobody with repo access alone can
# complete this — it wasn't run as part of building this file. See the
# printed output for whether it actually reached your AC.

import asyncio
import getpass
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import DATA_DIR
from tools.haismart.vendor import haismart_hrdp as hrdp
from tools.haismart.vendor.haismart_extractor import (
    SEA_APP_CREDENTIALS,
    CloudError,
    GatewayClient,
    GatewayCreds,
    GatewayError,
    HaierCloud,
)
from tools.haismart.vendor.haismart_extractor.cloud import Request, Response

DEVICES_PATH = DATA_DIR / "haismart_devices.json"

DISCOVER_TIMEOUT = 5.0
GATEWAY_TIMEOUT = 15.0


async def _requests_transport(req: Request) -> Response:
    """
    HaierCloud's HTTP transport is injectable (see cloud.py's Transport type) specifically so a
    host isn't forced to bring httpx — this repo already depends on `requests` for everything else
    (web_tools.py, otp_tools.py, ...), so reusing it here is one dependency instead of two. requests
    is sync, so it runs in a worker thread rather than blocking the event loop the login/device-list
    calls run on.
    """
    import requests

    def call():
        resp = requests.request(
            req.method, req.url, headers=req.headers,
            data=req.body.encode("utf-8"), timeout=15,
        )
        return Response(status=resp.status_code, text=resp.text)

    return await asyncio.to_thread(call)


def _confirm_local(host: str, device_id: str, local_key: str) -> str:
    """
    Actually connect to the AC and confirm the fetched key decrypts its status. Returns "" on
    success, or a human-readable reason it didn't work — this must never report success on a key
    that silently fails, which is exactly what a bad key looks like without this check (a
    connection that completes but decrypts nothing).
    """
    try:
        blobs = hrdp.read_status(host, device_id, local_key, timeout=6.0)
    except (OSError, TimeoutError) as e:
        return f"couldn't open a TCP connection to {host}:56800 — {e}"

    if not any(hrdp.derive_status_layout(b) is not None for b in blobs):
        version = hrdp.probe_localkey_version(host, device_id)
        return (
            f"connected, but nothing decrypted with this key (AC is on localKey version "
            f"{version}) — the key may already be stale; re-run this script"
        )
    return ""


def _locate(device_id: str) -> str:
    """Broadcast for this device's current LAN IP, or ""."""
    for info in hrdp.discover(timeout=DISCOVER_TIMEOUT):
        if info.device_id.upper() == device_id.upper():
            return info.host
    return ""


async def _run() -> int:
    print("Haier AC (Haismart / U+ / uHome) local-key setup — one-time, needs your Haier account.\n")

    username = input("Haier account email or phone: ").strip()
    if not username:
        print("error: no username given", file=sys.stderr)
        return 2

    password = getpass.getpass("Haier account password (hidden): ")
    if not password:
        print("error: no password given", file=sys.stderr)
        return 2

    region = input(
        "Country dialling code the ACCOUNT was registered in (e.g. 66 Thailand, 65 Singapore, "
        "91 India) — NOT where the AC is installed: "
    ).strip().lstrip("+")
    if not region:
        print("error: no region given", file=sys.stderr)
        return 2
    if not region.isdigit():
        # Confirmed live 2026-08-20: entering the country NAME ("india")
        # instead of its dialling code ("91") isn't rejected here or by
        # the server at login -- zoneInfo just silently scopes every
        # later call (including the device list) to an unrecognized
        # region, which looks exactly like "this account has no
        # appliances" for an account that has one. Catch it here instead.
        print(f"error: '{region}' isn't a dialling code — give the number (e.g. 91), not the country name.", file=sys.stderr)
        return 2

    print("\nSigning in...")
    try:
        client, login = await HaierCloud.login(
            SEA_APP_CREDENTIALS, username, password,
            zone_info=region, transport=_requests_transport,
        )
    except CloudError as e:
        print(f"error: sign-in failed — {e}", file=sys.stderr)
        if "30032" in str(e):
            print(
                "hint: 30032 also means the region is wrong — it's the account's registration "
                "country, not the AC's.", file=sys.stderr,
            )
        return 1
    except (OSError, RuntimeError, TimeoutError) as e:
        print(f"error: could not reach Haier — {e}", file=sys.stderr)
        return 1

    devices = await client.list_devices_v2()
    if not devices:
        # Print the RAW list_devices_v2 response too, not just "it came
        # back empty" — confirmed live 2026-08-20 that fixing an
        # obviously-wrong zoneInfo (the region name typed instead of its
        # dialling code) did NOT fix this, so the empty result is real,
        # not the earlier region bug repeating. Calling client.get()
        # directly (a public method the vendored class already exposes,
        # not editing the vendored file) to see retCode/retInfo/data
        # exactly as the server sent them, since list_devices_v2() itself
        # discards everything but the parsed (currently empty) list.
        from tools.haismart.vendor.haismart_extractor.cloud import DEVICE_LIST_PATH_V2
        raw_v2 = await client.get(client.domains.uhome, DEVICE_LIST_PATH_V2)
        print(f"list_devices_v2 raw response:\n{json.dumps(raw_v2, indent=2)}")
        # list_devices_v2 is ONE of three device-list paths the vendored
        # library exposes (list_user_devices, list_devices are the other
        # two) — confirmed live 2026-08-20: a real account with a real,
        # actively-controlled AC still got an empty list back from this
        # one path. The other two's response SHAPE is marked unconfirmed
        # by the library's own docstrings ("response shape to confirm on
        # first call"), so rather than guess a parse that might also
        # silently show 0, fall back to them and print the raw response —
        # a human (or a future pass here once the real shape is known)
        # can read it directly instead of trusting an unverified parser.
        print("list_devices_v2 returned no appliances — trying the other two device-list paths...")
        for name, call in (
            ("list_user_devices", client.list_user_devices),
            ("list_devices (alt)", client.list_devices),
        ):
            try:
                raw = await call()
            except CloudError as e:
                print(f"  {name}: failed — {e}", file=sys.stderr)
                continue
            print(f"  {name} raw response:\n{json.dumps(raw, indent=2)}")
        print(
            "\nThis account has no appliances via list_devices_v2, and the two "
            "fallback paths' response shape isn't parsed yet (see raw output "
            "above) — this needs a human to look at that JSON and confirm "
            "whether the AC is actually listed under a different key/shape "
            "before this script can proceed automatically.",
            file=sys.stderr,
        )
        return 2

    print(f"Found {len(devices)} appliance(s): {', '.join(d.name or d.device_id for d in devices)}")
    print("Fetching local keys...")

    creds = GatewayCreds.derive(usdk_client_id=login.client_id, access_token=login.access_token)
    try:
        keys = await asyncio.to_thread(
            GatewayClient(creds).get_localkeys,
            [d.device_id for d in devices], timeout=GATEWAY_TIMEOUT,
        )
    except (GatewayError, OSError, RuntimeError, TimeoutError) as e:
        print(f"error: signed in fine, but the key service couldn't be reached — {e}", file=sys.stderr)
        return 1

    print("Locating appliances on this LAN (broadcasting, a few seconds)...")

    out = []
    for d in devices:
        key = keys.get(d.device_id)
        name = d.name or d.device_id
        if key is None:
            print(f"  {name}: NO KEY (offline, or cut off from Haier) — skipped")
            continue

        host = _locate(d.device_id)
        if not host:
            print(f"  {name}: key fetched, but not found on this LAN — skipped "
                  "(run this script again from the same network as the AC)")
            continue

        problem = _confirm_local(host, d.device_id, key.key)
        if problem:
            print(f"  {name}: key fetched, found at {host}, but {problem} — skipped")
            continue

        print(f"  {name}: OK — {host}, confirmed live")
        out.append({
            "name": name,
            "device_id": d.device_id,
            "local_key": key.key,
            "type_id": d.prod_no or d.uplus_id or None,
            "host": host,
        })

    if not out:
        print("\nNo appliance was fully confirmed — nothing written.", file=sys.stderr)
        return 3

    DEVICES_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEVICES_PATH.write_text(json.dumps({"devices": out}, indent=2), encoding="utf-8")
    print(f"\nWrote {len(out)} confirmed appliance(s) to {DEVICES_PATH}")
    print("haismart_tools.py is ready to use.")
    return 0


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
