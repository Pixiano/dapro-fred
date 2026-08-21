# FRED Voice Pipeline — Rebuild-Grade Reference

This document covers everything under `Core/audio/`, `Core/input/`, and the
controller that wires them together, `Core/ui/pill_app.py`. It is written so
a rebuild can reproduce not just the code shape but the reasoning that
produced it — several of these decisions were arrived at by shipping the
"obvious" version first, hitting a real failure in live use, and fixing it.
Re-implementing the obvious version without reading this will reintroduce
those bugs.

## 0. The two-mode split: why CLI and GUI use entirely different engines

FRED has two independent input/output stacks that never mix:

| | CLI mode (`Core/main.py`) | GUI mode (`Core/ui/pill_app.py`) |
|---|---|---|
| Trigger | (script-driven, no hotkey) | Left Ctrl+Left Alt hold, or "Hey FRED" |
| STT | `audio/stt.py` — Vosk (`vosk-model-en-in-0.5`, Indian-English-tuned, 1.5GB) | `audio/stt_whisper.py` — faster-whisper (`large-v3-turbo`) |
| TTS | `audio/tts.py` — Windows SAPI via `pyttsx3` | `audio/tts_kokoro.py` — Kokoro ONNX, sentence-streamed |
| Endpointing | VAD silence gaps inside Vosk's `KaldiRecognizer` | The hotkey **release itself** is the endpoint |

The split is not accidental duplication — it's a direct consequence of how
each mode learns "the user is done talking":

- **CLI/Vosk**: there's no keypress to mark start/end, so Vosk's own
  `AcceptWaveform` streaming + partial-result mechanism has to guess the
  utterance boundary from the audio itself. Streaming partials is a
  Vosk-specific capability CLI mode leans on for this reason.
- **GUI/Whisper**: hold-to-talk means the key-up event *is* the boundary —
  no guessing needed. Whisper has no streaming/partial-result mode at all,
  but that's fine here because nothing in the pill UI displays partials;
  the finished transcript appears only after release. This is explicitly
  called out in `stt_whisper.py`'s module docstring: "Hold-to-talk changes
  what the right STT engine is... here the key release *is* the endpoint,
  so there is nothing to guess." Whisper is also simply more accurate than
  Vosk on the same audio, which only matters once you have a clean,
  pre-segmented clip to hand it — exactly what hold-to-talk provides for
  free.
