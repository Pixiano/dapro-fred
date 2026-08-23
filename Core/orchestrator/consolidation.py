# Core/orchestrator/consolidation.py
#
# Sleep-mode consolidation: while Vatsal's away, AUTO-WRITE the day
# summary (session_summary.save_session_summary) and any MAP.md gap
# entries (vault_map.append_missing), then speak one bundled,
# LLM-polished recap the moment he's back (on_sleep_exit). Changed
# 2026-08-22 per Vatsal's explicit request: this used to be
# propose-only, requiring "save it" / "add them" afterward, but since
# this can fire on every sleep-mode cycle (potentially several times an
# hour) that got repetitive fast. Both writes carry an auto-logged
# marker (see save_session_summary/append_missing's `auto` param) so
# it's clear later which entries were unattended.
#
# Deep-reflection self-facts (reflection.py's personal/pending-review/
# staged-review flow) are DELIBERATELY NOT part of this — that's the
# one thing Vatsal explicitly excluded ("everything other than the
# gpt-oss deep work"), and it still requires his explicit review before
# anything about him personally gets written anywhere permanent.
#
# _pending (the not-yet-spoken recap) is in-memory only, fire-once,
# lost on restart — same reasoning as sleep_mode.py's own state: a
# restart is a real event, not a crash to recover from. The ask-count
# DEDUP WATERMARK is the one exception, persisted to
# CONSOLIDATION_STATE_PATH (see that constant's own comment) — a
# restart must not make FRED forget it already reported today's asks,
# or the exact same recap gets rebuilt and re-spoken from scratch.
#
# Deliberately does NOT import orchestrator.sleep_mode — sleep_mode.py
# imports this module (not the other way), so importing it back here
# would be circular. Also does NOT go through proactive_checks.notify:
# same import-cycle reason, and by the time on_sleep_exit runs
# is_sleeping() has already gone False, so that gate would pass through
# regardless — utils.notifier.notify is called directly instead, the
# same underlying function proactive_checks.py itself wraps.

import json
from datetime import datetime

from config.settings import CONSOLIDATION_STATE_PATH
from orchestrator import reflection
from tools import session_summary, vault_map
from utils.notifier import notify

_llm = None
_pending = None  # str | None — the bundled recap, waiting to be spoken


