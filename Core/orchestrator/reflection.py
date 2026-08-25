# Core/orchestrator/reflection.py
#
# Sleep-mode reasoning pass: while Vatsal's away, re-read recent session
# logs (plus the existing people/*.md corpus, so the model knows who
# already has a file) with the "Reflect" tier (gpt-oss-20b, medium
# reasoning effort — see settings.py's MODEL_TIERS/TIER_PROMPT_MARKERS)
# and pull out two kinds of durable facts: things about the people he
# talks about, and things about him.
#
# Two write paths, deliberately different trust levels:
#   - people/*.md — written directly, unattended. Vatsal's own words:
#     this is the concrete mechanic he asked for by name.
#   - self-facts — staged to VAULT_DIR/personal/pending-review/, never
#     touched unattended. Offered for review on the next presence-
#     detected wake ("would you want to review the changes, sir?"),
#     re-offered periodically until he says yes.
#
# Separate module from consolidation.py on purpose: consolidation never
# writes anything (its own docstring is explicit about that), and this
# does — keeping them apart keeps consolidation's "never writes"
# property visibly true rather than something you have to go verify.
#
# Gated on ACCUMULATED NEW MATERIAL, not on sleep-mode entry itself —
# checked every time sleep_mode.py calls run_if_due() on entry. Below
# REFLECTION_MIN_NEW_EVENTS new user_speech/tool_call events since the
# last pass, this silently does nothing (same fail-open-silent
# convention consolidation.py uses on its own short-circuits) and does
# NOT touch reflection_state.json — so a quiet stretch doesn't reset the
# counter, it just keeps accumulating toward the threshold.
#
# INTERRUPT SAFETY: a single llm.generate() call can't be interrupted
# mid-generation (see llm_client.py's generate_with_tools docstring —
# no stopping_criteria hook on that path), so interruption granularity
# here is "between chunks" (one chunk = one session-log file). Before
# starting each chunk, sleep_mode.is_sleeping() AND _turn_in_progress()
# are both checked; the instant either says Vatsal's back (camera
# presence returned, or a real turn is using the LLM/STT/TTS pipeline
# right now), everything buffered so far for this run is discarded —
# no writes, no state update — so the same unprocessed material is
# picked up whole on the next qualifying window rather than being
# half-credited. The _turn_in_progress() half was added 2026-08-25 —
# see its own docstring for the crash that made it necessary.

import json
import os
import re
from datetime import date, datetime
from pathlib import Path

from config.settings import (
    VAULT_DIR,
    REFLECTION_STATE_PATH,
    REFLECTION_MIN_NEW_EVENTS,
    REFLECTION_PENDING_DIR,
)
from tools.session_summary import _read_events
from utils.event_log import SESSION_DIR

PEOPLE_DIR = VAULT_DIR / "people"
REVIEWED_DIR = REFLECTION_PENDING_DIR / "reviewed"

# Same event kinds session_summary.py treats as "real content" — fillers
# and canned replies carry no information worth reflecting on.
_COUNTED_TYPES = ("user_speech", "tool_call")

_llm = None

# Set by orchestrator.py alongside proactive_checks.register's own
# on_agenda_ask wiring (same _prime_carry bound method) — lets the
# review-offer route a bare "yes" to the review_pending_reflection tool
# on the very next turn, without inventing a second confirmation
# channel. None (CLI, or anything that never calls configure()) just
# means the offer is spoken but a "yes" reply isn't specially routed.
_prime_carry = None


def configure(llm, prime_carry=None):
    global _llm, _prime_carry
    _llm = llm
    _prime_carry = prime_carry


# =========================================================
# STATE (same tmp-file-then-replace pattern as proactive_checks.py)
# =========================================================

