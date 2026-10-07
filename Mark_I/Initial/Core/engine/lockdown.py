"""Lockdown: an engine mode, not a skill. Chat still works; almost nothing else does.

Sockets
  in : command(text, locked) -> None | "engage" | ("release", phrase)   deterministic phrase matching on the user's words, no model involved
       set_passphrase(phrase) / check(phrase) -> bool                   salted scrypt hash in <data dir>/lockdown.json (outside the repo; a PIN of digits works as the passphrase, the old FRED used a PIN); `python -m engine.lockdown set` asks for it without echo
  out: Engine uses these (engine/loop.py): engaging clears the conversation and any pending yes/no, limits the registry to `config.lockdown_tools`, skips memory lookups and bus turn events;
       releasing needs the passphrase when one is set ("unlock <phrase>"). With no passphrase set, "end lockdown" releases, and the engage reply says so.
stash(vector): locked-down chat is kept as an embedding only, in a file apart from the memory graph.
saved()/save(): the locked flag in <data dir>/lockdown_state.json, so a restart or reboot stays locked (a bad file means unlocked).
Not here yet: swapping the 4B brain for the 2B talker while locked (the talker model arrives with P4 voice): Engine(lock_chat=...) is the socket, it answers chat turns while locked.
Self-check: python engine/lockdown.py
"""
import hashlib
import hmac
import json
import os
import re
import sys
import time
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config

ENGAGE = re.compile(r"^\W*(?:please\s+)?(?:(?:go|start|engage|enter|begin)\s+(?:into\s+)?(?:a\s+|the\s+)?lock\s?down(?:\s+mode)?|lock\s?down(?:\s+now|\s+mode)?|lock\s+(?:it|everything|fred)\s+down)\W*$", re.I)
RELEASE = re.compile(r"^\W*(?:unlock|(?:end|lift|disengage|exit|stop)\s+(?:the\s+)?lock\s?down)\b[\W]*(.*)$", re.I)


def _norm(s: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", s.lower()))


def _file() -> Path:
    return config.data_dir() / "lockdown.json"


def saved() -> bool:
    """Lockdown survives a restart; a missing or unreadable file means unlocked (never shut the owner out)."""
    try:
        return bool(json.loads((config.data_dir() / "lockdown_state.json").read_text(encoding="utf-8")).get("locked"))
    except (OSError, ValueError, AttributeError):
        return False


def save(locked: bool):
    (config.data_dir() / "lockdown_state.json").write_text(json.dumps({"locked": locked}), encoding="utf-8")


def stash(vector: list):
    """Locked-down chat is kept ONLY as an embedding (no text, no graph, no memory write) in <data dir>/lockdown_vectors.jsonl; a later job can promote what mattered."""
    with (config.data_dir() / "lockdown_vectors.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"t": round(time.time()), "v": [round(x, 5) for x in vector]}) + "\n")


def _hash(phrase: str, salt: bytes) -> str:
    return hashlib.scrypt(_norm(phrase).encode(), salt=salt, n=2 ** 14, r=8, p=1).hex()


def set_passphrase(phrase: str):
    if len(_norm(phrase)) < 4:
        raise ValueError("passphrase too short")
    salt = os.urandom(16)
    _file().write_text(json.dumps({"salt": salt.hex(), "hash": _hash(phrase, salt)}), encoding="utf-8")


def has_passphrase() -> bool:
    return _file().exists()


def check(phrase: str) -> bool:
    try:
        d = json.loads(_file().read_text(encoding="utf-8"))
        return hmac.compare_digest(_hash(phrase, bytes.fromhex(d["salt"])), d["hash"])
    except (OSError, ValueError, KeyError):
        return False


def command(text: str, locked: bool):
    if locked:
        m = RELEASE.match(text)
        return ("release", m.group(1)) if m else None
    return "engage" if ENGAGE.match(text) else None


def _selfcheck():
    import tempfile
    config.set_data_dir(tempfile.mkdtemp())
    for t in ("lockdown", "Lock down now.", "go into lockdown", "please engage lockdown mode", "lock fred down", "Lockdown!"):
        assert command(t, False) == "engage", t
    for t in ("what is a lockdown", "the lockdown ended yesterday", "turn the volume up", "end lockdown"):
        assert command(t, False) is None, t
    assert command("what time is it", True) is None and command("lockdown", True) is None
    assert command("end lockdown", True) == ("release", "") and command("Unlock, Blue Falcon 42!", True) == ("release", "Blue Falcon 42!")
    assert not saved()
    save(True)
    assert saved()
    save(False)
    assert not saved()
    stash([0.1234567, -0.5])
    row = json.loads((config.data_dir() / "lockdown_vectors.jsonl").read_text(encoding="utf-8"))
    assert row["v"] == [0.12346, -0.5] and set(row) == {"t", "v"}
    assert not has_passphrase() and not check("anything")
    set_passphrase("Blue Falcon 42")
    assert has_passphrase() and check("blue falcon 42!") and not check("blue falcon 43") and not check("")
    assert "falcon" not in _file().read_text(encoding="utf-8").lower()                       # only a salted hash is stored
    try:
        set_passphrase("ab")
        raise AssertionError
    except ValueError:
        pass
    print("lockdown ok")


if __name__ == "__main__":
    if sys.argv[1:2] == ["set"]:
        if len(sys.argv) > 2:                                                        # python -m engine.lockdown set 1234   (no echo prompt needed, e.g. from a "!" line)
            a = b = " ".join(sys.argv[2:])
        else:
            import getpass
            a, b = getpass.getpass("New lockdown passphrase: "), getpass.getpass("Again: ")
        if a != b:
            sys.exit("They differ; nothing saved.")
        set_passphrase(a)
        print("Saved to", _file())
    else:
        _selfcheck()
