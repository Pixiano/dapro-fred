I deleted the unused old face references, so only the 20 in use remain. The summary of everything up to P1 is in `Mark_I/Documentation/2026/10/06/core_summary_p0_p1.md`, committed and pushed as `2ff5c5f` (22/22 self-checks pass). It is reproduced below.

## The idea
Old FRED was one big program where listening, looking, thinking and tools all knew about each other. That's how a camera glitch ended with the model "describing a black frame", and how presence and speech-to-text crashed each other on the GPU. The new Core is built as separate parts that only talk through one event bus. A part announces "wake word heard" or "screen changed", and anyone interested listens. Each part has its inputs and outputs written at the top of its file and one runnable self-check.

## P0: the plumbing
- **Settings:** every number lives in one place and can be overridden by environment variable. Private data lives in `~/FRED_Private`, outside the repo.
- **Logs:** the session log never crashes, and test output goes to a temp folder. Old FRED's real log had test noise in it.
- **GPU queue:** in-process GPU work waits in one line with priorities: a waiting turn first, senses next, background last. This fixes the 4,302 logged CUDA errors.
- **Model client and tools:**
  - One way to talk to the playground, with errors in four kinds only.
  - A parser for the different tool-call formats.
  - A tool runner that checks arguments and converts types ("50" becomes 50) first, and never crashes the loop.
- **Playground:** it now has four model slots (chat, embed, talk, vision), each on the GPU. A "window" mode borrows the whole GPU for a big job and restores it afterwards. A bug that silently dropped vision on an already-loaded model is fixed.
- **Setup:** two Python environments, one for Core and one for memory. The brain is Qwen3.5-4B only for now.

## P1a: hearing
- **Microphone:** one part owns it and hands out 80 ms pieces. Recordings can be replayed through the same path for testing.
- **Wake word:** the frozen model, unchanged. On your labelled clips with no tuning, it fired on 78.6% of your 131 real "Hey FRED" clips. It also fired on 37.5% of the hard negatives (clips saved around real fires and cancels). Real tuning waits for the recording plan.
- **Speech detection:** marks when you start and stop talking.
- **Speech to text:** Whisper on the GPU takes about 0.2 s for a short command. It got 3 of 4 test sentences exactly right. The fourth wrote "thirty percent" instead of "30%". On the CPU it gives the same text but takes about 4 s, so the GPU stays.
- **Hotkey:** Left Ctrl + Left Alt, hold to talk.

## P1b: seeing the screen
- **Watching:** a screenshot every 0.2 s costs about 2.5% of one CPU core and no GPU. A flash of 0.2 s or longer is always seen, and two changes 0.3 s apart stay separate.
- **Change detection:** the screen is cut into a grid, and a sha256 fingerprint skips unchanged frames. A `settled` event fires 0.5 s after the screen stops moving, flagged as a duplicate if the screen is back to how it was.
- **Reading text:** Windows OCR on the CPU takes 15–50 ms per crop. On 12 complex invented screens, 76% of phrases were exact and 88% with a small tolerance. It's perfect on tables, chat and slides. It's weak on text on thumbnails, tiny grey diagram text, dark code and terminals, and long URLs.
- **Understanding the screen:** the vision model describes or answers in about 1 s. It passed 11 of 12 test screenshots. The miss is adding up a column, so arithmetic belongs to the brain. A grounding rule replaces any quote or number that OCR didn't find with "(unverified text)", so it can't invent an error message.
- **Glance:** frames are taken at the start of your speech, every 3 s, and at the end. They're described while you're still talking, so the answer is ready when your words are.
- **Policy for P3:** OCR on every settled, non-duplicate change. The vision model runs only when asked, or when a large area changed with little text.
- **`localhost` penalty:** it adds 2 s per request on this PC, versus 0.05 s for `127.0.0.1`. Core now uses `127.0.0.1`, but the frozen memory scripts still use `localhost`.

## P1c: camera and presence
- **Camera:** one picture per request, and the camera is released at once. A dead picture (black, flat, or frozen) gets one retry, then a fault event. It never hands the model a black frame. The real webcam is index 1; indexes 0 and 2 are frozen virtual cameras.
- **Presence:** every 7 s, about 88 ms of CPU. It reports present on the first face and absent after 49 s without one. Looking down for 40 s kept your face detected.
- **Limits:** it says "someone", not "you". A second person in view keeps it present after you leave, which identification is meant to fix.
- **Face references:** 13 static and 7 dynamic, stored outside the repo and never logged. There's no matching code yet.

## What this means for real-life FRED
- **It can now:** hear you, know when you speak, and transcribe quickly. It can see what changed on screen, read the text and describe it, and know whether someone is at the desk. Camera and GPU faults are handled instead of crashing.
- **Not yet:**
  - No brain loop (P3), no voice replies (P4) and no UI (P5).
  - No memory or proactive layers (P6–P7).
  - It isn't one running program yet, and there's been no 8-hour soak (P8).
  - Smart Turn and voice ID come with P2, and the look-through-camera tool comes with P3.
- **Open items:** wake-word accuracy and presence identification.