def _load_state() -> dict:
    if not REFLECTION_STATE_PATH.exists():
        return {}
    try:
        return json.loads(REFLECTION_STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(state: dict):
    REFLECTION_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = REFLECTION_STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    tmp.replace(REFLECTION_STATE_PATH)


# =========================================================
# TRIGGER GATE + PASS ORCHESTRATION
# =========================================================

def _new_session_files_and_count(last_run_ts: str) -> tuple:
    """
    Every session-log file that could hold events newer than
    `last_run_ts`, plus how many countable (user_speech/tool_call)
    events actually are. Doesn't bother excluding files entirely before
    that date — one file is one calendar day, cheap to read in full —
    so the per-event ts filter below is the only thing that matters.
    """
    if not SESSION_DIR.exists():
        return [], 0

    files = sorted(SESSION_DIR.glob("session_*.jsonl"))
    count = 0
    for path in files:
        for event in _read_events([path]):
            if event.get("ts", "") > last_run_ts and event.get("type") in _COUNTED_TYPES:
                count += 1
    return files, count


def run_if_due():
    """
    Called from sleep_mode.py's enter hook, alongside
    consolidation.on_sleep_enter(). Never raises — a failure here must
    not block sleep-mode entry, same rule consolidation.py holds itself
    to. Returns a short audit line to fold into consolidation's own
    recap text if the pass actually ran and wrote something, else None.
    """
    try:
        return _run_if_due()
    except Exception as e:
        print(f"[reflection] pass failed: {e}")
        return None


def _turn_in_progress() -> bool:
    """True if pill_app's own _turn_lock is currently held — a real
    conversational turn (or a proactive utterance) is using the
    STT/LLM/TTS pipeline RIGHT NOW. Checked alongside
    sleep_mode.is_sleeping() before every chunk's LLM call below —
    confirmed live 2026-08-25: is_sleeping() alone isn't enough, since
    it only flips once PRESENCE_PRESENT_DEBOUNCE consecutive camera
    polls clear, not the instant a hotkey/wake-word/HUD turn actually
    starts. That gap let this module's own _reflect_on_chunk() generate
    call collide with a live turn's faster-whisper transcription — two
    separate native libraries with no lock between them — and
    hard-aborted the whole process (Fatal Python error: Aborted, inside
    llama_cpp's decode()), not a catchable Python exception. See
    ui/pill_app.py's own _turn_lock comment (~line 813) for the
    matching turn-vs-turn version of this same crash signature.

    Imported locally, not at module level: ui.pill_app already imports
    orchestrator modules, so a top-level import here would cycle (same
    reasoning tools/system_tools.py's own get_current_app imports
    already follow)."""
    from ui.pill_app import get_current_app

    app = get_current_app()
    lock = getattr(app, "_turn_lock", None)
    return lock is not None and lock.locked()


def _run_if_due():
    # Imported here, not at module level: sleep_mode.py calls
    # run_if_due() from its own module, so a top-level import here would
    # cycle back through it (same reasoning consolidation.py's own
    # docstring gives for avoiding a top-level sleep_mode import
    # entirely — this module can't avoid it outright since it genuinely
    # needs is_sleeping() mid-pass, so it's deferred instead).
    from orchestrator import sleep_mode

    state = _load_state()
    last_run_ts = state.get("last_run_ts")

    if last_run_ts is None:
        # First run ever — bootstrap the watermark to now rather than
        # ingesting the entire pre-existing log history in one pass.
        # Nothing to reflect on yet by definition (there's no "since"
        # to measure from), so this cycle is a silent no-op.
        _save_state({"last_run_ts": datetime.now().isoformat(), "last_run_event_count": 0})
        return None

    files, new_count = _new_session_files_and_count(last_run_ts)
    if new_count < REFLECTION_MIN_NEW_EVENTS:
        return None

    if _llm is None:
        print("[reflection] no llm configured, skipping pass")
        return None

    corpus = _people_corpus()

    friend_entries = []
    self_facts = []

    for path in files:
        if not sleep_mode.is_sleeping() or _turn_in_progress():
            # Vatsal's back mid-pass — discard everything buffered for
            # THIS run and bail without touching state, per the module
            # docstring's interrupt-safety contract. _turn_in_progress()
            # catches the case is_sleeping()'s debounce hasn't caught up
            # to yet — see that function's own docstring.
            return None

        events = [e for e in _read_events([path]) if e.get("ts", "") > last_run_ts]
        if not events:
            continue

        parsed = _reflect_on_chunk(events, corpus)
        friend_entries.extend(parsed.get("friend_entries", []))
        self_facts.extend(parsed.get("self_facts", []))

    # One last check before committing anything — the loop above can
    # finish its final chunk in the same instant presence returns.
    if not sleep_mode.is_sleeping() or _turn_in_progress():
        return None

    audit_lines = []

    for entry in friend_entries:
        person = (entry.get("person") or "").strip()
        content = (entry.get("content") or "").strip()
        if not person or not content:
            continue
        path = _write_friend_entry(person, entry.get("file_action", "update"), content)
        if path:
            audit_lines.append(f"Updated people/{path.name} while reviewing recent history.")

    if self_facts:
        draft_path = _write_staged_draft(self_facts)
        audit_lines.append(
            f"Staged {len(self_facts)} self-observation(s) for review ({draft_path.name})."
        )

    _save_state({
        "last_run_ts": datetime.now().isoformat(),
        "last_run_event_count": new_count,
    })

    return " ".join(audit_lines) if audit_lines else None


# =========================================================
# THE REASONING PASS ITSELF
# =========================================================

def _people_corpus() -> str:
    """Small, already-curated — every people/*.md file's content
    (minus the template/contacts/whatsapp-tier files, same exclusions
    the vault router itself applies), so the model knows whether a
    person already has a file to update rather than guessing."""
    if not PEOPLE_DIR.exists():
        return ""

    skip = {"_TEMPLATE.md", "contacts.md"}
    parts = []
    for path in sorted(PEOPLE_DIR.glob("*.md")):
        if path.name in skip or path.name.startswith("whatsapp-tiers-"):
            continue
        try:
            parts.append(f"### {path.name}\n{path.read_text(encoding='utf-8')}")
        except OSError:
            continue
    return "\n\n".join(parts)


_REFLECT_SYSTEM_PROMPT = """You are reviewing a slice of a personal assistant's session logs to \
extract durable, worth-remembering facts. Two kinds only:

1. FRIEND-FILE facts: something learned about a specific named person \
Vatsal talks about (not Vatsal himself) that would help the assistant \
be more useful about them later — a preference, a recurring plan, real \
context. You are given the existing people/*.md files below; decide \
whether the person already has a file (file_action "update") or needs \
a new one (file_action "new").

2. SELF facts: a durable observation about Vatsal himself — thinking \
style, psychology, work ethic, recurring patterns — not a one-off \
event.

Ignore small talk, one-off logistics, and anything already obviously \
recorded in the existing files below. If there is nothing worth \
recording, return empty lists. Never invent people or facts not \
actually present in the log.

Respond with ONLY a JSON object, no prose, no markdown fences:
{"friend_entries": [{"person": "name", "file_action": "new"|"update", "content": "one factual sentence"}], \
"self_facts": [{"content": "one factual sentence"}]}"""


def _extract_json(text: str) -> dict:
    """Model output is occasionally wrapped in prose or fences despite
    the instruction not to — pull the first balanced {...} object out
    rather than trusting json.loads on the raw string."""
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}


