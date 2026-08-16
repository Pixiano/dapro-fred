# Core/tools/whatsapp_tools.py
#
# Reading and sending WhatsApp, over adb, with a per-contact trust tier
# deciding what FRED is allowed to do about each sender.
#
# WHY NOT A PROPER API
# --------------------
# The official Cloud API needs a Business account and a different phone
# number. The unofficial protocol clients (Baileys, whatsapp-web.js) are
# far more capable and get numbers banned. This drives the phone's own UI
# and notifications instead: no ToS problem beyond automating a device
# Vatsal owns, and nothing can get his account banned.
#
# THE TIERS (Vatsal, 2026-08-16)
# ------------------------------
#   useless  - dropped before anything is read. Not just noise reduction:
#              every message FRED reads is attacker-controlled text going
#              into an agent that holds tools, so dropping a whole class
#              of sender shrinks the prompt-injection surface rather than
#              filtering after the fact. The live notification stream
#              already contained a bank promo with a link.
#   basic    - readable, never messaged, never interrupts.
#   trusted  - FRED may send to them.
#   vip      - FRED may send, AND interrupts Vatsal when they message.
#
# Tiers are PER PHONE (each handset is a separate WhatsApp account with
# its own chats) and are only ever changed through set_contact_tier,
# which is confirmation-gated. Nothing automatic can promote anyone:
# being in the address book is not consent to be messaged by an AI.
#
# READING WORKS WITH THE PHONE LOCKED. Verified 2026-08-16 against a real
# group while the screen was off and dozing — which is what makes the VIP
# watcher possible at all. SENDING does not: UI automation needs an
# unlocked screen.

import json
import re
import time

from config.settings import DATA_DIR, VAULT_DIR
from tools.phone_tools import _adb, _resolve, device_status

# One file per phone, in the vault — these are real names and real
# message senders. Same reasoning as contacts.md, and like that file
# these are excluded from the vault index so they never ride along in a
# prompt bound for a cloud model.
TIER_DIR = VAULT_DIR / "people"

TIERS = ("useless", "basic", "trusted", "vip")

# Default tier for a sender nobody has classified.
#
#   strict       - everyone is useless until Vatsal promotes them, even
#                  saved contacts. Chosen for phone 1.
#   repeat-basic - an unknown number is useless the first time and basic
#                  once it has messaged before, on the theory that a
#                  repeat unknown is a real person (delivery, someone who
#                  got the number) and a one-off is spam. Phone 2.
POLICIES = ("strict", "repeat-basic")
DEFAULT_POLICY = "strict"

# Notification records repeat within a single dumpsys — the same message
# appeared three times in one capture. Dedupe is by (sender, text, time),
# and `time` is a real epoch-ms field rather than something inferred.
SEEN_PATH = DATA_DIR / "whatsapp_seen.json"

# WhatsApp uses MessagingStyle, so each message is its own bundle with
# separate fields. Parsing the rendered "Name: body" string instead would
# break on any body containing a colon.
# sender must not run past its own field. A photo message inserts a uri=
# field between sender and text:
#     sender=Mom, uri=content://com.whatsapp.provider.media/..., text=..
# and a lazy `.*?` happily swallowed it, yielding a sender literally named
# "Mom, uri=content://...". That never matches a tier entry, so VIP alerts
# silently skipped every photo (found 2026-08-16). [^,] stops the sender
# at its own comma; text stays greedy because message bodies do contain
# commas and `time=` is always last.
_MSG = re.compile(
    r"sender=(?P<sender>[^,]*)(?P<between>.*?), text=(?P<text>.*), time=(?P<time>\d+)"
)

# Rendered placeholder WhatsApp uses when the message is an attachment.
_MEDIA = re.compile(r"^\s*(?:📷|🎥|🎤|📎|📄)\s*\w+")

# When someone replies to your message, one bundle arrives with `sender`
# set to a marker and the real sender folded into the text as
# "Name: body". Seen live 2026-08-16. Without this FRED announces a
# message from somebody called "You got a reply".
_REPLY_MARKER = re.compile(r"you got a reply", re.IGNORECASE)


# =========================================================
# TIER FILE
# =========================================================


def _tier_path(serial: str):
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", serial or "unknown")
    return TIER_DIR / f"whatsapp-tiers-{safe}.md"


