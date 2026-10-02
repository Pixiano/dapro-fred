import glob
import os

import numpy as np
import soundfile as sf
from openwakeword.model import Model

MODEL_PATH = "models/wakeword/hey_fred.onnx"
POS_TEST_DIR = "data/wakeword_training/output/hey_fred/positive_test"
NEG_TEST_DIR = "data/wakeword_training/output/hey_fred/negative_test"
THRESHOLD = 0.35  # matches config.settings.WAKEWORD_THRESHOLD


def score_file(model, path):
    audio, sr = sf.read(path, dtype="int16")
    if audio.ndim > 1:
        audio = audio[:, 0]
    model.reset()
    scores = []
    chunk = 1280
    for i in range(0, max(len(audio) - chunk, 1), chunk):
        pred = model.predict(audio[i:i + chunk])
        scores.append(list(pred.values())[0])
    return max(scores) if scores else 0.0


model = Model(wakeword_models=[MODEL_PATH], inference_framework="onnx")

pos_files = sorted(glob.glob(os.path.join(POS_TEST_DIR, "*.wav")))
neg_files = sorted(glob.glob(os.path.join(NEG_TEST_DIR, "*.wav")))

pos_scores = [score_file(model, f) for f in pos_files]
neg_scores = [score_file(model, f) for f in neg_files]

pos_correct = sum(1 for s in pos_scores if s >= THRESHOLD)
neg_correct = sum(1 for s in neg_scores if s < THRESHOLD)

print(f"positive_test: {len(pos_files)} files, {pos_correct} correctly fired (>= {THRESHOLD}), "
      f"{len(pos_files) - pos_correct} missed")
print(f"  score range: min={min(pos_scores):.3f} max={max(pos_scores):.3f} mean={np.mean(pos_scores):.3f}")
print(f"negative_test: {len(neg_files)} files, {neg_correct} correctly stayed silent (< {THRESHOLD}), "
      f"{len(neg_files) - neg_correct} false-fired")
print(f"  score range: min={min(neg_scores):.3f} max={max(neg_scores):.3f} mean={np.mean(neg_scores):.3f}")
