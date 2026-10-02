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
import json
import os
import re
import subprocess
import time
from datetime import datetime

from config.settings import DATA_DIR, VAULT_DIR

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


# Which phone a command goes to. Every adb call is targeted with -s from
# 2026-08-16 on, because bare commands stop working the moment a second
# device appears — confirmed live with both phones attached:
# "adb: more than one device/emulator". One phone at a time is the normal
# case, but "normal" is not "guaranteed", and the failure is total.
#
# Phones are named rather than indexed so "call Mom from my work phone"
# means something, and so the serial (which never changes) is the identity
# rather than the address (which changes constantly — see _discover).
#
#   FRED_PHONES = "personal=O3PRIS25DB005413,work=RZGL50FCL4W"
#
# First entry is the default. A bare serial with no name is allowed and
# gets named "phone".
_PHONES_RAW = os.environ.get("FRED_PHONES", "")

# Legacy single-address setting, still honoured as the last-resort address
# for the default phone (see _discover step 3).
_ADDR = os.environ.get("FRED_PHONE_ADB", "")

_active_phone = ""      # name; empty means "the default"


# Wireless enabled 2026-08-17 at Vatsal's call, after a day wired-only.
#
# The reservation that put it behind this flag still stands and is worth
# keeping in view: messaging drives the phone's UI, and a link dropping
# mid-send is worse than one dropping mid-read. Reading survives a flaky
# link (poll again in two minutes); a half-finished send does not.
# send_message's verify-before-tap and its landed/cleared confirmation
# are what make that acceptable rather than merely tolerable.
#
# Not per-phone: only A is paired for wireless, so a per-phone policy
# would be machinery for a distinction that doesn't exist yet. B simply
# won't be found wirelessly until it's paired. Set back to True to go
# wired-only again.
WIRED_ONLY = False

_WIRELESS = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}:\d+$")


def _is_wireless(target: str) -> bool:
    return bool(_WIRELESS.match(target or ""))


def _serial_of(target: str) -> str:
    """
    The phone's own stable serial, whatever it is currently attached as.

    A wireless session is keyed by ip:port, and that port changes every
    time wireless debugging restarts. Anything that PERSISTS per phone —
    tier files above all — must key on this, not on the target. Confirmed
    2026-08-16: VIP settings were written to a file named after the port
    and would have been orphaned on the next reconnect, leaving FRED
    silently seeing no tiers at all.
    """
    if not target:
        return ""
    if not _is_wireless(target):
        return target
    return _adb("shell", "getprop", "ro.serialno",
                target=target, timeout=10).stdout.strip() or target


def _phones() -> dict:
    """{name: serial} in declaration order. Empty when unconfigured."""
    phones = {}
    for i, part in enumerate(p.strip() for p in _PHONES_RAW.split(",")):
        if not part:
            continue
        name, _, serial = part.partition("=")
        if serial:
            phones[name.strip().lower()] = serial.strip()
        else:
            phones["phone" if i == 0 else f"phone{i + 1}"] = name.strip()
    return phones


