# Core/tests/test_think_cap.py
#
# Thinking-token cap (settings.THINKING_TOKEN_CAP, default off): synthetic
# self-check of the logits processor, no model loaded.

import numpy as np

from llm.think_cap import ThinkCap

OPEN, CLOSE, V = 5, 6, 10


def _scores():
    return np.zeros(V, dtype=np.float32)


def _forced(out):
    return int(np.argmax(out)) == CLOSE and np.isinf(out).sum() == V - 1


def test_forces_close_after_the_cap_when_the_prompt_opens_the_thought():
    cap = ThinkCap(3, OPEN, CLOSE)
    ids = [1, 2, OPEN]                       # prompt ends with an open <think>
    assert not _forced(cap(np.array(ids), _scores()))
    for _ in range(2):                       # two thought tokens: still free
        ids.append(1)
        assert not _forced(cap(np.array(ids), _scores()))
    ids.append(1)                            # third generated token already made -> force
    assert _forced(cap(np.array(ids), _scores()))
    ids.append(CLOSE)                        # after </think> the answer is free again
    assert not _forced(cap(np.array(ids), _scores()))
    for _ in range(5):
        ids.append(1)
        assert not _forced(cap(np.array(ids), _scores()))


def test_a_thought_the_model_opens_itself_is_capped_too():
    cap = ThinkCap(2, OPEN, CLOSE)
    ids = [1, 2]
    cap(np.array(ids), _scores())
    ids.append(OPEN)
    assert not _forced(cap(np.array(ids), _scores()))
    ids.append(1)
    assert _forced(cap(np.array(ids), _scores()))


def test_no_thinking_no_forcing():
    cap = ThinkCap(1, OPEN, CLOSE)
    ids = [1, OPEN, CLOSE]                   # prompt already closed it (thinking off)
    for _ in range(4):
        assert not _forced(cap(np.array(ids), _scores()))
        ids.append(1)
