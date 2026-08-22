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
# In-memory only, fire-once, lost on restart — same reasoning as
# sleep_mode.py's own state: a restart is a real event, not a crash to
# recover from.
#
# Deliberately does NOT import orchestrator.sleep_mode — sleep_mode.py
# imports this module (not the other way), so importing it back here
# would be circular. Also does NOT go through proactive_checks.notify:
# same import-cycle reason, and by the time on_sleep_exit runs
# is_sleeping() has already gone False, so that gate would pass through
# regardless — utils.notifier.notify is called directly instead, the
# same underlying function proactive_checks.py itself wraps.

from datetime import datetime

from orchestrator import reflection
from tools import session_summary, vault_map
from utils.notifier import notify

_llm = None
_pending = None  # str | None — the bundled recap, waiting to be spoken

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
    paragraph. Falls back to a plain join without an llm handle or on
    any generation failure — never blocks the recap on this."""
    raw = "\n".join(parts)
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
        note_path = session_summary._daily_note_path(day)
        existing_note = note_path.read_text(encoding="utf-8") if note_path.exists() else None
        summary_text = session_summary.summarise_today(day, llm=_llm, existing_note=existing_note)
        session_summary.save_session_summary(day, llm=_llm, summary=summary_text, auto=True)
        parts.append(summary_text)
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
    try:
        notify(message, title="Welcome back")
    except Exception as e:
        print(f"[consolidation] sleep-exit notify failed: {e}")


if __name__ == "__main__":
    # Self-check: pending-state lifecycle with the real tools stubbed
    # out (no LLM, no real vault needed to prove enter->exit->cleared).
    import tools.session_summary as _ss
    import tools.vault_map as _vm
    from orchestrator import reflection as _refl

    written = []
    calls = []
    globals()["notify"] = lambda msg, title="F.R.E.D.": calls.append((msg, title))
    _ss._daily_note_path = lambda day=None: type("P", (), {"exists": lambda self: False})()
    _ss.summarise_today = lambda day=None, llm=None, existing_note=None: "3 requests today."
    _ss.save_session_summary = lambda day=None, llm=None, summary="", auto=False: written.append(summary)
    _vm.scan_missing = lambda: ["a.md", "b.md"]
    _vm.append_missing = lambda auto=False: written.append(("map", auto))
    _refl.has_pending_review = lambda: False

    assert _pending is None
    on_sleep_exit()  # nothing pending yet — must be a no-op
    assert calls == []

    on_sleep_enter()  # no llm configured — polish falls back to a plain join
    assert _pending is not None
    assert "3 requests today." in _pending
    assert "a.md" in _pending and "b.md" in _pending
    assert written == ["3 requests today.", ("map", True)]  # both auto-written

    on_sleep_exit()
    assert calls and "3 requests today." in calls[0][0]
    assert _pending is None  # fire-once

    on_sleep_exit()  # already cleared — still a no-op
    assert len(calls) == 1

    print("consolidation self-check: all passed")
