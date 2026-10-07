# Add-on skills: design and decisions (2026-10-07)

Add-ons live in `Core/skills/extra/<group>/<skill>/` (same folder layout as the built-in set; a broken add-on is skipped and listed, never stops start-up). One skill per area with an `action` argument where it fits (as `volume` and `system_stats`), so the menu the brain sees stays small. Each skill carries `refuse_if` patterns for the neighbouring jobs the 4B brain confuses it with, a self-check, and its old-log labels become routing tests.

Shares below are of 835 logged requests in the old FRED.

| Category | Skills | Old share | Status |
|---|---|---|---|
| audio | `audio_device` | 16.6% | built (see below) |
| screen | `look_at_screen`, `look_with_camera` | 8.2% | proposed |
| lockdown | engine mode, not a skill | 8.0% | built (see below), 2B swap waits for P4 |
| files and vault | `files`, `vault` | about 8% | proposed |
| apps | `open_app`, `close_window` (yes/no first) | about 5% | proposed |
| web | search, weather, Wikipedia, news, places and maps, currency and stocks | about 5% | proposed |
| agenda | `agenda` (add, list, done, today) | about 4% | proposed |
| reminders | `reminder` | about 7% | skipped until P6 (decided) |

Skipped on purpose: `restart_fred` (FRED stays down until the revamp ends), `power_action`, phone calls (scope still open).

## Decisions (Vatsal)
- **Lockdown** blocks all tools except the basic ones (time and similar) and keeps chat; the model context is cleared; CPU work, proactive and routine checks keep running. While locked, only the **2B talker model** runs (the 4B brain is unloaded): saves VRAM, nothing needed is lost because there are no tools. Disengage reloads the brain. Still to decide: how it is released (voice ID is off, so a spoken code alone is weak) and whether the per-turn automatic memory lookup is also blocked.
- **Reminders**: skipped until the proactive layer (P6).
- **Web**: everything except YouTube.
- **Vault**: FRED gets full access (read, search, write with a yes/no before write or delete). Reading uses the same untrusted-content rule as web pages: after reading, a tool that sends something out is refused in that turn.
- **Audio**: scope Windows default + FRED; matching by name and meaning, memory of what the user called it, elimination, and asking when unsure; ask before remembering a name; headsets switch speaker and microphone together; a device that is not connected is named and left alone (no Bluetooth connecting).
- Questions to the user go through the multiple-choice mechanism (answers separated by ";").

## audio_device (built)
`skills/extra/audio/audio_device/`. Windows Core Audio through pycaw lists every endpoint with its state; paired Bluetooth devices that are not connected show as `unplugged`, ghosts of old hardware are ignored. The default device is set with IPolicyConfig for the console, multimedia and communications roles. A headset group's stereo output is chosen over its hands-free twin; its hands-free capture endpoint is the microphone.
Matching (`resolve`, pure): remembered alias +3; each informative word shared with the device name +2; embedding similarity (when the runtime passes `embed`) up to +2; category agreement +1.5 (headphone words vs a headset-like name, speaker words vs "Speakers", monitor words vs a display-like name), contradiction -1.5; generic wording +1.5 for every candidate (the current device then gets -1, so "the other mic" is elimination); "new" favours a device never seen before (+0.5). Accepted at 1.5 with a lead of 1.0, else it asks "Which one: A or B?" (the next request with the name finishes it, no hidden state). One connected device whose name says nothing is asked about ("Do you mean X? Yes or no?") through the engine's yes/no slot, never switched to on category wording alone. After a switch that was not picked by an explicit name or an existing alias it asks "Should I remember that <word> means <device>?"; yes saves `audio device alias: <word> = <device>` as an ordinary, visible personal-model item.
Engine socket added for this: `registry.confirmer` (the engine's Confirmer), so any skill can ask a yes/no whose "yes" runs a call it stored. `skills.loader.build(recall, **shared)` passes `mic`, `bus`, `embed` to `bind(ctx)`; a switch also reopens FRED's mic on the new input and emits `audio.device_changed`.
Tested with a fake backend and invented names (63/63 self-checks; 12 skills, 78 examples all route to their own skill with the real embedder; live brain on a fake backend). The real Windows layer was checked read-only plus a no-op "set the current default again". Lesson: a first probe of mine really switched the real microphone before I stubbed the backend; it was restored at once, and real-device experiments are now no-op or fake only.

## Embedder: EmbeddingGemma-2 side-port result
On llama.cpp b11462 (CUDA 13.4, in `C:\Users\Dhiraj Vatsal\FRED_embtest\`, outside the repo) the model loads on the GPU, 768-d, about 0.5 GB for text and 1.1 GB with the multimodal projector. It embeds text, images and audio into one space; working request shapes: image `{"input":[{"content":[{"type":"image_url","image_url":{"url":"data:image/png;base64,..."}}]}]}`, audio `{"input":[{"content":[{"type":"input_audio","input_audio":{"data":"<b64 wav>","format":"wav"}}]}]}`, text as plain strings in the same list; bare parts return 400. Image and audio queries matched the right items in a small invented test (about 45 ms per image; audio is flagged experimental by the server). Text: on the old 101-example set (the test used the legacy 44-tool registry, not the 11 skills) routing leave-one-out was better than Qwen3 (66 vs 54 of 101) but the memory thresholds separate worse (precedent gap +0.033 vs +0.131, MIN_SIM +0.030 vs +0.101), and scores sit in a compressed high band, so every cosine threshold would need retuning. 256-d truncation is nearly free. Verdict: switch later, when multimodal memory is actually built; then retune. Core's `Embedder` would need `{"content":[...]}` inputs and per-use prefixes (query, document, symmetric).

## Lockdown (built, `engine/lockdown.py` + hooks in `engine/loop.py`)
Say "lockdown" (or "go into lockdown", "lock fred down") to engage; it is matched on the words, not by the model. While locked: the registry hides every tool except `config.lockdown_tools` (time, days_until, calculate), so a blocked tool is neither offered nor callable; the conversation and any pending yes/no are cleared; no memory lookup, no memory-graph feed (no bus turn events), no history. Locked chat is kept only as an embedding in `<data dir>/lockdown_vectors.jsonl` (no text, apart from the memory graph) so a later job can promote what mattered. The flag persists across restarts (a bad file means unlocked). Release is "unlock <passphrase>" or "end lockdown" when no passphrase is set; the old FRED used a PIN (hard-coded 1111, plus a stranger-detection auto-lock lifted by a yes and the PIN), so a digit PIN works as the passphrase here: `python -m engine.lockdown set` stores a salted hash outside the repo. A spoken passphrase is never logged or remembered. Not yet: unloading the 4B brain and answering from the 2B talker while locked; `Engine(lock_chat=...)` is the socket and the playground unload/reload goes with the talker in P4. The old stranger-detection auto-engage belongs to P6.
