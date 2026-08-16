# Core/tools/phone_tools.py
#
# Remote call access: FRED dials from the PC, the phone places the call.
#
# Control and audio are deliberately split. This file is control only —
# an Android CALL intent over adb. Audio stays on the phone (speakerphone
# or your own headset). The Bluetooth HFP route FRED described on
# 2026-08-14 would put call audio on the PC speakers, but it needs the PC
# to take the hands-free role, which Windows only does inside Phone Link,
# and it flips the default mic to an 8 kHz endpoint — which is exactly the
# mic the wake word listens on. Not worth it for the audio path alone.
#
# Setup, once: enable USB debugging on the phone, plug it in, accept the
# RSA prompt. For wireless, `adb tcpip 5555` then `adb connect <ip>:5555`,
# and set FRED_PHONE_ADB=<ip>:5555 so reconnects are automatic.
#
# subprocess is called with a fixed argument list (never shell=True), and
# the number is re-built from scratch out of digits after validation, so
# nothing a caller passes can become an adb or shell argument.

import difflib
import os
import re
import subprocess

from config.settings import VAULT_DIR

_ADDR = os.environ.get("FRED_PHONE_ADB", "")

# Digits, optional leading +, with spaces/dashes/parens allowed as noise.
# 5-20 digits covers short codes through international numbers.
_NUMBER = re.compile(r"^\+?[\d(][\d \-()]{3,24}$")

# The contact list lives in the vault, not in Core/data/ — 50 real names
# and numbers are exactly the kind of personal data the vault exists to
# hold outside this repo.
#
# TWO deliberate exceptions to vault rules are made here, both chosen by
# Vatsal on 2026-08-15:
#
# 1. settings.py states the vault is read-only to FRED and "nothing
#    should" grant write access. sync_contacts writes this ONE file and
#    nothing else. The write is append-only: a name already in the file
#    is never removed, and its number is only ever replaced when the
#    phone — the authority on its own contacts — disagrees, which is the
#    "unless it's a wrong number" case. Deletions never propagate.
#
# 2. contacts.md is in VAULT_EXCLUDED_FILES so the vault router never
#    embeds or injects it. Vault chunks go to the cloud APIs (the
#    sensitive-local-only flag is off), and a phone book has no business
#    riding along in a prompt. Dialing reads this file directly by path,
#    which needs no index at all.
CONTACTS_PATH = VAULT_DIR / "people" / "contacts.md"

_HEADER = (
    "# Contacts\n\n"
    "Synced from the phone by tools/phone_tools.py, ranked by call\n"
    "frequency. Append-only: entries are never deleted by a sync, and a\n"
    "number changes only when the phone disagrees with it. Edit by hand\n"
    "freely — hand edits survive, and a name not on the phone stays.\n\n"
    "Excluded from the vault index on purpose. See phone_tools.py.\n\n"
)


def _clean_number(number: str) -> str:
    """
    Return a bare +digits number, or "" if it doesn't look like a phone
    number. Trust boundary: this string ends up in an intent argument.
    """
    number = (number or "").strip()
    if not _NUMBER.match(number):
        return ""

    plus = "+" if number.startswith("+") else ""
    digits = re.sub(r"\D", "", number)

    if not 5 <= len(digits) <= 20:
        return ""

    return plus + digits


def _match_key(number: str) -> str:
    """
    Key for deciding whether two written forms are the same phone.

    ponytail: last 10 digits. Right for Indian mobiles, where the same
    contact appears as 98765 43210, +919876543210 and 09876543210 across
    the call log and the contact list. Two international numbers sharing
    a 10-digit tail would collide; switch to full-number comparison with
    a country-code normaliser if a non-IN number ever matters.
    """
    digits = re.sub(r"\D", "", number or "")
    return digits[-10:] if len(digits) >= 10 else digits


def _adb(*args, timeout: int = 15) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["adb", *args], capture_output=True, text=True, timeout=timeout
    )


