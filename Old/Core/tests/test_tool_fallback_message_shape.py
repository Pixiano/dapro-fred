# Core/tests/test_tool_fallback_message_shape.py
#
# When local tool-calling fails, generate_with_tools falls back to
# generate(), which tries the CLOUD first. _apply_thinking rewrites
# tool_call arguments from a JSON string to a dict for local native
# templates — handing that adapted list to the cloud is a 400 on every
# provider. Confirmed live 2026-08-04.

import pytest

from llm.llm_client import LLMClient

MESSAGES = [
    {"role": "user", "content": "log it"},
    {
        "role": "assistant",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "append_to_file", "arguments": '{"text": "x"}'},
            }
        ],
    },
    {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
]


@pytest.fixture
def client(monkeypatch):
    c = LLMClient.__new__(LLMClient)
    c.temperature, c.top_p, c.max_tokens = 0.7, 1.0, 256
    c.tiers, c.default_tier, c._loaded = {"Standard": None}, "Standard", {}
    # No cloud, and local model loading always fails — the fallback path.
    monkeypatch.setattr(LLMClient, "_cloud_providers", lambda self: [])
    monkeypatch.setattr(
        LLMClient, "_get_model", lambda self, tier: (_ for _ in ()).throw(RuntimeError("no model"))
    )
    return c


def test_fallback_hands_generate_string_arguments(client, monkeypatch):
    """A dict here is what both providers answered 400 to."""
    seen = {}
    monkeypatch.setattr(
        LLMClient, "generate", lambda self, msgs, **kw: seen.setdefault("msgs", msgs) and "" or ""
    )

    client.generate_with_tools(MESSAGES, tools=[], tier="Standard")

    args = seen["msgs"][1]["tool_calls"][0]["function"]["arguments"]
    assert isinstance(args, str), f"cloud needs a JSON string, got {type(args).__name__}"


def test_the_callers_list_is_never_mutated(client, monkeypatch):
    monkeypatch.setattr(LLMClient, "generate", lambda self, msgs, **kw: "")
    client.generate_with_tools(MESSAGES, tools=[], tier="Standard")
    assert MESSAGES[1]["tool_calls"][0]["function"]["arguments"] == '{"text": "x"}'
