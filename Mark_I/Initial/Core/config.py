"""Core settings: one typed object, defaults in code, state outside the repo, env overrides.

Sockets
  in : env FRED_DATA (state/secrets/personal data dir, default ~/FRED_Private), env FRED_<FIELD> (any field below, upper-cased)
  out: get() -> Settings (cached); data_dir() -> Path; set_data_dir(path) re-points everything (tests use a temp dir, so test output never reaches the real logs)
Every threshold the later layers need is a field here, never a literal in the code that uses it.
Self-check: python config.py
"""
import os
import tempfile
from dataclasses import dataclass, fields
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent


@dataclass
class Settings:
    playground_url: str = "http://localhost:8090"   # /v1 is appended by the client
    brain: str = "unsloth/qwen3.5-4b"                 # chat model id; Qwen only for now (Vatsal, 2026-10-06), override with FRED_BRAIN
    embedder: str = "text-embedding-qwen3-embedding-4b"
    http_timeout_s: float = 600.0                     # one model call, incl. a cold load
    http_retries: int = 1                             # extra tries on a connection error only
    max_tool_rounds: int = 4                          # P3 loop
    max_calls_per_round: int = 4
    tool_timeout_s: float = 15.0                      # default per tool call
    gpu_default_timeout_s: float = 120.0
    event_ring_s: float = 600.0                       # EventLog in-memory window (~10 min)
    log_max_bytes: int = 20_000_000                   # one jsonl before it rotates


def _coerce(raw: str, like):
    return (raw.lower() in ("1", "true", "yes", "on")) if isinstance(like, bool) else type(like)(raw)


def _build() -> Settings:
    s = Settings()
    for f in fields(s):
        raw = os.environ.get("FRED_" + f.name.upper())
        if raw is not None:
            setattr(s, f.name, _coerce(raw, getattr(s, f.name)))
    return s


_cache: Settings | None = None
_data: Path | None = None


def get() -> Settings:
    global _cache
    if _cache is None:
        _cache = _build()
    return _cache


def data_dir() -> Path:
    global _data
    if _data is None:
        _data = Path(os.environ.get("FRED_DATA") or Path.home() / "FRED_Private")
        _data.mkdir(parents=True, exist_ok=True)
    return _data


def set_data_dir(path) -> Path:
    """Re-point state to `path` (tests, replays). Callers that cached a path under the old dir must re-open."""
    global _data
    _data = Path(path)
    _data.mkdir(parents=True, exist_ok=True)
    return _data


def reload() -> Settings:
    global _cache
    _cache = None
    return get()


def _selfcheck():
    s = get()
    assert s.max_tool_rounds == 4 and s.playground_url.startswith("http") and "qwen" in s.brain
    os.environ["FRED_MAX_TOOL_ROUNDS"] = "7"
    os.environ["FRED_EVENT_RING_S"] = "30"
    assert reload().max_tool_rounds == 7 and get().event_ring_s == 30.0
    del os.environ["FRED_MAX_TOOL_ROUNDS"], os.environ["FRED_EVENT_RING_S"]
    reload()
    real = data_dir()
    with tempfile.TemporaryDirectory() as t:
        assert set_data_dir(t) == Path(t) and data_dir() == Path(t)
    set_data_dir(real)
    print("config ok")


if __name__ == "__main__":
    _selfcheck()
