"""Standalone (no FRED pipeline): a contender's thinking modes on the same scored prompts.

    capN  - hard cap: at most N thinking tokens, then </think> is forced
    on    - template default, the reply starts inside <think>
    off   - <think></think> pre-closed (what LFM_NO_THINK does in the benchmark)
    brief - thinking on, plus a system instruction to keep the reasoning short

    Core\\venv\\Scripts\\python.exe think_modes.py lfm|mercury [modes...]
"""

import re
import sys
import time
from pathlib import Path

from llama_cpp import Llama

HERE = Path(__file__).resolve().parent
SYSTEM = "You are FRED, a voice assistant. Answer in one or two short spoken sentences."
BRIEF = " Think briefly - at most two short sentences of reasoning - then answer directly."
# (prompt, regex the spoken answer must match): the first 8-question set (2026-10-01)
# plus four added when its bat-and-ball and weekday questions proved coin flips at n=1.
PROMPTS = [
    ("a bat and a ball cost 1.10 in total. the bat costs 1.00 more than the ball. how much does the ball cost?",
     r"0?\.05\b|\b5 cents|five cents"),
    ("if today were Wednesday, what day would it be in 10 days?", r"saturday"),
    ("what's 15 percent of 2400", r"\b360\b"),
    ("bhai 2 aur 2 kitne hote hain", r"\b(4|four|char|chaar)\b"),
    ("aaj mood off hai yaar", r"sorry|what's wrong|kya hua|what happened|talk|here|baat"),
    ("who won the 2031 cricket world cup?", r"hasn't|has not|not happened|future|don't know|can't know|yet|no information|don't have"),
    ("summarise in one sentence: The meeting was moved from Tuesday to Thursday because the client was travelling. "
     "Everyone should bring the Q3 numbers. Lunch will be provided.", r"thursday"),
    ("good morning", r"morning"),
    ("what's 12 times 13", r"\b156\b"),
    ("my meeting starts at 3 pm and lasts 90 minutes. when does it end?", r"4[:.]30|half past four"),
    ("which is bigger, 0.8 or 0.75?", r"0?\.8\b(?!\d)"),
    ("what's the capital of Japan", r"tokyo"),
]


# name: (gguf, generation-prompt opener, what "off" appends) - both from the GGUF's own chat template
MODELS = {
    "lfm": (HERE / "models" / "LFM2.5-2.6B-Q8_0.gguf", "<think>", "</think>"),
    "mercury": (HERE.parent / "3" / "models" / "AgentMercury-Qwen3.5-4B.Q6_K.gguf", "<think>\n", "\n</think>\n\n"),
}
GGUF, OPEN, CLOSE = MODELS[sys.argv[1]]


def prompt(user, mode):
    system = SYSTEM + (BRIEF if mode == "brief" else "")
    p = f"<|im_start|>system\n{system}<|im_end|>\n<|im_start|>user\n{user}<|im_end|>\n<|im_start|>assistant\n{OPEN}"
    return p + (CLOSE if mode == "off" else "")


def capped(user, cap):
    """Hard cap: think for at most `cap` tokens, then force </think> and generate the answer."""
    p = prompt(user, "on")
    think = m.create_completion(p, max_tokens=cap, temperature=0.1, stop=["</think>"])["choices"][0]["text"]
    answer = m.create_completion(p + think + CLOSE, max_tokens=512, temperature=0.1,
                                 stop=["<|im_end|>"])["choices"][0]["text"]
    return think + "</think>" + answer


m = Llama(str(GGUF), n_gpu_layers=-1, n_ctx=4096, verbose=False)
MODES = sys.argv[2:] or ["on", "off", "brief", "cap32", "cap64", "cap96"]
rows = []
for mode in MODES:
    for user, want in PROMPTS:
        t = time.perf_counter()
        if mode.startswith("cap"):
            text = capped(user, int(mode[3:]))
        else:
            text = m.create_completion(prompt(user, mode), max_tokens=3000, temperature=0.1,
                                       stop=["<|im_end|>"])["choices"][0]["text"]
        secs = time.perf_counter() - t
        if mode == "off":
            think, answer = "", text
        elif "</think>" in text:
            think, _, answer = text.rpartition("</think>")
        else:  # ran out of tokens while still thinking: no answer, so it can't pass
            think, answer = text, ""
        ok = bool(re.search(want, answer, re.I))
        rows.append((mode, user, len(m.tokenize(think.encode(), add_bos=False)), secs, answer.strip(), ok))
        print(f"[{mode:5}] {'PASS' if ok else 'FAIL'} {secs:5.1f}s think={rows[-1][2]:4} tok | {user[:40]!r} -> {answer.strip()[:110]!r}", flush=True)

for mode in MODES:
    r = [x for x in rows if x[0] == mode]
    print(f"{mode:5}: {sum(x[5] for x in r)}/{len(r)} correct, mean {sum(x[3] for x in r) / len(r):.1f}s, max {max(x[3] for x in r):.1f}s, "
          f"mean think {sum(x[2] for x in r) / len(r):.0f} tok")
