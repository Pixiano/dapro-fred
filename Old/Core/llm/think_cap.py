# Core/llm/think_cap.py
#
# Thinking-token cap: "generate up to N thinking tokens, then force </think>
# and continue". A llama.cpp logits processor, so it runs inside the one
# generation call (no second pass, nothing re-rendered): once N tokens have
# been generated while a thought is open, every logit but </think> is
# masked, the model emits it, and carries on to the answer.
#
# Off unless settings.THINKING_TOKEN_CAP > 0. Only built for templates whose
# <think> and </think> are single tokens; anything else returns None and the
# call runs uncapped.

import numpy as np


class ThinkCap:
    def __init__(self, cap: int, open_id: int, close_id: int):
        self.cap, self.open_id, self.close_id = cap, open_id, close_id
        self.start = None      # length of the prompt, learned on the first call
        self.thinking = False  # is a thought open at the end of what we have seen

    def __call__(self, input_ids, scores):
        ids = np.asarray(input_ids)
        if self.start is None:
            self.start = len(ids)
            opens, closes = np.where(ids == self.open_id)[0], np.where(ids == self.close_id)[0]
            self.thinking = bool(len(opens)) and (not len(closes) or opens[-1] > closes[-1])
        elif len(ids) > self.start:
            last = int(ids[-1])
            if last == self.open_id:
                self.thinking = True
            elif last == self.close_id:
                self.thinking = False
        if self.thinking and len(ids) - self.start >= self.cap:
            scores = np.full_like(scores, -np.inf)
            scores[self.close_id] = 0.0
        return scores


def build(model, cap: int):
    """A ThinkCap for this model, or None if the cap is off or the model's
    think markers are not single tokens (the self-check)."""
    if cap <= 0:
        return None
    try:
        o = model.tokenize(b"<think>", add_bos=False, special=True)
        c = model.tokenize(b"</think>", add_bos=False, special=True)
    except Exception:
        return None
    if len(o) != 1 or len(c) != 1:
        print("[LLM] thinking cap disabled: <think>/</think> are not single tokens in this model")
        return None
    return ThinkCap(cap, o[0], c[0])
