# Usage data store (live runs, from 2026-10-08)

Everything that really happens in a live run is saved for later tuning and training: wake word, ignored utterances, transcripts, turns, speech, presence. One central module, `Mark_I/Core/usage.py`.

## Where
`Project_FRED/Data/` (git-ignored: it holds real voice and speech). By component, then year, month, day:

```
Data/<component>/<YYYY>/<MM>/<DD>.jsonl               events of that component, one JSON per line
Data/<component>/<YYYY>/<MM>/<DD>/<HHMMSS_mmm>.wav    audio clips (16 kHz mono)
```

A clip has a line `{"kind": "clip", "file", "audio_s", "peak", ...}` in the same day's jsonl.

## What goes in each component
| Component | Events (jsonl) | Clips (wav) |
|-----------|----------------|-------------|
| `audio` | wake, wake_near (scores), vad, hotkey, mic state | none |
| `wakeword` | none | the last 2 s of mic on every wake and every near-miss, with the score |
| `clock` | every state: LISTEN, ADDRESSED, END_OF_TURN, BARGE_IN, BACKCHANNEL, IGNORE with its reason | every ignored utterance, with the reason (`not_addressed`, `not_vatsal`) |
| `stt` | every transcript, partial and final | every final utterance, with its text |
| `engine`, `tts`, `presence`, `camera`, `proactive`, ... | their events | none |

Skipped: `screen.change` (700 a minute, only a box). Past 400 clips per component and day (`usage_clip_cap`) clips stop, events keep going. The wake clips are raw mic audio before gain.

## Notes
- The older per-day event log (`FRED_Private/events`) and `voice_data` stay as they were and are no longer written for speech clips; earlier recordings remain in `FRED_Private/voice_data`.
- Tests write to a temp folder, never to `Data/`. Live runs switch to `Data/` at start (`usage.use_repo_data()`); `FRED_USAGE` overrides the folder.
- Check: `python usage.py`.