def _reflect_on_chunk(events: list, corpus: str) -> dict:
    lines = []
    for e in events:
        kind = e.get("type")
        if kind == "user_speech" and e.get("text"):
            lines.append(f"Vatsal: {e['text']}")
        elif kind == "fred_speech" and e.get("text") and not e.get("filler"):
            lines.append(f"FRED: {e['text']}")
        elif kind == "tool_call" and e.get("tool"):
            lines.append(f"[tool: {e['tool']}]")
    transcript = "\n".join(lines)
    if not transcript.strip():
        return {}

    messages = [
        {"role": "system", "content": _REFLECT_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Existing people files:\n{corpus or '(none yet)'}\n\n"
                f"Session log slice:\n{transcript}"
            ),
        },
    ]

    try:
        # local_only=True is not optional here — this reads people/ and
        # personal/-shaped content, exactly what rules.md forbids
        # sending anywhere but a local model.
        answer = _llm.generate(messages, tier="Reflect", local_only=True)
    except Exception as e:
        print(f"[reflection] chunk generation failed: {e}")
        return {}

    return _extract_json(answer)


# =========================================================
# WRITE PATH 1 — friend files: free-reign, no confirmation
# =========================================================

def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "unknown"


_FRIEND_TEMPLATE = """---
type: reference
status: active
sensitive: true
updated: {today}
source: inferred
---

# {name}

**Relationship:** unknown — recorded by FRED's sleep-mode reflection pass, correct if wrong
**Comes up in:** (auto-generated, needs review)

---

## Context

-

---

## Recurring

-

---

## Notes

- `{today}` — {content}

---

> Remember: this is someone else's information. Record what makes FRED useful, not everything known about them.
"""


