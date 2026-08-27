"""Checks for the 2026-08-20 llama-server.exe vision path
(llm/vision_server.py), which replaced the in-process llama-cpp-python
Vision handler after confirming a real binding bug on Qwen3.5-arch
vision models (wrong output, not a crash — same find_slot warning as
the correct llama.cpp CLI output, so that warning alone isn't the
signal). No real subprocess or network here — urllib/subprocess are
monkeypatched, matching this repo's bare-assert test style (see
test_llm_client_silent_turn_fixes.py)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import llm.vision_server as vs


class _FakeResponse:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# describe_image() must send chat_template_kwargs.enable_thinking=False —
# confirmed live 2026-08-20: without it, the model's <think> block ate
# the whole max_tokens budget and content came back empty.
_real_ensure_running = vs.ensure_running
_real_urlopen = vs.urllib.request.urlopen
captured_request = {}
vs.ensure_running = lambda *a, **kw: True
vs.urllib.request.urlopen = lambda req, timeout=None: (
    captured_request.update(req=req)
    or _FakeResponse({"choices": [{"message": {"content": "a cat on a windowsill"}}]})
)
try:
    result = vs.describe_image("data:image/png;base64,AAAA", "What's here?", max_tokens=150)
finally:
    vs.ensure_running = _real_ensure_running
    vs.urllib.request.urlopen = _real_urlopen

assert result == "a cat on a windowsill", result

sent = json.loads(captured_request["req"].data)
assert sent["chat_template_kwargs"] == {"enable_thinking": False}, sent
assert sent["messages"][0]["content"][0] == {
    "type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"},
}
assert sent["messages"][0]["content"][1] == {"type": "text", "text": "What's here?"}
assert sent["max_tokens"] == 150


# system_prompt, when given, becomes a leading system message; the image+
# text user message still follows it, unshifted from the no-system case.
vs.ensure_running = lambda *a, **kw: True
vs.urllib.request.urlopen = lambda req, timeout=None: (
    captured_request.update(req=req)
    or _FakeResponse({"choices": [{"message": {"content": "ok"}}]})
)
try:
    vs.describe_image("data:image/png;base64,AAAA", "What's here?", system_prompt="Be terse.")
finally:
    vs.ensure_running = _real_ensure_running
    vs.urllib.request.urlopen = _real_urlopen

sent = json.loads(captured_request["req"].data)
assert sent["messages"][0] == {"role": "system", "content": "Be terse."}, sent
assert sent["messages"][1]["content"][1] == {"type": "text", "text": "What's here?"}


# ensure_running() must not spawn anything when already healthy.
_real_is_healthy = vs._is_healthy
_real_popen = vs.subprocess.Popen
vs._is_healthy = lambda *a, **kw: True
vs.subprocess.Popen = lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should not have spawned"))
try:
    assert vs.ensure_running() is True
finally:
    vs._is_healthy = _real_is_healthy
    vs.subprocess.Popen = _real_popen


# describe_image() raises if the server never comes up.
vs.ensure_running = lambda *a, **kw: False
try:
    threw = False
    try:
        vs.describe_image("data:image/png;base64,AAAA", "hi")
    except RuntimeError:
        threw = True
    assert threw, "describe_image() should raise RuntimeError when ensure_running() fails"
finally:
    vs.ensure_running = _real_ensure_running

print("ok")
