# Reacting to what is on the screen (design, 2026-10-11, not built)

Goal (Vatsal): when a deadline shows up on the screen, FRED notices and asks about it unasked ("I see the physics assignment is due Friday. Want a reminder?").

## What already exists (reuse, build nothing new for these)
- `senses/screen.py` emits `screen.settled` when the picture stops changing; `senses/ocr.py` reads text from a frame.
- `proactive/scamwatch.py` is the pattern: on `screen.settled`, OCR on a worker thread, a code-based check (not a model's opinion), cooldown per item, a toast or spoken line, nothing but the verdict logged.
- `memory_bridge/ask.py` (AskDelivery): FRED asks a question and the next answer is routed back to it, not treated as a new turn.
- `proactive/scheduler.py` and the `reminder` tool: reminders and timers; `memory`: stores facts.
- `proactive/judge.py` (shadow mode): decides whether speaking up now is worth it.

## Design: a "deadline watch", a second screen reactor next to the scam watch
1. **Detect (code, no model):** on `screen.settled`, OCR the frame; find date phrases (14 Oct, Oct 14, 14/10, Friday, tomorrow, in 3 days, 11:59 pm) that sit within a few words of a cue (due, deadline, submit by, last date, closes, exam on, ends). A cue with no date, or a date with no cue, is not a deadline. The line of text around it becomes the candidate (title = that line, trimmed).
2. **Filter:** skip dates already past; skip a candidate FRED already handled (stable key = normalised line + date, kept in a small file in `FRED_Private`); one ask per candidate per day at most; never while FRED is speaking, while Vatsal is mid-sentence, or when a night job holds the GPU.
3. **Ask:** one short spoken question through the ask queue, with the pill caption: "I see 'Physics lab report due Fri 14 Oct'. Want a reminder?" Vatsal answers by voice: yes / no / "remind me Thursday evening" / "not this one".
4. **On yes:** create a reminder through the scheduler (default: the evening before at 18:00, plus the morning of at 08:00) and store a memory event (type "deadline", importance 0.6) so the news digest and recall can mention it. **On no / "not this one":** remember the key so it is never asked again. **No answer within 20 s:** drop the question quietly, ask again at most once later the same day.
5. **Safety:** OCR text is judged in memory only; the log keeps the key hash and the date, never the page text or pixels. The ask text quotes one line from the screen and is spoken, so it is the only thing that leaves the detector, and it comes from the code, not the model.
6. **Later reactors on the same hook (not now):** meeting links, an unanswered question in a chat window, a battery/low-space warning.

## Decisions for Vatsal
- Which screens count: everything, or only school and work windows (browser tabs on the school site, the PDF viewer, the email client)? Default: everything, with a per-window block list.
- Voice or silent: spoken question, or only a toast with a yes/no button? Default: spoken (it asked "ask me about it").
- Reminder defaults: evening before at 18:00 and the morning of at 08:00?
- Privacy line: OK to OCR the whole screen continuously? (The scam watch already does.)

## Decisions (Vatsal, 2026-10-11)
- Which screens: everything. Continuous OCR of the whole screen is fine (already how the scam watch works).
- The question is spoken AND shown as a toast with yes / no buttons.
- Reminder defaults: the evening before at 18:00, and the morning of at 06:00 (not 08:00); on a weekend the morning one is at 12:00 noon.
- Timing: this belongs to the NEXT VERSION, not the current build. Nothing is built; the design above stands.
