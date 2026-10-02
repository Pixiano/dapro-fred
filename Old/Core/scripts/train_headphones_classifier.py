# Core/scripts/train_headphones_classifier.py
#
# Trains the headphones-on/off classifier orchestrator/headphone_watch.py
# prefers over the vision-LLM comparison when it's present. Vatsal's own
# call 2026-08-23: structure built now, the 30-50 photos per class
# captured later (scripts/enroll_headphones.py --train-shot on/off),
# run this once the photos exist.
#
# Run by hand, same "not wired into FRED's voice/turn flow" convention
# as every other scripts/enroll_*.py — this needs a human to look at
# the printed cross-validation accuracy and decide whether the result
# is trustworthy enough to actually use, not something to run silently.
#
# NOT CI-safe in the sense of needing real labeled photos on disk, but
# the pipeline mechanics (feature extraction, train/eval/save) are
# covered by headphone_features.py's own self-check plus the dry-run
# check at the bottom of this file (synthetic data, no real photos).
#
# Deliberately a SEPARATE model/data pool from the 6-shot vision-LLM
# reference set (HEADPHONES_ON_PATHS/HEADPHONES_OFF_PATHS,
# scripts/enroll_headphones.py's default --shot mode) — that one stays
# as the fallback for whenever this classifier isn't trained yet or its
# own confidence is low.

import sys
from pathlib import Path

import cv2
import joblib
import numpy as np
from sklearn.model_selection import StratifiedKFold, cross_val_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import (
    HEADPHONES_CLASSIFIER_PATH,
    HEADPHONES_TRAINING_OFF_DIR,
    HEADPHONES_TRAINING_ON_DIR,
)
from input.presence import _get_analyzer
from orchestrator.headphone_features import extract_feature

# Below this many photos per class, cross-validation folds get too
# small to mean anything (a 5-fold split needs at least 5 per class per
# fold to be meaningful) — refuse to train a model nobody should trust
# yet rather than silently producing one anyway. Matches the "30-50 per
# class" range this was scoped for, not a hard requirement to hit 30.
_MIN_PHOTOS_PER_CLASS = 10

_CV_FOLDS = 5


def _load_class(directory: Path, label: int, analyzer):
    X, y, skipped = [], [], []
    for path in sorted(directory.glob("*.jpg")):
        frame = cv2.imread(str(path))
        if frame is None:
            skipped.append((path.name, "unreadable"))
            continue
        feature = extract_feature(frame, analyzer)
        if feature is None:
            skipped.append((path.name, "no face detected"))
            continue
        X.append(feature)
        y.append(label)
    return X, y, skipped


def main():
    if not HEADPHONES_TRAINING_ON_DIR.is_dir() or not HEADPHONES_TRAINING_OFF_DIR.is_dir():
        print(
            f"No training photos yet — capture some first with "
            f"scripts/enroll_headphones.py --train-shot on/off "
            f"(writes into {HEADPHONES_TRAINING_ON_DIR.parent})."
        )
        sys.exit(1)

    analyzer = _get_analyzer()

    X_on, y_on, skipped_on = _load_class(HEADPHONES_TRAINING_ON_DIR, 1, analyzer)
    X_off, y_off, skipped_off = _load_class(HEADPHONES_TRAINING_OFF_DIR, 0, analyzer)

    for name, skipped in (("on", skipped_on), ("off", skipped_off)):
        for filename, reason in skipped:
            print(f"skipped {name}/{filename}: {reason}")

    print(f"on: {len(X_on)} usable photos, off: {len(X_off)} usable photos")

    if len(X_on) < _MIN_PHOTOS_PER_CLASS or len(X_off) < _MIN_PHOTOS_PER_CLASS:
        print(
            f"Need at least {_MIN_PHOTOS_PER_CLASS} usable photos per class to train "
            f"anything worth trusting — not doing it with fewer than that."
        )
        sys.exit(1)

    X = np.array(X_on + X_off)
    y = np.array(y_on + y_off)

    # Linear SVM on a standardized, already-low-dimensional (64-d,
    # see headphone_features.py) feature — a reasonable default for a
    # few dozen samples, not k-NN (curse-of-dimensionality-sensitive
    # even at 64-d with this few samples) and not anything requiring
    # more data than this was scoped for.
    model = make_pipeline(StandardScaler(), SVC(kernel="linear", probability=True))

    folds = min(_CV_FOLDS, len(X_on), len(X_off))
    scores = cross_val_score(model, X, y, cv=StratifiedKFold(n_splits=folds, shuffle=True, random_state=0))
    print(f"{folds}-fold cross-validation accuracy: {scores.mean():.2f} (+/- {scores.std():.2f})  {scores}")

    # Cross-validation estimates generalization; the SAVED model is
    # still fit on everything available, same standard practice as any
    # small-data classical-ML pipeline (CV picks the honest number to
    # report, doesn't withhold data from the final artifact).
    model.fit(X, y)
    HEADPHONES_CLASSIFIER_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, HEADPHONES_CLASSIFIER_PATH)
    print(f"saved {HEADPHONES_CLASSIFIER_PATH}")


if __name__ == "__main__":
    if "--dry-run" in sys.argv:
        # Self-check: pipeline mechanics only (fit/cross-validate/save/
        # load round-trip), synthetic separable data, no real photos or
        # camera needed. Doesn't exercise _load_class's real-file glob.
        import tempfile

        rng = np.random.RandomState(0)
        X = np.vstack([
            rng.normal(0, 1, (20, 64)),
            rng.normal(3, 1, (20, 64)),
        ])
        y = np.array([0] * 20 + [1] * 20)

        model = make_pipeline(StandardScaler(), SVC(kernel="linear", probability=True))
        scores = cross_val_score(model, X, y, cv=StratifiedKFold(n_splits=5, shuffle=True, random_state=0))
        assert scores.mean() > 0.9, f"separable synthetic data should train near-perfectly, got {scores}"

        model.fit(X, y)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "model.joblib"
            joblib.dump(model, path)
            reloaded = joblib.load(path)
            assert (reloaded.predict(X) == model.predict(X)).all()

        print("train_headphones_classifier --dry-run: all passed")
    else:
        main()
