# Core/tests/test_voice_id.py
#
# Pure logic test for input/voice_id.py's cosine-similarity/threshold
# handling — no real mic, no SpeechBrain model load. Real speaker
# discrimination is validated by hand (see voice_id.py's own docstring
# for the measured Kokoro-TTS-synthesized-voice numbers this module's
# MATCH_THRESHOLD is based on).

import json

import numpy as np
import pytest

from input import voice_id


def test_cosine_similarity_self_is_one():
    v = np.array([1.0, 2.0, 3.0])
    assert voice_id._cosine_similarity(v, v) == pytest.approx(1.0)


def test_cosine_similarity_opposite_is_negative_one():
    v = np.array([1.0, 0.0])
    assert voice_id._cosine_similarity(v, -v) == pytest.approx(-1.0)


def test_best_similarity_is_negative_one_when_nothing_enrolled(monkeypatch, tmp_path):
    monkeypatch.setattr(voice_id, "ENROLLMENT_PATH", tmp_path / "missing.json")
    monkeypatch.setattr(voice_id, "_enrollment_embeddings", None)
    assert voice_id.best_similarity(np.zeros(192)) == -1.0
    assert voice_id.is_match(np.zeros(192)) is False


def test_best_similarity_and_is_match_against_enrolled_embedding(monkeypatch, tmp_path):
    path = tmp_path / "voice_enrollment.json"
    ref = np.ones(192)
    path.write_text(json.dumps({"embeddings": [ref.tolist()]}), encoding="utf-8")
    monkeypatch.setattr(voice_id, "ENROLLMENT_PATH", path)
    monkeypatch.setattr(voice_id, "_enrollment_embeddings", None)
    monkeypatch.setattr(voice_id, "embed", lambda audio: ref)  # skip the real model load

    assert voice_id.best_similarity(np.zeros(1)) == pytest.approx(1.0)
    assert voice_id.is_match(np.zeros(1)) is True


def test_best_similarity_picks_the_closer_of_multiple_enrolled_embeddings(monkeypatch, tmp_path):
    path = tmp_path / "voice_enrollment.json"
    near = np.array([1.0, 0.0])
    far = np.array([0.0, 1.0])
    path.write_text(json.dumps({"embeddings": [far.tolist(), near.tolist()]}), encoding="utf-8")
    monkeypatch.setattr(voice_id, "ENROLLMENT_PATH", path)
    monkeypatch.setattr(voice_id, "_enrollment_embeddings", None)
    monkeypatch.setattr(voice_id, "embed", lambda audio: near)

    assert voice_id.best_similarity(np.zeros(1)) == pytest.approx(1.0)
