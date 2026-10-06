# 2026-10-06: Core P1c (camera + presence) built

Plan: `Initial/Core/PLAN.md` P1 1c. Code in `Initial/Core/senses/camera.py` and `presence.py`; self-checks in `tests/run_all.py`.

## camera.py
One frame per request: open, discard `camera_warmup` (6) frames, grab, release at once (never a held feed). A frame is **dead** when its 99th-percentile brightness is under 16 or its pixel std under 2. That catches a black frame, a covered lens, and the flat virtual "no signal" camera, while a dim room (noise, a lamp) stays alive. Dead or cannot-open: wait 0.5 s, re-open, try once more; still dead -> `camera.fault {reason, retried}` on the bus and `CameraFault` (never an image, so the old "model describes a black frame" failure cannot happen). Success -> `camera.snap {ms, retried}`. Frames are never saved or put on the bus.
Live check (numbers only, no pictures looked at): `python senses/camera.py --probe` on this PC: index 0 1280x720 std 31 ok; index 1 2560x1440 p99 9 std 0.3 DEAD (the flat virtual camera: the old black-frame trap); index 2 640x480 ok; index 3 closed. `camera_index` default 0, same as the old FRED setting. Windows can renumber after a reboot, so re-run `--probe` if it ever faults.

## presence.py
Poll every `presence_poll_s` (10 s): snapshot -> count faces -> state. **Present** on the first face poll; **absent** once no face has been seen for 45 s (`presence_absent_s`, Vatsal 2026-10-06; noticed at the next poll, so 50 s worst case at the 10 s period), so looking away or one missed detection does not flip it. Emits `presence.poll {faces}` each poll and `presence.state {present, faces, since_s}` only on change. A camera fault holds the state and counts nothing. Detection only: no recognition, no embeddings, nothing stored, frame dropped after counting; identity matching is a later, separate socket (old FRED's enrollment file is personal data and was not touched). A detector crash is logged as `presence.error` and the loop continues.
Detector: insightface SCRFD (buffalo_l detection model only) on **CPU** (your answer on 2026-10-06: onnxruntime in the Core venv has no CUDA provider; small model, added to the CPU-allowed list with the others). ~88 ms per poll, 2.5 s one-off load. Sanity: finds 6 faces in the sample photo bundled with insightface; live camera polls returned 0 faces (nobody in view at test time). CPU, so it never touches `gpu.run`: the old "CUDA operation not permitted when stream is capturing" presence errors (4,302 of them) are gone by construction.
Cost: ~0.09 s of one core per 10 s = ~0.9 %, one frame in memory.

## Update (same day): 7 s / 49 s, camera index, frozen frames
- Poll every **7 s**, absent after **49 s** (= 7 polls exactly), Vatsal's call.
- `camera.py` also treats a **frozen** camera as dead: two bit-identical frames in a row (a real sensor always has noise). `--probe` after the lens was uncovered: index 0 (1280x720) and index 2 (640x480) are frozen virtual cameras; **index 1 (2560x1440, PHOCUS 40A) is the real webcam** and was the flat "dead" one earlier because the lens was covered. Old FRED's "index 0" is out of date: `camera_index` is now 1. (The listed devices are PHOCUS 40A and EOS Webcam Utility; the old iBall is not present.)
- Live with the real camera: snapshot ~1.0 s (open + warm-up), face detect ~88 ms, **2 faces counted** every poll (someone or a picture besides the user was in view).
- `python senses/presence.py --watch` prints faces per second for 40 s (numbers only) to test turning away / looking down.

## Not done / next
- Presence 8-hour soak (the P1 gate line) belongs to P8; run it once the engine is wired.
- Look-through-camera tool (snapshot + vision describe) is P3 (tools over the P1 events).

## Face references imported (13 static : 7 dynamic), pose test
- `senses/face_store.py`: two tiers, static 13 (deliberate, never auto-changed, `add_static` refuses past the cap) and dynamic 7 (FIFO of recent confident matches). Stored at `<FRED_DATA>/face_refs.json`, outside the repo, never logged or put in an event. No matching code yet (identity comes after presence is tuned to Vatsal's expectations).
- Old FRED's file (20 base, 0 hard, 30 dynamic, 512-d ArcFace) was imported with `python senses/face_store.py --import-old <old file>`: static = the 13 most DIVERSE of the 20 base shots (farthest-point sampling, so 13 different looks rather than near-duplicates), dynamic = the 7 most recent of the 30. Only these 20 are kept: the unused old ones (7 base, 23 dynamic) were deleted at Vatsal's request. The old dynamic tier was meant to cap at 15 but held 30.
- Pose test (`--watch`, 40 s, Vatsal looking down): faces counted stayed at 2 for the whole 40 s (main face score 0.73-0.87, box ~470x610 px; a second, small detection at the right edge, ~150x175 px, score 0.86). So looking down did not lose the face at that distance. The second detection means presence is currently "someone", not "Vatsal": another person in view keeps it present after Vatsal leaves, which is what the identification step is for.
