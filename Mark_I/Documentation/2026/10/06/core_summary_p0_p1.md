# Core rebuild so far (P0 and P1), in plain terms

Where: `Mark_I/Initial/Core/`. Done and pushed: P0 foundations, P1a hearing, P1b seeing the screen, P1c camera and presence. Next: P2 (the clock). The old FRED is untouched and stays down; nothing here is wired to the memory pipeline or the UI yet.

## The idea in one paragraph
Old FRED was one big program where listening, looking, thinking and tools all knew about each other, which is why a camera glitch could end up with the model "describing a black frame", or presence and speech-to-text could crash each other on the GPU. The new Core is built as separate parts that only talk through one **event bus** (a part announces "wake word heard", "screen changed", "person present"; anyone interested listens). Every part has its sockets (what goes in, what comes out) written at the top of its file and one runnable self-check. All 22 checks pass.

## P0: the plumbing (nothing visible, everything depends on it)
- **Settings**: one place for every number (thresholds, periods, caps), changeable by environment variable. Private data (face references, logs) lives in `~/FRED_Private`, outside the repo.
- **Logs and events**: a session log that never crashes the program; tests write to a temp folder so test noise cannot pollute the real log (old FRED's log had `boom` and `camera exploded` test output in it).
- **GPU queue**: anything that runs on the GPU inside Core waits in one line with priorities (a waiting turn first, senses next, background last). This is the fix for the 4,302 logged "CUDA operation not permitted" errors.
- **Model client and tools**: one way to talk to the playground (chat, embeddings, vision, "slots"), errors only in four kinds, a parser that understands the different tool-call formats, and a tool runner that checks and coerces arguments ("50" becomes 50) before any tool runs and never crashes the loop.
- **Playground upgrade**: it now has four slots (chat, embed, talk, vision), each its own model process fully on the GPU, plus a "window" mode to borrow the whole GPU for a big job and restore afterwards. A bug where asking for an already-loaded model silently dropped its vision ability was fixed.
- **Two Python environments**: `Core/.venv` (everything) and `memory/.venv` (memory only). Brain for now: Qwen3.5-4B only.

## P1a: hearing
- **Microphone**: one part owns it and hands 80 ms pieces to whoever needs them; a recording can be replayed through the exact same path for testing.
- **Wake word** ("Hey FRED"): the frozen model, read in place and never swapped. Replayed on your labelled recordings with no tuning: it fired on 78.6% of your 131 real "Hey FRED" clips, but also on 37.5% of the hard negatives (clips saved around real fires and cancels). This is the model as it is; real tuning waits for the recording plan.
- **Speech detection (VAD)**: marks when you start and stop talking (200 ms hangover; the smarter end-of-turn check comes with P2).
- **Speech to text**: Whisper large-v3-turbo on the GPU, about 0.2 s for a short command, error-free on 3 of 4 test sentences (the fourth only wrote "thirty percent" instead of "30%"). On CPU it gives the same text but takes about 4 s, so GPU stays. No silent fallback: a failure is an error you can see.
- **Hotkey**: Left Ctrl + Left Alt hold-to-talk.

## P1b: seeing the screen
- **Watching**: a screenshot every **0.2 s** costs about 2.5% of one CPU core and no GPU. Every screen flash of 0.2 s or longer is seen, and two changes 0.3 s apart stay separate (at 1 s polling, a short toast is missed more often than not).
- **Deciding something changed**: the screen is cut into a 64x36 grid; a tile that moves by more than a tiny amount counts. An exact fingerprint (sha256) skips unchanged frames for free. Changes are grouped: a `settled` event fires 0.5 s after the screen stops moving, and is marked `duplicate` if the screen is back to how it looked before (a toast came and went).
- **Reading text (OCR)**: Windows OCR on the CPU, 15-50 ms per crop. On 12 invented complex screens (video sites, search results, diagrams, code, tables, chat, terminal) it read 76% of phrases exactly and 88% with a small tolerance: perfect on tables, chat and slides; weak on text drawn on thumbnails, tiny grey text in diagrams, dark code and terminals, and long URLs.
- **Understanding the screen**: the vision model (Qwen3.5-4B) answers a question or describes the screen in about 1 s. It passed 11 of 12 test screenshots; the miss is adding up a column (it misreads a digit), so arithmetic belongs to the brain. A **grounding rule** drops any quoted text or number the model claims to see that OCR did not find ("(unverified text)"), so it cannot make up an error message.
- **Glance**: while you talk, frames are taken at the start, every 3 s, and the end, and described in parallel while you are still speaking, so the answer is ready when your words are. Identical frames are not described twice.
- **Policy for P3**: OCR on every settled, non-duplicate change; the vision model only when asked (or when a big area changed with little text, such as a diagram or video).
- A finding that changed earlier numbers: `localhost` costs a flat 2 s per request on this PC; `127.0.0.1` costs 0.05 s. Core uses `127.0.0.1` now. The frozen memory scripts still use `localhost`.

## P1c: camera and presence
- **Camera**: one picture per request, camera released at once, never a held feed. A dead picture (black, flat "no signal", or frozen: two identical frames in a row) gets one retry, then a fault event; it never hands the model a black frame. The real webcam is **index 1** (the 2K PHOCUS 40A); indexes 0 and 2 are frozen virtual cameras. Windows may renumber after a reboot: `python senses/camera.py --probe` checks.
- **Presence**: every **7 s** a snapshot, a face counted by a small CPU detector (about 88 ms per poll, under 1% of one core). Present on the first face; absent after **49 s** without one. Looking down for 40 s kept your face detected. A camera fault changes nothing by itself.
- **Limits today**: it says "someone", not "you": a second person in view keeps it present. Identifying you is the next step, once presence behaves as you want.
- **Face references**: 13 static + 7 dynamic (the 20 imported from old FRED: the 13 most different base shots and the 7 newest dynamic ones). Stored outside the repo, never logged. No matching code yet. Later security upgrades: liveness check, stricter multi-frame matching for security actions, no automatic additions for those, voice + face together, encrypted storage.

## What this means for real-life FRED
- It can hear you, know when you speak, and transcribe fast on the GPU; it can see what changed on the screen, read its text and describe it, and know whether you are at the desk, with fixed fault handling instead of the old crashes.
- Not yet: no brain loop (P3), no voice replies (P4), no UI (P5), no memory/proactive layers (P6-P7), nothing running as one program, no 8-hour soak (P8). The wake word accuracy and the presence "who" question are known open items.
- Also not done: Smart Turn and voice ID (P2), the look-through-camera tool (P3).
