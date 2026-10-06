"""Validate and coerce tool arguments against the tool's JSON schema before the tool runs (fixes the old `int.strip` crash class).

Sockets
  in : coerce(args, schema, base_dir=None) -> (args, errors)   schema = the tool's "parameters" JSON schema (object with properties/required)
  out: args are fixed to the schema types (model output is loose: "50" for an int, "true" for a bool, "[1,2]" for an array, one value for a list);
       errors is a list of short human strings ("level: must be <= 100, got 150"); empty = safe to call. The registry turns errors into error_kind bad_args.
Rules: missing required arg -> error unless the schema has a default; unknown keys are dropped (models add extras); enum matches case-insensitively and is
       fixed to the canonical value; numeric bounds, string/array length and enum violations are errors, never silent clamps (the model sees why and may retry);
       a property with "format": "path" gets %VAR%/$VAR/~ expanded and a relative path resolved against base_dir (default: the user's home).
Self-check: python tools/coerce.py
"""
import json
import os
import re
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_TRUE, _FALSE = {"true", "yes", "on", "1"}, {"false", "no", "off", "0"}


class _Bad(Exception):
    pass


def _int(v):
    if isinstance(v, bool):
        raise _Bad("expected an integer, got a boolean")
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, str) and re.fullmatch(r"\s*[-+]?\d+(\.0+)?\s*", v):
        return int(float(v))
    raise _Bad(f"expected an integer, got {v!r}")


def _num(v):
    if isinstance(v, bool):
        raise _Bad("expected a number, got a boolean")
    if isinstance(v, (int, float)):
        return v
    try:
        return float(v.strip())
    except (ValueError, AttributeError):
        raise _Bad(f"expected a number, got {v!r}")


def _bool(v):
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    raise _Bad(f"expected true or false, got {v!r}")


def _str(v):
    if v is None or isinstance(v, (dict, list)):
        raise _Bad(f"expected text, got {type(v).__name__}")
    return v if isinstance(v, str) else json.dumps(v) if isinstance(v, bool) else str(v)


def _json_of(v, want):
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except json.JSONDecodeError:
            pass
    return v


def _value(v, sch: dict, base_dir):
    t = sch.get("type")
    if t == "integer":
        v = _int(v)
    elif t == "number":
        v = _num(v)
    elif t == "boolean":
        v = _bool(v)
    elif t == "string":
        v = _str(v)
        if sch.get("format") == "path":
            v = os.path.expanduser(os.path.expandvars(v.strip()))
            v = str(Path(v) if os.path.isabs(v) else Path(base_dir or Path.home()) / v)
    elif t == "array":
        v = _json_of(v, list)
        v = v if isinstance(v, list) else [v]
        if "items" in sch:
            v = [_value(x, sch["items"], base_dir) for x in v]
    elif t == "object":
        v = _json_of(v, dict)
        if not isinstance(v, dict):
            raise _Bad(f"expected an object, got {type(v).__name__}")
    if "enum" in sch and v not in sch["enum"]:
        hit = [e for e in sch["enum"] if str(e).lower() == str(v).strip().lower()]
        if not hit:
            raise _Bad(f"must be one of {sch['enum']}, got {v!r}")
        v = hit[0]
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if "minimum" in sch and v < sch["minimum"]:
            raise _Bad(f"must be >= {sch['minimum']}, got {v}")
        if "maximum" in sch and v > sch["maximum"]:
            raise _Bad(f"must be <= {sch['maximum']}, got {v}")
    if isinstance(v, (str, list)):
        if "minLength" in sch and len(v) < sch["minLength"]:
            raise _Bad(f"must not be shorter than {sch['minLength']}")
        if "maxLength" in sch and len(v) > sch["maxLength"]:
            raise _Bad(f"must not be longer than {sch['maxLength']}")
    return v


def coerce(args, schema: dict | None, base_dir=None):
    schema = schema or {}
    props = schema.get("properties", {})
    if isinstance(args, str):
        args = _json_of(args, dict)
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return {}, [f"arguments must be an object, got {type(args).__name__}"]
    out, errors = {}, []
    for k, sch in props.items():
        if k in args and args[k] is not None:
            try:
                out[k] = _value(args[k], sch, base_dir)
            except _Bad as ex:
                errors.append(f"{k}: {ex}")
        elif "default" in sch:
            out[k] = sch["default"]
        elif k in schema.get("required", []):
            errors.append(f"{k}: required")
    if schema.get("additionalProperties") is True:
        out.update({k: v for k, v in args.items() if k not in props})
    return out, errors


def _selfcheck():
    sch = {"type": "object", "required": ["level"], "properties": {
        "level": {"type": "integer", "minimum": 0, "maximum": 100},
        "mode": {"type": "string", "enum": ["Mute", "Unmute"]},
        "fast": {"type": "boolean", "default": False},
        "ids": {"type": "array", "items": {"type": "integer"}},
        "path": {"type": "string", "format": "path"},
        "name": {"type": "string", "maxLength": 5}}}
    ok = lambda a, **kw: coerce(a, sch, **kw)
    assert ok({"level": "50"}) == ({"level": 50, "fast": False}, [])                  # the int/str mismatch
    assert ok({"level": 50.0, "extra": 1})[0] == {"level": 50, "fast": False}          # unknown key dropped
    assert ok('{"level": "7", "fast": "yes"}')[0] == {"level": 7, "fast": True}       # whole args as a JSON string
    assert ok({"level": 1, "mode": "mute"})[0]["mode"] == "Mute"                      # enum fixed to canonical
    assert ok({"level": 1, "ids": "[1, \"2\"]"})[0]["ids"] == [1, 2] and ok({"level": 1, "ids": 3})[0]["ids"] == [3]
    assert ok({"level": 1, "name": 12345})[0]["name"] == "12345"
    errs = ok({"level": 150, "mode": "loud", "name": "toolong"})[1]
    assert len(errs) == 3 and "level: must be <= 100, got 150" in errs
    assert ok({})[1] == ["level: required"] and ok({"level": "loud"})[1] and ok({"level": True})[1]
    assert ok(["x"])[1] and ok(None)[1] == ["level: required"]
    os.environ["FRED_T"] = "sub"
    assert Path(ok({"level": 1, "path": "$FRED_T/a.txt" if os.name != "nt" else "%FRED_T%/a.txt"}, base_dir="C:/base")[0]["path"]) == Path("C:/base/sub/a.txt")
    assert Path(ok({"level": 1, "path": "x.txt"}, base_dir="C:/base")[0]["path"]) == Path("C:/base/x.txt")
    assert os.path.isabs(ok({"level": 1, "path": "~/x"})[0]["path"])
    print("coerce ok")


if __name__ == "__main__":
    _selfcheck()
