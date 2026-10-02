# Core/tests/test_tool_call_empty_reply.py
#
# Real transcript: session_2026-08-12.jsonl, 14:13:19-14:14:38 — "Find
# turfs to play football near Malaad West." called find_file_smart twice
# (both failed, no such file), then FRED's spoken reply logged
# `"text": "", "spoken": true` — dead silence.
#
# generate()'s own docstring already names this exact failure mode and
# its fix (session_2026-08-01_18-41-50.jsonl: three turns logged
# `"text": ""` because _strip_thinking() correctly returns "" when a
# reasoning block opens and never closes — the model hit max_tokens
# mid-thought — but nothing substituted a real reply, so it reached TTS
# as silence). generate_with_tools() strips thinking the same way but
# never got the same honest-fallback protection, so the identical bug
# was reachable again on the tool-calling path specifically — plausible
# here since a model stuck with two failed tool results and nothing left
# to try can easily blow its token budget explaining the dead end.
#
# This pins LLMClient._strip_thinking_for_tools(), the helper that now
# backs both call sites in generate_with_tools().

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.setdefault("llama_cpp", types.SimpleNamespace(Llama=object))

from llm.llm_client import LLMClient


def test_unclosed_think_block_with_no_tool_call_gets_an_honest_reply():
    # Model ran out of max_tokens mid-reasoning and called no tool —
    # _strip_thinking alone would return "", which used to reach TTS
    # as total silence.
    result = LLMClient._strip_thinking_for_tools(
        "<think>Neither find_file_smart call found anything, I should",
        has_tool_calls=False,
    )
    assert result, "must not be empty — silence is worse than admitting it"
    assert "ran out of room" in result


def test_unclosed_think_block_with_a_tool_call_is_left_empty():
    # A tool call carries the turn; the orchestrator never speaks
    # `content` when tool_calls is present, so inventing a line here
    # would be pure noise, not a fix.
    result = LLMClient._strip_thinking_for_tools(
        "<think>let me check the file", has_tool_calls=True,
    )
    assert result == ""


def test_a_closed_think_block_is_just_stripped_normally():
    result = LLMClient._strip_thinking_for_tools(
        "<think>reasoning here</think>Here are the results, sir.",
        has_tool_calls=False,
    )
    assert result == "Here are the results, sir."
