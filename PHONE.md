# Phone

Two separate things share this page, because they run in opposite directions:

| | Direction | Runs on |
|---|---|---|
| **Calling** | PC tells the phone to dial | `Core/tools/phone_tools.py`, over adb |
| **Remote** | Phone tells FRED to do something | `Core/web/phone_api.py`, over HTTP |

Neither needs the other. Calling works with no app; the remote works with no cable.

---

## Calling

FRED dials, the phone places the call, and the audio stays on the phone — speakerphone or whatever headset you already wear. Control and audio are deliberately split.

The Bluetooth hands-free route (PC as headset, call audio on the PC speakers) was considered and rejected: Windows only takes the hands-free role inside Phone Link, and engaging HFP flips the default mic to an 8 kHz endpoint — the same mic the wake word listens on. Not worth it for the audio path alone.

**iOS is out of scope permanently.** iOS cannot place a call programmatically, app or not; `tel:` requires a physical tap and no entitlement changes that.

### Setup

Once, over USB:

```
adb devices          # accept the RSA prompt on the phone
adb tcpip 5555       # switch to wireless
```

Then unplug and:

```
adb connect 192.168.0.105:5555
setx FRED_PHONE_ADB 192.168.0.105:5555
```

`_device_ready()` reconnects to `FRED_PHONE_ADB` automatically when it finds no device, which is the daily failure mode once the phone sleeps.

Give the phone a static IP (Wi-Fi → network → Advanced → IP settings → Static) or the address moves and the env var rots.

**`adb tcpip` does not survive a phone reboot.** Plug in and re-run it after one. There is no way around this short of Android's Wireless Debugging pairing flow, which has the same problem plus a rotating port.

### Using it

```
"call Mom"      -> "Calling Mom (+91…) — confirm? (yes/no)"  -> "yes"
"hang up"
"sync contacts"
```

`call_phone` is registered `destructive=True`, so it routes through the confirmation gate. The prompt resolves the name *before* asking, so the number you confirm is the number that gets dialled — a name that doesn't resolve dies at the prompt rather than after a "yes". Same reasoning as the `kill_process` branch beside it.

### Contacts

`sync_contacts` reads the phone's call log and contact list over adb, ranks by call frequency, and writes the top 50 to `<vault>/people/contacts.md` as `- Name: number` lines.

Two deliberate exceptions to standing vault rules, both chosen explicitly:

1. **The vault is read-only to FRED**, except this one file. Writes are append-only: a name already on file is never removed, and its number changes only when the phone disagrees with it. Deleting a contact on the phone does not delete it here.
2. **`contacts.md` is in `VAULT_EXCLUDED_FILES`**, so the router never embeds or injects it. Vault chunks reach the cloud APIs; a phone book has no business riding along in a prompt. Dialing reads the file directly by path and never needs the index.

Hand edits survive every sync. Adding someone by hand is a supported workflow, not a workaround.

Name resolution is exact → substring → `difflib`. An ambiguous match asks which one rather than guessing, because the cost of guessing is calling the wrong person.

---

## Remote (phone → FRED)

`Core/web/phone_api.py` binds `0.0.0.0:8779` and is started by `pill_app._on_ready`. It shares the file bus the HUD console already uses — `command.json` in, `command_reply.json` out — so every registered tool is reachable from the phone with no per-command code.

`hud/server.py` stays loopback-only and is not involved. It serves live machine telemetry, which has no business on the network; that separation is why this is a second server rather than a flag on the first.

### Endpoints

| | |
|---|---|
| `POST /command` | `{"text": "..."}` or a bare text/plain body → `{"reply": "..."}` |
| `GET /ping` | `{"ok": true}` |
| `GET /phone/next` | `204` — reserved for the PC→phone action queue |

All require `X-FRED-Token`.

### Setup

Tokens are generated on first run into `Core/data/phone_tokens.json` (gitignored), one per device, so a lost phone is revoked on its own rather than by rotating everything.

Firewall, elevated, once:

```
New-NetFirewallRule -DisplayName "FRED phone API" -Direction Inbound `
  -Protocol TCP -LocalPort 8779 -Action Allow -Profile Any -RemoteAddress LocalSubnet
```

`LocalSubnet` also excludes Tailscale peers, keeping this a LAN-only door.

Then on the phone, the *HTTP Shortcuts* app: `POST` to `http://<pc-ip>:8779/command`, header `X-FRED-Token`, body type **plain text** with the command in it. A blank body is the usual first-try mistake and answers `400`.

Must be `http://`, not `https://` — there is no TLS here. A self-signed cert on a LAN is a pinning chore that only helps against an attacker already on the Wi-Fi, at which point the token is the control that matters.

### What this assumes

FRED's replies can contain vault content, and the vault's own rules forbid personal material leaving the machine. A reply crossing the LAN to your own phone is treated as still on this machine's network rather than leaving it. That holds only while the firewall stays scoped to the local subnet, the Wi-Fi is yours and not shared, and this is never port-forwarded, UPnP'd, or tunnelled. Break any of those and the vault rule breaks with it.

---

## Next

An Android app replaces the transport inside `phone_tools._send()` — the tool names, schemas, confirmation gate and intent routing do not change. Phase 0 is this page. Phase 1 is the same thing with a real UI; Phase 2 adds the PC→phone long-poll and retires the adb cable.

Do not build the app to avoid an `adb connect`. Build it when the HTTP Shortcuts version annoys you.
