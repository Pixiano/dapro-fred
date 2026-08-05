# Core/tests/test_day_rollover.py
#
# The overnight rollover: idle long enough, the new day's note exists and
# yesterday's still-open tasks are in it. Run: python -m tests.test_day_rollover

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator import proactive_checks as pc
from tools import daily_tasks, session_summary


class _FakeLLM:
    """Keeps task 1, drops the rest — enough to prove the answer is read.
    Also captures the prompt so the test can check the day's conversation
    was included as context."""
    prompt = ""

    def generate(self, messages, **kwargs):
        assert kwargs.get("tier") == "Deep"
        _FakeLLM.prompt = messages[-1]["content"]
        return "1"


def _setup(tmp, day, prev_day):
    daily_tasks.VAULT_DIR = tmp
    pc.PROACTIVE_STATE_PATH = tmp / "state.json"

    # A previous-day session log, so _recent_transcript has something to
    # read — same shape event_log.py writes.
    sessions = tmp / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    session_summary.SESSIONS_DIR = sessions
    (sessions / f"session_{prev_day}.jsonl").write_text(
        '{"type": "user_speech", "text": "what is left"}\n'
        '{"type": "fred_speech", "text": "the errand, sir", "filler": false}\n'
        '{"type": "fred_speech", "text": "one moment", "filler": true}\n'
        '{"type": "tool_call", "tool": "list_tasks"}\n',
        encoding="utf-8",
    )

    daily_tasks.add_task("finish the report", day=prev_day)
    daily_tasks.add_task("obsolete errand", day=prev_day)
    daily_tasks.complete_task("finish", day=prev_day)  # done -> must not carry


def main():
    import tempfile

    day, prev = "2026-08-05", "2026-08-04"
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _setup(tmp, day, prev)

        assert daily_tasks.carryover_candidates(day) == ["obsolete errand"], \
            daily_tasks.carryover_candidates(day)

        # Not idle enough -> nothing happens.
        pc._idle_seconds = lambda: 60
        pc.check_day_rollover(llm=_FakeLLM())
        assert not (tmp / "daily" / "2026-08" / f"{day}.md").exists()

        # Idle past the threshold -> note created, open task carried.
        pc._idle_seconds = lambda: pc.ROLLOVER_IDLE_HOURS * 3600 + 1
        pc.check_day_rollover(llm=_FakeLLM())
        text = (tmp / "daily" / "2026-08" / f"{day}.md").read_text(encoding="utf-8")
        assert "- [ ] obsolete errand" in text, text
        assert "finish the report" not in text, text

        # The day's turns reached the judge, in order, fillers dropped.
        p = _FakeLLM.prompt
        assert "Vatsal: what is left" in p, p
        assert "FRED: the errand, sir" in p, p
        assert "[tool: list_tasks]" in p, p
        assert "one moment" not in p, p

        # Second run is a no-op — no duplicate lines.
        pc.check_day_rollover(llm=_FakeLLM())
        assert text == (tmp / "daily" / "2026-08" / f"{day}.md").read_text(encoding="utf-8")

    print("day rollover: ok")


if __name__ == "__main__":
    main()