- SAPI (`pyttsx3`) only exposes **word-boundary events**, never raw PCM —
  so CLI's "speaking" feedback (if any) can only ever be word-timed, not
  amplitude-reactive. Kokoro returns real float32 samples, which is what
  lets the GUI pill's waveform pulse to actual RMS instead of a fake
  per-word tick (see `tts_kokoro.py`'s module docstring, point 2).

Do not try to unify these into one STT/TTS abstraction on rebuild — the
engines were picked *because* they fit their respective endpointing
mechanism, not swapped in for unrelated reasons.

---

## 1. `Core/audio/stt.py` — Vosk STT (CLI mode)

`STTManager` wraps Vosk fully offline:

```python
model = Model(STT_MODEL_PATH)            # STT_MODEL_PATH = vosk-model-en-in-0.5
recognizer = KaldiRecognizer(model, STT_SAMPLE_RATE)  # STT_SAMPLE_RATE = 16000
```

`listen_once(timeout=10)` opens an `sd.InputStream(samplerate=16000,
channels=1, dtype="int16", callback=self._audio_callback,
extra_settings=device_info.input_extra_settings())`, pushes raw int16 bytes
into a `queue.Queue`, and blocks in a loop calling
`recognizer.AcceptWaveform(data)` until it returns true (i.e. Vosk itself
decides an utterance is complete), then parses `recognizer.Result()` JSON
for `"text"`.

`list_microphones()` is a simple debug print of `sd.query_devices()`.

Model path note: `STT_MODEL_PATH = BASE_DIR / "models" / "vosk-model-en-in-0.5"`
— not committed to git (>100MB, same convention as Kokoro's model files).

---

## 2. `Core/audio/stt_whisper.py` — faster-whisper STT (GUI mode)

### Class shape: `WhisperSTT`

Two-phase usage, strictly paired: `start_recording()` then
`stop_and_transcribe()` (or `cancel_recording()` to abandon).

**Lazy load, deliberately decoupled from recording.** `sd.InputStream`
needs no model at all, so `start_recording()` opens the mic stream
immediately on hotkey-down while `ensure_loaded()` can run concurrently on
another thread — Whisper's ~3s load time overlaps with the user's speech
instead of stalling in front of it.

```python
self.model = WhisperModel(self._name, device=self._device, compute_type=self._compute_type)
```
Config: `WHISPER_MODEL = "large-v3-turbo"`, `WHISPER_DEVICE = "auto"`,
`WHISPER_COMPUTE_TYPE = "auto"`. `"auto"` prefers CUDA when CTranslate2
(faster-whisper's runtime) can find CUDA+cuDNN itself — **CTranslate2 does
its own CUDA/cuDNN discovery and never touches torch**, so a CPU-only torch
install in the venv tells you nothing about whether Whisper actually runs
on GPU. After load, the code re-reads the *resolved* device/compute_type
off `self.model.model` (the CTranslate2 object) rather than trusting the
requested `"auto"` string — because `"auto"` can silently resolve to CPU,
and that's the difference between a 0.25s and a 4s transcript.

**Warm-up** (`_warm_up`, gated by `WHISPER_WARMUP_ON_RELOAD = True`):
decodes 1 second of zeros through the model once at load time. The first
CUDA transcription in a process pays for context creation/kernel selection
— measured ~14s cold vs ~0.25s warm. Without this the entire 14s cost lands
on the user's literal first utterance and reads as FRED being broken.

**Recording** (`start_recording` / `_callback` / `stop_and_transcribe`):
- `sd.InputStream(samplerate=16000, channels=1, dtype="float32",
  blocksize=1024, callback=self._callback,
  extra_settings=device_info.input_extra_settings())`.
- The callback computes a display-gain RMS (`rms * 12.0`, clamped to 1.0)
  into `self.level`, which the pill polls (`_begin_recording` in
  `pill_app.py`) to drive the listening waveform, and which
  `watch_for_silence` (wakeword.py) polls to detect end-of-utterance for
  wake-triggered turns.
- Two separate locks: `self._lock` (guards the block list / `_recording`
  flag) and `self._stream_lock` (an `RLock`, guards the PortAudio stream's
  open/close lifetime). They're split because the stream is opened on the
  recording thread but closed from the turn thread (or the cancel button's
  thread) — an open racing a close can hand PortAudio a half-freed stream,
  which surfaces as a bare access violation, not a catchable exception.
  `_close_stream_locked()` always calls `stream.stop()` (blocks until the
  PortAudio callback finishes) **before** `stream.close()` — closing under
  a running callback is the use-after-free case.
- `cancel_recording()` and `stop_and_transcribe()` both set
  `self.last_audio` — the raw concatenated float32 buffer of whatever was
  captured, even on the cancel path. This exists specifically for
  `wakeword_capture.py`, which needs to save whatever audio existed even
  when a wake-triggered turn was aborted via the cancel button (this was a
  real gap: cancelled-turn audio "was being thrown away before it could
  ever be logged", confirmed live 2026-08-13).

**Transcription** (`stop_and_transcribe`):
```python
segments, _info = self.model.transcribe(
    audio, language="en", beam_size=WHISPER_BEAM_SIZE,   # 5
    vad_filter=True, initial_prompt=_build_prompt(),
    condition_on_previous_text=WHISPER_CONDITION_ON_PREVIOUS,  # False
)
```
Guards before that call:
1. `len(audio) < min_seconds(0.25) * samplerate` → return `""` (a spurious
   tap; Whisper will happily hallucinate a phrase out of milliseconds of
   room noise).
2. `np.max(np.abs(audio)) < _SILENCE_PEAK_FLOOR (0.02)` → return `""`
   without ever calling `transcribe`. This is a *peak* check across the
   whole buffer, not mean RMS — a short loud word in an otherwise quiet
   buffer must still pass, which an averaged RMS over the whole clip could
   wash out. This exists because a wake-triggered turn that heard nothing
   used to still run a full Whisper pass over silence and "stay listening"
   for several seconds after the mic went quiet — most of that dead time
   wasn't the (deliberately generous) 1.2s silence timeout, it was this
   wasted transcription pass.

**Beam size (`WHISPER_BEAM_SIZE = 5`, raised from 1 on 2026-08-15).**
Greedy decoding (beam=1) commits to the top token every step with no
backtracking — the documented failure: "Call Mom" was misheard as "God,
Mom" (a voicing confusion reverb makes near-coin-flip at the first
phoneme). Beam search keeps competing hypotheses alive long enough for the
rest of the utterance to disambiguate it. Cost is roughly linear in beam
width on the decode step but judged imperceptible live on this hardware
(`large-v3-turbo` decodes a few seconds of audio fast enough that 5x a
small number is still small; the wake-to-reply latency is dominated by the
LLM, not the transcribe). Kept as a tunable rather than inlined — first
thing to drop back to 1 on slower hardware or much longer utterances.

**`WHISPER_PROMPT_BASE` / `_build_prompt()` — vocabulary priming.**
```python
WHISPER_PROMPT_BASE = (
    "Fred. Call Mom. Hang up. Sync contacts. What's on my agenda? "
    "Open the file. Set a timer. Search the web."
)
```
`initial_prompt` biases decoding toward this vocabulary — command words
("call", "Fred") are exactly what far-field decode mangles, and are a
small, closed set worth priming. `_build_prompt()` (`stt_whisper.py`)
appends the most-called contact names read live from
`tools.phone_tools._read_contacts()` at runtime — **never written to any
file this process persists**, only interpolated into the string handed to
the decoder — capped at the top 25 (`_PROMPT_CONTACT_LIMIT`) since
`initial_prompt` only has room to matter for a short list, and
`sync_contacts` already writes names in most-called-first rank order. The
built prompt is cached for 300s (`_PROMPT_TTL_SECONDS`) since it only
changes when contacts are resynced; a missing/unreadable contacts file just
falls back to the base prompt. **Do not hardcode real names into this repo
or into settings.py** — they belong only in the user's vault, read at
runtime.

**`WHISPER_CONDITION_ON_PREVIOUS = False`.** faster-whisper defaults this
to `True` (feed the previous segment's text as context for the next) —
good for continuous dictation, wrong here: FRED's utterances are
independent commands seconds apart, so the only effect is letting one bad
transcript bias the next (the documented mechanism behind Whisper's
repetition-loop failure mode).

**`unload()`**: frees the model, measured to reclaim 1072 of 1287 MiB
VRAM. Refuses to unload mid-recording (`self._recording` true) — the
watchdog (see `ModelLifecycle`, not in this doc's scope) retries on its
next tick instead.

`MAX_UTTERANCE_SECONDS = 60` — hard ceiling so a stuck key can't grow the
recording buffer unbounded.

---

## 3. `Core/audio/tts.py` — SAPI TTS (CLI mode)

`TTSManager.speak(text, on_word=None, on_end=None)` spins a daemon thread
per call and never blocks the caller. Inside the thread:

```python
pythoncom.CoInitialize()
engine = pyttsx3.init()
self._apply_voice(engine)          # match TTS_VOICE ("David") by name fragment
if on_word:
    engine.connect("started-word", lambda name, location, length: on_word())
engine.say(text)
engine.runAndWait()
engine.stop()
```
`pythoncom.CoInitialize()` is called explicitly because pyttsx3's SAPI
backend is COM-based and this always runs on a freshly spawned thread —
without the explicit call, COM initialization silently fails ("CoInitialize
has not been called") in some trigger contexts (e.g. the background
scheduler). `CoUninitialize()` runs in a `finally`. `on_word`/`on_end` are
optional hooks (used by GUI-adjacent overlay code, not by the CLI itself);
SAPI reports word *boundaries* only — never raw PCM — so any animation
driven off `on_word` is timing-based, not amplitude-based.

---

## 4. `Core/audio/tts_kokoro.py` — Kokoro TTS (GUI mode, streaming)

The single most complex file in this pipeline. Two problems it solves that
SAPI structurally cannot:

1. **Dead air.** Old flow: generate the *entire* reply, then start
   speaking — seconds of silence per turn on a local model. Kokoro instead
   synthesizes and plays sentence-by-sentence, with the next sentence
   synthesizing while the current one plays.
2. **Fake animation.** SAPI has no raw audio; Kokoro returns real float32
   samples, so `on_level` carries the RMS of the block actually on the
   speaker — a true waveform reaction, not a word-timer fake.

### Text cleanup pipeline (`clean_for_speech`)

Applied only at synthesis time, never at split time (see below). In order:
1. `_LATEX_FRAC` (`\frac{3}{4}` → "3 over 4") — must run before `_MD_NOISE`
   strips the backslash/braces, or Kokoro reads them as raw symbols
   ("backslash frac open brace three...").
2. `_SLASH_FRAC` (bare `3/4` → "3 over 4") — digit-bounded regex so it
   can't collide with a URL path (letters, not digits, on both sides).
3. `_LATEX_SQRT` (`\sqrt{x}` → "square root of x").
4. `_LATEX_SYMBOLS` table: `\times`/`\cdot`→"times", `\pm`→"plus or minus",
   `\leq`/`\geq`/`\neq`→spelled out, `\approx`→"approximately",
   `\infty`→"infinity", `\pi`→"pi".
5. `_EXPONENT` (`x^2`→"x squared", `x^3`→"x cubed", `x^{10}`→"x to the
   power 10", via `_EXPONENT_WORDS = {"2": "squared", "3": "cubed"}`).
6. `_STAR_MULT` (`3 * 4`→"3 times 4", digit-bounded so it can't collide
   with markdown `*bold*`/`_italic_`).
7. `_MD_LINK` (`[label](https://...)`→"label") — a markdown link speaks its
   label, never the URL. Confirmed bug: a task with the raw URL as its own
   label got read character-by-character ("h t t p s colon slash slash...").
8. `_URL` (any surviving bare URL → just its hostname label, e.g.
   `https://example.com/report` → "example").
9. `_BRACKET_TAG` (`\[[^\]]*\]` → deleted) — strips internal job-id tags
   like `[reminder_1785718306_1]` from scheduler output; runs *after*
   `_MD_LINK` has already consumed real `[label](url)` pairs, so anything
   still bracketed here is metadata, not content.
10. `_BULLET` (leading `-`/`•` stripped).
11. `_MD_NOISE` (`[*_`#>]+` stripped) — catches remaining markdown noise.

### Splitting (`split_for_speech`)

Splits on `(?<=[.!?])\s+`. Front-loads for latency: the **first** chunk is
capped at 90 chars (`first_chunk_max`) and broken at a clause boundary
(`", "`) if too long, so time-to-first-audio never depends on the first
sentence's raw length; later chunks are allowed to run long because more
context gives Kokoro better prosody. A short fragment merges into the
previous span if under `min_chunk=45` chars — this is what makes
abbreviations ("Dr. Smith", "3.5") fall out for free without special-casing
them: the split still happens after "Dr." but the tiny fragment
immediately re-merges.

**Critical invariant, called out explicitly in the code**: splitting must
happen on the *raw* (uncleaned) text so that chunks are exact slices the
streaming producer can `buffer.find(head)` to locate and consume. Cleaning
used to happen inside the splitter and this silently ate long replies —
a cleaned chunk (markdown stripped) is not findable inside a raw buffer
that still has the markdown, so the whole remaining buffer got dropped.
Any reply containing a bullet, link, or bold span was cut off at its first
sentence. **Rebuild rule: clean only at `emit()`/synthesis time, split on
raw text always.**

### `KokoroTTS` class

Lazy-loaded (`_ensure_model`) — Kokoro runs `CPUExecutionProvider` only
(`kokoro_onnx` hardcodes it; this environment's onnxruntime has no GPU
provider at all), so "loading" costs ~340MB RAM, not VRAM — different
resource, different urgency than the LLM/Whisper unload pair. A session
whose every spoken phrase hits `phrase_cache` never loads Kokoro at all.

`_resolve_voice`: supports blending two Kokoro voicepacks
(`KOKORO_VOICE_BLEND = None` by default) since voicepacks are plain
embedding tensors — a weighted sum of two is itself a valid voice. There is
no published finetuning pipeline, so blending is as far as custom voices go.

`synth(text)` is deliberately **not** guarded by `self._lock` — that lock
serializes whole `speak()` calls end-to-end and is held by a different
thread (the producer thread) for the call's entire duration, so taking it
inside `synth()` too would deadlock. This means the phrase-cache
warm-up thread can call `synth()` concurrently with an in-flight
`speak()`'s own `synth()` calls — relies on onnxruntime's
`InferenceSession.run()` being safe for concurrent multi-thread calls
(a documented ORT design goal), explicitly flagged in the code as *not
independently verified*, unlike llama.cpp's C bindings which are known
**not** to have that guarantee (see `orchestrator.py`'s `producer.join()`
fix, referenced but out of this doc's scope).

### `speak(text, on_level=None, on_first_audio=None, cancel=None)`

`text` can be a finished string or an iterable of streaming pieces — speech
starts as soon as the first *complete sentence* has arrived, not once the
whole reply is generated.

**Mute short-circuit**: if `mute_state.is_muted()`, the source is fully
drained (so conversation history still gets the complete text) but nothing
is synthesized or played — no model call, no output stream at all.

**Producer/consumer with a 1-slot queue** (`pending = queue.Queue(maxsize=1)`):
- `producer()` accumulates streamed pieces into `buffer`, only flushing a
  chunk via `emit()` when there's a *provably complete* sentence
  (`len(split_for_speech(buffer)) > 1` or the buffer already ends on
  terminal punctuation) — a partial sentence is never synthesized, because
  Kokoro's prosody depends on seeing the full clause and a half-sentence
  spoken then corrected sounds worse than waiting. Whatever's left after
  the source is exhausted is spoken as the tail even without terminal
  punctuation, or the last words would be lost. A `None` sentinel signals
  the consumer that the producer is done.
- `emit(chunk)`: checks `phrase_cache.get(speakable)` first — a full cache
  hit means `self.kokoro` never has to load at all for a turn that only
  speaks filler/tool captions. On miss, calls `self.synth(speakable)`.
- The consumer loop opens `sd.OutputStream(samplerate=sr, channels=1,
  dtype="float32", extra_settings=device_info.output_extra_settings())`
  lazily, on first chunk, then writes `TTS_PREROLL_SEC` (1.5s) of silence
  before any real audio — see §6 below. Playback writes in `PLAY_BLOCK =
  1024`-sample chunks (~43ms at 24kHz), which is also the cancellation
  granularity: `stream.write()` blocks until the device accepts the block,
  so that's how fast an interrupt can actually silence playback, and it's
  also why `on_level` (computed per-block RMS × 4.0, clamped) tracks what's
  actually audible rather than running ahead of it.
- **Teardown order matters, spelled out in the module docstring**: kill the
  audio device first (that's the part the user perceives as "it stopped"),
  then the queue, then let the generator die. On cancel: `stream.abort()`
  (immediate). On natural end: write `TTS_POSTROLL_SEC` (1.0s) of silence
  before `stream.stop()`/`close()` — see §6. `finished.set()` always runs
  in the `finally` to unblock a still-running producer.
- Returns the text that was **actually spoken**, not the full input — on
  an interrupt this is shorter than the reply, and `pill_app.py` relies on
  this to avoid recording words the user never heard into conversation
  history (see §8).

---

## 5. `Core/audio/fillers.py`, `greetings.py`, `phrase_cache.py`, `mute_state.py`

### `fillers.py`
Three pools spoken immediately every turn, *before* the real reply is
ready, to mask time-to-first-word (local-tier LLM generation can spend
real time reasoning before anything is streamable):
- `FILLER_SOCIAL` = ("One moment.", "Just a second.", "Give me a moment.",
  "One sec.")
- `FILLER_ACTION` = ("On it.", "Let me check on that.", "Working on it
  now.", "Let me have a look.", "Give me one second.")
- `FILLER_DEFAULT` = ("Let me think about that.", "Give me a second.",
  "Hold on, thinking it through.", "Let's see here.", "Just a moment.")

`pick_filler(text)` picks the *flavor* via cheap word-cue checks — not a
model call, same shape as `orchestrator/intent.py`/`vault_intent.py`:
`intent.looks_social(text)` → social pool; `intent.match_categories(text)`
→ action pool; anything else, or any classification exception → default
pool. Rationale explicitly noted: "let me have a look" answering "how are
you doing" reads as broken, so the filler must match the turn's apparent
shape.

### `greetings.py`
`NEUTRAL` (7 lines) + `BY_TIME` (morning/afternoon/evening/night bands via
`_band(hour)`, boundaries 5/12/17/22). Every line addresses the user as
"sir" — deliberate house style, "the whole point of the greeting is that it
sounds like the same assistant every time." No line is phrased as a
question — a greeting that asks something and falls silent reads as though
FRED is waiting for an answer, but the hotkey hasn't been touched so
nothing is recording. `pick_greeting(now=None)` picks from `NEUTRAL +
(time-band line,)` — the time-appropriate line is only *one candidate*
among the neutrals, never a forced pick, so restarting twice in one
afternoon doesn't produce the identical sentence both times.

### `phrase_cache.py`
Pre-synthesizes Kokoro audio for the closed ~50-phrase vocabulary (filler
pool + `orchestrator.TOOL_LABELS` captions like "Calculating...") that gets
spoken nearly every turn. Cache format is raw float32 PCM in `.npz`
(numpy's own save format) — not WAV (would just ferry identical bytes
through a container for nothing) or MP3 (adds a codec dependency + lossy
round-trip for millisecond-long phrases) or MP4 ("raised as an option...
has no business here at all").

`phrase_key(text)` = first 24 hex chars of `sha256(f"{text}\x00{voice}|{blend}|{speed}")`
— keyed by a **fingerprint of the raw config**, not the resolved (possibly
blended-tensor) voice object, since the tensor isn't stably hashable but
the settings that produce it are. This means changing `KOKORO_VOICE` or
`KOKORO_SPEED` in settings.py automatically invalidates old cache entries
instead of silently playing stale audio at the wrong voice/pace.

`put()` writes via a `.tmp.npz` → `path.replace()` atomic pattern — but
note the gotcha documented in the code: `np.savez` **silently appends
`.npz`** to any path lacking that extension, so the temp file must itself
already end in `.npz` (`f"{key}.tmp.npz"`), or you get a file literally
named `....npz.tmp.npz` and the subsequent `tmp.replace(path)` fails
looking for a file that was never created — this bug was reproduced live
before being fixed. **Rebuild must preserve the `.tmp.npz` suffix
convention.**

`warm(tts, phrases)` is the *only* place that forces Kokoro to load purely
to build the cache; `pill_app._warm_phrase_cache` calls it on a background
thread at startup with `ALL_FILLERS + ALL_TOOL_CAPTIONS + ALL_GREETINGS`,
then unloads Kokoro again once done — a session that only ever speaks
cached phrases never needs Kokoro resident at all.

### `mute_state.py`
A bare module-level `_muted` bool with `set_muted`/`is_muted` — silences
**only FRED's own TTS output** (checked inside `tts_kokoro.speak()`), not
system volume. This replaced an earlier mute button that called
`machine_tools.mute()`, which flipped the Windows default-speaker endpoint
via `pycaw` — silencing the *entire PC*, not just FRED, confirmed wrong
live 2026-08-04. No class wrapper: there's exactly one FRED process and
exactly one mute state, no per-instance reason for one.

---

## 6. `TTS_PREROLL_SEC` / `TTS_POSTROLL_SEC` — the Bluetooth ramp bug

Both live in `config/settings.py`, consumed in `tts_kokoro.py`'s `speak()`.

**Symptom**: on Bluetooth output, the first ~0.5-1s of every reply's audio
was inaudible or attenuated.

**Root cause, confirmed not to be FRED's own pipeline**: Bluetooth outputs
ramp/attenuate audio for roughly the first 0.5-1s while the link wakes from
idle and the codec stabilizes. Verified directly: Kokoro's own generated
opening has *higher* RMS than the rest of the utterance (not quiet at
source), and the playback loop showed no underrun (writes never returned
early, settling at 41.5ms against a 42.7ms block) — so the swallowed
opening was the device, not generation or playback timing.

**Two fixes that were tried and explicitly reverted** (documented in the
code specifically so neither gets tried again on rebuild):
1. Peak-normalizing every chunk to a fixed target (0.95) — made things
   *worse*, since Kokoro's own level was never the problem; this just
   amplified the already-audible part after the ramp, widening the
   perceived gap.
2. Replacing the silent preroll with a quiet 80Hz tone, on the theory that
   a BT receiver's gain ramp only triggers on real signal energy — no
   improvement to the quiet phase.

**Actual fix**: `TTS_PREROLL_SEC` writes N seconds of literal silence to
the output stream *before* any real samples, giving the BT link something
inaudible to ramp through. History of the constant: started at 0.35
(undershot), raised to 1.0 (matched an initial documented ~0.5-1s
estimate), then raised again to **1.5** after live feedback showed the
filler phrase itself was still quiet even at 1.0 — meaning this specific
device's ramp runs longer than the estimate, not just up against it. Left
as "a floor, not a hard limit" — if quiet-filler reports recur, raise it
further. Set to `0` on wired output where none of this applies.

Critically, **preroll is paid once per turn, not once per reply chunk** —
this only works because `pill_app.py`'s merged-stream design (§8) routes
filler + tool captions + real reply through **one continuous
`sd.OutputStream`**, so the ramp only has to happen once, at the very start
of the turn, rather than re-triggering on every individual `speak()` call
that used to open its own stream.

`TTS_POSTROLL_SEC = 1.0` is the tail-side twin, added after a *different*
live report (2026-08-12): playback cut off the last ~1s of a reply.
`sounddevice.Stream.stop()` only blocks until PortAudio's own **host**
buffer has drained — it has no visibility into a Bluetooth link's own
downstream buffering/transmission latency past that point, so
`stop()+close()` immediately after the last real write can tear the stream
down while the device is still physically catching up. Fix is symmetric:
write `TTS_POSTROLL_SEC` of silence before `stop()`, so real final words
are already fully out by the time teardown happens. Explicitly **skipped
on a user-initiated cancel** (`stream.abort()` instead) — an interrupt
should cut off promptly, not linger.

---

## 7. `Core/audio/device_info.py` — device selection

Sits on top of `sounddevice` (`sd`), and handles three concerns:

**WASAPI de-duplication.** PortAudio lists every physical device once per
Windows host API (MME, DirectSound, WASAPI, WDM-KS) — confirmed on a real
machine: ~35 entries for 4 real devices. `_wasapi_index()` finds the
"Windows WASAPI" hostapi and every device-listing function
(`_devices_for`, used by `list_input_devices`/`list_output_devices`)
filters to just that hostapi. Filtering by hostapi rather than de-duping by
name was a deliberate choice — name collisions across genuinely distinct
devices (two entries both literally named "Headphones ()", or WDM-KS's
unnamed Realtek entries) make name-matching unreliable.

**`output_extra_settings()` / `input_extra_settings()`**: return
`sd.WasapiSettings(auto_convert=True)` when the current default device is
on WASAPI, else `None`. Needed because opening a WASAPI stream at Kokoro's
fixed synth rate raised `Invalid sample rate [PaErrorCode -9997]` — MME/
DirectSound silently resample, WASAPI validates the rate against the
device's own native list unless explicitly told to let Windows' audio
engine convert (`auto_convert=True`). Every `InputStream`/`OutputStream`
construction site across `stt.py`, `stt_whisper.py`, `tts_kokoro.py`, and
`wakeword.py` passes these as `extra_settings=`.

**Persisted device preference by NAME, not index.** `sd.default.device` is
process-global and mutable via `set_input_device`/`set_output_device`
(used by the HUD's dropdowns). Preference is saved to
`config.DATA_DIR / "audio_device_prefs.json"` keyed by device *name*, not
PortAudio index — because indices shift between runs as devices
connect/disconnect (a reconnecting Bluetooth headset doesn't get the same
index twice). `apply_saved_devices()` (called once at startup, before
anything opens a stream — see `PillApp.__init__`) resolves the saved name
against devices present *right now*; a device that's gone is silently
skipped for that side rather than forced, which is exactly "fall back to
whatever PortAudio's own default already is."

**Cross-process publication.** `_publish_selection()` writes the current
selection to `~/voice-line/audio_devices.json` (the same file-bus
`utils/voice_line.py` uses elsewhere) so the HUD server — a separate OS
process — can display FRED's actual current device selection instead of
its own process's independent OS default.

`describe_audio_devices()` / `list_audio_devices()` are the human-readable
query surfaces (used for a spoken "what devices do I have").

The module's own `if __name__ == "__main__":` block is a self-check that
mocks `sounddevice` entirely (never touches real hardware) and asserts the
name-based fallback behavior and the save/round-trip of
`set_input_device`.

---

## 8. `Core/input/hotkey.py` — Left Ctrl+Alt hold-to-talk

`HoldHotkey` wraps a Windows **low-level keyboard hook**
(`SetWindowsHookExW(WH_KEYBOARD_LL, ...)`, via raw `ctypes` bindings to
`user32`), not `RegisterHotKey` — `RegisterHotKey` only fires on key-down,
and press-**and-hold** is the whole interaction here, so both edges
(down/up) are required and only a low-level hook delivers both.

Two hard constraints baked into the design (documented at the top of the
file):

1. **The hook callback must return almost immediately.** Windows silently
   *unhooks* a process whose callback exceeds `LowLevelHooksTimeout`
   (default 300ms) — no error, the hotkey just silently stops working. So
   `_callback` only mutates `self._down`/`self._engaged` and invokes
   `on_press`/`on_release`, which are contractually non-blocking; all real
   work happens on other threads.
2. **`install()` must run on a thread that pumps Windows messages** — a
   low-level hook is delivered through the installing thread's message
   queue. In FRED, `pill_app.py`'s `_on_ready` calls `self.hotkey.install()`
   from the same thread that later calls `PillWindow.run()` →
   `PumpMessages`.

**Chord**: `VK_LCONTROL (0xA2)` + `VK_LMENU (0xA4)` — specifically the
*left*-hand keys. On international layouts, AltGr reports as
`LeftCtrl + RightAlt`, so binding LeftAlt keeps the FRED chord from ever
colliding with AltGr.

**Deliberately observe-only** — the hook always calls
`CallNextHookEx(...)`, never swallows the chord. Reasoning: at the moment
Ctrl goes down, you can't yet know whether Alt is coming next, and
swallowing a keyup whose keydown was already delivered leaves the
foreground app with a stuck modifier. Ctrl+Alt tap-and-release alone is a
common no-op (it's literally the AltGr shape), so passing it through is
safe by default — if Alt's menu-bar activation ever visibly leaks through
in practice, that assumption is the thing to revisit, not the pass-through
itself.

`self._proc = HOOKPROC(self._callback)` is held as a hard instance
reference specifically to prevent ctypes from garbage-collecting the
callback trampoline while Windows still holds its raw address — without
this, the next keypress calls into freed memory.

---

## 9. `Core/input/wakeword.py` — "Hey FRED" acoustic wake model

`WakewordListener` runs a real trained openWakeWord ONNX model
**alongside** `HoldHotkey`, never replacing it (decided 2026-08-09).
Two structurally different problems from hold-to-talk (spelled out in the
module docstring):

1. **No keyup to mark utterance end.** Hold-to-talk's whole design leans
   on key release as the endpoint; wake-word has no equivalent, so
   end-of-utterance must be *guessed* from silence — `watch_for_silence()`.
2. **Continuous mic capture competes with WhisperSTT's turn-scoped
   stream** for the same input device. `WakewordListener` owns its own
   `InputStream` and the caller (`pill_app.py`) is responsible for calling
   `pause()` before a real STT stream opens and `resume()` once the turn
   ends.

### Frame/model mechanics
`CHUNK = 1280` samples = 80ms @ `SR = 16000` — openwakeword's required
frame size. Model loaded lazily (`_ensure_model`):
```python
self._oww = Model(wakeword_models=[WAKEWORD_MODEL_PATH], inference_framework="onnx")
```
`WAKEWORD_MODEL_PATH = BASE_DIR / "models" / "wakeword" / "hey_fred.onnx"`
— trained by `wakeword_train.py` (§11), gitignored like the other model
binaries.

### Adaptive gain control (AGC)
Added 2026-08-10 after directly measuring Windows' own mic level meter:
~80% peak close to the mic, ~20% at normal speaking distance.
`_AGC_TARGET_PEAK = 0.7`, `_AGC_MAX_GAIN = 100.0` (~40dB ceiling),
`_AGC_MIN_PEAK_TO_BOOST = 0.01` (hold gain rather than amplify near-silence
noise), `_AGC_SMOOTHING = 0.3`.

Deliberately **not** a fixed/maximum multiplier applied irrespective of
level — tested and confirmed dangerous: pushing gain far enough to clip
collapsed a detection score from 0.998 to 0.013 the same day. The correct
read of "maximum boost in any condition" is targeting a safe peak with a
generous ceiling, not blindly amplifying regardless of how loud the input
already is.

Gain **increase** is smoothed (`_AGC_SMOOTHING` convergence per chunk,
matching openwakeword's own ~760ms/76-frame feature window so a jump
doesn't look like an artifact inside that window), but gain **decrease**
snaps instantly, no smoothing — confirmed by test 2026-08-10 that smoothing
the decrease too let leftover high gain overshoot onto a genuinely loud
chunk (measured post-gain peaks hitting the clip ceiling despite the cap,
because gain caught up one chunk late). AGC is computed on raw audio; the
2.5s **pretrigger buffer** (`_PRETRIGGER_SECONDS`, `collections.deque`)
saved for `wakeword_capture.py` stores audio *pre-AGC* deliberately, since
AGC gain swings per-chunk and baking a runtime-only artifact into training
data would be inconsistent with the rest of the training corpus (all raw
mic capture).

### `_process_chunk` — scoring and the two crash bugs it fixes
```python
fired = bool(score > self._threshold and now - self._last_fire > _REFIRE_COOLDOWN_SECONDS)
```
The `bool(...)` cast is **load-bearing**, not style: openwakeword's scores
are numpy floats, so `score > threshold` produces `numpy.bool_`, which
`json.dumps` cannot serialize. Confirmed live 2026-08-10: this crashed
*inside the PortAudio callback thread itself* with a visible error dialog
on every near-miss score specifically — exactly the case the logger exists
to capture, and the most common case once someone is actually testing
detection. `wakeword_log.py`'s writer also carries a `default=str` fallback
as belt-and-suspenders on top of this fix.

The entire `_process_chunk` body is wrapped in a last-resort
`try/except Exception` inside `_callback` — added after confirming *two*
separate live failure shapes: the JSON bug above (visible error dialog) and
separately a run that produced **zero log output at all** (no score, no
error, nothing), consistent with an exception hitting the unguarded AGC
math before any per-step guard existed. Both are unacceptable for something
under active tuning, hence the catch-all.

`_REFIRE_COOLDOWN_SECONDS = 2.0` — refuses a second fire this soon after
the last, since the model re-scores every 80ms and a single "hey fred"
utterance spans several chunks, any of which could independently cross
threshold.

On fire: `self._oww.reset()`, snapshot `last_trigger_audio` (the
pretrigger buffer), `last_fired_score`, `last_fired_gain`, then **dispatch
`on_wake` on a new thread** rather than calling it inline — stopping a
`sounddevice` stream from inside its own callback is unsafe, same reasoning
as `hotkey.py`'s callback constraints.

### `resume()` self-healing retry
Confirmed live 2026-08-15: a USB plug/unplug event shifted PortAudio
device indices underneath a long-running FRED process, and `resume()`
retrying on the same now-dead index failed repeatedly with
`PaErrorCode -9999` — FRED sat deaf for an hour with only a log line. Fix:
on the first `_open_stream()` failure, call
`device_info.apply_saved_devices()` (re-resolves the saved device by
*name* among devices present right now) and retry **exactly once** —
bounded, not a loop, so a genuinely-gone device (unplugged, driver
crashed) fails fast with a clear log line instead of spinning.

### `watch_for_silence(stt, on_timeout, stop_flag)`
Runs on its own thread once a wake-triggered turn starts recording. Polls
`stt.level` (already computed by `WhisperSTT`'s own callback, no separate
VAD model needed) every 50ms:
- `SILENCE_TIMEOUT_SECONDS = 1.2` — deliberately generous; cutting off a
  trailing word is judged worse than half a second of extra dead air.
- `SPEECH_RMS_FLOOR = 0.02` — same floor value as `stt_whisper.py`'s
  `_SILENCE_PEAK_FLOOR`, applied here to live streaming level instead of a
  buffer peak.
- `MAX_UTTERANCE_SECONDS = 15.0` — safety cap if the user never speaks at
  all after triggering.
- `stop_flag` (a `threading.Event`) lets the caller cancel the watch early
  — e.g. a real hotkey press superseding an in-flight wake-triggered turn.

---

## 10. `Core/input/wakeword_capture.py` and `wakeword_log.py`

**`wakeword_log.py`** — a dedicated append-only JSONL log
(`DATA_DIR / "wakeword_log.jsonl"`), separate from the general session
event log, because scores arrive every ~80ms while listening and would
drown out everything else in a shared log almost instantly. `log_score`
only writes when `score >= _LOG_FLOOR (0.03)` or the event actually fired —
true silence/room-tone scores sit around 0.000-0.002 in practice, so
logging every idle chunk would produce thousands of "nothing happened"
lines per idle hour. Anything above the floor is a real attempt worth
seeing, fired or not — a near-miss just under threshold is exactly the
signal needed to evaluate an AGC or threshold tuning change.

**`wakeword_capture.py`** — builds a **live-usage retraining dataset**
passively during normal use, distinct from the scripted read-through data
`wakeword_train.py` generates. On every fired wake event,
`pill_app._save_wake_capture` calls `save(trigger_audio, followup_audio,
cancelled, transcript, wake_score, wake_gain)`, which concatenates the
pretrigger clip + a 0.3s silent gap (`_GAP_SECONDS`, keeps the two segments
audibly distinct on playback and prevents them being misread as one
continuous utterance if fed back into training) + whatever followed, and
writes one `.wav` under `DATA_DIR/wakeword_training/live_captures/` plus
one manifest line to `manifest.jsonl` recording `spoke_after` (real speech
peak ≥ `_SILENCE_PEAK_FLOOR` in the followup) independent of whether the
turn was later cancelled — so "said something, then hit cancel on a bad
transcript" still logs correctly as `spoke_after=True`. Never raises into
the caller; this is a logging side-effect of a real conversation turn and
must not be able to take the turn down with it.

---

## 11. `Core/input/wakeword_train.py` — training the model (standalone)

Not imported by FRED at runtime at all. Run by hand:
```
Core/venv/Scripts/python.exe -m input.wakeword_train
```
All intermediate data lives under `Core/data/wakeword_training/`
(gitignored); the finished model is copied to
`Core/models/wakeword/hey_fred.onnx` (also gitignored, same convention as
the Kokoro/Vosk model binaries). Every pipeline step is idempotent (skips
work whose output directory already has files in it), so re-running after
adding new data (e.g. a room recording as better negatives) only does the
incremental part.

### Design decisions (all made 2026-08-09, all worth preserving on rebuild)

- **Positive "Hey FRED" clips come from Kokoro TTS**, not openWakeWord's
  own recommended `piper-sample-generator` — that generator needs
  `piper-phonemize`, which has no Windows wheel
  (upstream issue still open). Kokoro was already a working local
  dependency.
- **Negatives are DEMAND** (real recorded ambient noise — kitchen, living
  room, office, cafeteria, street traffic, from zenodo.org/records/1227121)
  **plus numpy-synthesized white/pink/brown noise** — explicitly *not*
  openWakeWord's standard recipe (ACAV100M ~17GB + FMA ~8GB), which is
  sized for a production model, not a same-day v1. A real room recording
  was planned to supplement/replace DEMAND later.
- No RIR (room-impulse-response) reverb augmentation — `rir_paths=[]` in
  the training config — same same-day-v1 scoping call.
- Runs CPU-only; the model itself is a few hundred thousand params, so
  this is acceptable even without CUDA torch in the venv.

### Windows-specific monkeypatches (all required, all documented inline —
preserve every one on rebuild or training will crash on Windows)

1. **UTF-8 stdout/stderr reconfigure** — torch's ONNX exporter prints a
   Unicode checkmark in verbose output; Windows console defaults to cp1252
   and crashes on it, at the very last step, after export already
   succeeded.
2. **`scipy.special.sph_harm` shim** — `openwakeword.data` imports the
   `acoustics` package for one unused colored-noise helper; `acoustics`'
   unrelated `directivity` submodule imports a scipy name renamed upstream
   (`sph_harm` → `sph_harm_y`). Never actually called — just must not raise
   `ImportError` at import time.
3. **`torchaudio.load` shim** (`_load_via_soundfile`) — this venv's
   torchaudio routes `.load()` through torchcodec by default, which needs
   FFmpeg native libs not installed. Replaced with a `soundfile`-based
   reader (soundfile is already a working dependency via kokoro-onnx).
   `torchaudio.info` is replaced with a no-op lambda since its one caller
   in `openwakeword/data.py` immediately overwrites the result anyway.
4. **`speechbrain` fake-module stub** — `openwakeword.data` imports
   `read_audio`/`reverberate` from real speechbrain, but this pipeline
   never reaches the code paths that call them (RIR is empty). Importing
   the *real* speechbrain package registers a lazy-loading system for
   optional integrations (needs the `k2` package, not installed) that
   PyTorch's `torch._dynamo` frame-inspection machinery incidentally
   crashes on — unrelated to anything actually used. Fix: inject fake
   `types.ModuleType` stubs into `sys.modules` *before* `openwakeword.data`
   ever imports the real package, so its `__init__.py` never executes.
5. **`trim_mmap` Windows-safe replacement** — the original
   `openwakeword.data.trim_mmap` holds an mmap-mode `'r'` handle open on a
   file while calling `os.remove()` on that same file — legal on POSIX
   (unlink-while-open), always crashes on Windows with `WinError 32`. Fix
   closes both mmap handles explicitly via `._mmap.close()` (plain `del` +
   `gc.collect()` doesn't reliably release a numpy memmap's Windows file
   handle — a known numpy gotcha, no public `.close()` exists), then
   retries `os.remove()` up to 10 times with 0.3s sleeps (Windows can take
   a moment to fully release a file after an mmap unmap even once both
   handles are closed).
6. **`compute_features_from_generator` replacement** — the caller's own
   write-mode memmap (`fp`) stays open on its stack frame through the
   *entire* call into `trim_mmap`, so no retry inside `trim_mmap` alone can
   help; `fp`'s handle can't release until this function itself returns.
   Same fix, one level up: explicitly close `fp` before handing off to
   `trim_mmap`.
7. **Single-process DataLoader** — `openwakeword`'s `train.py` builds
   DataLoaders with `num_workers=os.cpu_count()//2`, which on Windows means
   spawning worker *processes* (no fork). That needs the entire
   dataset/generator graph pickled — `train.py` stores label transforms as
   lambdas, never picklable — and even past that, each spawned worker
   re-executes `train.py` fresh via runpy with none of this file's
   monkeypatches applied. Fix: monkeypatch `torch.utils.data.DataLoader`
   to force `num_workers=0` (and drop `prefetch_factor`, which PyTorch
   rejects when workers=0). Harmless at this dataset's size (a few hundred
   clips).
8. **`generate_samples` stub** — openwakeword's own `train.py` does an
   unconditional `from generate_samples import generate_samples` in its
   `__main__` block regardless of whether `--generate_clips` is passed.
   `PIPER_SAMPLE_GENERATOR_DIR` is added to `sys.path` pointing at a stub
   that satisfies the import without ever calling the real
   (Windows-broken) generator — positive clips come from
   `generate_positive_clips()` instead.

### Pipeline steps (`main()`, in order)

1. `download_demand()` — pulls 5 DEMAND environments (`DKITCHEN_16k`,
   `DLIVING_16k`, `OOFFICE_16k`, `PCAFETER_16k`, `STRAFFIC_16k`) from
   zenodo, extracts only the `ch01.wav` (mono) channel from each 16-channel
   array recording.
2. `generate_synthetic_noise()` — white/pink/brown noise via
   `_NOISE_GENERATORS`, 6 clips per color, 30s each, normalized then scaled
   to 0.3 amplitude.
3. `generate_positive_clips()` — Kokoro TTS renders `POSITIVE_PHRASINGS =
   ["Hey FRED.", "Hey, Fred.", "Hey Fred!"]` at `POSITIVE_SPEEDS = [0.9,
   1.0, 1.1]` across every `af/am/bf/bm`-prefixed Kokoro voice; 20% of
   voices (`POSITIVE_TEST_VOICE_FRACTION`) are held out entirely for test,
   speaking only `POSITIVE_TEST_PHRASING = "Fred, hey."` (a phrasing never
   used in training at all).
4. `ingest_real_positive_clips()` — added 2026-08-11 after real near-miss
   scores (0.14-0.28, well under threshold) on genuine "Hey Fred" attempts
   revealed every positive training clip up to that point was synthetic
   Kokoro TTS — the model had never heard Vatsal's actual voice/mic/room.
   Drops files from `REAL_POSITIVE_DIR` into `POS_TRAIN`/`POS_TEST` (85/15
   split via `REAL_POSITIVE_TEST_FRACTION = 0.15`), runs unconditionally
   (not gated behind step 3's "already populated" skip) so new recordings
   are always picked up on rerun. Checks **both** splits before writing
   (not just the one this run's shuffle assigned) — because
   `np.random.default_rng(2).shuffle(names)` with a fixed seed still
   depends on the *list length*, so adding/removing files between runs can
   flip which split an already-ingested file lands in, and checking only
   the current-run destination would silently duplicate the clip into both
   splits (inflating eval numbers with no visible error).
5. `generate_negative_speech_clips()` — added after a critical live
   finding on 2026-08-09: a model trained only against DEMAND (ambient,
   no clear speech) + synthetic noise learned "human voice = positive"
   rather than "the specific phrase 'hey fred' = positive" — it scored
   0.88-0.997 on completely unrelated sentences like "The weather today is
   quite nice." This step generates the missing signal: phonetically
   adversarial decoys via openwakeword's own `generate_adversarial_texts`
   (using the `pronouncing` package), plus `GENERIC_NEGATIVE_SENTENCES` (18
   ordinary unrelated sentences) plus `PHONETIC_NEIGHBOR_WORDS` (34
   single-syllable near-rhymes of "Fred" — "Red", "Bread", "Dead", "Fed",
   "Friend", "Freddy", etc., added 2026-08-11 after a live false positive
   scored 0.76 on unrelated speech never trained against; spoken bare, not
   in a sentence, to match "hey fred"'s own clip length) — all spoken
   through the same Kokoro voices the positives use.
6. `assemble_negatives()` — segments long recordings into
   `NEGATIVE_SEGMENT_SECONDS = 2.5`s clips via `_segment_audio_file`.
   **Critical finding, 2026-08-09**: copying the DEMAND WAVs in wholesale
   (one file = one training example) gave only ~5 negative examples against
   ~200 positive ones — a trained model on that data scored 0.7-0.998 on
   *every single* negative test clip, having simply learned to always say
   yes. Segmenting each long file into many 2.5s windows fixes the
   class imbalance. `HELD_OUT_ENV = "DLIVING_16k.wav"` is excluded from
   `negative_train` entirely, reserved for `negative_test` and the
   false-positive validation set.
7. `build_false_positive_validation()` — builds a long continuous
   embedding stream (not per-clip like `negative_test`) from the *original*
   whole `HELD_OUT_ENV` recording, for `train.py`'s
   `false_positive_validation_data_path`, which internally slides its own
   window over this file.
8. `write_config()` — training config (`training_config.yaml`):
   `target_phrase: ["hey fred"]`, `model_type: "dnn"`, `layer_size: 128`,
   `steps: 20000`, `max_negative_weight: 1500`,
   `target_false_positives_per_hour: 0.2`,
   `batch_n_per_class: {"positive": 200, "adversarial_negative": 200}`.
9. `run_training()` — invokes openwakeword's real `train.py` via
   `runpy.run_path`. Tolerates a failure at the very tail end
   (`convert_onnx_to_tflite`, which needs `onnx_tf`→TensorFlow, a
   multi-GB dependency for a format nothing in this runtime reads) — the
   ONNX export (`export_model`) always completes *before* the TFLite step
   even starts, so if `hey_fred.onnx` already exists on disk when
   something raises, the run got everything actually needed.
10. `install_runtime_model()` — copies the trained model to
    `Core/models/wakeword/hey_fred.onnx`. Uses `_replace_file` (copy to a
    `.tmp` then `os.replace()`, never a direct `shutil.copy` onto the live
    path) — confirmed live 2026-08-11 that FRED running concurrently keeps
    `hey_fred.onnx.data` memory-mapped for its whole process lifetime, so a
    plain `shutil.copy`'s truncating `open(dst, 'wb')` raised `OSError
    [Errno 22]`, and had already overwritten the `.onnx` graph before
    hitting that error — leaving a NEW graph paired with the OLD
    `.onnx.data`, a silently broken runtime pair. `os.replace()` only swaps
    the directory entry, doesn't truncate the live mmap'd file, and is
    atomic. **Data file replaced before the graph file** — if FRED blocks
    one of the two replacements, an OLD graph paired with NEW data is safe
    (architecture is identical across retrainings, only weights differ);
    the reverse order (new graph / old data) is what actually broke a run
    once and is the failure this ordering avoids.

### Threshold tuning history (in `settings.py`, all same-day 2026-08-10)
```
0.6  -> 0.4   under-triggering at ~2m (every training clip was studio-clean
              Kokoro TTS, no real-room reverb)
0.4  -> 0.25  wakeword_log.jsonl caught a real near-miss peaking at 0.278
0.25 -> 0.35  0.25 over-corrected: a genuine FALSE trigger at 0.701 (mic
              opened, heard nothing, gave up ~2.8s later, confirmed against
              the session log — no transcription followed), alongside a
              real correct trigger at 0.978 moments later
```
`WAKEWORD_THRESHOLD = 0.35` is explicitly **not** a clean optimum — it's
"the best call available from two data points in each direction." The
real fix (RIR augmentation, real recorded voice per step 4 above) is the
actual plan, not a permanent substitute for retuning this number. A
**memory note applies here**: do not retrain or swap the wake-word model
without an explicit request from the user (frozen as of 2026-08-20) —
tuning `WAKEWORD_THRESHOLD` alone is a settings change, not a model swap,
but retraining `hey_fred.onnx` itself is off-limits without asking.

---

## 12. `Core/ui/pill_app.py` — the full turn lifecycle

`PillApp` is the GUI-mode controller. Its own module docstring states the
threading contract plainly:
- **main thread**: creates the window, installs the keyboard hook, pumps
  Windows messages — all three *must* share one thread (hook delivery and
  message pumping are coupled, per `hotkey.py`).
- **render thread**: owned by `PillWindow` (not covered in this file).
- **one turn thread per activation**: runs STT → orchestrator → TTS.

Hotkey callbacks execute inside the low-level hook and must return
immediately — they only flip state and hand off to a new thread.

### Why hold-to-talk instead of always-on listening at all
Stated directly in the file's header comment: "nothing listens at rest (so
idle cost is the pill's render loop and nothing else), there are no false
triggers, there's no 'Yes?' round trip before you can speak, and
key-release gives Whisper a precisely bounded utterance instead of a VAD
silence guess." Wake-word (§9) was added *alongside* this later for
hands-busy moments and because it "just feels more like talking to an
assistant" — never as a replacement.

### 12.1 Startup sequence (`__init__` → `_on_ready`)
`__init__` builds, in order: `FREDOrchestrator`; calls
`device_info.apply_saved_devices()` **before** any STT/TTS object is
constructed (stream defaults must be resolved first); conditionally builds
`WhisperSTT` (if `STT_ENABLED`) and `KokoroTTS` (if `TTS_ENABLED`); builds
`PillWindow`, `TypeInputPopup`; builds `HoldHotkey(on_press=
self._on_hold_start, on_release=self._on_hold_end)`; builds
`WakewordListener(on_wake=self._on_wake_detected)`; builds
`ScreenWatcherManager`; wires the voice-line file bus (`VoiceLineBus`) by
monkey-patching `window.set_state`/`window.set_level` to also publish to
the bus (`_mirror_window_to_bus` — wraps once rather than adding a publish
call at ~8 separate state-change sites, so nothing added later can forget
to mirror); starts `HudManager`; wires `orchestrator.on_tool_event` and
`on_ambiguous_choice`; wires `notifier.set_voice(self._speak_proactive)`
**only if** `self.tts` exists, and **before** `orchestrator.scheduler.start()`
— starting the scheduler earlier risked a persisted overdue reminder firing
within moments of construction while `notifier._voice` was still `None`,
which would speak through the robotic SAPI fallback even in GUI mode;
builds `ModelLifecycle` with `busy=lambda: self._recording or
self._turn_lock.locked()`.

`_on_ready()` (called once the window's message pump is live): installs
the hotkey hook, calls `wakeword.resume()` (if STT enabled), starts the
tray icon, `lifecycle.start()`, `screen_watcher.start()`,
`hud.start_server()`, `_start_phone_api()` (spawns `web/phone_api.py` as
its own subprocess — adopts an already-running instance on port 8779
rather than fighting for it, since Windows' `SO_REUSEADDR` would otherwise
let a second process silently split incoming requests nondeterministically),
starts the HUD command polling thread, schedules the greeting
(`_schedule_greeting`), and — if TTS is enabled — starts
`_warm_phrase_cache` on a background thread.

**Greeting delay**: `GREETING_DELAY_STARTUP = 120.0`s vs
`GREETING_DELAY_NOW = 6.0`s (`--greet-now` flag). At log-on, FRED competes
with everything else Windows is starting (other startup chimes, a cold
Kokoro, disk thrashing) — greeting immediately would be talking into a mess
nobody's listening to yet; 2 minutes in, things have settled. A manual
launch is the opposite case — the greeting *is* the confirmation FRED came
up, so it should be near-immediate. The greeting is skipped outright (not
queued) if a real conversation already started by the time the timer fires
— "a greeting arriving after a real exchange has begun is worse than no
greeting at all."

### 12.2 Hotkey press → recording (`_on_hold_start`)
In order:
1. `screen_watcher.touch()` — first, unconditional, before anything else,
   so the screen watcher (if mid-analysis) stops competing for GPU the
   instant a real conversation turn is about to start.
2. `sleep_mode.wake("hotkey")` — a hotkey press (or a wake-word detection,
   which also routes through this method) counts as "the user manually did
   something," ending sleep mode; no-op if not currently sleeping.
3. `self._wake_triggered = False` — the default for every activation;
   `_on_wake_detected` (§12.4) flips it to `True` immediately after calling
   this, so a genuine hotkey press is never mistaken for a wake-triggered
   one.
4. `self.wakeword.pause()` + reset `self._silence_watch_stop` — releases
   the wake listener's mic device before STT opens its own stream, and
   cancels any earlier wake-triggered silence watch (a hotkey press
   mid-wake-turn is treated as an ordinary interrupt).
5. `self._cancel.set()` — pressing while FRED is mid-answer is an
   interrupt. Safe without echo cancellation specifically because the mic
   is closed during playback (wake listener paused, no STT stream open) —
   the keypress alone is unambiguous.
6. New random pill indicator (`random_indicator()`), `lifecycle.preload()`
   (starts reloading anything the idle watchdog freed, *now*, so loading
   overlaps with the user speaking rather than happening after), clear
   transcript, `set_state("listening")`, `set_level(0.0)`, `show()`.
7. `self._recording = True`, spawn `_begin_recording` thread.

`_begin_recording` calls `self.stt.start_recording()` then polls
`self.stt.level` into `window.set_level` every 30ms while recording.

### 12.3 Hotkey release → turn dispatch (`_on_hold_end`)
Re-touches the screen watcher (from *release*, not press — the idle-timer
should count from when the user stopped talking, not when they started).
Sets `self._silence_watch_stop` (ends any wake-triggered silence watch).
If not currently recording, returns (no-op).

**Turn sequencing / stale-press discard**: `self._turn_seq += 1;
my_seq = self._turn_seq` — claimed on *this* (the hotkey) thread, before
spawning the turn thread, closing a race where two rapid presses could
otherwise both read the counter before either increments it. This exists
because of a real crash: rapid repeat presses used to genuinely queue —
`_turn_lock` only ever guaranteed no two turns ran *simultaneously*, not
that a backlog wouldn't each run to completion regardless of whether the
user had moved on, because **llama.cpp's generation call has no
cooperative cancel point** — an interrupted turn only actually stops at its
next checked boundary. `_run_turn` re-checks `my_seq != self._turn_seq`
the instant it actually acquires `_turn_lock` (not when queued) and
discards outright — no transcription, no generation, no speech, no UI
touch — if a newer press has since superseded it.

### 12.4 Wake-word trigger (`_on_wake_detected`)
Runs on `WakewordListener`'s own dispatch thread (never the audio
callback). Calls `_on_hold_start()` directly (reusing the exact same entry
path a hotkey press takes), then sets `self._wake_triggered = True` and
snapshots `self._wake_trigger_audio = self.wakeword.last_trigger_audio`
(a local copy — cheap, and makes the "listener overwrites this on its next
fire" invariant not load-bearing even though `pause()` inside
`_on_hold_start` already makes a same-turn overwrite impossible). Then
spawns `watch_for_silence(self.stt, self._on_hold_end, stop_flag)` on its
own thread — note it calls `self._on_hold_end` directly as the timeout
callback, i.e. **a wake-triggered turn ends its recording exactly the same
way a hotkey release would**, just triggered by silence instead of a
keyup.

### 12.5 The turn itself (`_run_turn` → `_turn_body`)
`_run_turn(my_seq, text=None)` acquires `_turn_lock`, re-checks `my_seq`
(discard if stale), clears `self._cancel`, and runs `_turn_body(text)`
inside a try/except that on failure logs, flashes the HUD's one red state
(`self.voice_line.alert()`), and forces idle.

`_turn_body`:
1. If `text` wasn't pre-supplied (i.e. not a typed submission), calls
   `self.stt.stop_and_transcribe()`. If this turn was wake-triggered, logs
   the wake capture (`_save_wake_capture(cancelled=False,
   transcript=text)`) and resets `_wake_triggered`.
2. Empty transcript → straight to idle, no further work.
3. Logs `user_speech`, shows the transcript on the pill for
   `TRANSCRIPT_TTL = 2.5`s (deliberately **not** a confirmation gate — a
   mandatory pause before every query would cost more than the visibility
   is worth; the X/cancel button exists for a mishearing).
4. Checks `self._cancel` again (a press could have landed between
   transcription finishing and here) → idle if set.
5. `set_state("thinking")`.
6. **If no TTS configured**: calls `orchestrator.process(text)`
   synchronously (blocking, non-streamed), logs, done — this is the
   text-only degraded path.
7. **Normal (TTS) path** — this is the streaming core:
   - `gen_queue = queue.Queue()`; `collected = []`.
   - `produce()` runs on a background thread, iterating
     `orchestrator.process_stream(text)`, pushing each piece into
     `gen_queue` and `collected`. **Critical detail, explicitly flagged in
     a code comment as something not to "simplify" away**: on
     `self._cancel.is_set()`, the function does a bare `return` —
     abandoning the generator *without exhausting it* is what actually
     stops llama.cpp's token loop mid-generation (confirmed: GPU load drops
     from 74% to idle within 0.2-0.4s of that return executing). Draining
     the rest of the loop and discarding pieces afterward would silently
     reintroduce the exact background-generation cost this is meant to
     avoid. A `None` sentinel is always pushed in `finally`, so the
     consumer never blocks on a dead producer.
   - `merged_source()` is the generator actually handed to
     `tts.speak()`. Logic:
     - If `canned_replies.is_canned(text)` — a reply that never touches the
       model at all — skip filler entirely and just relay `queued_source()`
       (playing ~1s of filler in front of "thank you" would add pure
       latency with nothing to hide).
     - Otherwise, wait up to `FILLER_GRACE_SECONDS = 1.2`s for the first
       real generated piece (`gen_queue.get(timeout=...)`). If something
       arrives in time, yield it immediately with **no filler at all** — the
       cloud LLM cascade (Groq then Cerebras) frequently answers with
       sub-second time-to-first-token, and the filler's entire reason to
       exist is masking *slow* generation, so playing it unconditionally in
       front of a fast reply was pure added latency. Only a genuinely slow
       response (typically the local-tier fallback) ever misses this
       window and gets the filler.
     - If the window is missed: `pick_filler(text)` (§5), append it to
       `prefix_texts` (tracked so it can be stripped back out of
       conversation history), log it as `fred_speech` with `filler=True`,
       yield it, then continue with `queued_source()`.
   - **Single continuous TTS stream for filler + tool captions + real
     reply**: `self._active_queue = gen_queue; self._active_prefix =
     prefix_texts` are set *before* calling `tts.speak()`, so
     `_on_tool_event` (called from the orchestrator's background thread
     mid-generation when a tool fires) can inject its caption text
     (`f"{label}..."`) into the *same* live queue/prefix rather than
     opening a second `speak()` call. This is directly why
     `TTS_PREROLL_SEC`'s Bluetooth ramp only has to happen once per turn —
     two separate `speak()` calls used to mean two `sd.OutputStream`s, and
     the ramp re-triggered on the second one, right at the start of the
     actual answer, exactly where it mattered most.
   - `tts.speak(merged_source(), on_level=window.set_level,
     on_first_audio=lambda: window.set_state("speaking"),
     cancel=self._cancel)` — blocks until done/cancelled, returns `spoken`
     (the actually-audible text).
   - **`finally` block — the second llama.cpp-safety fix, and the more
     subtle one**: `producer.join(timeout=120)`. Explicitly documented
     as **not** merely cleanup — `_turn_lock` alone does *not* protect
     llama.cpp: the lock releases when `_turn_body` returns, but `produce`
     runs on its own thread and can still be mid-`create_chat_completion`
     at that exact moment. Without this join, interrupting a reply and
     immediately speaking again could start a second generation on the
     same model while the first was still decoding — **two concurrent
     `llama_decode` calls, which aborts the whole Python process with no
     catchable error.** This is documented as the literal root cause of a
     real reported crash ("crashes if you hit the hotkey more than twice"):
     two presses 5s apart, each turn logging only its filler and never its
     reply, then `Fatal Python error: Aborted` inside
     `llama_cpp/_internals.py`'s `decode()`. Joining inside this `finally`
     keeps generation inside the lock's effective lifetime — the next turn
     genuinely cannot start until this one has left llama.cpp, even if that
     costs a moment of latency on a fast re-press. The 120s timeout is a
     backstop against a wedged generation deadlocking the app forever, not
     an expected occurrence.
8. `reply = "".join(collected).strip()`; `self._last_exchange` updated for
   the type-popup's context display.
9. **Interrupt detection and history correctness**: `prefix_joined = "
   ".join(prefix_texts)`; `heard_reply` strips that prefix back off
   `spoken` if present. `interrupted = bool(spoken) and heard_reply !=
   reply` — deliberately checks `bool(spoken)` (the *unstripped* original),
   not `bool(heard_reply)`, because a turn cut off during the filler/
   captions phase — before a single character of the real reply played —
   leaves `heard_reply` as `""`, which is falsy and would otherwise read as
   "not interrupted," the exact same signature as a genuinely uninterrupted
   turn. `spoken` is only empty when *literally nothing* was heard,
   including the filler. **If interrupted, only `heard_reply` (what was
   actually spoken) matters for conversation history** — recording the full
   `reply` after a cut-off would leave FRED believing it said things the
   user never heard, and follow-up turns go incoherent. An interrupted turn
   also logs weak negative feedback on whatever tool it called via
   `tool_call_log.log_turn_feedback(orchestrator.last_turn_id,
   interrupted=True)`.
10. `_to_idle_and_hide()`.

### 12.6 `_to_idle_and_hide`
Restarts the idle-reclaim clock from the *end* of the turn
(`lifecycle.touch()`), sets state to idle, sleeps `IDLE_LINGER = 0.7`s
(without this, the pill vanishing the instant audio ends reads as a crash
rather than a clean completion), then — only if a *new* activation hasn't
started during the linger (`not self._recording`) — hides the window,
clears the transcript, and resumes the wake listener. The "don't hide if
recording restarted" guard prevents a fresh press during the linger window
from having its own pill yanked out from under it.

### 12.7 Interruption via cancel/accept buttons
`_on_cancel_button`: sets `self._cancel`, stops recording, calls
`stt.cancel_recording()` (which returns whatever audio *was* captured —
per the code comment this is "the exit path a wake-triggered turn most
often actually takes in practice," confirmed live 2026-08-13: "I have
mostly cancelled the thread using the FRED button" — so this audio must
still be logged, not discarded, hence `_save_wake_capture(cancelled=True,
transcript="")` still runs if `_wake_triggered`). Clears transcript, hides.

`_on_accept_button`: just sets `self._cancel` — mid-answer, this stops
talking but keeps the exchange as-is. The two buttons are noted as
"near-equivalent right now, and that's expected" — with pure hold-to-talk,
releasing the key already sends, so there's nothing left for a separate
"confirm" to confirm; they diverge only if a future latch/toggle mode is
added.

### 12.8 Type-to-talk path (`_on_type_button` / `_on_type_submit`)
`_on_type_submit(text)` runs the **exact same** `_run_turn`/`_turn_body`
pipeline a mic release does, just pre-loaded with `text` instead of a
transcription — filler/streaming/TTS/locking all behave identically to a
spoken turn. Uses the same `_turn_seq` claiming discipline as
`_on_hold_end`.

### 12.9 Proactive speech (`_speak_proactive`)
Used for reminders/timers, wired to `notifier.set_voice` at startup so
proactive interruptions speak in the same Kokoro voice as a real turn
rather than the SAPI fallback. Skipped outright if `self._recording` is
true (mid-recording) or if `_turn_lock.acquire(blocking=False)` fails (a
real turn is already running) — logged explicitly in the latter case so a
"silent" reminder is distinguishable from a real bug rather than looking
identical to one. **Pauses `self.wakeword` for the duration of the
speech**, same reasoning as a real turn — confirmed live: two captures in
the wake-word training set turned out to be FRED's *own* startup greeting,
picked up by the mic and mis-logged as user speech (speaker-to-mic bleed
false-firing the wake word). Every proactive utterance (greeting,
reminders, timers, proactive checks) routes through this one function, so
pausing here covers all of them uniformly.

### 12.10 HUD text console (`_hud_command_loop` / `_answer_hud_command`)
Polls `~/voice-line/command.json` every `HUD_COMMAND_POLL = 0.4`s (a plain
file, not a socket — deliberate, since this is "one text box, used
occasionally, not a stream," and the whole point of the file-bus design is
neither side needs to be running for the other to work). Ignores commands
older than `HUD_COMMAND_MAX_AGE = 15.0`s (leftover from a restarted FRED or
an abandoned browser tab). `_answer_hud_command` runs the text through
`orchestrator.process()` (synchronous, non-streamed — no background
producer thread, so the `_turn_lock` hold trivially covers both the
llama.cpp call and the speech, same non-overlap guarantee `_run_turn`
achieves via `producer.join()`), fires `on_reply(text)` (writes
`command_reply.json`) **the instant the reply text exists**, before
speaking even starts — the waiting HTTP request on the HUD side only wants
the text, not for FRED to finish reading it aloud — then still speaks
inside the same lock hold afterward, so a following command still can't
start until this one has actually finished talking.

---

## 13. Constants quick-reference (all in `Core/config/settings.py`)

| Constant | Value | Purpose |
|---|---|---|
| `STT_SAMPLE_RATE` | 16000 | Both Vosk and Whisper input rate |
| `STT_MODEL_PATH` | `models/vosk-model-en-in-0.5` | CLI Vosk model, India-English tuned |
| `WHISPER_MODEL` | `"large-v3-turbo"` | GUI STT model |
| `WHISPER_DEVICE` / `WHISPER_COMPUTE_TYPE` | `"auto"` / `"auto"` | CTranslate2 resolves CUDA vs CPU independently of torch |
| `WHISPER_BEAM_SIZE` | 5 | Raised from 1 (greedy) 2026-08-15 after a far-field misrecognition |
| `WHISPER_CONDITION_ON_PREVIOUS` | `False` | Prevents cross-utterance bias / repetition loops |
| `WHISPER_PROMPT_BASE` | command-word string | Primes decoding toward FRED vocabulary; contact names appended at runtime |
| `WHISPER_WARMUP_ON_RELOAD` | `True` | Avoids ~14s cold-CUDA cost landing on first utterance |
| `MAX_UTTERANCE_SECONDS` | 60 | Hard cap on one held recording |
| `TTS_VOICE` | `"David"` | SAPI voice name fragment, CLI only |
| `KOKORO_VOICE` | `"am_michael"` | GUI voice |
| `KOKORO_VOICE_BLEND` | `None` | Optional (name, weight) tensor blend |
| `KOKORO_SPEED` | 1.2 | Playback speed |
| `TTS_PREROLL_SEC` | 1.5 | Silent lead-in per turn; Bluetooth ramp fix, tuned 0.35→1.0→1.5 |
| `TTS_POSTROLL_SEC` | 1.0 | Silent tail per turn; BT downstream-latency cutoff fix |
| `WAKEWORD_MODEL_PATH` | `models/wakeword/hey_fred.onnx` | Trained openWakeWord model |
| `WAKEWORD_THRESHOLD` | 0.35 | Tuned 0.6→0.4→0.25→0.35, all 2026-08-10, all from live measurements |
| `WAKE_WORD_ENABLED` / `WAKE_PHRASES` | — | Legacy CLI Vosk-text-match wake list; superseded in GUI mode by the acoustic model |
| `THINKING_LENGTH_THRESHOLD` | 175 | Not voice-specific, but affects perceived reply latency — messages longer than this trigger LLM "thinking" mode, which is silent for 60-110s+ before any streamed audio; raised from 75 same-day 2026-08-20 because 75 triggered on nearly every normal message |
| `WHISPER_UNLOAD_AFTER_LLM_SECONDS` | 15 min (after LLM's own 60 min) | Idle VRAM reclaim waterfall |
| `KOKORO_UNLOAD_AFTER_WHISPER_SECONDS` | 15 min | RAM only (Kokoro is CPU-only in this environment) — reclaim is a bonus, not a VRAM necessity |

---

## 14. Gaps / things this doc could not fully verify from source alone

- `Core/audio/__init__.py` is an **empty file** — the `audio` package has no
  package-level exports; every consumer imports submodules directly
  (`from audio import device_info`, `from audio.tts_kokoro import
  KokoroTTS`, etc.).
- `ModelLifecycle` (`utils/model_lifecycle.py`), `orchestrator.py`'s
  `process_stream`/`llm_client.generate_stream`, `PillWindow`
  (`ui/pill/window.py`), and the HUD server (`hud/server.py`,
  `utils/voice_line.py`) are referenced constantly by `pill_app.py` but are
  out of this doc's assigned scope — read those files directly before
  relying on any behavior this doc only describes from the call-site side
  (e.g. exactly how `ModelLifecycle.preload()`/`touch()` schedule reloads,
  or the voice-line bus's on-disk JSON schema).
- The exact wire format of `~/voice-line/command.json` /
  `command_reply.json` / `audio_devices.json` is inferred from
  `pill_app.py` and `device_info.py`'s read/write code, not from a
  separate schema — treat the field names shown in this doc's code excerpts
  as authoritative, not a formal spec.
- `orchestrator.intent.looks_social` / `match_categories` (used by
  `fillers.pick_filler`) and `orchestrator.canned_replies.is_canned` (used
  by `merged_source`) were not read in full — only their call-site contract
  is documented here.
