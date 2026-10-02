# Core/tests/test_describe_self.py
#
# New tool (2026-08-12): "what tools do you have" / "what model are
# you running" answered from the live registry + config/settings.py,
# not a doc that can go stale. Pins that it reads DEFAULT_TIER/
# MODEL_TIERS live (a monkeypatched tier shows up in the reply) and
# that it stays a short spoken summary rather than dumping every tool
# name — the whole point of "spoken-friendly, not a raw dump".

from pathlib import Path

import config.settings as settings
from tools import system_tools


def test_reports_tool_count_and_active_tier(monkeypatch):
    monkeypatch.setattr(settings, "DEFAULT_TIER", "Standard")
    monkeypatch.setattr(settings, "MODEL_TIERS", {"Standard": Path("models/qwen3-8b.gguf")})

    names = [f"tool_{i}" for i in range(12)]
    result = system_tools.describe_self(names)

    assert "12 tools" in result
    assert "Standard tier" in result
    assert "qwen3-8b" in result


def test_does_not_dump_every_tool_name(monkeypatch):
    monkeypatch.setattr(settings, "DEFAULT_TIER", "Standard")
    monkeypatch.setattr(settings, "MODEL_TIERS", {"Standard": Path("models/qwen3-8b.gguf")})

    names = [f"tool_{i}" for i in range(40)]
    result = system_tools.describe_self(names)

    # A spoken summary, not all 40 names read aloud.
    assert sum(name in result for name in names) <= 6