def _load_ask_count_state() -> dict:
    if not CONSOLIDATION_STATE_PATH.exists():
        return {}
    try:
        return json.loads(CONSOLIDATION_STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_ask_count_state(state: dict):
    CONSOLIDATION_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONSOLIDATION_STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    tmp.replace(CONSOLIDATION_STATE_PATH)

_POLISH_SYSTEM_PROMPT = (
    "Turn this into ONE short spoken sentence for a voice assistant's "
    "welcome-back recap — a single line, not a paragraph. If there are "
    "multiple things (e.g. a summary saved and files logged to "
    "MAP.md), combine them into that one sentence rather than listing "
    "them separately. Informational tone — you're reporting things "
    "that were already done automatically, not asking for "
    "confirmation. No markdown, no bullet points, no headings, no "
    "instructions to say anything back. Do not invent anything that "
    "isn't in the source material. Example of the target length: "
    "'While you were away, I saved today's summary and logged 2 new "
    "files to MAP.md.'"
)

_POLISH_MAX_TOKENS = 60  # one short sentence, not a paragraph


def configure(llm):
    global _llm
    _llm = llm


def _polish_recap(parts: list) -> str:
    """Combine the raw auto-write material into one smooth spoken
    sentence. Falls back to a plain join without an llm handle or on any
    generation failure — never blocks the recap on this.

    Joined with a space, not a newline: Vatsal's explicit call
    2026-08-23, the spoken recap must be one line, never several — the
    LLM-polish path is already told this in _POLISH_SYSTEM_PROMPT, but
    the no-LLM fallback below used to join with "\n", which could
    produce a literal multi-line message if both a day-summary and a
    MAP.md-gap part were pending in the same cycle."""
    raw = " ".join(parts)
    if _llm is not None:
        try:
            # local_only=True — same reasoning as summarise_today's own
            # llm.generate call: this reads a summary of session/vault
            # content, unattended, same sensitivity class.
            #
            # force_no_thinking=True — same fix as summarise_today's own
            # call (session_summary.py): `raw` is a data dump (an
            # already-built summary plus MAP.md lines), often over
            # THINKING_LENGTH_THRESHOLD, so the length-based heuristic
            # would turn thinking on for a one-sentence rewrite that
            # never needs it.
            return _llm.generate(
                [
                    {"role": "system", "content": _POLISH_SYSTEM_PROMPT},
                    {"role": "user", "content": raw},
                ],
                local_only=True,
                max_tokens=_POLISH_MAX_TOKENS,
                force_no_thinking=True,
            )
        except Exception as e:
            print(f"[consolidation] recap polish failed: {e}")
    return f"While you were away: {raw}"


def on_sleep_enter():
    """Auto-write the day summary and any MAP.md gap entries while
    Vatsal's away, then stash one polished recap to speak on wake.
    Never raises — a failure here must not block sleep-mode entry."""
    global _pending
    parts = []

    try:
        day = datetime.now().strftime("%Y-%m-%d")
        ask_count = len(session_summary.collect_today(day)["asks"])
        # Skip re-summarising (and re-speaking) when nothing new was asked
        # since the last cycle — the LLM rephrases an unchanged day
        # differently every call, so comparing its wording can't catch a
        # repeat; the ask count is the actual signal for "anything new."
        # Persisted (not a module-level int) so a restart mid-day doesn't
        # reset this to 0 and cause the whole day's recap to rebuild and
        # re-speak from scratch — confirmed live 2026-08-23.
        ask_state = _load_ask_count_state()
        if ask_state.get("day") != day or ask_state.get("ask_count") != ask_count:
            note_path = session_summary._daily_note_path(day)
            existing_note = note_path.read_text(encoding="utf-8") if note_path.exists() else None
            summary_text = session_summary.summarise_today(day, llm=_llm, existing_note=existing_note)
            session_summary.save_session_summary(day, llm=_llm, summary=summary_text, auto=True)
            parts.append(summary_text)
            _save_ask_count_state({"day": day, "ask_count": ask_count})
    except Exception as e:
        print(f"[consolidation] day-summary auto-write failed: {e}")

    try:
        missing = vault_map.scan_missing()
        if missing:
            vault_map.append_missing(auto=True)
            names = ", ".join(missing[:8])
            more = f" (+{len(missing) - 8} more)" if len(missing) > 8 else ""
            parts.append(f"Logged {len(missing)} new vault file(s) to MAP.md: {names}{more}.")
    except Exception as e:
        print(f"[consolidation] MAP.md auto-write failed: {e}")

    _pending = _polish_recap(parts) if parts else None


def append_pending(text: str):
    """
    Fold another module's own audit line into the same bundled recap,
    rather than competing with it as a second proactive announcement.
    reflection.py's sleep_mode.py hook calls this with its own short
    audit line ("Updated people/x.md ...") right after on_sleep_enter()
    runs, same call site, same cycle.
    """
    global _pending
    if not text:
        return
    _pending = f"{_pending} {text}" if _pending else text


def on_sleep_exit(greeting: str = None):
    """
    Speak the bundled recap once, then clear it — fire-once, not
    re-spoken on a later wake with nothing new pending.

    greeting: an optional short wake-greeting (e.g. "Welcome back,
    sir.") to prefix onto the same message, passed in by
    sleep_mode.on_presence_poll on a real presence-triggered wake.
    Confirmed live 2026-08-22: proactive_checks.check_presence used to
    fire its own separate notify() for this right after this function's
    notify() call — both landed in the same tick, competing for
    pill_app._speak_proactive's turn_lock, and the second one silently
    lost the race and never spoke. Folded in here instead, same fix
    this function already applies to reflection's review-offer below:
    one bundled message, not competing proactive announcements.

    Also offers reflection's staged self-fact review right alongside
    it, same wake moment, same single notify call — not a second,
    competing proactive announcement. This can fire even when there's
    no `_pending` recap at all (an unreviewed draft can be sitting
    there from days ago, long after the cycle that staged it).
    """
    global _pending
    message = _pending
    if greeting:
        message = f"{greeting} {message}" if message else greeting

    try:
        if reflection.has_pending_review():
            offer = reflection.offer_review_text()
            message = f"{message} {offer}" if message else offer
    except Exception as e:
        print(f"[consolidation] reflection review-offer failed: {e}")

    _pending = None
    if not message:
        return
    # Defensive one-line collapse (Vatsal's explicit call 2026-08-23):
    # _POLISH_SYSTEM_PROMPT already asks the LLM for a single sentence,
    # but a local model doesn't always follow that, and this is the last
    # point before speaking regardless of which path produced `message`.
    message = " ".join(message.split())
    try:
        notify(message, title="Welcome back")
    except Exception as e:
        print(f"[consolidation] sleep-exit notify failed: {e}")


if __name__ == "__main__":
    # Self-check: pending-state lifecycle with the real tools stubbed
    # out (no LLM, no real vault needed to prove enter->exit->cleared).
    import tempfile
    from pathlib import Path

    import tools.session_summary as _ss
    import tools.vault_map as _vm
    from orchestrator import reflection as _refl

    globals()["CONSOLIDATION_STATE_PATH"] = Path(tempfile.mkdtemp()) / "consolidation_state.json"

    written = []
    calls = []
    asks = ["what's the weather", "add a task", "read my email"]  # 3 asks
    map_missing = ["a.md", "b.md"]  # cleared once "logged", like the real scan_missing/append_missing pair
    globals()["notify"] = lambda msg, title="F.R.E.D.": calls.append((msg, title))
    _ss._daily_note_path = lambda day=None: type("P", (), {"exists": lambda self: False})()
    _ss.collect_today = lambda day=None: {"asks": asks}
    # LLM-free rephrasing stand-in: real local-model output varies wording
    # call to call even for unchanged content, so the fixture varies too —
    # proves the dedup below can't be relying on exact-text matching.
    _ss.summarise_today = lambda day=None, llm=None, existing_note=None: f"{len(asks)} requests today (call {len(written) + 1})."
    _ss.save_session_summary = lambda day=None, llm=None, summary="", auto=False: written.append(summary)
    _vm.scan_missing = lambda: list(map_missing)
    _vm.append_missing = lambda auto=False: (map_missing.clear(), written.append(("map", auto)))[-1]
    _refl.has_pending_review = lambda: False

    assert _pending is None
    on_sleep_exit()  # nothing pending yet — must be a no-op
    assert calls == []

    on_sleep_enter()  # no llm configured — polish falls back to a plain join
    assert _pending is not None
    assert "3 requests today" in _pending
    assert "a.md" in _pending and "b.md" in _pending
    assert len(written) == 2  # summary + map, both auto-written
    assert "\n" not in _pending  # one line, per Vatsal's 2026-08-23 call

    # The dedup watermark landed on disk, not just in memory — this is
    # what makes it survive a restart (confirmed live 2026-08-23: without
    # this, several same-day FRED restarts each re-triggered the
    # identical recap from scratch).
    on_disk = json.loads(CONSOLIDATION_STATE_PATH.read_text(encoding="utf-8"))
    assert on_disk["ask_count"] == 3

    on_sleep_exit()
    assert calls and "3 requests today" in calls[0][0]
    assert _pending is None  # fire-once

    on_sleep_exit()  # already cleared — still a no-op
    assert len(calls) == 1

    # Next sleep cycle, same asks count (nothing new) — must not
    # re-summarise or re-speak, per Vatsal's 2026-08-22/23 request. This
    # is checked via the ask-count gate, not by comparing the (variable)
    # LLM wording, since that's what silently failed to catch a repeat
    # before.
    on_sleep_enter()
    assert _pending is None  # asks unchanged, MAP.md already caught up — nothing to report
    assert len(written) == 2  # no new writes either
    on_sleep_exit()
    assert len(calls) == 1  # no new notify call

    # Simulate a restart: reset everything that would actually reset on
    # one (module-level _pending, per this module's own docstring) while
    # leaving the on-disk watermark alone. Same asks as before must
    # still not re-trigger — this is the actual bug being fixed here.
    globals()["_pending"] = None
    on_sleep_enter()
    assert _pending is None
    on_sleep_exit()
    assert len(calls) == 1  # still no new notify call, post-"restart"

    # A genuinely new ask arrives — must summarise and speak again.
    asks.append("one more thing")
    on_sleep_enter()
    assert _pending is not None
    assert "4 requests today" in _pending
    on_sleep_exit()
    assert len(calls) == 2

    print("consolidation self-check: all passed")
