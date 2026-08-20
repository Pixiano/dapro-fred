# Core/tests/test_otp_tools.py
#
# find_otp is deliberately the ONLY SMS-reading surface in this codebase
# (see tools/otp_tools.py's module docstring) — narrow scope, so the tests
# pin exactly that: recent OTP-shaped messages are found, old ones are
# excluded even when OTP-shaped, a service_hint prefers a matching sender
# over a more recent non-matching one, and "nothing found" is a plain,
# honest string rather than a guess.
#
# Same fake-adb shape as phone_tools.py's own __main__ self-test: _adb is
# swapped for a function that returns a canned `content query` dump, so
# nothing here touches a real device.

import subprocess

from tools import otp_tools

_NOW_MS = 1_800_000_000_000  # arbitrary fixed "now" for device time


def _row(idx, address, body, age_ms):
    return f"Row: {idx} address={address}, body={body}, date={_NOW_MS - age_ms}"


def _install_fake(rows, now_ms=_NOW_MS):
    """Patch otp_tools._adb and _device_now_ms; caller must restore both."""
    dump = "\n".join(rows) + "\n"

    def _fake_adb(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout=dump, stderr="")

    otp_tools._adb = _fake_adb
    otp_tools._resolve = lambda: "FAKESERIAL"
    otp_tools._device_now_ms = lambda: now_ms


def _restore(real_adb, real_resolve, real_now):
    otp_tools._adb = real_adb
    otp_tools._resolve = real_resolve
    otp_tools._device_now_ms = real_now


def test_recent_otp_shaped_message_is_found():
    real_adb, real_resolve, real_now = (
        otp_tools._adb, otp_tools._resolve, otp_tools._device_now_ms
    )
    try:
        _install_fake([
            _row(0, "AD-EXAMPLB-S", "482910 is OTP to complete your transfer", 30_000),
        ])
        result = otp_tools.find_otp()
        assert "482910" not in result  # spoken form is spaced out
        assert "4 8 2 9 1 0" in result
        assert "AD-EXAMPLB-S" in result
    finally:
        _restore(real_adb, real_resolve, real_now)


def test_old_otp_shaped_message_is_excluded():
    real_adb, real_resolve, real_now = (
        otp_tools._adb, otp_tools._resolve, otp_tools._device_now_ms
    )
    try:
        # 6 minutes old — OTP-shaped, but past the 5-minute hard bound.
        _install_fake([
            _row(0, "AD-EXAMPLB-S", "517203 is OTP to complete your transfer", 6 * 60_000),
        ])
        result = otp_tools.find_otp()
        assert "517203" not in result
        assert "No OTP-looking message" in result
    finally:
        _restore(real_adb, real_resolve, real_now)


def test_service_hint_prefers_matching_sender_over_newer_message():
    real_adb, real_resolve, real_now = (
        otp_tools._adb, otp_tools._resolve, otp_tools._device_now_ms
    )
    try:
        _install_fake([
            # Newest first, as content query returns them.
            _row(0, "VM-PAYTM-S", "555555 is your Paytm OTP", 10_000),
            _row(1, "AX-AMAZON-S", "222222 is your Amazon verification code", 60_000),
        ])
        result = otp_tools.find_otp(service_hint="Amazon")
        assert "2 2 2 2 2 2" in result
        assert "555555" not in result and "5 5 5 5 5 5" not in result
    finally:
        _restore(real_adb, real_resolve, real_now)


def test_nothing_found_is_a_plain_honest_string():
    real_adb, real_resolve, real_now = (
        otp_tools._adb, otp_tools._resolve, otp_tools._device_now_ms
    )
    try:
        _install_fake([
            _row(0, "VM-EXAMPLB-S", "Your account was debited Rs 2,340 today", 10_000),
        ])
        result = otp_tools.find_otp()
        assert result == "No OTP-looking message in the last 5 minutes."

        result_hint = otp_tools.find_otp(service_hint="Amazon")
        assert result_hint == "No OTP-looking message from Amazon in the last 5 minutes."
    finally:
        _restore(real_adb, real_resolve, real_now)


if __name__ == "__main__":
    test_recent_otp_shaped_message_is_found()
    test_old_otp_shaped_message_is_excluded()
    test_service_hint_prefers_matching_sender_over_newer_message()
    test_nothing_found_is_a_plain_honest_string()
    print("ok")
