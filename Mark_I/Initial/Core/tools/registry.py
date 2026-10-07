"""Tool registry: the one contract between the engine and every tool. The engine never sees a raw exception or traceback.

Sockets
  in : registry.register(ToolSpec(name, fn, schema, description, group, destructive, timeout_s, reads_untrusted, sends, isolate))
         fn(**args) returns data | Result; it may raise ToolError(kind, speakable) for an expected failure; any other exception becomes `failed`
       registry.call(name, args) -> Result     args may be loose model output (coerced against the schema first, tools/coerce.py)
  out: Result(ok, data, error_kind, speakable, message, ms)
         error_kind in {bad_args, not_found, denied, timeout, unavailable, failed}; `message` is for the model/log (what exactly was wrong, so a retry can differ),
         `speakable` is the only text FRED may say about a failure
       registry.schemas(names=None, groups=None) -> OpenAI function schemas to offer the model; registry.groups() / .spec(name) for the router and the gates
       Every call writes one line to tools.jsonl (log.tool).
Timeouts: every call runs in a worker thread bounded by timeout_s. A thread cannot be killed, so a tool that can hang or run long (file search, subprocess)
must set isolate=True: it then runs in a child process that IS killed on timeout (fn must be a picklable module-level function, result picklable).
Flags destructive / reads_untrusted / sends are metadata for the engine gates (confirmation, read-vs-send separation); the registry does not act on them.
Routing metadata lives with the tool: `group` (the category the cue match offers as a set), `cues` (regex fragments), `examples` (embedded phrasings), `say` (data is the spoken answer).
Self-check: python tools/registry.py
"""
import multiprocessing as mp
import queue
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import log
from tools.coerce import coerce

ERROR_KINDS = ("bad_args", "not_found", "denied", "timeout", "unavailable", "failed")
_SPEAK = {"bad_args": "I couldn't work out what to do with that.", "not_found": "I don't have that.", "denied": "I'm not allowed to do that.",
          "timeout": "That took too long.", "unavailable": "That isn't available right now.", "failed": "That didn't work."}


class ToolError(Exception):
    """Raise from a tool for an expected failure. `speakable` is what FRED may say; `message` (default: speakable) is for the model and the log."""

    def __init__(self, kind: str, speakable: str = "", message: str = ""):
        assert kind in ERROR_KINDS, kind
        super().__init__(message or speakable or kind)
        self.kind, self.speakable, self.message = kind, speakable or _SPEAK[kind], message or speakable or kind


@dataclass
class Result:
    ok: bool
    data: Any = None
    error_kind: str | None = None
    speakable: str = ""
    message: str = ""
    ms: int = 0

    @classmethod
    def fail(cls, kind: str, speakable: str = "", message: str = ""):
        return cls(False, None, kind, speakable or _SPEAK[kind], message or speakable or kind)


@dataclass
class ToolSpec:
    name: str
    fn: Callable
    schema: dict = field(default_factory=lambda: {"type": "object", "properties": {}})
    description: str = ""
    group: str = "general"
    destructive: bool = False
    timeout_s: float | None = None    # None -> settings.tool_timeout_s
    reads_untrusted: bool = False
    sends: bool = False
    isolate: bool = False
    say: bool = False                 # the returned data is already a complete spoken sentence: the engine speaks it as is (no second model round) unless the request is compound
    cues: tuple = ()                  # regex fragments (case-insensitive, word-bounded by the router) that make this tool's GROUP a candidate for an utterance
    preview: Callable | None = None   # preview(args) -> str: what exactly a destructive call would hit, shown in the confirmation question ("kills chrome.exe x7")
    examples: tuple = ()              # how a person actually asks for it; embedded once for the semantic ranking
    not_for: tuple = ()               # what this tool must NOT be used for; shown to the model after the description, never embedded (naming a neighbour there would pull the ranking toward it)


def _child(q, fn, args):
    try:
        q.put(("ok", fn(**args)))
    except ToolError as ex:
        q.put(("tool_error", (ex.kind, ex.speakable, ex.message)))
    except BaseException as ex:
        q.put(("error", repr(ex)))


