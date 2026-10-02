# Wireless adb auto-reconnect — test report

**2026-08-16 · phone O3PRIS25DB005413 (LZX417, Android 15)**

Question: can a small boot-time app keep FRED connected to the phone over
wireless adb, with no manual step after a phone reboot?

**Answer: yes, and the app is far smaller than expected — about one line
of real work.** Every link was proven over adb without writing any app,
because `adb shell` already holds the exact permission the app would need.

---

## What was tested

| # | Test | Result |
|---|---|---|
| 1 | Is the toggle a readable setting? | **Yes** — `global adb_wifi_enabled` |
| 2 | Is there a port setting? | **No** — nothing in `settings list global` |
| 3 | Is the port in netstat on-device? | **No** — no listener visible |
| 4 | Does writing the setting control the service? | **Yes** — off/on both took effect |
| 5 | Does Android disable it on reboot? | **Yes** — read back `0`, uptime 56s |
| 6 | Can it be re-enabled purely by the setting, post-boot? | **Yes** |
| 7 | Does mDNS then announce the new port? | **Yes**, within seconds |
| 8 | Can the PC discover-then-connect with no manual step? | **Yes** |
| 9 | Does the wireless link carry the real work? | **Yes** — notifications, uiautomator, backup dir |

Ports observed across sessions: **37443 → 36563 → 40017.** Randomised
every time the service starts, confirming there is nothing to cache.

### The decisive sequence (reboot, wired, nothing touched by hand)

    uptime                          56s        <- genuinely rebooted
    settings get adb_wifi_enabled   0          <- Android turned it off
    adb mdns services               (empty)    <- nothing announcing
    settings put adb_wifi_enabled 1
    settings get adb_wifi_enabled   1
    adb mdns services               192.168.0.105:40017   <- announced itself
    adb connect 192.168.0.105:40017 connected
    adb -s ...:40017 shell          works

---

## What the app has to do

**One thing.** On `BOOT_COMPLETED`, write one setting:

    Settings.Global.putInt(resolver, "adb_wifi_enabled", 1)

That is the whole app. It does **not** need to discover or report the
port — test 7 shows mDNS publishes it automatically once the service
starts, and test 8 shows the PC can find it from there. An earlier plan
had the app POSTing its address to FRED's `:8779` endpoint; that is now
unnecessary.

Manifest needs:

- `android.permission.RECEIVE_BOOT_COMPLETED`
- `android.permission.WRITE_SECURE_SETTINGS`
- a `BroadcastReceiver` for `android.intent.action.BOOT_COMPLETED`

No foreground service, so Android 15's background-start restrictions do
not apply — a receiver doing a single settings write is allowed.

### One-time setup per phone, over USB

    adb install app.apk
    adb shell pm grant <package> android.permission.WRITE_SECURE_SETTINGS

Normal apps cannot hold `WRITE_SECURE_SETTINGS`; it is granted this way
and then persists. It is revoked only if the app is reinstalled.

---

## What FRED needs to change

`_device_ready()` in `Core/tools/phone_tools.py`, in order:

1. Already connected? Use it.
2. `adb mdns services` -> find the `_adb-tls-connect._tcp` entry whose
   name contains this phone's serial -> `adb connect <ip:port>`.
   The service name embeds the serial (`adb-O3PRIS25DB005413-uKhkEy`),
   which is also how the right phone gets picked when two are present.
3. Fall back to a configured fixed address (legacy `adb tcpip` route).
4. Fail with something actionable: name the phone and say wireless
   debugging looks off, rather than "phone isn't connected".

**Do NOT cache the port.** Three sessions produced three different ports;
a cache would be stale more often than not.

`_adb()` must also pass `-s <target>`. Confirmed necessary and confirmed
working: with USB and wireless both attached, `adb devices` listed two
entries and bare commands would have failed with "more than one
device/emulator". `adb -s 192.168.0.105:40017 shell ...` targeted
correctly.

---

## Corrections to earlier assumptions

- **"mDNS discovery is unreliable."** Wrong. The one failure happened
  while wireless debugging was switched off after a reboot — there was
  nothing to discover. Every test with the service running found it
  within seconds.
- **"Cache the last-known-good address as the primary path."** Wrong.
  The port changes on every service start, not just on reboot.
- **"Wireless debugging may reset on this OEM ROM."** Not an OEM quirk —
  Android disables it on every reboot on every device, by design.

---

## Not yet tested

- **The app itself.** Everything above was driven through `adb shell`,
  which already holds `WRITE_SECURE_SETTINGS`. The assumption that a
  third-party app granted the same permission behaves identically is
  standard and well-used, but it is an assumption until an APK runs.
