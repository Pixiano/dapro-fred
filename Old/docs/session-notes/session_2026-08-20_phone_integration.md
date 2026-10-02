# Session 2026-08-20 — Phone integration push (call log, alarms, OTP, HTTP Shortcuts, Haismart, camera)

Six tasks run in parallel via subagents against the real paired phone
(`O3PRIS25DB005413`, USB adb). This doc covers outcomes and the BIG errors
only — see each tool's own module docstring for implementation detail.

## Shipped, tested against the real device

- **`get_call_log()`** (`Core/tools/phone_tools.py`) — "who called me" /
  missed-call readback, contact-name resolution via `_read_contacts()`,
  time phrasing matching `scheduler.describe_when`'s convention.
- **`check_recent_calls()`** (`Core/tools/phone_tools.py` +
  `Core/orchestrator/proactive_checks.py`) — proactive "X called while I
  was away" on a 2-minute cycle, **gated on WhatsApp's VIP tier**, reusing
  `whatsapp_tools._read_tiers`/`tier_of` directly rather than a second
  tier system. Scope corrected mid-build (see below).
- **`find_otp()`** (`Core/tools/otp_tools.py`, its own file, deliberately
  narrow) — 5-minute recency window, OTP-shaped heuristic (keyword
  proximity, not raw digit matching), confirmation-gated same as
  `call_phone`/`send_message`. No `pm grant` needed on this device.
- **Camera remote capture** — researched and proven live, not yet wired
  into a tool. Standard `IMAGE_CAPTURE`→MediaStore→pull silently drops
  frames on this phone's scoped storage (Android 15) with no error
  signal. Working alternative: `IMAGE_CAPTURE` intent + `KEYCODE_CAMERA`
  to fire the shutter + `screencap` of the live viewfinder + pull. ~3s
  end to end, no root, no companion app.
- **Haismart AC control** — redirected away from uiautomator entirely.
  Found `github.com/enapt/haismart-local`: a real local-LAN protocol
  (port 56800, AES, one-time cloud key fetch, fully offline afterward).
  Update-cadence assumption behind the original UI-automation plan was
  checked and was wrong (real releases every 4-5 weeks) — moot now since
  the local protocol reacts to protocol changes, not app UI changes.
- **HTTP Shortcuts** — phone-side shortcuts wired to `phone_api.py`'s
  existing `POST /command` (token-authenticated, LAN-only), which had
  already anticipated this exact use case.

## Real alarms set tonight

5:10, 5:15, 5:20 AM tomorrow, labelled "School" — confirmed enabled on
the real device via clock-app screenshot.

## BIG errors

**1. Real personal data leaked into a permanent code comment.**
The OTP-finder agent quoted an actual bank SMS off the live phone
verbatim while documenting its testing — real OTP code, real transaction
amount, real account number, real name — directly in
`Core/tools/otp_tools.py`'s header comment and in test fixtures. This
violates the standing rule: no vault/personal content in commits,
comments, or tests. **Caught immediately after the agent's report, before
anything was committed.** Fixed: every real value replaced with a
synthetic equivalent of the same shape, across both `otp_tools.py` and
`test_otp_tools.py`; full test suite re-run clean afterward (4/4 passing).
Root cause: the agent treated "prove I tested against the real device" as
license to paste the real evidence, rather than describing the shape and
using synthetic data in the artifact itself. Worth remembering for future
agent instructions that touch sensitive live data: explicitly forbid
quoting real content, not just imply it via house convention.

**2. Test alarms accumulated on the real phone, no reliable way to clean
them up automatically — and a SECOND, unrelated agent then blind-tapped
the same app.** Building/testing `set_alarm()` left several leftover
alarms on the actual device (explicitly labelled "Wake up test" / "Wake
up test two", plus unlabelled disabled ones at round hours). There is no
content-provider or intent-based way to *delete* a specific Android
alarm — same class of limitation that ruled out Haismart's uiautomator
path, discovered here instead. Coordinator (me) attempted cleanup via
blind adb UI navigation and stopped partway rather than risk wiping a
real pre-existing alarm with no "before" baseline to diff against.

Separately, the HTTP Shortcuts agent — while trying to auto-tap an
"Import" confirmation dialog in an unrelated app — sent a stray swipe
that its own before/after screenshots suggest may have toggled the
**6:45 AM and 7:15 AM alarms from on to off**. It stopped rather than
guess its way through further taps to "fix" it, and flagged this
explicitly rather than silently leaving it. **Confirmed real alarms
(5:10/5:15/5:20 AM, "School") are unaffected** — verified via screenshot
after both incidents. **Status: unresolved, needs Vatsal to check the
Clock app directly** — two separate agents produced uncertain side
effects in the same app tonight, which is exactly the kind of thing
blind coordinate-based UI automation on a real device produces, and why
uiautomator was avoided for Haismart in the first place.

**3. Mid-build scope correction on `check_recent_calls()`.** Initially
built to announce every call; corrected mid-flight (agent redirected via
message while still running, not after) to VIP-tier-only, reusing
WhatsApp's tier file rather than a parallel one. Not a bug that shipped,
but worth noting since it's the kind of thing that would have shipped
wrong if the agent had been left to finish and report before the
correction landed.

## Haismart — built, vendored, not live-verified

`Core/tools/haismart_tools.py` (`get_ac_status`/`set_ac_power`/
`set_ac_temperature`/`set_ac_mode`/`set_ac_fan_speed`) plus a one-time
`Core/tools/haismart_setup.py` credential/key-fetch script. The protocol
client itself is vendored (not reimplemented) from `enapt/haismart-local`
commit `8e78351`, MIT-licensed, attribution kept — into
`Core/tools/haismart/vendor/`. Tests mock the protocol layer (482 passed).
**Needs a human to run `haismart_setup.py` once** with real Haier account
credentials on the AC's LAN — an agent has no account/hardware to do this
with, and the script won't report success unless it actually opens a TCP
connection and confirms the fetched key decrypts real status from the
unit.

## Deliberately not built tonight

- Camera capture is proven feasible but not wired into an actual FRED
  tool yet — this session only confirmed the mechanism works.
