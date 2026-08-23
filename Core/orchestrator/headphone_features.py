# Core/orchestrator/headphone_features.py
#
# Shared crop + feature extraction for the trained headphones-on/off
# classifier (scripts/train_headphones_classifier.py writes the model,
# orchestrator/headphone_watch.py reads it). Deliberately its own tiny
# module rather than duplicated in both places — train/serve skew (the
# training script and the live check computing features even slightly
# differently) is a classic, hard-to-notice way to silently break a
# classifier, so there is exactly one implementation both sides import.
#
# Bins reduced from the earlier one-off histogram experiment's 50x60
# (3000-dim) down to 8x8 (64-dim) — Vatsal's own call 2026-08-23 to
# build this for 30-50 photos total, and a few thousand-dimensional
# feature vector on a few dozen samples is a textbook overfitting setup
# regardless of which classifier sits on top of it.

import cv2
import numpy as np

_HIST_BINS = (8, 8)
_CROP_SIZE = (128, 128)


def head_region(frame, analyzer):
    """Crop around the largest detected face, expanded upward and
    outward — where over-ear headphones and their band actually sit,
    which a plain face bbox doesn't cover. None if no face detected.

    `analyzer` is passed in (not imported/loaded here) so this module
    has no opinion on which face-detector instance to use — callers
    already have one warm (presence.py's _get_analyzer() singleton)."""
    faces = analyzer.get(frame)
    if not faces:
        return None

    face = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
    x1, y1, x2, y2 = face.bbox.astype(int)
    w, h = x2 - x1, y2 - y1

    top = max(0, y1 - int(h * 0.6))
    left = max(0, x1 - int(w * 0.3))
    right = min(frame.shape[1], x2 + int(w * 0.3))
    bottom = min(frame.shape[0], y2)

    crop = frame[top:bottom, left:right]
    if crop.size == 0:
        return None
    return cv2.resize(crop, _CROP_SIZE)


def histogram_feature(crop) -> np.ndarray:
    """Flattened HSV histogram, `_HIST_BINS` bins per channel pair —
    a fixed-length feature vector suitable for a small-sample
    scikit-learn classifier. See this module's own docstring for why
    the bin count is small on purpose."""
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, list(_HIST_BINS), [0, 180, 0, 256])
    cv2.normalize(hist, hist)
    return hist.flatten()


def extract_feature(frame, analyzer) -> np.ndarray | None:
    """head_region + histogram_feature in one call — what both the
    training script and the live check actually want. None if no face
    was detected in `frame`."""
    crop = head_region(frame, analyzer)
    if crop is None:
        return None
    return histogram_feature(crop)


if __name__ == "__main__":
    # Self-check: pure shape/plumbing, no real camera or trained model
    # needed. A fake analyzer stands in for insightface's FaceAnalysis.
    import types

    class _FakeFace:
        def __init__(self, box):
            self.bbox = np.array(box, dtype=float)

    fake_frame = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)

    no_face_analyzer = types.SimpleNamespace(get=lambda frame: [])
    assert extract_feature(fake_frame, no_face_analyzer) is None

    one_face_analyzer = types.SimpleNamespace(
        get=lambda frame: [_FakeFace([200, 150, 400, 350])]
    )
    feature = extract_feature(fake_frame, one_face_analyzer)
    assert feature is not None
    assert feature.shape == (_HIST_BINS[0] * _HIST_BINS[1],)
    assert np.isclose(np.linalg.norm(feature), 1.0, atol=1e-3)  # cv2.normalize default is L2

    # Largest-of-multiple-faces picked, same convention as presence.py's
    # own enrollment picker.
    multi_face_analyzer = types.SimpleNamespace(
        get=lambda frame: [_FakeFace([0, 0, 50, 50]), _FakeFace([200, 150, 400, 350])]
    )
    feature2 = extract_feature(fake_frame, multi_face_analyzer)
    assert feature2 is not None

    print("headphone_features self-check: all passed")