def _device_ready() -> bool:
    """True if exactly one authorised device is attached, reconnecting once."""
    for attempt in range(2):
        try:
            lines = _adb("devices").stdout.splitlines()[1:]
        except (FileNotFoundError, subprocess.SubprocessError):
            return False

        if any(l.split("\t")[-1] == "device" for l in lines if "\t" in l):
            return True

        if attempt == 0 and _ADDR:
            _adb("connect", _ADDR, timeout=10)

    return False


# =========================================================
# CONTACTS
# =========================================================


def _split_row(line: str, first: str, second: str):
    """
    Pull two named columns out of one `content query` row.

    Rows look like `Row: 0 display_name=Some Name, data1=+919876543210`.
    Partitioning on ", <second>=" rather than splitting on commas,
    because a display_name legitimately contains them ("Sharma, Anil").
    """
    if not line.startswith("Row:") or f"{first}=" not in line:
        return None

    head, sep, tail = line.partition(f", {second}=")
    if not sep:
        return None

    return head.split(f"{first}=", 1)[1].strip(), tail.strip()


def _parse_contacts(output: str) -> dict:
    """{match_key: (display_name, clean_number)} from the phone's contacts."""
    found = {}
    for line in output.splitlines():
        row = _split_row(line, "display_name", "data1")
        if not row:
            continue
        name, raw = row
        number = _clean_number(raw)
        # USSD entries (*111#) and blanks live in the contact list too;
        # they fail _clean_number and drop out here rather than becoming
        # undialable "contacts".
        if name and number:
            found.setdefault(_match_key(number), (name, number))
    return found


def _parse_call_counts(output: str) -> dict:
    """{match_key: times seen} across the call log, most-called first."""
    counts = {}
    for line in output.splitlines():
        if not line.startswith("Row:") or "number=" not in line:
            continue
        raw = line.split("number=", 1)[1].split(",", 1)[0].strip()
        key = _match_key(raw)
        if key:
            counts[key] = counts.get(key, 0) + 1
    return counts


def _read_contacts() -> dict:
    """{name: number} from the vault file, insertion-ordered."""
    if not CONTACTS_PATH.exists():
        return {}

    entries = {}
    for line in CONTACTS_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("- ") or ":" not in line:
            continue
        name, _, number = line[2:].rpartition(":")
        name, number = name.strip(), number.strip()
        if name and number:
            entries[name] = number
    return entries


def _write_contacts(entries: dict):
    """Atomic rewrite — a half-written phone book is worse than none."""
    body = "".join(f"- {name}: {number}\n" for name, number in entries.items())
    CONTACTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONTACTS_PATH.with_suffix(".md.tmp")
    tmp.write_text(_HEADER + body, encoding="utf-8")
    tmp.replace(CONTACTS_PATH)


def _merge(existing: dict, incoming: list) -> tuple:
    """
    Append-only merge. Returns (merged, added, corrected).

    `incoming` is [(name, number)] in rank order. Names already present
    keep their position; only their number can change, and only because
    the phone says so. Nothing is ever dropped — a contact deleted on the
    phone stays in the file, which is the point of append-only.

    Duplicate names WITHIN one batch are not corrections. A contact with
    a mobile and a landline arrives twice under one display_name, and
    treating the second as a correction of the first both loses the
    more-called number and reports a bogus "corrected" on every sync.
    First occurrence wins — `incoming` is rank-ordered, so that is the
    number actually dialled most.
    """
    merged = dict(existing)
    added, corrected = [], []
    seen = set()

    # Identity is the NUMBER, not the name. Hand-renaming an entry —
    # adding a possessive, a nickname, a note in brackets — is a supported
    # workflow, and keying on name meant the phone's original spelling
    # came back as a brand-new contact on the very next sync. Confirmed
    # 2026-08-16: one person on file twice within minutes of a rename.
    # Left alone it does that to EVERY renamed contact, every sync,
    # forever.
    #
    # So a number already on file under any name is skipped entirely: the
    # label Vatsal chose wins over the phone's, permanently.
    by_number = {_match_key(num): name for name, num in existing.items()}

    for name, number in incoming:
        if name in seen:
            continue
        seen.add(name)

        known_as = by_number.get(_match_key(number))
        if known_as is not None and known_as != name:
            continue
        if name not in merged:
            merged[name] = number
            added.append(name)
        elif merged[name] != number:
            corrected.append((name, merged[name], number))
            merged[name] = number

    return merged, added, corrected