def _read_tiers(serial: str) -> tuple:
    """(policy, {lowercased name: tier}, {lowercased name: True seen})."""
    path = _tier_path(serial)
    policy, tiers, seen = DEFAULT_POLICY, {}, {}
    if not path.exists():
        return policy, tiers, seen

    section = ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.lower().startswith("policy:"):
            value = line.split(":", 1)[1].strip().lower()
            if value in POLICIES:
                policy = value
        elif line.startswith("## "):
            section = line[3:].strip().lower()
        elif line.startswith("- ") and section:
            name = line[2:].strip()
            if not name:
                continue
            if section == "seen":
                seen[name.lower()] = True
            elif section in TIERS:
                tiers[name.lower()] = section
    return policy, tiers, seen


def _write_tiers(serial: str, policy: str, tiers: dict, seen: dict):
    path = _tier_path(serial)
    lines = [
        f"# WhatsApp tiers - {serial}", "",
        "Written by tools/whatsapp_tools.py. Tiers change only through",
        "set_contact_tier, which asks first - nothing automatic promotes",
        "anyone. Hand edits are respected.", "",
        f"policy: {policy}", "",
    ]
    for tier in TIERS:
        lines.append(f"## {tier}")
        for name in sorted(n for n, t in tiers.items() if t == tier):
            lines.append(f"- {name}")
        lines.append("")

    lines.append("## seen")
    lines.append("<!-- senders encountered before; drives the repeat-basic policy -->")
    for name in sorted(seen):
        lines.append(f"- {name}")
    lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text("\n".join(lines), encoding="utf-8")
    tmp.replace(path)


def tier_of(sender: str, policy: str, tiers: dict, seen: dict) -> str:
    """The tier a sender falls into right now."""
    key = (sender or "").strip().lower()
    if key in tiers:
        return tiers[key]
    if policy == "repeat-basic" and seen.get(key):
        return "basic"
    return "useless"


# =========================================================
# READING
# =========================================================


def _parse_notifications(dump: str) -> list:
    """
    [{sender, text, time}] from `dumpsys notification --noredact`,
    newest last, deduplicated.
    """
    found = {}
    for match in _MSG.finditer(dump):
        sender = match.group("sender").strip()
        text = match.group("text").strip()
        stamp = int(match.group("time"))

        # Reply-marker recovery: the true sender is the prefix of the body.
        if _REPLY_MARKER.search(sender) and ":" in text:
            sender, _, text = text.partition(":")
            sender, text = sender.strip(), text.strip()

        if not sender or not text:
            continue
        found[(sender, text, stamp)] = {
            "sender": sender, "text": text, "time": stamp
        }

    return sorted(found.values(), key=lambda m: m["time"])


