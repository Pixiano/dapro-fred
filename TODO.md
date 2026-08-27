# TODO

- **Empty-room YOLO person-detection control** (`Core/input/presence.py`,
  `_frame_has_person`/`PRESENCE_YOLO_PERSON_CONFIDENCE`) — skipped 2026-08-27
  during verification because family was in the camera background (a real
  person in frame would correctly trigger detection, not give a clean
  negative). Run once the desk area can be genuinely empty: step fully out
  of frame, capture, confirm YOLO reports no `person` above
  `PRESENCE_YOLO_PERSON_CONFIDENCE` (0.5). Real no-face-with-person sample
  already confirmed (0.925 confidence); this is the missing negative
  control.