def sync_contacts(limit: int = 50) -> str:
    """
    Pull the most-called contacts off the phone into the vault file.
    Append-only: nothing already there is deleted.
    """
    if not _device_ready():
        return "The phone isn't connected, so I can't sync contacts."

    limit = max(1, min(int(limit), 500))

    calls = _adb("shell", "content", "query", "--uri",
                 "content://call_log/calls", "--projection", "number", timeout=60)
    book = _adb("shell", "content", "query", "--uri",
                "content://com.android.contacts/data/phones",
                "--projection", "display_name:data1", timeout=60)

    if calls.returncode != 0 or book.returncode != 0:
        return "The phone refused the contacts or call-log query."

    contacts = _parse_contacts(book.stdout)
    counts = _parse_call_counts(calls.stdout)

    if not contacts:
        return "Couldn't read any contacts off the phone."

    # Rank by call count, then keep unseen contacts as a tail so a
    # limit larger than the number of people actually called still fills.
    #
    # Name and number are tiebreakers, not decoration: `content query`
    # gives no ordering guarantee, so two entries on the same call count
    # (usually zero) came back in a different order run to run. With
    # _merge keeping the first of a duplicated name, that made every
    # sync "correct" a two-number contact back and forth forever.
    ranked = sorted(
        contacts.items(),
        key=lambda kv: (-counts.get(kv[0], 0), kv[1][0].lower(), kv[1][1]),
    )
    incoming = [name_num for _, name_num in ranked][:limit]

    merged, added, corrected = _merge(_read_contacts(), incoming)

    if not added and not corrected:
        return f"Contacts already up to date — {len(merged)} on file."

    _write_contacts(merged)

    parts = [f"Synced {len(merged)} contacts"]
    if added:
        parts.append(f"{len(added)} new ({', '.join(added[:5])})")
    if corrected:
        fixed = ", ".join(f"{n} now {new}" for n, _, new in corrected[:3])
        parts.append(f"{len(corrected)} corrected ({fixed})")

    return ". ".join(parts) + "."


def find_contact(name: str):
    """
    Resolve a spoken name to (name, number).

    Returns (None, message) when it can't — no file, no match, or an
    ambiguous one. Voice transcription mangles names constantly, so an
    exact match is tried first, then substring, then difflib; but an
    ambiguous result is never guessed at, because the cost of guessing
    is calling the wrong person.
    """
    entries = _read_contacts()
    if not entries:
        return None, "I don't have a contact list yet — say 'sync contacts' first."

    query = (name or "").strip().lower()
    if not query:
        return None, "I didn't catch a name."

    for known, number in entries.items():
        if known.lower() == query:
            return (known, number), ""

    partial = [k for k in entries if query in k.lower()]
    if len(partial) == 1:
        return (partial[0], entries[partial[0]]), ""
    if len(partial) > 1:
        return None, f"That matches {len(partial)}: {', '.join(partial[:5])}. Which one?"

    close = difflib.get_close_matches(query, [k.lower() for k in entries], n=3, cutoff=0.7)
    if len(close) == 1:
        match = next(k for k in entries if k.lower() == close[0])
        return (match, entries[match]), ""
    if len(close) > 1:
        return None, f"Did you mean {' or '.join(close)}?"

    return None, f"I don't have anyone called {name} in my contacts."


def resolve_target(number: str):
    """
    Turn whatever the model passed — a number or a name — into
    (label, clean_number). Returns (None, message) on failure.

    Shared by call_phone and the orchestrator's confirmation prompt, so
    the number Vatsal is asked to confirm is the number that gets dialled.
    """
    clean = _clean_number(number)
    if clean:
        return (clean, clean), ""

    hit, message = find_contact(number)
    if not hit:
        return None, message

    name, stored = hit
    clean = _clean_number(stored)
    if not clean:
        return None, f"{name}'s number in my contacts isn't dialable."

    return (f"{name} ({clean})", clean), ""


# =========================================================
# CALLING
# =========================================================