def _write_friend_entry(person: str, file_action: str, content: str) -> Path:
    PEOPLE_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    path = PEOPLE_DIR / f"{_slugify(person)}.md"

    if file_action == "new" or not path.exists():
        path.write_text(
            _FRIEND_TEMPLATE.format(name=person, today=today, content=content),
            encoding="utf-8",
        )
        return path

    try:
        existing = path.read_text(encoding="utf-8")
    except OSError:
        return None

    bullet = f"- `{today}` — {content}\n"
    marker = "\n---\n\n> Remember:"
    if marker in existing:
        existing = existing.replace(marker, f"\n{bullet}{marker}", 1)
    else:
        existing = existing.rstrip("\n") + f"\n\n{bullet}"

    path.write_text(existing, encoding="utf-8")
    return path


# =========================================================
# WRITE PATH 2 — self-facts: staged review, not a direct write
# =========================================================

def _write_staged_draft(self_facts: list) -> Path:
    REFLECTION_PENDING_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    path = REFLECTION_PENDING_DIR / f"{today}_self-observations.md"

    bullets = "\n".join(f"- {f.get('content', '').strip()}" for f in self_facts if f.get("content"))

    if path.exists():
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"\n{bullets}\n")
    else:
        path.write_text(
            f"---\ntype: draft\nstatus: pending-review\nupdated: {today}\n---\n\n"
            f"# Self-observations — {today}\n\n"
            f"Staged by FRED's sleep-mode reflection pass. Not yet reviewed or "
            f"merged into [[profile]].\n\n{bullets}\n",
            encoding="utf-8",
        )
    return path


# =========================================================
# STAGED-REVIEW OFFER (wake-moment) + RECURRING NUDGE
# =========================================================

def _pending_files() -> list:
    """Un-reviewed staged drafts, oldest first — anything directly under
    REFLECTION_PENDING_DIR that hasn't been moved into reviewed/."""
    if not REFLECTION_PENDING_DIR.exists():
        return []
    return sorted(p for p in REFLECTION_PENDING_DIR.glob("*.md") if p.is_file())


def has_pending_review() -> bool:
    return bool(_pending_files())


def offer_review_text() -> str:
    """Short, sir-suffixed, matches canned_replies.py's register. Called
    once from the wake moment (and again from the periodic nudge) —
    primes the tool carry-forward so a bare 'yes' on the next turn
    routes to review_pending_reflection rather than falling through to
    chat."""
    if _prime_carry is not None:
        try:
            _prime_carry(["review_pending_reflection"])
        except Exception as e:
            print(f"[reflection] prime_carry failed: {e}")
    return "Sir, I made a few notes about you while you were away — review them, or keep working?"


def review_pending() -> str:
    """
    The 'yes' path: open the oldest un-reviewed draft with its default
    program (os.startfile — this is a markdown file, not a custom
    viewer) and mark it reviewed by moving it out of the un-reviewed
    set, so the periodic nudge below stops re-offering it.
    """
    pending = _pending_files()
    if not pending:
        return "Nothing pending, sir."

    path = pending[0]
    try:
        os.startfile(str(path))
    except Exception as e:
        return f"Couldn't open {path.name}: {e}"

    REVIEWED_DIR.mkdir(parents=True, exist_ok=True)
    try:
        path.replace(REVIEWED_DIR / path.name)
    except OSError as e:
        print(f"[reflection] couldn't mark {path.name} reviewed: {e}")

    return f"Opened {path.name}, sir."