- **Whether the boot receiver survives this ROM.** Aggressive OEM battery
  management is the usual reason a `BOOT_COMPLETED` receiver never fires.
  May need battery-optimisation exemption and autostart permission.
- **Second phone.** Everything here is phone 1 only.
- **Build tooling.** `gradle` is not on PATH and the installed JDK is
  Java 8, which is too old for current Android Gradle Plugin. Building
  the APK needs Android Studio's bundled JDK/Gradle, or a newer JDK.

---

## Recommendation

Build the app — it is genuinely tiny and it removes the only manual step
left in the wireless path. But **not before messaging works over USB.**
Wired is the right place to debug the feature; the app only fixes the
transport, and fixing transport for a feature that does not exist yet is
the wrong order.

---

# WhatsApp send + read — test report

**2026-08-16, same phone, over USB.** A real message was sent to a real
group and the replies were read back. Both paths work.

## Send

Verified end to end, with **no hardcoded coordinates anywhere** — every
tap position came from the live view hierarchy.

| Step | Mechanism |
|---|---|
| Stage the text | `am start -a android.intent.action.SEND -t text/plain --es android.intent.extra.TEXT '<msg>' -p com.whatsapp` |
| Find the chat | `uiautomator dump` -> node with `text="<chat name>"` |
| Open it | `input tap` on that node's centre |
| Verify | chat header matches AND staged text present, else abort |
| Send | `input tap` on the node with `content-desc="Send"` |
| Confirm | `com.whatsapp:id/entry` empty, new `com.whatsapp:id/message_text` bubble |

### Two things that would have broken a naive implementation

**Component-targeted intents are refused.** Aiming at
`com.whatsapp/.contact.ui.picker.ContactPicker` raises
`SecurityException: not exported`. The package-scoped form
(`-p com.whatsapp`) is the supported route and works.

**Coordinates are in the CURRENT rotation's space, not the physical
one.** `wm size` reported `720x1612` while the Send button sat at
`bounds=[1424,608][1520,704]` — nonsense in portrait, correct in
landscape. Reading bounds from the dump handles this automatically;
anything hardcoded would have tapped a random part of a live chat.

### Operational note

The device re-locks on the 60s screen timeout and the picker is then
dismissed, which killed one attempt mid-run. Also, immediately after a
reboot the device is pre-first-unlock (`FallbackHome`) and WhatsApp data
is not decrypted at all. Both mean: **UI automation requires an unlocked
screen**, and FRED must detect and say so rather than fail vaguely.

## Read

Works from `dumpsys notification --noredact`, and — this is the important
part — **while the phone is locked and dozing.** No unlock, no screen on,
no foreground app. That is exactly what proactive VIP alerts need.

### The data is structured, not a display string

WhatsApp uses MessagingStyle, so each message is its own bundle:

    Bundle[{ sender_person=..., sender=<name>, text=<body>, time=<epoch ms> }]

So sender, body and timestamp are separate fields. Do NOT parse the
rendered `"<name>: <body>"` string — the fields are already there.

Consequences:

- **Tier routing is exact.** `sender` is a field, so VIP matching is
  reliable rather than heuristic.
- **Dedupe by `time`.** The same notification record appeared three times
  in one dump. Without dedupe FRED announces a message three times.
- **A backlog is available.** `android.textLines` carried 7 entries
  across chats, so a poll that misses a beat still catches recent
  messages rather than only the newest.

### Two parsing traps, both seen live

**The "reply" wrinkle.** When someone replies to your message, one bundle
arrives with `sender` set to a marker string (an arrow plus "You got a
reply") and the real sender folded into `text` as `"<name>: <body>"`.
Detect that marker and recover the true sender from the text prefix,
or FRED will announce a message from someone called "You got a reply".

**Encoding.** Message bodies contain emoji. `dumpsys` output must be read
as UTF-8 explicitly or bodies are corrupted before FRED sees them.
Observed mangling was a console artefact, but the risk in the reader is
real.

### The "Useless" tier justified itself immediately

The notification stream contained a bank promotional blast, complete with
a link, sitting alongside family messages. That is precisely the class of
attacker-controlled text that should be dropped before it ever reaches
the model — the prompt-injection surface, not just noise.

## Still unproven

- 1:1 chats (this was a group; picker node layout not yet confirmed)
- Everything over wireless (this ran on USB)
- History via `msgstore.db` — still `.crypt14`, blocked on enabling
  end-to-end encrypted backups
- Second phone
