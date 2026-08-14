# Core/state/lockdown_state.py
#
# FRED's lockdown flag — while set, ToolRegistry.execute() (see
# tools/registry.py) refuses every tool call except lockdown() itself.
# Conversation still works; FRED just can't act on anything.
#
# Persisted to Core/data/lockdown_state.json (same atomic write-then-
# replace shape as proactive_checks.py's PROACTIVE_STATE_PATH) so a
# restart — including a full PC reboot — doesn't silently drop back to
# unlocked.
#
# Missing/corrupt state file -> fail OPEN (unlocked), never locked —
# a state file that didn't survive a crash should never be the reason
# someone's shut out.

import json

from config.settings import DATA_DIR

_STATE_PATH = DATA_DIR / "lockdown_state.json"


def _load() -> bool:
    if not _STATE_PATH.exists():
        return False
    try:
        return bool(json.loads(_STATE_PATH.read_text(encoding="utf-8")).get("locked", False))
    except (OSError, ValueError):
        return False


def _save(value: bool) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"locked": value}), encoding="utf-8")
    tmp.replace(_STATE_PATH)


_locked = _load()


def set_locked(value: bool) -> None:
    global _locked
    _locked = bool(value)
    _save(_locked)


def is_locked() -> bool:
    return _locked


if __name__ == "__main__":
    # Self-check, not Core/tests/ (regression-only, see its README).
    # Touches the real Core/data/lockdown_state.json; saves/restores it.
    _backup = _STATE_PATH.read_text(encoding="utf-8") if _STATE_PATH.exists() else None
    try:
        set_locked(True)
        assert is_locked() is True
        assert _load() is True  # actually round-tripped through disk, not just the module global

        set_locked(False)
        assert is_locked() is False
        assert _load() is False

        print("lockdown_state self-check: all passed")
    finally:
        if _backup is None:
            _STATE_PATH.unlink(missing_ok=True)
        else:
            _STATE_PATH.write_text(_backup, encoding="utf-8")
