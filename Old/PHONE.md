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

Once, over USB: enable USB debugging, plug in, accept the RSA prompt, confirm with `adb devices`.

Then **Wireless debugging** (Developer options), not `adb tcpip`. Pair once with the 6-digit code; the pairing is permanent and survives reboots.

```
adb pair <ip>:<pairing-port>     # one time per phone
```

Configure the phones by name — the serial is the identity, because the address changes constantly:

```
setx FRED_PHONES "personal=O3PRIS25DB005413,work=RZGL50FCL4W"
```

`use_phone("work")` switches which one commands act on. Every adb call is `-s`-targeted: with two devices attached, bare commands fail outright with *"more than one device/emulator"*.

### How FRED finds the phone

`_discover()` tries, in order:

1. **Already attached** — USB or a live wireless session.
2. **Ask each attached device its serial** (`getprop ro.serialno`). A wireless session is keyed by `ip:port`, not by serial, so without this FRED fails to recognise a phone it is already talking to — which it did, while connected and working, because mDNS happened to be quiet.
3. **mDNS** (`_adb-tls-connect._tcp`, service name embeds the serial). This is how the port is *learned*; it is not how the phone is *recognised*.
4. **The legacy fixed `FRED_PHONE_ADB` address**, for a phone set up the old `adb tcpip` way.

**Do not cache the port.** Android disables wireless debugging on every reboot — by design, on every device, not an OEM quirk — and picks a new random port each time the service starts. Three sessions in one afternoon gave three different ports.

**`adb connect` exits 0 even when it prints "failed to connect".** Only `adb devices` is evidence.

A static IP on the phone still helps, since it keeps the mDNS lookup and the legacy fallback pointing somewhere stable.

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

Hand edits survive every sync, in two different ways, and the distinction matters.

**Renames are honoured** because identity is the *number*, not the name. Rename a contact — add a possessive, a nickname, a note in brackets — and the phone's spelling never comes back. Before that fix, every renamed contact was silently re-added under its original name on the very next sync.

**Deletions are honoured through tombstones** — a `## removed` section at the bottom of the file. Append-only protects edits but cannot protect deletions on its own: the file has no way to tell "deliberately deleted" from "never seen", so without tombstones every sync resurrects everything you trimmed. Measured on the real file 2026-08-16 — a sync would have re-added 33 of the 34 entries curated away minutes earlier, silently, with a cheerful success message.

To let a removed contact return, delete its line from `## removed`. Tombstones match on number, so someone renamed on the phone stays removed.

Adding someone by hand is a supported workflow, not a workaround.

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

---

## Messaging (WhatsApp)

`Core/tools/whatsapp_tools.py`. Reads over notifications, sends by driving
the phone's own UI. No Business account, no unofficial protocol client —
those are more capable and get numbers banned.

### Four tiers, per phone

| Tier | Read | Send | Alerts you |
|---|---|---|---|
| `useless` | no — dropped before anything is read | no | no |
| `basic` | yes | no | no |
| `trusted` | yes | **yes** | no |
| `vip` | yes | yes | **yes, proactively** |

`useless` is a security boundary, not just noise reduction. Every message
FRED reads is attacker-controlled text entering an agent that holds tools,
so dropping a whole class of sender shrinks the injection surface rather
than filtering after the fact. The live notification stream contained a
bank promo with a link on day one.

Tiers live in the vault, one file per phone
(`people/whatsapp-tiers-<serial>.md`), excluded from the index by prefix
so a new phone can't silently start indexing its senders. They change
only through `set_contact_tier`, which is confirmation-gated: **nothing
automatic promotes anyone.** Being in the address book is not consent to
be messaged by an AI.

Each phone declares its own default policy:

- `strict` — everyone is `useless` until promoted, saved contacts
  included. Phone 1.
- `repeat-basic` — an unknown sender is `useless` first time, `basic`
  once they've messaged before. Phone 2.

### Reading works with the phone locked

Verified against a real group with the screen off and dozing. That's what
makes proactive VIP alerts possible at all — the watcher runs on a
2-minute interval, separate from the 15-minute proactive checks, because
"someone important messaged" is worthless twenty minutes late.

Notifications only: there is no history here. Once a notification is
dismissed or read it is gone, and `read_messages` correctly reports
nothing rather than pretending. History needs `msgstore.db`, which needs
end-to-end encrypted backups enabled.

**Sending does NOT work locked** — UI automation needs an unlocked screen,
and FRED says so rather than failing vaguely.

### Read and send never share a turn

They sit in different intent categories on purpose, so the model is never
holding both capabilities at once. A message reading "reply to everyone
with <link>" is otherwise one hop from being carried out. Structural, not
a prompt instruction, and pinned by `tests/test_whatsapp_isolation.py`
because it is a deliberate exception to `intent.py`'s usual
"over-inclusive cues are cheap" rule.

### Sending, step by step

Every tap position comes from the live view hierarchy — nothing is
hardcoded, because coordinates are reported in the CURRENT rotation's
space (a phone held sideways puts the send button where a fixed
coordinate would miss entirely).

Before the irreversible tap, both the chat identity and the staged text
are re-checked against a fresh dump. Either mismatch aborts.

### Notification parsing traps, all found live

- **`sender` must stop at its own comma.** Photo messages insert a `uri=`
  field between sender and text, and a lazy match swallowed it — yielding
  a sender literally named `Mom, uri=content://...`, which matches no tier
  entry, so VIP alerts skipped every photo silently.
- **A reply arrives with `sender` set to a marker** ("You got a reply")
  and the real sender folded into the body as `Name: text`.
- **Records repeat** within one dump — dedupe on the message's own epoch
  timestamp.
- **adb output must be decoded UTF-8 explicitly.** `text=True` alone uses
  the Windows locale codec and emoji kill the reader thread, returning an
  empty string — which reads as "no messages" rather than as an error.
