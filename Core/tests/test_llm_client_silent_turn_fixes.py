"""Two checks for the 2026-08-13 silent-turn/latency-visibility fixes.

llama_cpp isn't importable in a bare test env and isn't under test
here — same stub as test_cloud_429_retry.py."""
import sys, time, types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.setdefault("llama_cpp", types.SimpleNamespace(Llama=object))

import llm.llm_client as lc
from utils import event_log

# test_cloud_429_retry.py replaces lc.time with a bare sleep-only stub at
# collection time (no pytest fixture, so nothing reverts it) — restore
# the real module so _get_model's time.monotonic() call below works
# regardless of collection order.
lc.time = time


def _client():
    return lc.LLMClient(report_status=False)


# generate_stream: a thinking block opened but never closed (ran out of
# max_tokens mid-thought) must fall back to a spoken message, not silent
# nothing — generate() already guards this exact shape; generate_stream's
# leftover-buffer path didn't. Confirmed live: a plain chat turn logged
# `"text": ""` and FRED said nothing at all.
client = _client()
# "Standard" is no longer in TIER_TEMPLATE_KWARGS (2026-08-19, moved to
# Qwen3.5-4B which doesn't need the enable_thinking kwarg) — back to the
# plain create_chat_completion() path, matching the real call shape.
client._get_model = lambda tier: types.SimpleNamespace(
    create_chat_completion=lambda **kw: iter([
        {"choices": [{"delta": {"content": "<|channel>thought never closes"}}]},
    ])
)
pieces = list(client.generate_stream([{"role": "user", "content": "hi"}], local_only=True))
reply = "".join(pieces).strip()
assert reply, "generate_stream yielded nothing for an unclosed thinking block"
assert "ran out of room" in reply, reply

# generate_stream: ordinary short content that never got long enough to
# classify as thinking-or-not must still come through unchanged, not the
# fallback message — the fix must not swallow real short replies.
client2 = _client()
client2._get_model = lambda tier: types.SimpleNamespace(
    create_chat_completion=lambda **kw: iter([
        {"choices": [{"delta": {"content": "Hi."}}]},
    ])
)
pieces2 = list(client2.generate_stream([{"role": "user", "content": "hi"}], local_only=True))
assert "".join(pieces2).strip() == "Hi.", pieces2


# _get_model: a cold load must now be visible in the event log, not just
# a print() invisible in the headless process — this is the actual
# latency source a 2026-08-13 incident had no way to diagnose.
logged = []
_real_event_log_log = event_log.log
_real_llama = lc.Llama
event_log.log = lambda kind, **fields: logged.append((kind, fields))
try:
    client3 = _client()

    class _FakeLlama:
        def __init__(self, **kw):
            pass

    lc.Llama = _FakeLlama
    tier_name = client3.default_tier
    client3._get_model(tier_name)
finally:
    # Module-global patches, no pytest fixture here (matches this
    # file's bare-script style) — must restore both, or every test
    # collected after this one silently stops writing real event-log
    # entries and/or tries to construct a real Llama. Confirmed the
    # event_log half the hard way: broke test_event_log_merge.py
    # before this fix.
    event_log.log = _real_event_log_log
    lc.Llama = _real_llama

load_events = [f for k, f in logged if k == "llm_model_load"]
assert len(load_events) == 1, logged
assert load_events[0]["tier"] == tier_name
assert isinstance(load_events[0]["seconds"], float)

print("ok")
