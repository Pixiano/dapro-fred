# The one non-trivial bit: log_score skips near-silence (the vast
# majority of chunks while idle) but always writes a real attempt,
# fired or not — a near-miss score is exactly what's useful to see.

import json

from input import wakeword_log


def test_below_floor_and_not_fired_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(wakeword_log, "LOG_PATH", tmp_path / "wakeword_log.jsonl")

    wakeword_log.log_score(score=0.01, gain=1.0, threshold=0.4, fired=False)

    assert not (tmp_path / "wakeword_log.jsonl").exists()


def test_above_floor_is_written(tmp_path, monkeypatch):
    log_path = tmp_path / "wakeword_log.jsonl"
    monkeypatch.setattr(wakeword_log, "LOG_PATH", log_path)

    wakeword_log.log_score(score=0.35, gain=2.0, threshold=0.4, fired=False)

    record = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert record["score"] == 0.35
    assert record["fired"] is False


def test_fired_is_always_written_even_below_floor(tmp_path, monkeypatch):
    log_path = tmp_path / "wakeword_log.jsonl"
    monkeypatch.setattr(wakeword_log, "LOG_PATH", log_path)

    wakeword_log.log_score(score=0.02, gain=1.0, threshold=0.4, fired=True)

    record = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert record["fired"] is True
