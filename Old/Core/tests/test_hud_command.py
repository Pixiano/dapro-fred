# The HUD console's text box (hud/index.html's #cmd) was reported "not
# giving a response back" — it was, at the time, wired to do nothing but
# local-echo (see git history: a `TODO(wiring)` comment). Wiring it up
# added a new file-based request/reply protocol between hud/server.py's
# submit_command() and Core/ui/pill_app.py's _hud_command_loop(), talking
# through ~/voice-line/command.json + command_reply.json. This checks
# submit_command()'s half: write a command, have a fake FRED answer it,
# confirm the right reply comes back matched by id — including the case
# where a stale reply for an older request is sitting there first.
#
# hud/server.py lives outside Core/, so conftest.py's sys.path insert
# (Core/ only) doesn't reach it — added here instead of touching that
# shared config for one test file.

import json
import sys
import threading
import time
from pathlib import Path

HUD_DIR = Path(__file__).resolve().parents[2] / "hud"
if str(HUD_DIR) not in sys.path:
    sys.path.insert(0, str(HUD_DIR))

import server as hud_server  # noqa: E402


def _respond_once(bus_dir, reply_text, delay=0.05):
    """Stands in for pill_app._hud_command_loop: waits for command.json,
    then writes the matching command_reply.json."""

    def run():
        cmd_path = bus_dir / "command.json"
        deadline = time.time() + 5
        while time.time() < deadline:
            if cmd_path.is_file():
                break
            time.sleep(0.02)
        data = json.loads(cmd_path.read_text(encoding="utf-8"))
        time.sleep(delay)
        (bus_dir / "command_reply.json").write_text(
            json.dumps({"id": data["id"], "text": reply_text}), encoding="utf-8"
        )

    threading.Thread(target=run, daemon=True).start()


def test_submit_command_returns_the_matching_reply(tmp_path, monkeypatch):
    monkeypatch.setattr(hud_server, "BUS_DIR", tmp_path)
    monkeypatch.setattr(hud_server, "COMMAND_TIMEOUT", 3.0)
    monkeypatch.setattr(hud_server, "COMMAND_POLL", 0.02)

    _respond_once(tmp_path, "Reminder set for 6pm.")

    assert hud_server.submit_command("set a reminder") == "Reminder set for 6pm."


def test_submit_command_ignores_a_stale_reply_for_a_different_id(tmp_path, monkeypatch):
    monkeypatch.setattr(hud_server, "BUS_DIR", tmp_path)
    monkeypatch.setattr(hud_server, "COMMAND_TIMEOUT", 3.0)
    monkeypatch.setattr(hud_server, "COMMAND_POLL", 0.02)

    # A leftover reply from a previous, unrelated request.
    (tmp_path / "command_reply.json").write_text(
        json.dumps({"id": "some-old-id", "text": "stale answer"}), encoding="utf-8"
    )
    _respond_once(tmp_path, "fresh answer", delay=0.1)

    assert hud_server.submit_command("hello") == "fresh answer"


def test_submit_command_times_out_when_nobody_answers(tmp_path, monkeypatch):
    monkeypatch.setattr(hud_server, "BUS_DIR", tmp_path)
    monkeypatch.setattr(hud_server, "COMMAND_TIMEOUT", 0.2)
    monkeypatch.setattr(hud_server, "COMMAND_POLL", 0.02)

    assert hud_server.submit_command("anyone there?") == "FRED didn't respond in time."
