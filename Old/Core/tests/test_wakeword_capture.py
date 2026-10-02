# wakeword_capture.save()'s non-trivial bits: spoke_after must come
# from the followup audio's actual peak (not from whether a transcript
# was produced — a cancelled turn has no transcript but may well have
# real speech in it), and a fire with silence afterward must still
# produce a clip (that's the false-positive half of the dataset).

import json

import numpy as np
import soundfile as sf

from input import wakeword_capture


def _loud(seconds=1.0, sr=16000):
    return np.full(int(seconds * sr), 0.5, dtype=np.float32)


def _quiet(seconds=1.0, sr=16000):
    return np.zeros(int(seconds * sr), dtype=np.float32)


def _last_record(manifest_path):
    with open(manifest_path, encoding="utf-8") as f:
        lines = [l for l in f if l.strip()]
    return json.loads(lines[-1])


def test_real_followup_speech_is_flagged(tmp_path, monkeypatch):
    monkeypatch.setattr(wakeword_capture, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(wakeword_capture, "MANIFEST_PATH", tmp_path / "manifest.jsonl")

    wakeword_capture.save(
        trigger_audio=_loud(0.5), followup_audio=_loud(1.0),
        cancelled=False, transcript="what's the weather",
        wake_score=0.9, wake_gain=10.0,
    )

    record = _last_record(tmp_path / "manifest.jsonl")
    assert record["spoke_after"] is True
    assert (tmp_path / record["file"]).exists()


def test_silence_after_fire_is_a_false_positive(tmp_path, monkeypatch):
    monkeypatch.setattr(wakeword_capture, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(wakeword_capture, "MANIFEST_PATH", tmp_path / "manifest.jsonl")

    wakeword_capture.save(
        trigger_audio=_loud(0.5), followup_audio=_quiet(1.2),
        cancelled=False, transcript="",
        wake_score=0.76, wake_gain=37.9,
    )

    record = _last_record(tmp_path / "manifest.jsonl")
    assert record["spoke_after"] is False


def test_cancelled_turn_with_real_speech_still_flags_spoke_after(tmp_path, monkeypatch):
    """The actual point of this feature: 'mostly cancelled using the
    FRED button' must not be mistaken for silence just because there's
    no transcript — spoke_after comes from the audio, not the text."""
    monkeypatch.setattr(wakeword_capture, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(wakeword_capture, "MANIFEST_PATH", tmp_path / "manifest.jsonl")

    wakeword_capture.save(
        trigger_audio=_loud(0.5), followup_audio=_loud(0.8),
        cancelled=True, transcript="",
        wake_score=0.62, wake_gain=21.5,
    )

    record = _last_record(tmp_path / "manifest.jsonl")
    assert record["spoke_after"] is True
    assert record["cancelled"] is True


def test_no_followup_audio_at_all_is_not_spoken(tmp_path, monkeypatch):
    monkeypatch.setattr(wakeword_capture, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(wakeword_capture, "MANIFEST_PATH", tmp_path / "manifest.jsonl")

    wakeword_capture.save(
        trigger_audio=_loud(0.5), followup_audio=None,
        cancelled=False, transcript="",
        wake_score=0.55, wake_gain=15.0,
    )

    record = _last_record(tmp_path / "manifest.jsonl")
    assert record["spoke_after"] is False
    assert record["followup_seconds"] == 0.0


def test_saved_clip_readable_and_right_length(tmp_path, monkeypatch):
    monkeypatch.setattr(wakeword_capture, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(wakeword_capture, "MANIFEST_PATH", tmp_path / "manifest.jsonl")

    trigger = _loud(0.5)
    followup = _loud(1.0)
    wakeword_capture.save(
        trigger_audio=trigger, followup_audio=followup,
        cancelled=False, transcript="hi",
        wake_score=0.9, wake_gain=10.0,
    )

    record = _last_record(tmp_path / "manifest.jsonl")
    saved, sr = sf.read(tmp_path / record["file"])
    assert sr == wakeword_capture.SR
    expected_len = len(trigger) + int(wakeword_capture.SR * wakeword_capture._GAP_SECONDS) + len(followup)
    assert abs(len(saved) - expected_len) <= 1  # float32 WAV round-trip tolerance
