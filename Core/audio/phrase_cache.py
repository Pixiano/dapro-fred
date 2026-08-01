# Core/audio/phrase_cache.py
#
# Pre-synthesised audio for FRED's fixed, small vocabulary of spoken
# phrases: the filler pool (audio/fillers.py) and tool-event captions
# ("Calculating...", "Listing processes...", from orchestrator.TOOL_LABELS).
# Both are drawn from a closed set of ~50 strings total, and both get
# spoken on essentially every turn — synthesising the same "On it." on
# Kokoro's CPU path fresh every single time is pure waste.
#
# Cached as raw float32 PCM (.npy — Kokoro's own output format, so
# loading a cache hit is a numpy file read, not an audio decode). Not
# WAV or MP3: WAV would ferry the exact same bytes through a container
# for no benefit, and MP3 would add a codec dependency and a lossy
# encode/decode round trip for phrases that are milliseconds long
# either way. MP4 (raised as an option) is a video container and has no
# business here at all.
#
# Keyed by a hash of (text, voice, speed) — see phrase_key() — so a
# KOKORO_VOICE or KOKORO_SPEED change invalidates old entries
# automatically instead of silently playing stale audio at the wrong
# voice or pace.

import hashlib
from pathlib import Path

import numpy as np

from config.settings import DATA_DIR, KOKORO_VOICE, KOKORO_VOICE_BLEND, KOKORO_SPEED

CACHE_DIR = DATA_DIR / "phrase_cache"


def _voice_fingerprint() -> str:
    """Raw config, not the resolved (possibly blended-tensor) voice —
    the tensor isn't hashable/stable to repr, but the settings that
    produce it are, and are exactly what should invalidate the cache
    when changed."""
    return f"{KOKORO_VOICE}|{KOKORO_VOICE_BLEND}|{KOKORO_SPEED}"


def phrase_key(text: str) -> str:
    raw = f"{text}\x00{_voice_fingerprint()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def get(text: str):
    """Returns (samples, sr) if cached, else None. Never raises — a
    corrupt or missing cache entry just means "synthesise it now",
    same as a cache miss."""
    path = CACHE_DIR / f"{phrase_key(text)}.npz"
    if not path.exists():
        return None
    try:
        with np.load(path) as data:
            return data["samples"], int(data["sr"])
    except Exception as e:
        print(f"[phrase_cache] unreadable entry for {text!r}: {e}")
        return None


def put(text: str, samples, sr: int):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = phrase_key(text)
    path = CACHE_DIR / f"{key}.npz"
    # Must itself end in ".npz" — np.savez silently APPENDS ".npz" to
    # any path that doesn't already have it, so a ".npz.tmp" name here
    # actually got written as ".npz.tmp.npz", and the rename below then
    # failed looking for a file that was never created. Reproduced
    # directly before fixing.
    tmp = CACHE_DIR / f"{key}.tmp.npz"
    np.savez(tmp, samples=samples.astype(np.float32), sr=np.int32(sr))
    tmp.replace(path)


def warm(tts, phrases) -> tuple:
    """
    Ensures every phrase in `phrases` has a cache entry, synthesising
    only the ones missing. Returns (already_cached, newly_generated).

    This is the ONLY place that forces Kokoro to load just to build the
    cache — once every phrase is cached (which after the first run on a
    given voice/speed, is all of them), a session that never hits an
    uncached phrase never needs to load the model at all.
    """
    hit = miss = 0
    for text in phrases:
        if get(text) is not None:
            hit += 1
            continue
        try:
            samples, sr = tts.synth(text)
            put(text, samples, sr)
            miss += 1
        except Exception as e:
            print(f"[phrase_cache] failed to pre-synthesise {text!r}: {e}")
    return hit, miss
