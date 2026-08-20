# Core/tools/otp_tools.py
#
# ONE tool: find a recent OTP/verification code in the paired phone's SMS
# inbox. Deliberately its own file, not folded into phone_tools.py's
# general surface — reading SMS is more sensitive than anything else the
# phone integration does (SMS is the recovery/verification channel for
# nearly every account), so the blast radius of "what can read SMS" needs
# to be one narrow function, visible at a glance, not buried among
# calling/contacts/alarms.
#
# This is NOT a general SMS reader. It only ever looks at the last 5
# minutes of the inbox, and it only ever returns something that matched an
# OTP-shaped heuristic. There is no "read my texts" tool here and there
# should never be one in this file.
#
# UX this serves (built elsewhere, not here): FRED notices something that
# looks like an OTP/login prompt on screen, asks "should I try to find the
# OTP, sir?", and only calls find_otp() on an explicit yes. It is
# registered destructive=True by the orchestrator for exactly that reason
# — same confirmation gate as call_phone/send_message.
#
# Confirmed live on O3PRIS25DB005413, 2026-08-20:
#   adb shell content query --uri content://sms/inbox \
#       --projection address:body:date
# returns rows with NO extra `pm grant` needed — `content query` runs as
# the shell user, which already holds READ_SMS on this device. A real bank
# OTP SMS off this phone's inbox confirmed the expected shape (sender
# address like a bank's alphanumeric ID, an OTP-shaped code near the word
# "OTP", a transaction amount, and other account details elsewhere in the
# body) — the real row is not reproduced here (see house rule: no vault/
# personal content in commits or comments), see `_extract_code`'s tests
# below for a synthetic row of the same shape.
# `date` is epoch milliseconds. If a future device ever refuses the query,
# the fix is `adb shell pm grant <shell pkg> android.permission.READ_SMS`
# — not needed here, so not wired in.

import re

from tools.phone_tools import _adb, _resolve, device_status

# Hard, non-configurable recency bound. OTPs expire in minutes; an old SMS
# — however OTP-shaped — must never be in scope for this tool. Not exposed
# as a parameter on purpose: a caller asking for a wider window is asking
# for the wrong tool.
_MAX_AGE_MS = 5 * 60 * 1000

# Words that show up next to a real OTP. Intentionally broad across
# services rather than tuned to one bank's exact wording, since the
# service issuing the code is never known in advance.
_OTP_KEYWORD = re.compile(
    r"\b(otp|one[- ]?time\s*(?:pass\w*|code)|verification\s*code|"
    r"verify(?:ing)?|passcode|security\s*code|login\s*code|auth\s*code)\b",
    re.I,
)

# A candidate code: 4-8 letters/digits, at least one digit (rules out
# words like "SECRET" or "ICICI" sitting near the keyword), and not the
# keyword itself ("OTP" is 3 letters and wouldn't match {4,8} anyway, but
# "CODE"/"VERIFY" etc. are excluded explicitly below to be safe).
_CODE_TOKEN = re.compile(r"\b[A-Za-z0-9]{4,8}\b")
_NOT_A_CODE = {"otp", "code", "verify", "passcode", "login", "auth"}

# How close (in characters) a candidate token must sit to an OTP keyword
# to count as OTP-shaped. Tight enough to skip unrelated numbers
# elsewhere in a long banking SMS (account numbers, amounts, UPI refs),
# loose enough to cover "482910 is OTP" and "Your OTP is 482910" both.
_PROXIMITY = 25


def _extract_code(body: str):
    """
    The single most OTP-shaped code in `body`, or None.

    What this catches: a 4-8 char alnum token (with a digit in it) sitting
    within _PROXIMITY characters of a word like "OTP", "verification
    code", "passcode". What it does NOT catch: a code with no nearby
    keyword (too risky to guess at — could be an amount, a UPI ref, an
    account tail), a code longer than 8 characters, or a purely numeric
    string with digits scattered outside a single {4,8} token (e.g. phone
    numbers, which run 10 digits and correctly fail the length check).
    """
    keywords = list(_OTP_KEYWORD.finditer(body))
    if not keywords:
        return None

    best, best_dist = None, None
    for tok in _CODE_TOKEN.finditer(body):
        word = tok.group(0)
        if word.lower() in _NOT_A_CODE or not any(c.isdigit() for c in word):
            continue
        dist = min(
            max(0, kw.start() - tok.end(), tok.start() - kw.end())
            for kw in keywords
        )
        if dist <= _PROXIMITY and (best_dist is None or dist < best_dist):
            best, best_dist = word, dist

    return best


