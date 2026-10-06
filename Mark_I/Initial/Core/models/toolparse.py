"""Tool-call parsing: one ToolCall shape out of whatever the model emitted, plus the cleaners that keep tool syntax and reasoning out of speech.

Sockets
  in : parse(message, valid=None) with message = an OpenAI-style assistant message dict (tool_calls and/or content) or plain text;
       `valid` = names of tools that exist: text-format calls naming anything else are dropped (hallucinated names never run)
  out: list[ToolCall(name, args, id, error)]; error is set (args == {}) when the arguments were not valid JSON, so the engine answers bad_args
       instead of crashing (the old int/strip and "malformed arguments" failures)
       strip_thinking(text), strip_debris(text), looks_leaked(text): cleaners/guards for text that may be spoken
Families: native (server-parsed tool_calls) and text: hermes_json, qwen_xml, gemma, bare_json. Each text family is one function in TEXT_FAMILIES;
add one there when the chosen brain needs it (pythonic, MiniCPM XML wait for the brain decision, plan open check 2).
Argument values from text families stay strings; tools/coerce.py turns them into the schema's types.
Self-check: python models/toolparse.py
"""
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@dataclass
class ToolCall:
    name: str
    args: dict = field(default_factory=dict)
    id: str = ""
    error: str | None = None


def _json_args(raw) -> tuple[dict, str | None]:
    if isinstance(raw, dict):
        return raw, None
    if raw is None or not str(raw).strip():
        return {}, None
    try:
        v = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}, f"arguments are not valid JSON: {str(raw)[:80]!r}"
    return (v, None) if isinstance(v, dict) else ({}, "arguments are not a JSON object")


def _native(message: dict):
    for i, tc in enumerate(message.get("tool_calls") or []):
        fn = tc.get("function") or {}
        args, err = _json_args(fn.get("arguments"))
        yield ToolCall(fn.get("name", ""), args, tc.get("id") or f"call_{i}", err)


def _hermes_json(text: str):
    for blk in re.findall(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", text, re.DOTALL):
        try:
            o = json.loads(blk)
        except json.JSONDecodeError:
            continue
        if isinstance(o, dict) and o.get("name"):
            args, err = _json_args(o.get("arguments"))
            yield ToolCall(o["name"], args, error=err)


def _qwen_xml(text: str):
    for name, inner in re.findall(r"<function=([\w.-]+)>(.*?)</function>", text, re.DOTALL):
        params = re.findall(r"<parameter=([\w.-]+)>(.*?)</parameter>", inner, re.DOTALL)
        if params:
            yield ToolCall(name, {k: v.strip() for k, v in params})
        else:
            args, err = _json_args(inner.strip())
            yield ToolCall(name, args, error=err)


def _gemma(text: str):
    for name, raw in re.findall(r"<\|tool_call>\s*call:\s*([\w.-]+)\s*(\{.*?\})?\s*<tool_call\|>", text, re.DOTALL):
        j = re.sub(r"([{,]\s*)([A-Za-z_]\w*)\s*:", r'\1"\2":', (raw or "{}").replace('<|"|>', '"'))
        args, err = _json_args(j)
        yield ToolCall(name, args, error=err)


def _bare_json(text: str):
    dec = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            o, _ = dec.raw_decode(text[i:])
        except json.JSONDecodeError:
            continue
        if isinstance(o, dict) and isinstance(o.get("name"), str) and ("arguments" in o or "parameters" in o):
            args, err = _json_args(o.get("arguments", o.get("parameters")))
            yield ToolCall(o["name"], args, error=err)


TEXT_FAMILIES = (_hermes_json, _qwen_xml, _gemma, _bare_json)


def parse(message, valid=None) -> list[ToolCall]:
    """Native tool_calls win; otherwise the first text family that finds a call. Duplicates of (name, args) in one message collapse to one."""
    msg = message if isinstance(message, dict) else {"content": message}
    calls = list(_native(msg))
    if not calls:
        text = strip_thinking(msg.get("content") or "")
        for fam in TEXT_FAMILIES:
            calls = [c for c in fam(text) if valid is None or c.name in valid]
            if calls:
                break
    seen, out = set(), []
    for i, c in enumerate(calls):
        key = (c.name, json.dumps(c.args, sort_keys=True))
        if key not in seen:
            seen.add(key)
            c.id = c.id or f"call_{i}"
            out.append(c)
    return out


_THINK = re.compile(r"<think>.*?</think>|<\|channel>thought.*?<channel\|>", re.DOTALL)
_DEBRIS = re.compile(r"<\|?tool_call\|?>|</?tool_call>|<tool_call\|>|\{[^{}]*\}", re.DOTALL)
_LEAK = re.compile(r"functions\.\w+\s*[:(]|</?tool_call>|<\|tool_call>|<tool_call\|>|<\|channel>|^\s*<function=|\{\s*\"name\"\s*:\s*\"\w+\"")


def strip_thinking(text: str) -> str:
    """Drop closed reasoning blocks; an unclosed opening block drops the rest (never speak a half-finished thought)."""
    t = _THINK.sub("", text or "")
    for tag in ("<think>", "<|channel>thought"):
        if tag in t:
            t = t.split(tag)[0]
    return t.strip()


def strip_debris(text: str) -> str:
    """Remove leftover tool-call syntax, innermost braces first, to a fixpoint (nested objects)."""
    while True:
        once = _DEBRIS.sub(" ", text)
        if once == text:
            return re.sub(r"[ \t]{2,}", " ", text).strip()
        text = once


def looks_leaked(text: str) -> bool:
    return bool(_LEAK.search(text or ""))


def _selfcheck():
    native = {"content": "", "tool_calls": [{"id": "a", "function": {"name": "set_volume", "arguments": '{"level": 40}'}}]}
    c = parse(native)
    assert c[0].name == "set_volume" and c[0].args == {"level": 40} and c[0].id == "a" and c[0].error is None
    bad = parse({"tool_calls": [{"function": {"name": "x", "arguments": "{oops"}}]})
    assert bad[0].args == {} and bad[0].error
    assert parse("<tool_call>\n{\"name\": \"get_time\", \"arguments\": {}}\n</tool_call>", {"get_time"})[0].name == "get_time"
    q = parse("<tool_call><function=calc><parameter=expr>2 + 2</parameter></function></tool_call>", {"calc"})
    assert q[0].args == {"expr": "2 + 2"}
    assert parse('<|tool_call>call:weather{city:<|"|>London<|"|>,days:3}<tool_call|>', {"weather"})[0].args == {"city": "London", "days": 3}
    assert parse('{"name": "list_tasks", "arguments": {}}', {"list_tasks"})[0].name == "list_tasks"
    assert parse('{"name": "fake_tool", "arguments": {}}', {"list_tasks"}) == []          # unknown names never run
    assert parse("<think>call get_time</think> hi", {"get_time"}) == []                     # calls inside reasoning do not count
    assert len(parse('<tool_call>{"name":"a","arguments":{}}</tool_call><tool_call>{"name":"a","arguments":{}}</tool_call>', {"a"})) == 1
    assert strip_thinking("<think>hmm</think>Done.") == "Done." and strip_thinking("<think>never closed") == ""
    assert strip_debris('Sure {"name": "x", "arguments": {}} done') == "Sure done"
    assert looks_leaked("functions.get_time:") and looks_leaked("<tool_call>") and not looks_leaked("It is noon.")
    print("toolparse ok")


if __name__ == "__main__":
    _selfcheck()
