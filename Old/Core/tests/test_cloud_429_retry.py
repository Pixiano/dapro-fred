"""One check: a 429 from a cloud provider is retried exactly once,
after honouring Retry-After, before the caller sees the failure."""
import sys, types, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# llama_cpp isn't importable in a bare test env and isn't under test here.
sys.modules.setdefault("llama_cpp", types.SimpleNamespace(Llama=object))

import llm.llm_client as lc


class FakeResponse:
    def __init__(self, status, headers=None):
        self.status_code = status
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"{self.status_code} Client Error")

    def json(self):
        return {"ok": True}


def run(statuses, headers=None):
    calls, slept = [], []
    lc.requests = types.SimpleNamespace(
        post=lambda *a, **k: (calls.append(1), FakeResponse(statuses[len(calls) - 1], headers))[1]
    )
    lc.time = types.SimpleNamespace(sleep=slept.append)
    provider = {"name": "x", "base_url": "u", "api_key": "k", "model": "m"}
    try:
        result = lc._cloud_request(provider, [{"role": "user", "content": "hi"}])
    except Exception as e:
        result = e
    return calls, slept, result


# 429 then success: two POSTs, one sleep, caller gets the body.
calls, slept, result = run([429, 200], {"retry-after": "3"})
assert len(calls) == 2, calls
assert slept == [3.0], slept
assert result == {"ok": True}, result

# No Retry-After header: still sleeps, using the fixed window.
calls, slept, _ = run([429, 200])
assert slept == [12.0], slept

# Absurd Retry-After is capped so a turn can't hang for minutes.
calls, slept, _ = run([429, 200], {"retry-after": "600"})
assert slept == [15.0], slept

# Two 429s: gives up after one retry, raises so the caller falls to local.
calls, slept, result = run([429, 429])
assert len(calls) == 2, calls
assert isinstance(result, Exception), result

# Non-429 failure is not retried at all.
calls, slept, result = run([400, 200])
assert len(calls) == 1, calls
assert slept == [], slept
assert isinstance(result, Exception), result

print("ok")
