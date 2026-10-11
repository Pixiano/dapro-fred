# Better local speech recognition (research, 2026-10-11, nothing switched)

Problem: Whisper large-v3-turbo (faster-whisper, beam 5) mishears names and short phrases in Vatsal's Indian-accented English at a lavalier mic ("PM Modi" -> "PM1", "the BMW", "E.M.O.D."; "Hey Fred" -> "Be afraid").

## What Wispr Flow and Typeless are
Cloud dictation products: speech recognition (partly their own model, plus a Whisper-family subprocessor) followed by an LLM that cleans the text (fillers, punctuation, tone). Wispr processes audio on its servers; Typeless's claims about on-device processing conflict between sources. They are not local models you can drop in; what is reusable is the idea: good ASR, then an LLM cleanup pass. Local equivalents exist (OpenWhispr, Handy, Amical, VoiceInk), all built on Whisper or Parakeet.

## Open models reported better than Whisper large-v3-turbo on English (leaderboard word error rate, general English, not Indian-accent specific)
| Model | Size | Reported WER | Note |
|---|---|---|---|
| IBM Granite Speech 4.1 | 2B | 5.33 | top of the Hugging Face Open ASR leaderboard in one tracker |
| Cohere Transcribe | 2B, Apache 2.0 | 5.42 (vendor claim) | 6.39 on Indian-accented English in a vendor test |
| NVIDIA Canary-Qwen | 2.5B | 5.63 | speech encoder + LLM decoder |
| Qwen3-ASR | 1.7B | 5.76 | multilingual |
| Voxtral Mini | small | n/a | 6.44 on Indian-accented English in the same vendor test |
| Whisper large-v3-turbo (current) | ~0.8B | ~7.75 | |
Parakeet-TDT-0.6B-v3 is the speed pick (25 European languages), not an accuracy leader. Rankings differ between sources and move monthly; the vendor numbers are marketing.

## Honest conclusion
- No published result settles Indian English at a lavalier mic. The gap between models on benchmark audio is usually smaller than the gap between benchmark audio and Vatsal's audio.
- Cheapest wins first (done today): Whisper hotwords and news-derived vocabulary. Next cheap step: the full large-v3 instead of the turbo (more accurate on accents, slower).
- A fair test needs ground truth: about 30 to 40 sentences read aloud by Vatsal into the lavalier (names: Modi, Manipur, Bihar, CBSE, NCERT, Anthropic, Claude, FRED, "Hey Fred"), then word error rate for Whisper large-v3, large-v3-turbo with hotwords, Cohere Transcribe, Granite Speech 2B and Canary-Qwen on the same clips. Only the winner would be wired in (a model slot behind the existing `Stt.transcribe` socket), and any loser deleted. GPU memory: all fit alongside the current stack only one at a time, so the test runs standalone.
- Local "Wispr-style" cleanup is already half there: the 4B model can clean a transcript, but fixing a misheard name needs the right audio model, not cleanup.

## Handy's models (screenshot from Vatsal, 2026-10-11)
Vatsal used two of these in Handy and found them better than FRED's Whisper: Parakeet Unified EN 0.6B (English, streaming), Nemotron Streaming 3.5 (28 languages, streaming), Canary 180M Flash (tiny, 4 languages, translate). All three are NVIDIA NeMo models, not Whisper; they join the bake-off (Parakeet Unified first: English-only, small, fits beside FRED).

## Test results (2026-10-11, 40 recent FRED clips, no ground truth, all on GPU, nothing wired in)
- Parakeet Unified EN 0.6B, int8 and full precision: same result both ways. 11 to 12 of 40 clips empty (including clean ones like "Thank you very much"), final words dropped ("What's the name?" -> "dame"), no punctuation, numbers as words. "PM Modi" -> "PM Bodhi" (closer in sound than Whisper's "PM1"). "Hey Fred" 2 of 4.
- Canary 180M Flash (NeMo, 1.4 GB VRAM, 64 to 199 ms per clip): 17 of 40 empty or a literal `<|endoftext|>.` string; "Hey Fred" 2 of 3; "PM Modi" -> "P. M. Body".
- Observation: both models go empty or drop endings on largely the same clips Whisper handled. That points at the clips (level, start/end clipping of the recorded utterance), not at the model. Next check: peak level and the last 0.5 s of those clips before trying any other model. Canary venv: C:\Users\Dhiraj Vatsal\stt_bakeoff_canary (delete when finished); Parakeet scratch: C:\Users\Dhiraj Vatsal\stt_bakeoff.
