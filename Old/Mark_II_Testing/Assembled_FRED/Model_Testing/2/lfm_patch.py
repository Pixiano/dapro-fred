"""Sandbox-only: teach FRED's tool-call text parser LFM2.5's native format.

Applied by benchmarking/run.py (FRED_LLM_PATCH) — FRED's Core code is never edited.
LFM2.5-2.6B's chat template (LiquidAI/LFM2.5-2.6B @ 654f9463) emits Pythonic calls:
    <|tool_call_start|>[get_weather(location='Pune'), set_volume(level=40)]<|tool_call_end|>
Both markers are NON-special tokens (tokenizer.json ids 124905/124906), so they survive
llama-cpp-python's detokenize. Also: the template wraps assistant turns in HF's
{% generation %} ... {% endgeneration %} tags, which plain Jinja2 (llama-cpp-python's
Jinja2ChatFormatter) rejects — they only mark training spans, so they are dropped.

    Core\\venv\\Scripts\\python.exe lfm_patch.py      # self-check
"""

import ast
import json
import os
import re

CALLS = re.compile(r"<\|tool_call_start\|>(.*?)<\|tool_call_end\|>", re.DOTALL)
# Measured 2026-10-01: llama-cpp-python still drops both markers from the text (despite
# special=false in tokenizer.json), so FRED actually receives the bare list.
BARE = re.compile(r"\[\s*[A-Za-z_]\w*\(.*\)\s*\]", re.DOTALL)
GEN_TAGS = re.compile(r"\{%-?\s*(end)?generation\s*-?%\}")
# The template has no thinking switch: its generation prompt always opens "<think>".
# LFM_NO_THINK=1 pre-closes it ("<think></think>") so the reply starts at the answer.
OPEN_THINK = r'"<|im_start|>assistant\n<think>"'


def pythonic_calls(text):
    """'[f(a='x'), g()]' -> [('f', {'a': 'x'}), ('g', {})]; [] if it isn't a list of calls."""
    try:
        tree = ast.parse(text.strip(), mode="eval").body
    except SyntaxError:
        return []
    calls = []
    for node in tree.elts if isinstance(tree, ast.List) else [tree]:
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            return []
        try:
            calls.append((node.func.id, {k.arg: ast.literal_eval(k.value) for k in node.keywords}))
        except ValueError:  # a non-literal argument: not a call we can run
            return []
    return calls


def apply(orchestrator_cls):
    from llama_cpp import llama_chat_format as lcf
    init = lcf.Jinja2ChatFormatter.__init__

    def jinja_init(self, template, *a, **k):
        template = GEN_TAGS.sub("", template)
        # Every formatter passes through here (FRED's embedder too), so only LFM's template is edited.
        if os.environ.get("LFM_NO_THINK") and OPEN_THINK in template:
            template = template.replace(OPEN_THINK, OPEN_THINK[:-1] + '</think>"')
            print("[lfm_patch] thinking OFF: generation prompt pre-closes <think>", flush=True)
        init(self, template, *a, **k)

    lcf.Jinja2ChatFormatter.__init__ = jinja_init

    parse, leaked = orchestrator_cls._parse_text_tool_calls, orchestrator_cls._looks_like_leaked_tool_syntax

    def parse_text_tool_calls(self, content):
        calls = parse(self, content)
        valid = set(self.tools.list_tools())
        blocks = CALLS.findall(content or "") or BARE.findall(content or "")
        for block in blocks:
            found = pythonic_calls(block)
            if found and all(name in valid for name, _ in found):
                calls += [{"id": f"lfm_call_{len(calls) + i}",
                           "function": {"name": name, "arguments": json.dumps(args)}}
                          for i, (name, args) in enumerate(found)]
        return calls

    def looks_like_leaked_tool_syntax(self, text):
        return (leaked(self, text) or "<|tool_call_start|>" in (text or "")
                or bool(re.match(r"\s*\[\s*[A-Za-z_]\w*\(", text or "")))

    orchestrator_cls._parse_text_tool_calls = parse_text_tool_calls
    orchestrator_cls._looks_like_leaked_tool_syntax = looks_like_leaked_tool_syntax


if __name__ == "__main__":
    import sys
    import types
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "Core"))
    from orchestrator.orchestrator import FREDOrchestrator
    apply(FREDOrchestrator)
    fred = FREDOrchestrator.__new__(FREDOrchestrator)
    fred.tools = types.SimpleNamespace(tools={}, list_tools=lambda: ["get_weather", "set_volume", "list_tasks"])
    calls = fred._parse_text_tool_calls(
        "On it.<|tool_call_start|>[get_weather(location='Pune'), set_volume(level=40)]<|tool_call_end|>")
    assert [(c["function"]["name"], json.loads(c["function"]["arguments"])) for c in calls] == [
        ("get_weather", {"location": "Pune"}), ("set_volume", {"level": 40})]
    assert fred._parse_text_tool_calls("<|tool_call_start|>[list_tasks()]<|tool_call_end|>")[0]["function"]["name"] == "list_tasks"
    assert fred._parse_text_tool_calls("<|tool_call_start|>[not_a_tool(x=1)]<|tool_call_end|>") == []
    assert fred._parse_text_tool_calls("<|tool_call_start|>[get_weather(location=city)]<|tool_call_end|>") == []
    assert fred._parse_text_tool_calls("It's 30 degrees in Pune, sir.") == []
    # what llama-cpp-python actually hands FRED (markers dropped)
    calls = fred._parse_text_tool_calls("[set_volume(level=40), get_weather(location='Pune')]")
    assert [c["function"]["name"] for c in calls] == ["set_volume", "get_weather"]
    assert fred._parse_text_tool_calls("[list_tasks()]")[0]["function"]["name"] == "list_tasks"
    assert fred._parse_text_tool_calls("Options: [print(x)] or [len(y)]") == []
    assert fred._looks_like_leaked_tool_syntax("[get_weather(location='Pune')]")
    assert fred._looks_like_leaked_tool_syntax("<|tool_call_start|>[get_weather(")
    assert not fred._looks_like_leaked_tool_syntax("Done, sir.")
    from llama_cpp.llama_chat_format import Jinja2ChatFormatter
    Jinja2ChatFormatter(template="{%- generation -%}hi{%- endgeneration -%}", eos_token="", bos_token="")
    gen = "{%- if add_generation_prompt -%}{{- " + OPEN_THINK + " -}}{%- endif -%}"
    os.environ["LFM_NO_THINK"] = "1"
    f = Jinja2ChatFormatter(template=gen, eos_token="", bos_token="")
    assert f(messages=[]).prompt.endswith("<think></think>"), f(messages=[]).prompt
    print("OK")
