"""Checks for tools.phone_tools.set_alarm (2026-08-20).

Same house style as test_llm_client_silent_turn_fixes.py: bare asserts,
sys.path insert, no framework. _adb is faked the same way phone_tools'
own __main__ block fakes it for _refresh_wireless_cache."""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tools.phone_tools as pt

_real_adb = pt._adb
_real_device_ready = pt._device_ready


def _fake_adb(returncode=0, stderr=""):
    calls = []

    def fake(*args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, returncode, stdout="", stderr=stderr)

    return fake, calls


# --- validation: bad input returns a plain error string, never raises,
# and never even reaches adb (device is "connected" throughout so a call
# to adb would prove the validation didn't short-circuit).
pt._device_ready = lambda: True
fake, calls = _fake_adb()
pt._adb = fake

assert "0-23" in pt.set_alarm(24, 0)
assert calls == [], "an out-of-range hour must not touch adb"

assert "0-59" in pt.set_alarm(7, 60)
assert calls == []

assert "not a valid time" in pt.set_alarm("noon", 0)
assert calls == []

assert "not a valid time" in pt.set_alarm(7, "half")
assert calls == []

# Negative hour, the sentinel default, must also be rejected rather than
# silently accepted as "midnight-ish".
assert "0-23" in pt.set_alarm(-1, 0)

# --- not connected: caught before adb, same wording style as call_phone.
pt._device_ready = lambda: False
result = pt.set_alarm(7, 15)
assert "isn't connected" in result
assert calls == []

# --- happy path, no label: SKIP_UI must be true or this is useless from
# a voice command (it would just open the "new alarm" screen and wait for
# a tap — confirmed live 2026-08-20 this is the difference that matters).
pt._device_ready = lambda: True
fake, calls = _fake_adb()
pt._adb = fake
result = pt.set_alarm(7, 5)
assert result == "Alarm set for 07:05.", result
(args,) = calls
assert "android.intent.action.SET_ALARM" in args
i = args.index("android.intent.extra.alarm.SKIP_UI")
assert args[i - 1] == "--ez" and args[i + 1] == "true"
hi = args.index("android.intent.extra.alarm.HOUR")
assert args[hi + 1] == "7"
mi = args.index("android.intent.extra.alarm.MINUTES")
assert args[mi + 1] == "5"
assert "android.intent.extra.alarm.MESSAGE" not in args, "no label given, no MESSAGE extra expected"

# --- a labelled alarm: the label must arrive wrapped in a literal pair of
# double quotes, because `adb shell` joins its argv into one string that
# the phone's OWN shell re-tokenizes on whitespace — an unquoted
# multi-word label breaks the intent (confirmed live: am start printed
# "pkg=<second word>" and refused to resolve the intent at all).
fake, calls = _fake_adb()
pt._adb = fake
result = pt.set_alarm(6, 30, "wake up")
assert result == "Alarm set for 06:30 (wake up).", result
(args,) = calls
mi = args.index("android.intent.extra.alarm.MESSAGE")
assert args[mi + 1] == '"wake up"', args[mi + 1]

# --- label sanitising: characters that could break out of that quoting
# on the phone's shell are stripped, not escaped — same instinct as
# _clean_number rebuilding a number from scratch rather than trying to
# be clever with what a caller handed in.
assert pt._clean_label('say "hi" `whoami` $HOME \\x') == "say hi whoami HOME x"
assert pt._clean_label("   ") == ""
assert pt._clean_label("a" * 200) == "a" * 80, "label must be capped, not left unbounded"

# --- adb itself refusing (non-zero exit) is reported, not raised.
fake, calls = _fake_adb(returncode=1, stderr="Error: something")
pt._adb = fake
result = pt.set_alarm(9, 0)
assert result.startswith("Couldn't set the alarm"), result

pt._adb = _real_adb
pt._device_ready = _real_device_ready

print("ok")
