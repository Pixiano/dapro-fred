# Core/utils/health_check.py
#
# A boot-time check that the things FRED silently depends on are
# actually working, written after two failures that hid for a long time
# because nothing ever asserted them:
#
#   1. The venv's llama-cpp-python was a CUDA build compiled against
#      CUDA 12. A toolkit upgrade to 13.1 removed the runtime it linked
#      against, so importing it raised at load. Nothing checked, so the
#      symptom was "FRED feels slow" rather than "the GPU build is
#      broken" — and a benchmark run against a different interpreter
#      then confirmed the wrong conclusion (see settings.GPU_LAYERS).
#   2. A stale THINKING_MARKER reference raised NameError on every
#      tool-calling turn. It surfaced only as the generic "cognitive
#      malfunction" line, which names no cause at all.
#
# The rule this encodes: an assumption that is expensive to be wrong
# about should be checked at boot, where it costs milliseconds, not
# discovered from a symptom weeks later.
#
# Never raises. A health check that can break startup is worse than the
# problems it reports.

import shutil

from config.settings import (
    MODEL_TIERS,
    DEFAULT_TIER,
    VAULT_DIR,
    KOKORO_MODEL_PATH,
    KOKORO_VOICES_PATH,
    STT_MODEL_PATH,
    GPU_LAYERS,
)

# (label, ok, detail) — ok=False means degraded, not necessarily fatal.
_OK = "ok"
_WARN = "warn"
_FAIL = "fail"


def _check_llm_backend():
    try:
        import llama_cpp
    except Exception as e:
        return (_FAIL, f"llama_cpp will not import: {e}")

    try:
        offload = bool(llama_cpp.llama_supports_gpu_offload())
    except Exception as e:
        return (_WARN, f"llama_cpp imported but GPU support unreadable: {e}")

    if GPU_LAYERS != 0 and not offload:
        # Not fatal — it still runs, ~20x slower. Worth saying out loud
        # rather than leaving to be rediscovered by benchmark.
        return (
            _WARN,
            "llama_cpp is a CPU-only build but GPU_LAYERS is set — "
            "inference will run on CPU",
        )

    return (_OK, f"llama_cpp {llama_cpp.__version__}, GPU offload {offload}")


def _check_model_files():
    missing = [t for t, p in MODEL_TIERS.items() if not p.exists()]
    if DEFAULT_TIER in missing:
        return (_FAIL, f"default tier '{DEFAULT_TIER}' model file is missing")
    if missing:
        return (_WARN, f"missing model files for tier(s): {', '.join(missing)}")
    return (_OK, f"{len(MODEL_TIERS)} tier(s) present")


def _check_vault():
    if not VAULT_DIR.exists():
        return (_WARN, f"vault not found at {VAULT_DIR} — running on the fallback prompt")
    count = sum(1 for _ in VAULT_DIR.rglob("*.md"))
    return (_OK, f"{count} markdown file(s)")


def _check_voice():
    missing = [
        str(p) for p in (KOKORO_MODEL_PATH, KOKORO_VOICES_PATH) if not p.exists()
    ]
    if missing:
        return (_WARN, "Kokoro model/voices missing — TTS will fall back to SAPI")
    return (_OK, "Kokoro model and voices present")


def _check_stt():
    if not STT_MODEL_PATH.exists():
        return (_WARN, f"STT model missing at {STT_MODEL_PATH}")
    return (_OK, "STT model present")


def _check_git():
    """Only affects the git_* tools, so a miss is purely informational."""
    if shutil.which("git") is None:
        return (_WARN, "git not on PATH — the git tools will not work")
    return (_OK, "git available")


CHECKS = (
    ("llm backend", _check_llm_backend),
    ("model files", _check_model_files),
    ("vault", _check_vault),
    ("voice", _check_voice),
    ("stt", _check_stt),
    ("git", _check_git),
)


def run(verbose: bool = True) -> dict:
    """
    Run every check. Returns {label: (status, detail)}.

    Prints a one-line-per-check summary when verbose, which is what
    shows up in the console FRED is launched from. Problems are also
    written to the event log so a GUI-mode launch (pythonw, no console)
    still leaves a record.
    """
    from utils import event_log

    results = {}
    problems = []

    for label, check in CHECKS:
        try:
            status, detail = check()
        except Exception as e:
            status, detail = _FAIL, f"check itself failed: {e}"

        results[label] = (status, detail)

        if verbose:
            mark = {_OK: "  ok  ", _WARN: " warn ", _FAIL: " FAIL "}[status]
            print(f"[health] [{mark}] {label}: {detail}")

        if status != _OK:
            problems.append(f"{label}: {detail}")

    if problems:
        try:
            event_log.log("health_check", status="degraded", problems=problems)
        except Exception:
            pass
    else:
        try:
            event_log.log("health_check", status="ok")
        except Exception:
            pass

    return results


def failures(results: dict) -> list:
    """Just the hard failures — what would make FRED not work at all."""
    return [f"{k}: {v[1]}" for k, v in results.items() if v[0] == _FAIL]
