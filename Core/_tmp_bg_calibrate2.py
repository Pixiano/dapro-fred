import sys
import time

sys.path.insert(0, ".")
import cv2

from input import presence

idx = presence.resolve_camera_index()
print(f"camera index: {idx}", flush=True)

cap = cv2.VideoCapture(idx)
if not cap.isOpened():
    print("camera failed to open")
    sys.exit(1)

# Let auto-exposure/white-balance settle before trusting any reading —
# a handful of throwaway reads, not counted.
for _ in range(15):
    cap.read()
    time.sleep(0.2)

subtractor = cv2.createBackgroundSubtractorMOG2(history=500, varThreshold=16, detectShadows=False)

DURATION_SECONDS = 600  # 10 minutes
INTERVAL_SECONDS = 10  # matches PRESENCE_POLL_SECONDS

start = time.time()
n = 0
try:
    while time.time() - start < DURATION_SECONDS:
        ok, frame = cap.read()
        if not ok:
            print(f"{time.time() - start:6.1f}s  camera read failed", flush=True)
            time.sleep(INTERVAL_SECONDS)
            continue

        fg_mask = subtractor.apply(frame)
        fraction = cv2.countNonZero(fg_mask) / fg_mask.size
        n += 1
        print(f"{time.time() - start:6.1f}s  n={n:3d}  foreground_fraction={fraction:.5f}", flush=True)
        time.sleep(INTERVAL_SECONDS)
finally:
    cap.release()

print("done", flush=True)