if __name__ == "__main__":
    # Self-check: volume gate, interrupt-discard, friend-file free-reign
    # write, staged-file creation. No real LLM/vault — everything below
    # is redirected into a tmp dir.
    import tempfile
    from pathlib import Path as _Path
    from orchestrator import sleep_mode

    with tempfile.TemporaryDirectory() as tmp:
        tmp = _Path(tmp)
        session_dir = tmp / "sessions"
        session_dir.mkdir()
        people_dir = tmp / "people"
        pending_dir = tmp / "pending"

        globals()["SESSION_DIR"] = session_dir
        globals()["PEOPLE_DIR"] = people_dir
        globals()["REFLECTION_PENDING_DIR"] = pending_dir
        globals()["REVIEWED_DIR"] = pending_dir / "reviewed"
        globals()["REFLECTION_STATE_PATH"] = tmp / "reflection_state.json"

        def _write_events(name, events, ts_base="2026-08-21T10:00:00"):
            path = session_dir / name
            with open(path, "w", encoding="utf-8") as f:
                for i, e in enumerate(events):
                    e = dict(e)
                    e.setdefault("ts", f"{ts_base}.{i:03d}")
                    f.write(json.dumps(e) + "\n")

        # --- volume gate: below threshold does nothing, doesn't touch state ---
        _save_state({"last_run_ts": "2026-08-20T00:00:00"})
        _write_events(
            "session_2026-08-21.jsonl",
            [{"type": "user_speech", "text": "hi"}] * (REFLECTION_MIN_NEW_EVENTS - 1),
        )
        assert _run_if_due() is None
        assert _load_state()["last_run_ts"] == "2026-08-20T00:00:00"

        # --- at threshold: runs (mock llm + sleep_mode.is_sleeping True throughout) ---
        _write_events(
            "session_2026-08-21.jsonl",
            [{"type": "user_speech", "text": "hi"}] * REFLECTION_MIN_NEW_EVENTS,
        )

        class _FakeLLM:
            def generate(self, messages, tier=None, local_only=False):
                assert local_only is True
                assert tier == "Reflect"
                return json.dumps({
                    "friend_entries": [{"person": "Test Friend", "file_action": "new", "content": "likes tea"}],
                    "self_facts": [{"content": "works best late at night"}],
                })

        configure(_FakeLLM())
        sleep_mode._sleeping = True
        result = _run_if_due()
        assert result is not None and "test-friend.md" in result, result
        assert (people_dir / "test-friend.md").exists()
        assert "likes tea" in (people_dir / "test-friend.md").read_text()
        pending = list(pending_dir.glob("*.md"))
        assert len(pending) == 1 and "works best late at night" in pending[0].read_text()
        assert _load_state()["last_run_ts"] != "2026-08-20T00:00:00"

        # --- interrupt mid-pass: no writes, state unchanged ---
        _save_state({"last_run_ts": "2026-08-21T00:00:00"})
        _write_events(
            "session_2026-08-22.jsonl",
            [{"type": "user_speech", "text": "hi"}] * REFLECTION_MIN_NEW_EVENTS,
            ts_base="2026-08-22T10:00:00",
        )
        sleep_mode._sleeping = False  # already "awake" before the pass even starts
        state_before = _load_state()
        result = _run_if_due()
        assert result is None
        assert _load_state() == state_before

        # --- review flow: pending -> offer primes carry -> review moves file ---
        primed = []
        configure(_FakeLLM(), prime_carry=lambda names: primed.append(names))
        assert has_pending_review()
        text = offer_review_text()
        assert "review" in text.lower() and primed == [["review_pending_reflection"]]

        import os as _os
        _os.startfile = lambda p: None  # no real shell-open in a test
        msg = review_pending()
        assert "Opened" in msg
        assert not has_pending_review()  # moved into reviewed/
        assert list((pending_dir / "reviewed").glob("*.md"))

    print("reflection self-check: all passed")
