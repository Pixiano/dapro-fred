"""Regression for the 2026-08-22 thinking-leak bug: session_summary.
summarise_today's prompt is a bulk data dump (up to 40 asks, sometimes an
existing daily note folded in) that's routinely over
THINKING_LENGTH_THRESHOLD, so llm_client._native_call's length-based
enable_thinking guess turned reasoning ON for a task that never needed
it — and the model's reasoning came out as plain "Thinking Process:"
prose with no <think>/<|channel> tag, so _strip_thinking didn't catch it
and FRED spoke a full chain-of-thought dump verbatim.

Same bare-script style as test_llm_client_silent_turn_fixes.py — llama_cpp
isn't importable in a bare test env and isn't under test here."""
import sys, time, types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.setdefault("llama_cpp", types.SimpleNamespace(Llama=object))

import llm.llm_client as lc
lc.time = time


def _client():
    return lc.LLMClient(report_status=False)


# 1. force_no_thinking=True must reach the jinja render as
# enable_thinking=False even when the last user message is long enough
# that the length heuristic alone would turn thinking on.
seen_kwargs = {}
client = _client()
long_user_text = "x" * (lc.THINKING_LENGTH_THRESHOLD + 500)
client._get_model = lambda tier: types.SimpleNamespace(
    chat_handler=lambda **kw: (seen_kwargs.update(kw), {
        "choices": [{"message": {"content": "Three bullet points."}}]
    })[1]
)
reply = client.generate(
    [{"role": "user", "content": long_user_text}],
    local_only=True, force_no_thinking=True,
)
assert seen_kwargs["enable_thinking"] is False, seen_kwargs
assert reply == "Three bullet points.", reply

# Sanity check the other direction: without force_no_thinking, the same
# long message DOES trip the heuristic — proves the fix is doing
# something, not just always-off.
seen_kwargs2 = {}
client2 = _client()
client2._get_model = lambda tier: types.SimpleNamespace(
    chat_handler=lambda **kw: (seen_kwargs2.update(kw), {
        "choices": [{"message": {"content": "Three bullet points."}}]
    })[1]
)
client2.generate([{"role": "user", "content": long_user_text}], local_only=True)
assert seen_kwargs2["enable_thinking"] is True, seen_kwargs2

# 2. Belt-and-suspenders: even if thinking leaks through untagged, a
# "Thinking Process:" prefixed response must never reach the caller —
# same "unfinished reasoning, no real answer" handling as an unclosed
# <think> block, which generate() already turns into an honest spoken
# fallback rather than the raw dump.
leaked = (
    "Thinking Process:\n\n1. **Analyze the Request:**\n"
    " * Goal: summarise what the user asked today."
)
assert lc.LLMClient._strip_thinking(leaked) == ""

client3 = _client()
client3._get_model = lambda tier: types.SimpleNamespace(
    chat_handler=lambda **kw: {"choices": [{"message": {"content": leaked}}]}
)
reply3 = client3.generate([{"role": "user", "content": "summarise today"}], local_only=True)
assert "Thinking Process" not in reply3, reply3
assert "ran out of room" in reply3, reply3

print("ok")