class Registry:
    def __init__(self):
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> ToolSpec:
        if spec.name in self._tools:
            raise ValueError(f"tool {spec.name!r} already registered")
        self._tools[spec.name] = spec
        return spec

    def spec(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def names(self, groups=None) -> list[str]:
        return [n for n, s in self._tools.items() if groups is None or s.group in groups]

    def groups(self) -> dict[str, list[str]]:
        g: dict[str, list[str]] = {}
        for n, s in self._tools.items():
            g.setdefault(s.group, []).append(n)
        return g

    def schemas(self, names=None, groups=None) -> list[dict]:
        pick = [n for n in (names if names is not None else self._tools) if n in self._tools and (groups is None or self._tools[n].group in groups)]
        return [{"type": "function", "function": {"name": n, "description": self._tools[n].description + (" Not for: " + "; ".join(self._tools[n].not_for) + "." if self._tools[n].not_for else ""), "parameters": self._tools[n].schema}} for n in pick]

    def call(self, name: str, args=None, base_dir=None) -> Result:
        t0 = time.monotonic()
        spec = self._tools.get(name)
        if spec is None:
            res = Result.fail("not_found", message=f"no tool named {name!r}")
        else:
            clean, errors = coerce(args, spec.schema, base_dir)
            res = Result.fail("bad_args", message="; ".join(errors)) if errors else self._run(spec, clean)
        res.ms = int((time.monotonic() - t0) * 1000)
        log.tool(name, ok=res.ok, error_kind=res.error_kind, message=res.message[:200], ms=res.ms, args=args if not res.ok else None)
        return res

    def _run(self, spec: ToolSpec, args: dict) -> Result:
        timeout = spec.timeout_s if spec.timeout_s is not None else config.get().tool_timeout_s
        return self._run_child(spec, args, timeout) if spec.isolate else self._run_thread(spec, args, timeout)

    @staticmethod
    def _wrap(out) -> Result:
        return out if isinstance(out, Result) else Result(True, out)

    def _run_thread(self, spec, args, timeout) -> Result:
        box: dict = {}

        def work():
            try:
                box["r"] = self._wrap(spec.fn(**args))
            except ToolError as ex:
                box["r"] = Result.fail(ex.kind, ex.speakable, ex.message)
            except BaseException as ex:
                box["r"] = Result.fail("failed", message=f"{type(ex).__name__}: {ex}")

        t = threading.Thread(target=work, daemon=True, name=f"tool-{spec.name}")
        t.start()
        t.join(timeout)
        return box.get("r") or Result.fail("timeout", message=f"{spec.name} did not finish in {timeout}s")

    def _run_child(self, spec, args, timeout) -> Result:
        ctx = mp.get_context("spawn")
        q = ctx.Queue()
        p = ctx.Process(target=_child, args=(q, spec.fn, args), daemon=True)
        p.start()
        try:
            kind, val = q.get(timeout=timeout)
        except queue.Empty:
            return Result.fail("timeout", message=f"{spec.name} killed after {timeout}s") if p.is_alive() else Result.fail("failed", message=f"{spec.name} worker exited without a result")
        finally:
            if p.is_alive():
                p.kill()
            p.join(2)
        if kind == "ok":
            return self._wrap(val)
        return Result.fail(*val) if kind == "tool_error" else Result.fail("failed", message=val)


registry = Registry()   # the process-wide registry; tests build their own Registry()


def _hang(seconds: float):   # module-level so the isolated child can import it
    time.sleep(seconds)
    return "late"


def _echo(x: int):
    return x * 2


def _selfcheck():
    import tempfile
    real = config.data_dir()
    with tempfile.TemporaryDirectory() as t:
        config.set_data_dir(t)
        r = Registry()
        r.register(ToolSpec("set_volume", lambda level: f"volume {level}", {"type": "object", "required": ["level"], "properties": {"level": {"type": "integer", "minimum": 0, "maximum": 100}}},
                            "Set the volume", group="audio"))
        r.register(ToolSpec("boom", lambda: 1 / 0, group="misc"))
        r.register(ToolSpec("deny", lambda: (_ for _ in ()).throw(ToolError("denied", "Not on that folder.")), group="misc"))
        r.register(ToolSpec("slow_thread", lambda: time.sleep(1), timeout_s=0.1))
        r.register(ToolSpec("slow_child", _hang, {"type": "object", "properties": {"seconds": {"type": "number"}}}, timeout_s=0.5, isolate=True))
        r.register(ToolSpec("echo_child", _echo, {"type": "object", "required": ["x"], "properties": {"x": {"type": "integer"}}}, timeout_s=30, isolate=True))
        ok = r.call("set_volume", {"level": "40"})
        assert ok.ok and ok.data == "volume 40" and ok.error_kind is None            # str -> int coerced before the call
        bad = r.call("set_volume", {"level": 400})
        assert not bad.ok and bad.error_kind == "bad_args" and "<= 100" in bad.message and bad.speakable
        assert r.call("set_volume", {}).error_kind == "bad_args"
        assert r.call("nope").error_kind == "not_found"
        b = r.call("boom")
        assert b.error_kind == "failed" and "ZeroDivisionError" in b.message and "Traceback" not in b.speakable   # no raw exception reaches the engine
        d = r.call("deny")
        assert d.error_kind == "denied" and d.speakable == "Not on that folder."
        assert r.call("slow_thread").error_kind == "timeout"
        t0 = time.time()
        assert r.call("slow_child", {"seconds": 30}).error_kind == "timeout" and time.time() - t0 < 20   # killed, not waited for
        assert r.call("echo_child", {"x": "21"}).data == 42
        assert [s["function"]["name"] for s in r.schemas(groups={"audio"})] == ["set_volume"] and r.groups()["misc"] == ["boom", "deny"]
        try:
            r.register(ToolSpec("boom", lambda: 0))
            raise AssertionError
        except ValueError:
            pass
        assert (config.data_dir() / "logs/tools.jsonl").read_text(encoding="utf-8").count("\n") >= 8
        config.set_data_dir(real)
    print("registry ok")


if __name__ == "__main__":
    _selfcheck()
