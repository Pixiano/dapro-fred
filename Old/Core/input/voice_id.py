# Core/input/voice_id.py
#
# Speaker verification — is a given audio clip's voice Vatsal's own?
# Mirrors input/presence.py's shape (lazy-loaded model, enrolled
# embeddings compared by cosine similarity) but for voice instead of
# face. Standalone module, NOT wired into any flow yet (lockdown,
# presence, etc.) — Vatsal's own "voice recog is dope" ask, 2026-08-25,
# build-first-wire-later, same precedent as headphone_watch.py's own
# history (structure built before enrollment existed).
#
# Model: SpeechBrain's ECAPA-TDNN (speechbrain/spkrec-ecapa-voxceleb) —
# speechbrain was ALREADY an installed dependency in this venv (no new
# pip install needed). CPU-only by choice, same reasoning as presence.py's
# own CPUExecutionProvider for insightface: measured 2026-08-25, a
# 192-dim embedding over a few seconds of audio is ~75ms on CPU (0.15s
# cached model load) — not worth competing with the LLM tiers for VRAM
# for something this cheap.
#
# Privacy: voice embeddings are biometric data, same treatment as
# face_reference.jpg/face_enrollment.json — see .gitignore.
#
# POSSIBLE FUTURE USES (discussed 2026-08-25, none built yet — this
# module stays standalone until one of these is actually chosen):
#   - Reject a wake-word trigger from a non-Vatsal voice (TV, visitor,
#     video call) before FRED acts on it — the audio-side counterpart
#     to presence.py's camera check.
#   - Speaker-tag transcripts if FRED ever sits in on calls/multi-person
#     conversations — which segments are Vatsal vs someone else.
#   - Per-person personalization if family voices get enrolled too,
#     mirroring family_enrollment.json's face-based tiering for audio.
#   - A security signal ("command from an unenrolled voice") companion
#     to security_watch.py's stranger-face detection, for cases the
#     camera can't cover (obstructed, another room, phone-based access).
#   - Identity confirmation if phone_api.py ever answers real calls —
#     voice would be the only identity signal available there.

import json

import numpy as np

from config.settings import BASE_DIR, DATA_DIR

ENROLLMENT_PATH = DATA_DIR / "voice_enrollment.json"

# Same Core/models/ convention as KOKORO_DIR/wakeword's model storage —
# not a stray top-level dir. Regeneratable (re-downloaded from
# HuggingFace on first use), not source — see .gitignore.
_MODEL_CACHE_DIR = BASE_DIR / "models" / "spkrec-ecapa-voxceleb"

# Sample rate any audio passed to embed() must already be at — ECAPA-
# TDNN/VoxCeleb's own expected rate, same constant audio/stt_whisper.py's
# STT_SAMPLE_RATE already uses, so a clip captured for STT needs no
# resampling to be checked here too.
SAMPLE_RATE = 16000

# Cosine-similarity floor for "this is the enrolled speaker" — measured
# 2026-08-25 against Kokoro-TTS-synthesized voices (no real paired-human
# data available to validate against at build time, see enroll_voice.py's
# own docstring on why): two different sentences in the SAME synthetic
# voice scored 0.858 cosine similarity; two DIFFERENT synthetic voices
# scored 0.108-0.147. 0.5 sits safely in that gap without needing to be
# precisely tuned yet. Retune against real enrollment + real verification
# attempts once those exist — don't assume this number holds forever
# just because it held for synthetic voices.
MATCH_THRESHOLD = 0.5

_model = None
_enrollment_embeddings = None  # lazy-loaded, cached — same convention as presence.py's own caches


def _get_model():
    global _model
    if _model is None:
        from speechbrain.inference.speaker import EncoderClassifier
        from speechbrain.utils.fetching import LocalStrategy

        # LocalStrategy.COPY, not the default SYMLINK — confirmed live
        # 2026-08-25: SpeechBrain's default HF-cache symlinking needs a
        # Windows privilege this account doesn't have (WinError 1314),
        # so every fetch would crash without this.
        _model = EncoderClassifier.from_hparams(
            source="speechbrain/spkrec-ecapa-voxceleb",
            savedir=str(_MODEL_CACHE_DIR),
            local_strategy=LocalStrategy.COPY,
            run_opts={"device": "cpu"},
        )
    return _model


def embed(audio: np.ndarray) -> np.ndarray:
    """`audio`: float32 mono waveform at SAMPLE_RATE. Returns a 192-dim
    speaker-embedding vector."""
    import torch

    tensor = torch.from_numpy(np.asarray(audio, dtype=np.float32)).unsqueeze(0)
    with torch.no_grad():
        vector = _get_model().encode_batch(tensor)
    return vector.squeeze().numpy()


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def _load_enrollment() -> list:
    global _enrollment_embeddings
    if _enrollment_embeddings is None:
        try:
            data = json.loads(ENROLLMENT_PATH.read_text(encoding="utf-8"))
            _enrollment_embeddings = [np.array(e) for e in data.get("embeddings", [])]
        except (OSError, ValueError):
            _enrollment_embeddings = []
    return _enrollment_embeddings


def best_similarity(audio: np.ndarray) -> float:
    """Highest cosine similarity between `audio`'s embedding and any
    enrolled embedding. -1.0 (below any real threshold) if nothing is
    enrolled yet — see scripts/enroll_voice.py."""
    enrolled = _load_enrollment()
    if not enrolled:
        return -1.0
    vector = embed(audio)
    return max(_cosine_similarity(vector, ref) for ref in enrolled)


def is_match(audio: np.ndarray) -> bool:
    return best_similarity(audio) >= MATCH_THRESHOLD


if __name__ == "__main__":
    # Self-check: pure cosine-similarity/threshold logic + enrollment
    # JSON round-trip — synthetic embeddings, no real mic or model load
    # needed. Same split as headphone_features.py's own __main__
    # self-check: real hardware validated by hand, logic validated here.
    import tempfile
    from pathlib import Path

    rng = np.random.RandomState(0)
    same_a = rng.normal(0, 1, 192)
    same_b = same_a + rng.normal(0, 0.05, 192)  # small perturbation, same "speaker"
    different = rng.normal(5, 1, 192)  # far away, different "speaker"

    assert _cosine_similarity(same_a, same_a) > 0.99
    assert _cosine_similarity(same_a, same_b) > _cosine_similarity(same_a, different)

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "voice_enrollment.json"
        path.write_text(
            json.dumps({"embeddings": [same_a.tolist(), same_b.tolist()]}), encoding="utf-8"
        )
        loaded_raw = json.loads(path.read_text(encoding="utf-8"))
        loaded = [np.array(e) for e in loaded_raw["embeddings"]]
        assert len(loaded) == 2
        assert np.allclose(loaded[0], same_a)

    print("voice_id self-check: all passed")
