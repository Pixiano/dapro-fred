# Core/tests/test_convert_file.py
#
# New tool (2026-08-12), not a bug fix — pins the contract instead of a
# session log: no real ffmpeg is spawned (subprocess.run is faked), no
# real disk outside tmp_path is touched, and a failure never leaks
# ffmpeg's raw stderr into the spoken reply (test_speech_safety.py's
# rule, checked here too since this is the tool most likely to grow a
# wall of codec/build text).

import subprocess

from tools import system_tools


def test_missing_source_file_is_reported(tmp_path):
    result = system_tools.convert_file(str(tmp_path / "nope.wav"), "mp3")
    assert "Couldn't find" in result


def test_missing_ffmpeg_is_reported_speech_safely(tmp_path, monkeypatch):
    source = tmp_path / "song.wav"
    source.write_bytes(b"fake audio")
    monkeypatch.setattr(system_tools.shutil, "which", lambda name: None)

    result = system_tools.convert_file(str(source), "mp3")
    assert "ffmpeg isn't installed" in result


def test_successful_conversion(tmp_path, monkeypatch):
    source = tmp_path / "song.wav"
    source.write_bytes(b"fake audio")
    monkeypatch.setattr(system_tools.shutil, "which", lambda name: r"C:\ffmpeg\bin\ffmpeg.exe")

    def fake_run(cmd, **kwargs):
        # Simulate ffmpeg actually writing the output file.
        (tmp_path / "song.mp3").write_bytes(b"fake mp3")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="a wall of codec build info")

    monkeypatch.setattr(system_tools.subprocess, "run", fake_run)

    result = system_tools.convert_file(str(source), "mp3")
    assert "Converted song.wav to song.mp3" in result
    # ffmpeg's own stderr must never be echoed into the spoken reply.
    assert "codec build info" not in result


def test_ffmpeg_failure_does_not_leak_raw_stderr(tmp_path, monkeypatch):
    source = tmp_path / "song.wav"
    source.write_bytes(b"fake audio")
    monkeypatch.setattr(system_tools.shutil, "which", lambda name: r"C:\ffmpeg\bin\ffmpeg.exe")

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, 1, stdout="", stderr="Unknown encoder 'mp3'\n" * 20
        )

    monkeypatch.setattr(system_tools.subprocess, "run", fake_run)

    result = system_tools.convert_file(str(source), "mp3")
    assert "Couldn't convert" in result
    assert "Unknown encoder" not in result


def test_existing_destination_is_not_overwritten(tmp_path, monkeypatch):
    source = tmp_path / "song.wav"
    source.write_bytes(b"fake audio")
    (tmp_path / "song.mp3").write_bytes(b"already here")
    monkeypatch.setattr(system_tools.shutil, "which", lambda name: r"C:\ffmpeg\bin\ffmpeg.exe")

    result = system_tools.convert_file(str(source), "mp3")
    assert "already exists" in result
