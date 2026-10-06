# 2026-10-06: Core P1c (camera + presence) built

Plan: `Initial/Core/PLAN.md` P1 1c. Code in `Initial/Core/senses/camera.py` and `presence.py`; self-checks in `tests/run_all.py`.

## camera.py
One frame per request: open, discard `camera_warmup` (6) frames, grab, release at once (never a held feed). A frame is **dead** when its 99th-percentile brightness is under 16 or its pixel std under 2. That catches a black frame, a covered lens, and the flat virtual "no signal" camera, while a dim room (noise, a lamp) stays alive. Dead or cannot-open: wait 0.5 s, re-open, try once more; still dead -> `camera.fault {reason, retried}` on the bus and `CameraFault` (never an image, so the old "model describes a black frame" failure cannot happen). Success -> `camera.snap {ms, retried}`. Frames are never saved or put on the bus.
Live check (numbers only, no pictures looked at): `python senses/camera.py --probe` on this PC: index 0 1280x720 std 31 ok; index 1 2560x1440 p99 9 std 0.3 DEAD (the flat virtual camera: the old black-frame trap); index 2 640x480 ok; index 3 closed. `camera_index` default 0, same as the old FRED setting. Windows can renumber after a reboot, so re-run `--probe` if it ever faults.

## presence.py
Poll every `presence_poll_s` (10 s): snapshot -> count faces -> state. **Present** on the first face poll; **absent** after 6 consecutive no-face polls (60 s), so looking away or one missed detection does not flip it. Emits `presence.poll {faces}` each poll and `presence.state {present, faces, since_s}` only on change. A camera fault holds the state and counts nothing. Detection only: no recognition, no embeddings, nothing stored, frame dropped after counting; identity matching is a later, separate socket (old FRED's enrollment file is personal data and was not touched). A detector crash is logged as `presence.error` and the loop continues.
Detector: insightface SCRFD (buffalo_l detection model only) on **CPU** (your answer on 2026-10-06: onnxruntime in the Core venv has no CUDA provider; small model, added to the CPU-allowed list with the others). ~88 ms per poll, 2.5 s one-off load. Sanity: finds 6 faces in the sample photo bundled with insightface; live camera polls returned 0 faces (nobody in view at test time). CPU, so it never touches `gpu.run`: the old "CUDA operation not permitted when stream is capturing" presence errors (4,302 of them) are gone by construction.
Cost: ~0.09 s of one core per 10 s = ~0.9 %, one frame in memory.

## Not done / next
- Presence 8-hour soak (the P1 gate line) belongs to P8; run it once the engine is wired.
- Look-through-camera tool (snapshot + vision describe) is P3 (tools over the P1 events).
