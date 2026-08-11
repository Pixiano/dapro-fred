# ingest_real_positive_clips is the one non-trivial bit added
# 2026-08-11: it must run even when POS_TRAIN is already populated by
# Kokoro clips (unlike generate_positive_clips, which skips itself in
# that case), split real recordings into train/test, and not re-copy a
# file that's already there.

import numpy as np
import soundfile as sf

from input import wakeword_train


def _write_wav(path, seconds=1.0, sr=16000):
    samples = np.zeros(int(seconds * sr), dtype=np.float32)
    sf.write(str(path), samples, sr)


def test_ingest_splits_into_train_and_test(tmp_path, monkeypatch):
    real_dir = tmp_path / "real_positive"
    real_dir.mkdir()
    for i in range(10):
        _write_wav(real_dir / f"utt_{i}.wav")

    pos_train, pos_test = tmp_path / "pos_train", tmp_path / "pos_test"
    monkeypatch.setattr(wakeword_train, "REAL_POSITIVE_DIR", str(real_dir))
    monkeypatch.setattr(wakeword_train, "POS_TRAIN", str(pos_train))
    monkeypatch.setattr(wakeword_train, "POS_TEST", str(pos_test))

    wakeword_train.ingest_real_positive_clips()

    train_files = list(pos_train.iterdir())
    test_files = list(pos_test.iterdir())
    assert len(train_files) + len(test_files) == 10
    assert len(test_files) >= 1
    assert all(f.name.startswith("real_") for f in train_files + test_files)


def test_ingest_runs_even_when_pos_train_already_populated(tmp_path, monkeypatch):
    real_dir = tmp_path / "real_positive"
    real_dir.mkdir()
    _write_wav(real_dir / "utt_0.wav")

    pos_train, pos_test = tmp_path / "pos_train", tmp_path / "pos_test"
    pos_train.mkdir()
    (pos_train / "kokoro_clip.wav").write_bytes(b"")  # pre-existing Kokoro output
    pos_test.mkdir()

    monkeypatch.setattr(wakeword_train, "REAL_POSITIVE_DIR", str(real_dir))
    monkeypatch.setattr(wakeword_train, "POS_TRAIN", str(pos_train))
    monkeypatch.setattr(wakeword_train, "POS_TEST", str(pos_test))

    wakeword_train.ingest_real_positive_clips()

    assert any(f.name.startswith("real_") for f in list(pos_train.iterdir()) + list(pos_test.iterdir()))


def test_ingest_is_idempotent_per_file(tmp_path, monkeypatch):
    real_dir = tmp_path / "real_positive"
    real_dir.mkdir()
    _write_wav(real_dir / "utt_0.wav")

    pos_train, pos_test = tmp_path / "pos_train", tmp_path / "pos_test"
    monkeypatch.setattr(wakeword_train, "REAL_POSITIVE_DIR", str(real_dir))
    monkeypatch.setattr(wakeword_train, "POS_TRAIN", str(pos_train))
    monkeypatch.setattr(wakeword_train, "POS_TEST", str(pos_test))

    wakeword_train.ingest_real_positive_clips()
    before = {f: f.stat().st_mtime for f in pos_train.iterdir()} if pos_train.exists() else {}
    wakeword_train.ingest_real_positive_clips()
    after = {f: f.stat().st_mtime for f in pos_train.iterdir()} if pos_train.exists() else {}

    assert before == after


def test_ingest_noop_when_dir_absent(tmp_path, monkeypatch):
    pos_train, pos_test = tmp_path / "pos_train", tmp_path / "pos_test"
    monkeypatch.setattr(wakeword_train, "REAL_POSITIVE_DIR", str(tmp_path / "does_not_exist"))
    monkeypatch.setattr(wakeword_train, "POS_TRAIN", str(pos_train))
    monkeypatch.setattr(wakeword_train, "POS_TEST", str(pos_test))

    wakeword_train.ingest_real_positive_clips()

    assert not pos_train.exists()
    assert not pos_test.exists()
