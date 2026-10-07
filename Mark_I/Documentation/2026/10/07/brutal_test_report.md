
## Follow-up: voice ID switch
`voice_id_enabled` (config, default False; `FRED_VOICE_ID_ENABLED=1` turns it on). Off: `VoiceId.score` / `is_me` return None (unknown = pass) and nothing is embedded; enrolment and stored references are untouched. It stays off until the gate is trained on the captured live-mic recordings. `tests/voice_eval.py` enables it for measuring.
