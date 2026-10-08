Progress snapshot at about 35% (P0 to P3 built, 2026-10-06). The P0 to P1 detail is in `20_percent.md` and is not repeated here.

## The idea
Old FRED was one big program where listening, looking, thinking and tools all knew about each other. The rebuild keeps every layer separate, each with a clear input and output and its own self-check. With P3 the loop closes for the first time: you can speak or type, the brain picks tools, and a spoken-style answer comes back (as text for now; the voice is P4).

## P2: knowing when you are done talking
- **Turn clock:** a 160 ms clock decides one of five things: keep listening, end of turn, barge-in, backchannel (a "mm-hm" that is not a turn), or ignore.
- **Hard cap:** the end of a turn is declared at 0.9 s of silence at the latest.
- **Smart Turn v3.2** (on the CPU) judges from the audio whether the sentence is finished, so a pause to think does not cut you off.
- **Voice ID:** the speaker check exists but nobody is enrolled. I did not train it; better recordings are coming from you.

## P3: the thinker
- **How a request is handled:**
  - The request is sorted by cue words first, then by meaning, down to 2 to 6 candidate tools out of 44. Chat and social phrasing gets no tools.
  - The brain (Qwen3.5-4B) calls a tool or answers.
  - Tools whose result is already a spoken sentence skip the second model round, which saves time.
- **Safety rules in the loop:**
  - Arguments are converted before each call ("50" becomes 50).
  - An identical failed call is refused with the earlier error. After two failures a tool is stopped and the model is told.
  - Limits: 4 rounds and 4 calls per message.
  - If the model asks for a tool outside its menu, the menu widens to that tool's group.
  - Destructive tools (delete, overwrite, kill, power, close window, forget) ask a yes/no question first, naming the real target. Nothing runs until the next turn says yes. "Yeah go ahead" counts; "no wait yes" and "yes... actually no" do not.
  - After reading untrusted content (a file, screen, camera, web page), a tool that sends something out is refused in that same turn.
  - Guards: leaked tool syntax is regenerated once; "Done" claims with no matching tool are caught; a failed tool must be admitted in the reply; if the model says "I can't" while it has tools for the job, it is reminded once.
- **The 44 tools, 18 groups:**
  - Time and maths: time, calculator, days until.
  - Volume and system status.
  - Files: read, list, create, overwrite and delete (delete moves to a trash folder), bounded name search. Your private folder is off limits and system folders are not writable.
  - Windows: list, switch, close.
  - Reminders: timer, reminder, alarm, repeating reminder. They are stored but fired later by the proactive layer (P6).
  - System: brightness, media keys, clipboard, processes, kill process, screenshot, network status, lock, shutdown/restart (10 s countdown, cancellable).
  - Apps: open apps, websites (http/https only) and paths (executables blocked).
  - Vision: describe the screen or look through the camera, only when asked.
  - Agenda: add, list, complete, delete.
  - Self-description (built from the real tool list, so it cannot invent abilities).
  - Memory: show what it knows, remember, correct, forget, correct a name, recall recent conversation.
- **Memory bridge:** turns, tool calls, speech, perception and errors go into the memory archive on their own streams. Each turn gets your personal model; the deeper search runs only when you refer to the past or a known name, and an interim "digging deeper" line is spoken. An unsure name asks "Do you mean X?". If nothing is found it asks for a keyword instead of guessing. Test result: recall changes the answer, and turns land in the archive.
- **Live voice loop:** it runs on the real mic. In the replay test 4 of 4 commands worked, with the reply 0.6 to 2.0 s after the audio ended.

## Results (old test cases, live Qwen3.5-4B)
- **Tests:** all 47 self-checks and tests pass. That includes 16 engine scenarios, one for each loop rule.
- **Tools suite (81 cases):** 56 pass overall (69.1%). Of the 61 cases whose tools exist, 56 pass (91.8%). The old baseline was 77.8%, but it counted missing tools against the score; here 20 cases need tools not built yet.
- **Conversation (21 cases):** 19 pass (90.5%), against an old baseline of 76.2%. It moved between 16 and 19 across runs, because the brain is not deterministic.
- **Safety:** 8 of 8. **Confirmation:** 8 of 8.
- **Still failing:**
  - "What's hogging my CPU" picks system status instead of the process list.
  - A Hinglish "remind me" picks the alarm tool.
  - The weather cases (no weather tool).
  - A bare "what can you do" tool call sometimes fires on an ambiguous chat turn.
  - One injection case lists a folder before refusing.
  - One maths case where the model words the answer wrongly.
- **Earlier failure, now gone:** "how many days until Christmas" was answered from imagination until a days-until tool existed.
- **Weak spots to keep watching:** the model sometimes answers a read question without calling the tool, and a "how do I set a timer on my phone" question once fired a timer.

## What this means for real-life FRED
- **It can now:** hear you, know when you are done, think with tools, remember and recall, and answer in text. All of it runs in one loop on the real microphone.
- **Not yet:**
  - No voice replies (P4), no UI (P5).
  - Nothing proactive (P6) and no nightly memory jobs (P7).
  - No 8-hour soak (P8).
  - Weather, mail, phone, WhatsApp and smart-AC tools are deferred pending a scope decision.
  - FRED is not restarted; the old program stays down until the revamp is done.
- **Next decision:** you want to revamp the whole tool set from zero, using the old tools and numbers as reference. That is the next discussion.