def _adb(*args, target: str = None, timeout: int = 15) -> subprocess.CompletedProcess:
    """
    Run adb against one specific device.

    target is an adb serial OR an ip:port; both are valid -s arguments.
    None means "whatever _resolve() picks", which is the normal path.
    """
    if target is None:
        target = _resolve()
    prefix = ["-s", target] if target else []
    # encoding is NOT optional. text=True alone decodes with the locale
    # codec, which on this machine is cp1252 — and a WhatsApp
    # notification dump is full of emoji. Confirmed 2026-08-16: the
    # subprocess reader thread died with UnicodeDecodeError on byte 0x8d,
    # stdout came back empty, and read_messages cheerfully reported "No
    # messages waiting" while the phone had plenty. A silent wrong answer,
    # not a crash. errors="replace" so one odd byte degrades a character
    # instead of losing the whole read.
    return subprocess.run(
        ["adb", *prefix, *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _attached() -> dict:
    """{serial_or_address: state} straight from `adb devices`."""
    try:
        out = subprocess.run(
            ["adb", "devices"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except (FileNotFoundError, subprocess.SubprocessError):
        return {}

    found = {}
    for line in out.splitlines()[1:]:
        if "\t" in line:
            name, _, state = line.partition("\t")
            found[name.strip()] = state.strip()
    return found


# Opportunistic wireless-address cache, refreshed off a live USB link (see
# _refresh_wireless_cache) so that an unplug later has something to try
# immediately instead of waiting on a fresh, sometimes-slow-or-flaky mDNS
# scan at that exact moment. serial -> (address, time.monotonic() cached).
# In-memory, per-process only, on purpose - mDNS re-discovers on the next
# USB connect anyway, so there's nothing worth persisting across restarts.
_wireless_cache = {}

# Every _resolve() call would otherwise pay for an mDNS scan on every USB
# command; five minutes is "healthily" without being "constantly" - the
# port only moves when the wireless debugging service itself restarts,
# which reboots and manual toggles cause, not the passage of time.
_WIRELESS_REFRESH_INTERVAL = 300


def _refresh_wireless_cache(serial: str) -> None:
    """
    Best-effort: while USB is the live target, look up the phone's current
    wireless address via mDNS (same lookup _discover does for wireless-only
    discovery) and cache it against its serial.

    Must never affect the USB command actually in flight - this rides
    along on _resolve() calls it did not originate, so any failure here is
    swallowed, same instinct as _adb's encoding fix: a side effect must
    degrade quietly, not take the real operation down with it.
    """
    if not serial:
        return
    cached = _wireless_cache.get(serial)
    if cached and time.monotonic() - cached[1] < _WIRELESS_REFRESH_INTERVAL:
        return
    try:
        services = _adb("mdns", "services", target="", timeout=10).stdout
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return
    for line in services.splitlines():
        if serial in line:
            match = re.search(r"(\d{1,3}(?:\.\d{1,3}){3}:\d+)", line)
            if match:
                _wireless_cache[serial] = (match.group(1), time.monotonic())
            return


def _discover(serial: str) -> str:
    """
    Address for `serial` that adb can actually talk to, or "".

    Order matters and each step earns its place (measured 2026-08-16):
      1. Already attached — USB, or a live wireless session.
      2. mDNS. Wireless debugging picks a NEW RANDOM PORT every time the
         service starts, and Android disables the whole thing on every
         reboot, so there is nothing stable to remember. Three sessions
         gave three ports. mDNS is how the port is found at all; the
         service name embeds the serial, which is what makes it pick the
         right phone out of two.
      3. The legacy fixed FRED_PHONE_ADB address, for a phone set up the
         old `adb tcpip 5555` way.
    """
    attached = _attached()

    if attached.get(serial) == "device":
        return serial

    # Wired only: a USB device is attached under its own serial, so the
    # check above is the whole story. No serial probing over a wireless
    # link, no mDNS, no legacy address.
    if WIRED_ONLY:
        return ""

    # A live WIRELESS session is keyed by ip:port, not by serial, so the
    # check above misses it entirely. Ask each attached device who it is.
    #
    # This has to come before mDNS, not after: confirmed 2026-08-16, the
    # phone was connected and working at 192.168.0.105:40017 while
    # `adb mdns services` returned nothing, and discovery reported "no
    # phone reachable" about a phone it was already talking to.
    # Announcements come and go; an open connection is the harder fact.
    for address, state in attached.items():
        if state != "device" or address == serial:
            continue
        who = _adb("shell", "getprop", "ro.serialno",
                   target=address, timeout=10).stdout.strip()
        if who == serial:
            return address

    # A cached address from a recent USB-live refresh (_refresh_wireless_
    # cache) is worth a direct connect attempt before paying for a fresh
    # mDNS scan - it's usually still current, since the port only moves
    # when the wireless debugging service itself restarts.
    cached = _wireless_cache.get(serial)
    if cached:
        address = cached[0]
        if attached.get(address) == "device":
            return address
        _adb("connect", address, target="", timeout=10)
        if _attached().get(address) == "device":
            return address

    # Nothing connected yet — find where it is listening. The port is
    # random every time the service starts, so mDNS is the only way to
    # learn it; it just isn't the only way to recognise the phone.
    try:
        services = _adb("mdns", "services", target="", timeout=10).stdout
    except (FileNotFoundError, subprocess.SubprocessError):
        services = ""

    for line in services.splitlines():
        if serial in line:
            match = re.search(r"(\d{1,3}(?:\.\d{1,3}){3}:\d+)", line)
            if not match:
                continue
            address = match.group(1)
            if attached.get(address) == "device":
                return address
            # `adb connect` exits 0 even when it prints "failed to
            # connect" (confirmed 2026-08-16), so its return code says
            # nothing. Only the device list is evidence.
            _adb("connect", address, target="", timeout=10)
            if _attached().get(address) == "device":
                return address

    if _ADDR:
        if attached.get(_ADDR) == "device":
            return _ADDR
        _adb("connect", _ADDR, target="", timeout=10)
        if _attached().get(_ADDR) == "device":
            return _ADDR

    return ""


def _resolve() -> str:
    """
    Address of the phone commands should go to, or "".

    With no FRED_PHONES configured this falls back to "the single attached
    device", which is the pre-2026-08-16 behaviour and correct for a
    one-phone setup.
    """
    attached = _attached()
    if WIRED_ONLY:
        # Ignore any wireless session that happens to be open, so "which
        # phone am I talking to" always means "the one on the cable".
        attached = {k: v for k, v in attached.items() if not _is_wireless(k)}

    ready = [k for k, v in attached.items() if v == "device"]
    phones = _phones()

    # One phone plugged in is unambiguous — use it, whatever it is, and
    # whatever use_phone() last selected. "Wired to A or B, whatever" is
    # the actual working style; making it depend on a remembered selection
    # would just be a way to act on the wrong phone. Unchanged by the
    # wireless-cache refresh below: this still returns immediately, before
    # _discover or the cache are ever consulted, so USB keeps winning.
    if len(ready) == 1:
        target = ready[0]
        # A wired target IS its own serial (see _serial_of). Piggyback the
        # opportunistic mDNS refresh here, not in _adb, so it costs one
        # dict lookup on every USB command and an actual scan only once
        # per _WIRELESS_REFRESH_INTERVAL.
        if not _is_wireless(target):
            _refresh_wireless_cache(target)
        return target

    if phones:
        name = _active_phone or next(iter(phones))
        serial = phones.get(name)
        return _discover(serial) if serial else ""

    if not ready and _ADDR and not WIRED_ONLY:
        _adb("connect", _ADDR, target="", timeout=10)
        return _ADDR if _attached().get(_ADDR) == "device" else ""

    # Several attached and nothing to disambiguate them: refuse rather
    # than guess which phone to message from.
    return ""


def use_phone(name: str = "") -> str:
    """Choose which configured phone subsequent commands act on."""
    global _active_phone

    phones = _phones()
    if not phones:
        return "Only one phone is set up, so there's nothing to switch between."

    if not name:
        current = _active_phone or next(iter(phones))
        return f"Using {current}. Also configured: {', '.join(phones)}."

    key = name.strip().lower()
    if key not in phones:
        return f"I don't have a phone called {name}. I have: {', '.join(phones)}."

    _active_phone = key
    return f"Using {key} from now on."


def _device_ready() -> bool:
    """True when the selected phone is reachable."""
    return bool(_resolve())


def device_status() -> str:
    """
    Why the phone isn't reachable, in terms that name the fix.

    "The phone isn't connected" sent Vatsal hunting on 2026-08-16 when the
    real cause was Android having switched wireless debugging off during a
    reboot — which it does every reboot, on every device, by design.
    """
    target = _resolve()
    if target:
        return f"Phone reachable at {target}."

    attached = _attached()
    unauthorised = [k for k, v in attached.items() if v == "unauthorized"]
    if unauthorised:
        return "The phone is attached but not authorised - accept the USB debugging prompt on its screen."

    wired = [k for k, v in attached.items() if v == "device" and not _is_wireless(k)]
    if len(wired) > 1 and not _phones():
        return (f"{len(wired)} phones are plugged in and I don't know which to use. "
                "Unplug one, or set FRED_PHONES.")

    if WIRED_ONLY:
        # Say WHY an open wireless session isn't being used, or this reads
        # as a bug: adb devices shows a phone, FRED says there isn't one.
        if any(_is_wireless(k) for k, v in attached.items() if v == "device"):
            return ("A phone is connected wirelessly, but I'm set to wired only "
                    "right now - plug it in over USB.")
        return "No phone is plugged in. Connect one over USB."

    return ("No phone is reachable. Check Wireless debugging is still on - "
            "Android turns it off on every reboot - or plug in over USB.")


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


def _read_contacts(with_removed: bool = False):
    """
    {name: number} from the vault file, insertion-ordered.

    with_removed also returns the tombstones — see _REMOVED_HEADING.
    """
    entries, removed = {}, {}
    if not CONTACTS_PATH.exists():
        return (entries, removed) if with_removed else entries

    section = ""
    for line in CONTACTS_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("## "):
            section = line[3:].strip().lower()
            continue
        if not line.startswith("- ") or ":" not in line:
            continue
        name, _, number = line[2:].rpartition(":")
        name, number = name.strip(), number.strip()
        if not (name and number):
            continue
        if section == "removed":
            removed[name] = number
        else:
            entries[name] = number

    return (entries, removed) if with_removed else entries


def _write_contacts(entries: dict, removed: dict = None):
    """Atomic rewrite — a half-written phone book is worse than none."""
    body = "".join(f"- {name}: {number}\n" for name, number in entries.items())

    tail = ""
    if removed:
        lines = "".join(f"- {n}: {num}\n" for n, num in sorted(removed.items()))
        tail = _REMOVED_HEADING + lines

    CONTACTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONTACTS_PATH.with_suffix(".md.tmp")
    tmp.write_text(_HEADER + body + tail, encoding="utf-8")
    tmp.replace(CONTACTS_PATH)


_REMOVED_HEADING = (
    "\n## removed\n\n"
    "Deliberately deleted. A sync must never bring these back.\n"
    "Delete a line from here to let that contact return.\n\n"
)


def _merge(existing: dict, incoming: list, removed: dict = None) -> tuple:
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

    # Tombstones. Append-only protects hand EDITS but not hand DELETIONS:
    # the file cannot tell "Vatsal deleted this" from "never seen", so
    # without this every sync resurrects everything he trimmed. Measured
    # 2026-08-16 on the real file — a sync would have re-added 33 of the
    # 34 entries he had just curated away, silently.
    gone = {_match_key(num) for num in (removed or {}).values()}

    # A name that arrives with SEVERAL different numbers is ambiguous, not
    # a correction. Confirmed 2026-08-16: phone B had two contacts both
    # named "Mom", and syncing it silently replaced the real Mom's number
    # — the one FRED had dialled successfully hours earlier — with a
    # different person's. "Call Mom" would then have rung a stranger.
    #
    # The phone is authority on a contact's number only while the name
    # identifies exactly one contact. Where it doesn't, keep what is on
    # file and leave it to a human.
    ambiguous = set()
    numbers_per_name = {}
    for name, number in incoming:
        keys = numbers_per_name.setdefault(name, set())
        keys.add(_match_key(number))
        if len(keys) > 1:
            ambiguous.add(name)

    for name, number in incoming:
        if name in seen:
            continue
        seen.add(name)

        if _match_key(number) in gone:
            continue

        known_as = by_number.get(_match_key(number))
        if known_as is not None and known_as != name:
            continue
        if name not in merged:
            merged[name] = number
            added.append(name)
        elif merged[name] != number and name not in ambiguous:
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

    existing, removed = _read_contacts(with_removed=True)
    merged, added, corrected = _merge(existing, incoming, removed)

    if not added and not corrected:
        return f"Contacts already up to date — {len(merged)} on file."

    _write_contacts(merged, removed)

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


# =========================================================
# ALARM
# =========================================================

# `adb shell` joins its whole argv into ONE string and runs it through the
# device's own shell, which re-tokenizes on whitespace. A label with a
# space in it — "wake up" — therefore arrives on the phone as two separate
# am-start arguments and the intent parser silently mis-binds them
# (confirmed live 2026-08-20: an unquoted two-word MESSAGE made am start
# print "pkg=<second word>" and fail to resolve the intent at all).
# Wrapping the value in a literal pair of double quotes here survives that
# re-tokenization, confirmed live the same way — a single word needs no
# quoting but quoting one doesn't break it either, so it's applied
# unconditionally rather than branching on whether the label has a space.
#
# Those literal quotes make the label a second trust boundary beyond the
# usual Python-string one: it becomes characters INSIDE a shell string on
# the phone. A stray `"` in the label would close that quote early and
# hand the rest of the label to the phone's shell as a new command.
# Stripped rather than escaped, same instinct as _clean_number rebuilding
# from scratch: don't try to be clever with a shell metacharacter, just
# don't let it through.
_UNSAFE_LABEL = re.compile(r'["\\`$]')


def _clean_label(label: str) -> str:
    """A phone-safe alarm label: no shell-quote-breaking characters, capped
    to a sane length so a rambling transcription doesn't become the name."""
    return _UNSAFE_LABEL.sub("", (label or "").strip())[:80]


def set_alarm(hour: int = -1, minute: int = 0, label: str = "") -> str:
    """Set an alarm on the paired Android phone at hour:minute (24h clock)."""

    try:
        hour, minute = int(hour), int(minute)
    except (TypeError, ValueError):
        return "That's not a valid time — give me an hour and a minute."

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return "That's not a valid time — hour has to be 0-23, minute 0-59."

    if not _device_ready():
        return "The phone isn't connected. Plug it in, or check it's awake on the network."

    args = [
        "shell", "am", "start", "-a", "android.intent.action.SET_ALARM",
        "--ei", "android.intent.extra.alarm.HOUR", str(hour),
        "--ei", "android.intent.extra.alarm.MINUTES", str(minute),
        # Without this the intent just opens the clock app's "new alarm"
        # screen and waits for a human to tap Save — useless from a voice
        # command. Confirmed live 2026-08-20 that SKIP_UI=true saves and
        # enables the alarm with no screen interaction at all.
        "--ez", "android.intent.extra.alarm.SKIP_UI", "true",
    ]

    label = _clean_label(label)
    if label:
        args += ["--es", "android.intent.extra.alarm.MESSAGE", f'"{label}"']

    result = _adb(*args)

    if result.returncode != 0 or "Error" in result.stderr:
        return f"Couldn't set the alarm: {result.stderr.strip() or 'the phone refused it'}"

    when = f"{hour:02d}:{minute:02d}"
    named = f" ({label})" if label else ""
    return f"Alarm set for {when}{named}."


# =========================================================
# CALL LOG
# =========================================================

# Android's CallLog.Calls constants — confirmed live 2026-08-20 against
# content://call_log/calls on the paired phone (adb -s O3PRIS25DB005413).
_CALL_TYPES = {"1": "incoming", "2": "outgoing", "3": "missed"}

# One row looks like:
#   Row: 0 number=+919967635204, name=Mom, type=2, date=1765799273822, duration=0
# name is the provider's own guess at a contact match — usually blank,
# sometimes populated, never trusted here (see get_call_log): it's parsed
# only as a last-resort label when _read_contacts() has nothing better.
# Non-greedy on number/name because, unlike a Contacts display_name, a
# call-log number or provider name is not expected to legitimately embed
# ", type=" — but the projection order (fixed by the query below) is what
# actually makes this safe, not the pattern.
_CALL_ROW = re.compile(
    r"^Row: \d+ number=(.*?), name=(.*?), type=(-?\d+), date=(-?\d+), duration=(-?\d+)\s*$"
)


def _parse_call_log(output: str) -> list:
    """[{number, name, type, date(ms, int), duration(s, int)}], raw row order."""
    rows = []
    for line in output.splitlines():
        m = _CALL_ROW.match(line)
        if not m:
            continue
        number, name, ctype, date, duration = m.groups()
        rows.append({
            "number": number.strip(),
            "name": name.strip(),
            "type": ctype,
            "date": int(date),
            "duration": int(duration),
        })
    return rows


def _describe_call_time(ms: int, now: datetime = None) -> str:
    """
    Same phrasing rule as scheduler.describe_when, mirrored rather than
    imported — importing orchestrator.scheduler here would drag in
    apscheduler for one two-line helper. "at <clock>", matching
    get_current_time's "%I:%M %p" convention, with "yesterday"/a day
    name prefixed once the call isn't from today.
    """
    now = now or datetime.now()
    when = datetime.fromtimestamp(ms / 1000)
    clock = when.strftime("%I:%M %p").lstrip("0")

    days = (now.date() - when.date()).days
    if days == 0:
        return f"at {clock}"
    if days == 1:
        return f"yesterday at {clock}"
    return f"{when.strftime('%A %d %B')} at {clock}"


def _fetch_call_log_rows():
    """
    [{number, name, type, date, duration}] straight off the phone, or
    None if the query itself failed. Shared by get_call_log and
    check_recent_calls so the adb/content-query/parsing logic exists in
    exactly one place — both need the SAME raw rows (each with its own
    `date`), not the pre-formatted sentence get_call_log builds from
    them.
    """
    result = _adb("shell", "content", "query", "--uri",
                  "content://call_log/calls",
                  "--projection", "number:name:type:date:duration", timeout=30)
    if result.returncode != 0:
        return None
    return _parse_call_log(result.stdout)


def get_call_log(limit: int = 10, missed_only: bool = False) -> str:
    """
    Recent calls off the paired phone, most recent first — "who called
    me" / "any missed calls".

    Names are resolved through _read_contacts(), the same phone book
    call_phone dials from, so a caller here and a name spoken to dial
    them are always the same lookup — never the call log's own `name`
    column, which is frequently blank and not something to lean on.
    """
    if not _device_ready():
        return "The phone isn't connected, so I can't check the call log."

    limit = max(1, min(int(limit), 50))

    rows = _fetch_call_log_rows()
    if rows is None:
        return "The phone refused the call-log query."

    if missed_only:
        rows = [r for r in rows if r["type"] == "3"]

    if not rows:
        return "No missed calls." if missed_only else "No calls in the log."

    rows.sort(key=lambda r: r["date"], reverse=True)
    rows = rows[:limit]

    known = {_match_key(num): name for name, num in _read_contacts().items()}
    now = datetime.now()

    parts = []
    for r in rows:
        name = known.get(_match_key(r["number"])) or r["name"] or f"Unknown number ({r['number']})"
        when = _describe_call_time(r["date"], now)
        if missed_only:
            parts.append(f"{name} {when}")
        else:
            parts.append(f"{name} ({_CALL_TYPES.get(r['type'], 'call')}) {when}")

    count = len(parts)
    label = "missed call" if missed_only else "call"
    return f"{count} {label}{'s' if count != 1 else ''}: {', '.join(parts)}."


# =========================================================
# PROACTIVE: "you missed a call from X" (mirrors
# whatsapp_tools.check_vip_messages' shape exactly)
# =========================================================

# Persisted watermark for the proactive call check, alongside
# whatsapp_tools.SEEN_PATH in the same data dir/convention. Call-log
# rows are already timestamped and naturally ordered, so tracking the
# single max `date` seen is enough — unlike WhatsApp's dedup, which
# needs a full stamp set because messages aren't a strictly increasing
# single stream the way one phone's call log is.
CALL_SEEN_PATH = DATA_DIR / "call_log_seen.json"


def _load_seen_call_stamp():
    """Max call `date` (epoch ms) seen so far, or None on first-ever run."""
    try:
        return int(json.loads(CALL_SEEN_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return None


def _save_seen_call_stamp(stamp: int):
    CALL_SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CALL_SEEN_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(int(stamp)), encoding="utf-8")
    tmp.replace(CALL_SEEN_PATH)


# =========================================================
# CAMERA
# =========================================================

_CAMERA_CAPTURE_PATH = DATA_DIR / "camera_capture.png"


def capture_camera_photo() -> str:
    """
    Wakes the phone's camera and captures whatever it's pointed at right
    now, pulling the frame back to this machine. Returns the local file
    path on success, or a plain error string.

    NOT the standard IMAGE_CAPTURE -> MediaStore -> pull flow — confirmed
    live 2026-08-20 that it silently drops the frame on this phone's
    scoped storage (Android 15): `am start` has no calling Activity to
    receive onActivityResult or honor an output Uri, so the camera app
    has nowhere trusted to persist it and just discards it, no error
    either way. Instead this screenshots the live viewfinder directly,
    before any shutter press — screen-resolution, not full sensor
    resolution, but that's a non-issue for the only consumer
    (llm_client.describe_image()'s vision pipeline). ~3s end to end.

    KEYCODE_HOME (not a package-specific force-stop) leaves the camera
    app afterward, so this stays portable across phones with a different
    stock camera package.
    """
    if not _device_ready():
        return "The phone isn't connected. Plug it in, or check it's awake on the network."

    remote_path = "/sdcard/fred_camera_capture.png"

    _adb("shell", "am", "start", "-a", "android.media.action.IMAGE_CAPTURE")
    time.sleep(1.2)  # let the camera init/autofocus settle - confirmed live 2026-08-20

    shot = _adb("shell", "screencap", "-p", remote_path, timeout=20)
    if shot.returncode != 0:
        _adb("shell", "input", "keyevent", "KEYCODE_HOME")
        return "Couldn't capture the camera preview."

    pull = _adb("pull", remote_path, str(_CAMERA_CAPTURE_PATH), timeout=20)
    _adb("shell", "rm", remote_path)
    _adb("shell", "input", "keyevent", "KEYCODE_HOME")

    if pull.returncode != 0 or not _CAMERA_CAPTURE_PATH.exists():
        return "Captured on the phone but couldn't pull the image back."

    return str(_CAMERA_CAPTURE_PATH)


def check_recent_calls() -> str:
    """
    Calls from VIP-tier people since the last check, for the proactive
    watcher. Returns "" when there's nothing new — the caller stays
    silent on empty, same contract as whatsapp_tools's check_vip_messages.

    Gated on VIP tier, same as the WhatsApp check, and on PURPOSE the
    SAME tier data: imports whatsapp_tools._read_tiers/tier_of directly
    rather than growing a second, call-specific tier file that could
    drift out of sync with the WhatsApp one. A person's trust level
    isn't a property of the channel they're reaching you through — one
    "vip" list, keyed by the resolved contact name, same as get_call_log
    already resolves through _read_contacts() before display. A number
    with no resolved name can never be VIP here, same as it can never be
    VIP on the WhatsApp side either (that keys on sender NAME too).

    Every call still advances the seen-watermark, VIP or not — mirrors
    check_vip_messages, which dedups every message it sees and only
    filters to VIP *after* marking it seen, so a non-VIP call is never
    re-examined and re-tiered on every future check just because it
    wasn't announced.

    First-ever run seeds the watermark at the current newest call and
    reports nothing retroactively, matching check_vip_messages' own
    first-run behaviour with an empty seen-set — VIP calls, like VIP
    messages, are a small deliberately-curated set, so there's no
    "backlog is too much to read out" problem the way there would be
    if every call qualified.
    """
    if not _device_ready():
        return ""

    rows = _fetch_call_log_rows()
    if not rows:
        return ""

    last_seen = _load_seen_call_stamp()
    max_date = max(r["date"] for r in rows)

    if last_seen is None:
        _save_seen_call_stamp(max_date)
        return ""

    new_rows = sorted((r for r in rows if r["date"] > last_seen), key=lambda r: r["date"])
    _save_seen_call_stamp(max(max_date, last_seen))

    if not new_rows:
        return ""

    from tools.whatsapp_tools import _read_tiers, tier_of

    target = _resolve()
    policy, tiers, seen = _read_tiers(target) if target else ("strict", {}, {})
    known = {_match_key(num): name for name, num in _read_contacts().items()}

    fresh = []
    for r in new_rows:
        # Same name lookup get_call_log uses: contacts.md first, the
        # call log's own (usually blank) provider name as fallback.
        # Nameless rows can't be looked up in a tier file keyed by name,
        # so they're skipped rather than tiered as "useless" via "".
        name = known.get(_match_key(r["number"])) or r["name"]
        if not name:
            continue
        if tier_of(name, policy, tiers, seen) == "vip":
            fresh.append((name, r))

    if not fresh:
        return ""
    if len(fresh) == 1:
        name, row = fresh[0]
        return f"{name} called {_describe_call_time(row['date'])}."

    names = [name for name, _ in fresh]
    uniq = list(dict.fromkeys(names))
    if len(uniq) == 1:
        return f"{uniq[0]} called {len(fresh)} times while I was away."
    return f"{', '.join(uniq[:-1])} and {uniq[-1]} called while I was away."


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

    # A deliberately deleted contact must not come back on the next sync.
    # Matched by NUMBER, so it stays dead even if the phone renames it.
    merged, added, _ = _merge(
        {"Kept": "+919000000011"},
        [("Kept", "+919000000011"), ("Deleted Person", "+919000000012")],
        {"Deleted Person": "+919000000012"},
    )
    assert added == [], added
    assert "Deleted Person" not in merged

    merged, added, _ = _merge(
        {}, [("Renamed On Phone", "09000000012")],
        {"Deleted Person": "+919000000012"},
    )
    assert added == [], "tombstone must match on number, not name"

    # Two different contacts sharing one display name must NOT overwrite
    # the number already on file. The real failure: a second phone had two
    # entries both called "Mom", and the sync replaced the real one.
    merged, added, corrected = _merge(
        {"Shared Name": "+919000000020"},
        [("Shared Name", "+919000000021"), ("Shared Name", "+919000000022")],
    )
    assert merged["Shared Name"] == "+919000000020", merged
    assert corrected == [], corrected

    # A genuine correction still works when the name is unambiguous.
    merged, added, corrected = _merge(
        {"Solo": "+919000000030"}, [("Solo", "+919000000031")]
    )
    assert merged["Solo"] == "+919000000031"
    assert [c[0] for c in corrected] == ["Solo"]

    # Wireless-address cache: refreshed off a live USB link, throttled so
    # it doesn't scan on every call, and consulted by _discover before a
    # fresh mDNS scan once USB is gone. Fake _adb stands in for adb itself
    # so this doesn't touch a real device or network.
    _real_adb = _adb
    calls = []

    def _fake_adb(*args, **kwargs):
        calls.append(args)
        out = "adb tls-connection\tSERIAL123\t192.168.1.50:41000\n"
        return subprocess.CompletedProcess(args, 0, stdout=out, stderr="")

    globals()["_adb"] = _fake_adb
    try:
        _wireless_cache.clear()
        _refresh_wireless_cache("SERIAL123")
        assert _wireless_cache["SERIAL123"][0] == "192.168.1.50:41000"
        assert len(calls) == 1

        # Within the interval, a second refresh must not scan again.
        _refresh_wireless_cache("SERIAL123")
        assert len(calls) == 1, "refresh must be throttled, not per-call"

        # Force the interval to have elapsed: it scans again.
        _wireless_cache["SERIAL123"] = (
            _wireless_cache["SERIAL123"][0],
            time.monotonic() - _WIRELESS_REFRESH_INTERVAL - 1,
        )
        _refresh_wireless_cache("SERIAL123")
        assert len(calls) == 2
    finally:
        globals()["_adb"] = _real_adb
        _wireless_cache.clear()

    # Call log: row parsing off a real captured shape, and the
    # contacts-over-provider-name precedence get_call_log relies on.
    real_rows = (
        "Row: 0 number=9967635204, name=, type=2, date=1764080748927, duration=0\n"
        "Row: 6 number=+919967635204, name=Mom, type=2, date=1764419915201, duration=0\n"
        "Row: 21 number=+919998612855, name=, type=3, date=1765442102928, duration=0\n"
    )
    parsed = _parse_call_log(real_rows)
    assert len(parsed) == 3
    assert parsed[1] == {
        "number": "+919967635204", "name": "Mom", "type": "2",
        "date": 1764419915201, "duration": 0,
    }
    assert parsed[2]["type"] == "3" and parsed[2]["name"] == ""

    print("ok")