def call_phone(number: str = "") -> str:
    """Place a call from the paired Android phone, by number or contact name."""

    target, message = resolve_target(number)
    if not target:
        return message

    label, clean = target

    if not _device_ready():
        return "The phone isn't connected. Plug it in, or check it's awake on the network."

    result = _adb("shell", "am", "start", "-a",
                  "android.intent.action.CALL", "-d", "tel:" + clean)

    if result.returncode != 0 or "Error" in result.stderr:
        return f"Couldn't place the call: {result.stderr.strip() or 'the phone refused it'}"

    return f"Calling {label} now."


def hang_up() -> str:
    """End the call in progress on the phone."""

    if not _device_ready():
        return "The phone isn't connected."

    result = _adb("shell", "input", "keyevent", "KEYCODE_ENDCALL")

    if result.returncode != 0:
        return "Couldn't hang up."

    return "Call ended."


if __name__ == "__main__":
    assert _clean_number("+91 98765 43210") == "+919876543210"
    assert _clean_number("(022) 2345-6789") == "02223456789"
    assert _clean_number("911") == ""           # too short
    assert _clean_number("") == ""
    assert _clean_number("hello") == ""
    assert _clean_number("555; rm -rf /") == "" # injection attempt
    assert _clean_number("+1-800-FLOWERS") == ""

    # Same phone, three written forms the providers actually return.
    assert _match_key("+919876543210") == _match_key("09876543210") == "9876543210"

    # A display_name containing a comma must not split the row.
    assert _split_row(
        "Row: 0 display_name=Sharma, Anil, data1=+919000000001",
        "display_name", "data1",
    ) == ("Sharma, Anil", "+919000000001")

    rows = (
        "Row: 0 display_name=Test One, data1=+919000000001\n"
        "Row: 1 display_name=Account Info, data1=*111#\n"      # USSD, dropped
        "Row: 2 display_name=, data1=+919000000002\n"          # nameless, dropped
    )
    parsed = _parse_contacts(rows)
    assert len(parsed) == 1 and parsed["9000000001"][0] == "Test One"

    counts = _parse_call_counts(
        "Row: 0 number=9000000001\nRow: 1 number=+919000000001\nRow: 2 number=9000000009\n"
    )
    assert counts["9000000001"] == 2 and counts["9000000009"] == 1

    # Append-only: absent names survive, a changed number is a correction.
    merged, added, corrected = _merge(
        {"Gone From Phone": "+919000000003", "Test One": "+919000000001"},
        [("Test One", "+919000000099"), ("Brand New", "+919000000004")],
    )
    assert "Gone From Phone" in merged           # never deleted
    assert added == ["Brand New"]
    assert corrected == [("Test One", "+919000000001", "+919000000099")]
    assert merged["Test One"] == "+919000000099"

    # One person, two numbers, one batch: not a correction, and the
    # higher-ranked (first) number is the one kept.
    merged, added, corrected = _merge(
        {}, [("Two Lines", "+919000000005"), ("Two Lines", "+919000000006")]
    )
    assert merged == {"Two Lines": "+919000000005"}
    assert added == ["Two Lines"] and corrected == []

    # A hand-renamed contact must not come back under the phone's own
    # spelling. Same number, different label: the label on file wins.
    merged, added, corrected = _merge(
        {"Renamed By Hand": "+919000000007"},
        [("Phone Spelling", "+919000000007")],
    )
    assert merged == {"Renamed By Hand": "+919000000007"}, merged
    assert added == [] and corrected == []

    # Same person, number written differently on each side — still one
    # contact, because identity is the last 10 digits, not the string.
    merged, added, corrected = _merge(
        {"Kept Label": "+919000000008"},
        [("Other Label", "09000000008")],
    )
    assert merged == {"Kept Label": "+919000000008"}, merged
    assert added == []

    # A genuinely new person is still added — the skip must not swallow
    # everything just because some numbers already exist.
    merged, added, _ = _merge(
        {"Kept Label": "+919000000008"},
        [("Other Label", "+919000000008"), ("Brand New", "+919000000010")],
    )
    assert added == ["Brand New"], added

    print("ok")
