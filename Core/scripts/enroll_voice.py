# Core/scripts/enroll_voice.py
#
# ONE-TIME setup for input/voice_id.py — records a handful of short
# utterances, extracts a speaker embedding from each via the same
# SpeechBrain ECAPA-TDNN model voice_id.py verifies against, and saves
# them to data/voice_enrollment.json. Same "run by hand, not wired into
# FRED's turn flow" convention as scripts/enroll_face.py and
# scripts/enroll_headphones.py.
#
# NOT CI-safe: needs a real microphone and Vatsal actually speaking on
# cue, turn-by-turn, same as the camera enrollment scripts. No automated
# test for the recording path itself — voice_id.py's own pytest coverage
# is pure cosine-similarity/threshold logic against synthetic
# embeddings; the only honest check of the mic capture + real-voice
# discrimination is running this by hand.
#
# Built 2026-08-25 without a live paired-human recording session to
# validate against (an autonomous background task, not a live back-and-
# forth with Vatsal) — voice_id.py's MATCH_THRESHOLD was instead
# measured against Kokoro-TTS-synthesized voices as a stand-in (see that
# module's own docstring). Run this for real, then sanity-check a real
# verification attempt before trusting the threshold for anything that
# matters.

import json
import sys
import time
from pathlib import Path

import sounddevice as sd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import DATA_DIR
from input.voice_id import SAMPLE_RATE, embed

ENROLLMENT_PATH = DATA_DIR / "voice_enrollment.json"

_UTTERANCE_SECONDS = 3.0

# One prompt per shot — enough variety (content, pace, volume) to not
# just be the same three words eight times, same "coverage, not just
# count" reasoning enroll_face.py's HARD_CONDITIONS shots use.
_PROMPTS = (
    "Say a sentence about your day.",
    "Count from one to ten out loud.",
    "Say your name and today's date.",
    "Describe what's on your desk right now.",
    "Say a sentence in a slightly different tone or pace than usual.",
    "Read this out loud: the quick brown fox jumps over the lazy dog.",
    "Say a sentence a bit quieter than normal.",
    "Say a sentence a bit louder than normal.",
)


def _record_one(seconds: float):
    audio = sd.rec(int(seconds * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="float32")
    sd.wait()
    return audio.flatten()


def main():
    embeddings = []
    for i, prompt in enumerate(_PROMPTS, start=1):
        print(f"[{i}/{len(_PROMPTS)}] {prompt} Recording in 3s...")
        time.sleep(3)
        print("  ...recording")
        audio = _record_one(_UTTERANCE_SECONDS)
        embeddings.append(embed(audio).tolist())
        print(f"  captured shot {i}")

    ENROLLMENT_PATH.parent.mkdir(parents=True, exist_ok=True)
    ENROLLMENT_PATH.write_text(json.dumps({"embeddings": embeddings}), encoding="utf-8")
    print(f"saved {len(embeddings)} embeddings to {ENROLLMENT_PATH}")


if __name__ == "__main__":
    main()