def _load_seen_stamps() -> set:
    try:
        return set(json.loads(SEEN_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def _save_seen_stamps(stamps):
    # Keep the tail only — this exists to avoid re-announcing, not as a
    # message archive, and an unbounded file would grow forever.
    keep = sorted(stamps)[-500:]
    SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SEEN_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(keep), encoding="utf-8")
    tmp.replace(SEEN_PATH)


def _fetch(serial: str) -> str:
    """Raw notification dump, read as UTF-8 so emoji survive."""
    result = _adb("shell", "dumpsys", "notification", "--noredact", timeout=30)
    return result.stdout or ""


def read_messages(limit: int = 10) -> str:
    """Recent WhatsApp messages, with useless senders dropped."""
    target = _resolve()
    if not target:
        return device_status()

    policy, tiers, seen = _read_tiers(target)
    messages = _parse_notifications(_fetch(target))

    visible = [m for m in messages
               if tier_of(m["sender"], policy, tiers, seen) != "useless"]

    if not messages:
        return "No messages waiting."
    if not visible:
        return f"Nothing worth reading - {len(messages)} message(s), all from senders you've left as useless."

    limit = max(1, min(int(limit), 30))
    shown = visible[-limit:]
    return "; ".join(f"{m['sender']}: {m['text']}" for m in shown)


def check_vip_messages() -> str:
    """
    New messages from VIPs only, for the proactive watcher. Returns ""
    when there is nothing new — the caller stays silent on empty.
    """
    target = _resolve()
    if not target:
        return ""

    policy, tiers, seen = _read_tiers(target)
    already = _load_seen_stamps()

    fresh = []
    stamps = set(already)
    for message in _parse_notifications(_fetch(target)):
        key = f"{message['time']}:{message['sender']}"
        if key in already:
            continue
        stamps.add(key)
        if tier_of(message["sender"], policy, tiers, seen) == "vip":
            fresh.append(message)

    _save_seen_stamps(stamps)

    if not fresh:
        return ""
    if len(fresh) == 1:
        return f"{fresh[0]['sender']} messaged: {fresh[0]['text']}"
    return "; ".join(f"{m['sender']}: {m['text']}" for m in fresh)


# =========================================================
# TIERS
# =========================================================


def set_contact_tier(name: str = "", tier: str = "") -> str:
    """Move someone between tiers on the phone currently in use."""
    target = _resolve()
    if not target:
        return device_status()

    name, tier = (name or "").strip(), (tier or "").strip().lower()
    if not name:
        return "Who should I re-classify?"
    if tier not in TIERS:
        return f"Tier must be one of {', '.join(TIERS)}."

    policy, tiers, seen = _read_tiers(target)
    previous = tiers.get(name.lower(), f"unlisted (default {policy})")
    tiers[name.lower()] = tier
    _write_tiers(target, policy, tiers, seen)
    return f"{name} moved from {previous} to {tier}."


def list_contact_tiers() -> str:
    """Who is in which tier on the phone currently in use."""
    target = _resolve()
    if not target:
        return device_status()

    policy, tiers, _ = _read_tiers(target)
    if not tiers:
        return f"Nobody classified yet - policy is '{policy}', so everyone defaults to useless."

    parts = []
    for tier in reversed(TIERS):
        names = sorted(n for n, t in tiers.items() if t == tier)
        if names:
            parts.append(f"{tier}: {', '.join(names)}")
    return f"Policy {policy}. " + ". ".join(parts)


# =========================================================
# SENDING
# =========================================================


def _may_send_to(name: str, target: str) -> tuple:
    """(allowed, reason). The single choke point for send permission."""
    policy, tiers, seen = _read_tiers(target)
    tier = tier_of(name, policy, tiers, seen)
    if tier in ("trusted", "vip"):
        return True, tier
    return False, tier


def _screen_blocked(target: str) -> str:
    """
    Why UI automation can't run right now, or "".

    Reading notifications works with the phone locked; driving the UI does
    not. Without this check a locked phone produced "I couldn't find a
    chat called <name> on the share screen" (2026-08-16) — technically
    true, since uiautomator was dumping the lockscreen, but it sends you
    looking for a chat-name problem that doesn't exist.
    """
    window = _adb("shell", "dumpsys window", target=target, timeout=20).stdout or ""
    if "mDreamingLockscreen=true" in window:
        return "The phone is locked - unlock it and I'll send."

    power = _adb("shell", "dumpsys power", target=target, timeout=20).stdout or ""
    if "mWakefulness=Asleep" in power or "mWakefulness=Dozing" in power:
        return "The phone's screen is off - wake it and I'll send."

    return ""


def _dump_ui(target: str) -> str:
    _adb("shell", "uiautomator", "dump", "/sdcard/fred_ui.xml", timeout=30)
    xml = _adb("shell", "cat", "/sdcard/fred_ui.xml", timeout=20).stdout or ""
    _adb("shell", "rm", "-f", "/sdcard/fred_ui.xml", timeout=10)
    return xml


def _node_center(xml: str, pattern: str):
    """(x, y) of the first node matching `pattern`, or None."""
    match = re.search(
        pattern + r'[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml
    )
    if not match:
        match = re.search(
            r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"[^>]*' + pattern, xml
        )
    if not match:
        return None
    x1, y1, x2, y2 = (int(match.group(i)) for i in range(1, 5))
    return (x1 + x2) // 2, (y1 + y2) // 2


def send_message(contact: str = "", text: str = "") -> str:
    """
    Send a WhatsApp message. Trusted and VIP contacts only.

    Bounds come from the live view hierarchy, never hardcoded: the device
    reports coordinates in the CURRENT rotation's space, so a phone held
    sideways puts the send button somewhere a fixed coordinate would miss
    entirely (measured 2026-08-16 — physical size 720x1612, send button
    at x=1424 in landscape).
    """
    target = _resolve()
    if not target:
        return device_status()

    contact, text = (contact or "").strip(), (text or "").strip()
    if not contact:
        return "Who should I message?"
    if not text:
        return "What should I say?"

    allowed, tier = _may_send_to(contact, target)
    if not allowed:
        return (f"I'm not allowed to message {contact} - they're {tier}. "
                f"Say 'trust {contact}' first if you want that to change.")

    blocked = _screen_blocked(target)
    if blocked:
        return blocked

    escaped = text.replace("'", "'\\''")
    started = _adb(
        "shell",
        "am start -a android.intent.action.SEND -t text/plain "
        f"--es android.intent.extra.TEXT '{escaped}' -p com.whatsapp",
        timeout=30,
    )
    if started.returncode != 0 or "Error" in (started.stderr or ""):
        return "WhatsApp wouldn't open the share screen."

    time.sleep(5)
    xml = _dump_ui(target)
    if not xml:
        return "Couldn't read the screen - is the phone unlocked?"

    chat = _node_center(xml, r'text="' + re.escape(contact) + r'"')
    if not chat:
        return f"I couldn't find a chat called {contact} on the share screen."
    _adb("shell", "input", "tap", str(chat[0]), str(chat[1]), timeout=20)
    time.sleep(4)

    # Verify BOTH the chat identity and that the text really is staged,
    # before anything irreversible. A message to the wrong chat cannot be
    # taken back the way a misdialled call can.
    confirm = _dump_ui(target)
    if f'text="{contact}"' not in confirm:
        return f"Opened the wrong chat - not sending. Expected {contact}."
    if text[:30] not in confirm:
        return "The message didn't reach the compose box - not sending."

    send = _node_center(confirm, r'content-desc="Send"')
    if not send:
        return "Couldn't find the send button - not sending."
    _adb("shell", "input", "tap", str(send[0]), str(send[1]), timeout=20)
    time.sleep(3)

    after = _dump_ui(target)
    if 'resource-id="com.whatsapp:id/message_text"' in after:
        return f"Sent to {contact}."
    return "Tapped send but couldn't confirm it went - check the phone."


if __name__ == "__main__":
    # Fixtures copied from a real `dumpsys notification --noredact`
    # capture on 2026-08-16, with names and bodies replaced.
    dump = (
        "Bundle[{extras=Bundle[{}], sender_person=android.app.Person@1, "
        "sender=Alpha, text=first message, time=1786872931000}]\n"
        # a photo: an extra uri= field sits between sender and text
        "Bundle[{extras=Bundle[{}], sender_person=android.app.Person@9, "
        "sender=Alpha, uri=content://com.whatsapp.provider.media/item/abc-123, "
        "text=📷 Photo, time=1786872940000}]\n"
        "Bundle[{extras=Bundle[{}], sender_person=android.app.Person@2, "
        "sender=Alpha, text=second, with a comma, time=1786872936000}]\n"
        "Bundle[{extras=Bundle[{}], sender_person=android.app.Person@3, "
        "sender=⤷ You got a reply, text=Beta: third one, time=1786873661000}]\n"
        # the same record repeated, as dumpsys really does
        "Bundle[{extras=Bundle[{}], sender_person=android.app.Person@3, "
        "sender=⤷ You got a reply, text=Beta: third one, time=1786873661000}]\n"
    )

    parsed = _parse_notifications(dump)
    assert len(parsed) == 4, parsed              # duplicate collapsed
    # Ordered by time, so the photo (940) lands after the comma one (936).
    assert parsed[0]["sender"] == "Alpha"
    assert parsed[1]["text"] == "second, with a comma"   # comma survived
    # A photo must NOT parse as a sender called "Alpha, uri=content://..."
    # — that matches no tier entry, so VIP alerts would skip it silently.
    assert parsed[2]["sender"] == "Alpha", parsed[2]
    assert "uri=" not in parsed[2]["sender"]
    assert parsed[3]["sender"] == "Beta"         # reply marker unwrapped
    assert parsed[3]["text"] == "third one"
    assert [m["time"] for m in parsed] == sorted(m["time"] for m in parsed)

    # strict: nobody is anything until promoted, saved contact or not
    assert tier_of("Alpha", "strict", {}, {"alpha": True}) == "useless"
    assert tier_of("Alpha", "strict", {"alpha": "vip"}, {}) == "vip"

    # repeat-basic: first contact useless, a repeat becomes basic
    assert tier_of("Gamma", "repeat-basic", {}, {}) == "useless"
    assert tier_of("Gamma", "repeat-basic", {}, {"gamma": True}) == "basic"
    # an explicit tier always beats the policy
    assert tier_of("Gamma", "repeat-basic", {"gamma": "useless"}, {"gamma": True}) == "useless"

    # send permission: only trusted and vip, and it is case-insensitive
    for tier, expected in (("useless", False), ("basic", False),
                           ("trusted", True), ("vip", True)):
        allowed = tier_of("Delta", "strict", {"delta": tier}, {}) in ("trusted", "vip")
        assert allowed is expected, (tier, allowed)

    # node bounds -> centre, both attribute orders
    xml_a = '<node content-desc="Send" bounds="[1424,608][1520,704]" />'
    xml_b = '<node bounds="[10,20][30,40]" content-desc="Send" />'
    assert _node_center(xml_a, r'content-desc="Send"') == (1472, 656)
    assert _node_center(xml_b, r'content-desc="Send"') == (20, 30)
    assert _node_center(xml_a, r'content-desc="Nope"') is None

    print("ok")
