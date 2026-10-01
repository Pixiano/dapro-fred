"""Sandbox-only: teach FRED's tool-call text parser MiniCPM5's native format.

Applied by benchmarking/run.py (FRED_LLM_MODEL runs) — FRED's own Core code is
never edited. MiniCPM5-2B's chat_template.jinja (2026-09) emits
    <function name="NAME"><param name="k">v</param></function>
with no <tool_call> wrapper; values containing <, & or newlines are CDATA-wrapped.
Those four markers are control tokens, and llama-cpp-python's detokenizer drops
them (measured: '<function' -> b''), so FRED actually receives the stripped form
    name="NAME"> name="k">v name="k2">v2
Both forms are parsed. In the stripped form a registered tool name with nothing
after it starts a call; every other name="k">value is that call's argument.

    venv\\Scripts\\python.exe minicpm5_patch.py      # self-check (FRED's Core venv)
"""

import json
import re

CALL = re.compile(r'<function name="([\w_]+)">(.*?)</function>', re.DOTALL)
PARAM = re.compile(r'<param name="([\w_]+)">(?:<!\[CDATA\[(.*?)\]\]>|(.*?))</param>', re.DOTALL)
STRIPPED = re.compile(r'\s*name="([\w_]+)">')


def _unwrap(v):
    v = v.strip()
    return v[9:-3] if v.startswith("<![CDATA[") and v.endswith("]]>") else v


def stripped_calls(content, valid):
    parts = STRIPPED.split(content)  # [before, name1, value1, name2, value2, ...]
    calls = []
    for name, value in zip(parts[1::2], parts[2::2]):
        if name in valid and not value.strip():
            calls.append((name, {}))
        elif calls:
            calls[-1][1][name] = _unwrap(value)
    return calls


def apply(orchestrator_cls):
    parse, leaked = orchestrator_cls._parse_text_tool_calls, orchestrator_cls._looks_like_leaked_tool_syntax

    def parse_text_tool_calls(self, content):
        calls = parse(self, content)
        valid = set(self.tools.list_tools())
        for name, inner in CALL.findall(content):
            if name in valid:
                args = {k: self._coerce_text_arg(cdata or plain) for k, cdata, plain in PARAM.findall(inner)}
                calls.append({"id": f"minicpm_call_{len(calls)}",
                              "function": {"name": name, "arguments": json.dumps(args)}})
        if not calls:
            for name, args in stripped_calls(content, valid):
                args = {k: self._coerce_text_arg(v) for k, v in args.items()}
                calls.append({"id": f"minicpm_call_{len(calls)}",
                              "function": {"name": name, "arguments": json.dumps(args)}})
        return calls

    def looks_like_leaked_tool_syntax(self, text):
        return leaked(self, text) or bool(re.match(r'^\s*(<function )?name="[\w_]+">', text or ""))

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
    fred.tools = types.SimpleNamespace(tools={"list_tasks": {"parameters": {"required": []}}},
                                       list_tools=lambda: ["list_tasks"])
    calls = fred._parse_text_tool_calls('Sure.\n<function name="list_tasks"><param name="day">2026-09-30</param>'
                                        '<param name="note"><![CDATA[a < b & c\nd]]></param></function>')
    assert [c["function"]["name"] for c in calls] == ["list_tasks"]
    assert json.loads(calls[0]["function"]["arguments"]) == {"day": "2026-09-30", "note": "a < b & c\nd"}
    assert fred._parse_text_tool_calls('<function name="not_a_tool"></function>') == []
    assert fred._looks_like_leaked_tool_syntax('<function name="nope"></function>')
    assert not fred._looks_like_leaked_tool_syntax("You have six tasks today, sir.")
    # what llama-cpp-python actually hands FRED (control tokens dropped)
    calls = fred._parse_text_tool_calls('name="list_tasks"> name="day">2026-09-30 name="note"><![CDATA[a\nb]]>')
    assert [c["function"]["name"] for c in calls] == ["list_tasks"]
    assert json.loads(calls[0]["function"]["arguments"]) == {"day": "2026-09-30", "note": "a\nb"}
    assert fred._looks_like_leaked_tool_syntax('name="list_tasks"> name="day">today')
    assert fred._parse_text_tool_calls('I named="x"> it') == []
    print("OK")