def _split_sms_row(line: str):
    """
    (address, body, date_ms) from one `content query` row, or None.

    Same partition style as phone_tools._split_row, extended to three
    fields: body legitimately contains commas ("Rs.1,570.00"), so the
    split is anchored on the field labels, not on comma-splitting. `date`
    is always the last field and always numeric, so an rpartition on
    ", date=" is safe even if body text itself contained that substring.
    """
    if not line.startswith("Row:") or "address=" not in line or ", body=" not in line:
        return None

    head, sep, rest = line.partition(", body=")
    if not sep:
        return None
    address = head.split("address=", 1)[1].strip()

    body, sep, date_str = rest.rpartition(", date=")
    if not sep or not date_str.strip().isdigit():
        return None

    return address, body.strip(), int(date_str.strip())


def _device_now_ms() -> int:
    """
    The PHONE's own clock, in epoch ms — not the PC's. The SMS `date`
    column is stamped by the device, and comparing it against a possibly
    drifted PC clock could silently widen or shrink the 5-minute window.
    Falls back to Python's clock only if the device can't be asked.
    """
    result = _adb("shell", "date", "+%s")
    out = (result.stdout or "").strip()
    if result.returncode == 0 and out.isdigit():
        return int(out) * 1000
    import time
    return int(time.time() * 1000)


def find_otp(service_hint: str = "") -> str:
    """Find a recent OTP/verification code in the phone's SMS inbox."""
    target = _resolve()
    if not target:
        return device_status()

    result = _adb(
        "shell", "content", "query", "--uri", "content://sms/inbox",
        "--projection", "address:body:date", timeout=20,
    )
    if result.returncode != 0:
        return "The phone refused the SMS query."

    cutoff = _device_now_ms() - _MAX_AGE_MS
    hint = (service_hint or "").strip().lower()

    candidates = []  # [(date_ms, address, body, code)], newest-queried order first
    for line in result.stdout.splitlines():
        row = _split_sms_row(line)
        if not row:
            continue
        address, body, date_ms = row
        if date_ms < cutoff:
            continue
        code = _extract_code(body)
        if code:
            candidates.append((date_ms, address, body, code))

    if not candidates:
        if hint:
            return f"No OTP-looking message from {service_hint} in the last 5 minutes."
        return "No OTP-looking message in the last 5 minutes."

    if hint:
        hinted = [c for c in candidates
                  if hint in c[1].lower() or hint in c[2].lower()]
        if hinted:
            _, address, _, code = hinted[0]
            return f"Found a code from {address} (matches {service_hint}): {_speak_code(code)}"

    _, address, _, code = candidates[0]
    return f"Found a code from {address}: {_speak_code(code)}"


def _speak_code(code: str) -> str:
    """
    Space a code out into individual characters ("482913" -> "4 8 2 9 1
    3") so it reads as discrete digits/letters instead of one big number
    or word. Core/audio/tts_kokoro.py's speech cleanup has no existing
    digit-sequence spacing to reuse (checked) — it only handles math
    notation (fractions, exponents) — so this is done here, once, at the
    point the code is turned into a sentence.
    """
    return " ".join(code)


if __name__ == "__main__":
    assert _extract_code("482910 is OTP to complete Rs 2,340.00 Fund Transfer") == "482910"
    assert _extract_code("Your verification code is 482913, don't share it") == "482913"
    assert _extract_code("Example Bank Acc XX129 debited Rs. 2,340.00 on 16-Aug-26") is None
    assert _extract_code("SMS BLOCK 129 to 9215676766 to dispute") is None
    assert _speak_code("482913") == "4 8 2 9 1 3"

    row = _split_sms_row(
        "Row: 7 address=AD-EXAMPLB-S, body=482910 is OTP, complete now, date=1786902111775"
    )
    assert row == ("AD-EXAMPLB-S", "482910 is OTP, complete now", 1786902111775), row

    print("ok")
