# 08 — UI and Vision: the Pill, the HUD, and the Screen Watcher

This document covers three subsystems of FRED that are visually/perceptually facing but architecturally separate from the voice-turn control flow (documented elsewhere in `03_voice_pipeline.md`, which owns `Core/ui/pill_app.py`'s turn logic — this file only documents pill_app.py's role as the thing that *drives the window*):

1. **The pill** — a native Win32 layered popup window (`Core/ui/pill/`) that is FRED's only visible on-screen presence during a turn.
2. **The HUD** — a full-screen "Iron Man arc reactor" browser page (`hud/index.html` + `hud/server.py`) that mirrors FRED's live state for spectator/monitoring purposes, opened on demand from the tray.
3. **The screen watcher** — a background, idle-triggered subsystem (`Core/vision/`) that periodically screenshots the desktop, describes it with a vision LLM, and caches a short text description other tools can read ("what's on my screen").

All three are independent processes or independent concerns from the orchestrator/LLM pipeline: the pill is a window inside the main process, the HUD server is a spawned child process, and the screen watcher is *also* a spawned child process (deliberately, for reasons below).

---

## 1. The pill: native Win32 layered window architecture

### 1.1 Why native Win32 over pywebview/WebView2

`Core/ui/pill/layered.py`'s module docstring is explicit about this, and it is worth quoting the reasoning precisely because it's a real technical constraint, not a style preference:

> `UpdateLayeredWindow` + a manually built ARGB DIB is the only way to get real desktop transparency here. The alternatives were tried and don't work: WebView2 can't do per-pixel alpha at all, and `DwmExtendFrameIntoClientArea` only fakes a frosted-glass tint on modern Windows. This is architecturally what a compositor-blended overlay (the NVIDIA Alt+R style) does, minus the GPU path — the one structural cost is that `UpdateLayeredWindow` requires a CPU-side bitmap, so pixels must round-trip through host memory.

In other words: the pill needs a borderless popup that sits over arbitrary desktop content with genuine per-pixel alpha (soft glow, rounded capsule edges, anti-aliased text) rather than a rectangular window with a solid or blurred-but-opaque backing. WebView2/pywebview (Chromium-backed) cannot produce per-pixel transparency at the OS compositor level — at best you get a translucent tint over whatever's behind the window (`DwmExtendFrameIntoClientArea`, the "frosted glass" effect), which is a flat single-alpha blend, not a real alpha channel per pixel. Real per-pixel alpha requires talking to `UpdateLayeredWindow` directly, and `pywin32` wraps that call but not the surrounding pieces needed to feed it a bitmap (`CreateDIBSection`, `BITMAPINFO`), hence the raw `ctypes` structures in `layered.py`.

### 1.2 The four files and their jobs

**`Core/ui/pill/layered.py`** — the Win32 primitives layer, no application logic:
- `BITMAPINFOHEADER`/`BITMAPINFO`/`POINT`/`SIZE`/`BLENDFUNCTION` — raw `ctypes.Structure` mirrors of the Win32 GDI structs pywin32 doesn't expose.
- `premultiplied_bgra(img)` — converts a PIL RGBA image to **premultiplied** BGRA. This matters: `UpdateLayeredWindow` with `AC_SRC_ALPHA` requires premultiplied alpha, and skipping this step produces visible bright fringing around every soft edge — exactly where a glowing pill shows it worst. (`out[:,:,0:3] = arr[:,:,0:3] * alpha`, then channel-swap RGB→BGR.)
- `update_layered_window(hwnd, pil_image, x, y)` — the actual blit: builds a top-down 32bpp DIB section via `CreateDIBSection`, `memmove`s the premultiplied bytes in, then calls `user32.UpdateLayeredWindow` with `ULW_ALPHA`.
- `get_screen_size()` / `get_work_area()` — `get_work_area()` reads `SPI_GETWORKAREA` (0x0030) via `SystemParametersInfoW`, i.e. the desktop area *excluding the taskbar* — this is what lets the pill sit just above a visible taskbar but drop to the true screen edge when the taskbar auto-hides.
- `register_class(class_name, wnd_proc)` — thin wrapper around `win32gui.RegisterClass`; swallows "already registered" (happens on same-process relaunch) since the class name is itself a valid reference at that point.
- `create_layered_popup(...)` — creates the borderless always-on-top window with `WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TOPMOST`, optionally `| WS_EX_TRANSPARENT` for full click-through. Notably: **`WS_EX_TOPMOST` is set at creation time, not via a later `SetWindowPos(HWND_TOPMOST)` call** — the docstring records that on this exact window type (layered + tool + noactivate), a post-hoc `SetWindowPos` call *reports success* yet silently fails to actually set the topmost bit, leaving the window sinking behind normal windows on the next click. This was confirmed by live testing, not theorized — passing the flag to `CreateWindowEx` is the only version that sticks.
- `raise_to_top(hwnd)` — called on every `show()`; only re-asserts z-order among *other* always-on-top windows (topmost-vs-topmost ordering), since the base topmost-ness already comes from creation-time `WS_EX_TOPMOST`. A failure here is cosmetic, not structural.
- `HTTRANSPARENT` / `HTCLIENT` constants — used for pass-through hit testing (see window.py below).

**`Core/ui/pill/window.py`** — owns the single native `HWND` and the render loop thread. Key architectural note from its docstring: this used to be **three separate windows** (an earlier "atticked orb overlay" design), because a click-through window (`WS_EX_TRANSPARENT`) can never receive input at all, so anything clickable had to live in its own separate HWND layered on top. The current design collapses this to **one window** by handling `WM_NCHITTEST` itself: for every pixel outside the three circular buttons it replies `HTTRANSPARENT` (click falls through to whatever's underneath — e.g. you can click through the pill onto your IDE), and inside a button it replies `HTCLIENT` so the click lands normally. `_hit_button()` and `_on_lbuttonup()` implement this by checking Euclidean distance to each button centre (`render.button_centres()` — see below) against `BTN_D/2`.

Class responsibilities:
- `create()` — computes bottom-centre screen position via `R.canvas_origin_for_bottom_centre()` against the *work area* (not full screen), creates the popup window, and draws **one frame before ever showing it** so it can't flash as an uninitialised black rectangle on first appearance.
- `run(on_ready=None)` — blocking call: starts the render-loop thread, then `win32gui.PumpMessages()`. This runs on the **main thread**, and per `pill_app.py`'s threading contract, the low-level keyboard hook is installed on this same thread (Windows delivers low-level hook callbacks through the installing thread's message queue).
- `show()`/`hide()` — toggles `SW_SHOWNOACTIVATE`/`SW_HIDE`. `show()` calls `raise_to_top()` afterward for the reason above.
- `reposition()` — recomputes placement against the current work area on demand (resolution change, taskbar shown/hidden).
- State setters pushed from the controller: `set_state`, `set_level` (clamped 0..1), `set_transcript(text, ttl)`, `clear_transcript()`, `set_indicator(indicator)`. (`pill_app.py` wraps `set_state`/`set_level` at the app layer to also mirror onto the voice-line file bus for the HUD — see `_mirror_window_to_bus` in that file; `window.py` itself knows nothing about the HUD.)
- **Render loop** (`_render_loop`, its own daemon thread, separate from both the main/message thread and any turn thread): while hidden it just sleeps 50ms and does nothing (this is *why the pill is cheap at rest* — no per-pixel image compositing happens unless the window is visible). While visible, it computes `dt` clamped to `[0, 0.25]` (so a debugger pause or heavy LLM/GPU load can't make the animation jump a large distance in a single frame), advances an internal `_phase` accumulator, and blits via `render.render_pill(...)` → `layered.update_layered_window(...)`.
- **Per-state frame rate** — `TICK_MS = {"idle": 80, "listening": 33, "thinking": 40, "speaking": 33, "working": 50}`. `listening`/`speaking` run near 30fps (they track live audio and need to look smooth); `idle`/`working` are throttled slower since nothing time-critical is happening.
- `_signed_lo_hi(lparam)` — a small but important correctness detail: Win32 packs x/y into a single `lparam` as *unsigned* 16-bit halves, but screen coordinates can be negative on a multi-monitor setup where the primary monitor isn't the leftmost/topmost. An unsigned read silently breaks hit-testing off the primary monitor; this function does the sign-extension by hand.

**`Core/ui/pill/render.py`** — pure rendering, no Win32 calls, produces one RGBA `PIL.Image` per frame (`render_pill(...)`) ready for `update_layered_window`. No side effects beyond a module-level font cache.
- **Layout geometry**: capsule is `PILL_W × PILL_H` = `(208 + BTN_D + BTN_GAP) × 46` px (widened from an original 2-button design to fit a third "type" button — see §1.2 button layout below), with circular `BTN_D=32` buttons inset `BTN_INSET=7` from each end and `BTN_GAP=8` between adjacent buttons.
- **Transcript sits *above* the capsule, not below.** The docstring is explicit this is forced by geometry, not preference: the capsule lives at the very bottom edge of the screen (`canvas_origin_for_bottom_centre` anchors it near the taskbar), so there is no room beneath it for a text line. Both the transcript band (`TEXT_H=44`) and the capsule live on **one canvas** (`CANVAS_W=620 × CANVAS_H = TEXT_H+TEXT_GAP+PILL_H+PAD*2`), i.e. one window, one blit — this keeps the transcript text and the capsule from ever visually drifting apart, since they're drawn together every frame from the same state.
- `button_centres()` returns `(left, right, type)` centres — shared between rendering and `window.py`'s hit-testing so the clickable region is always exactly what was drawn. Three buttons left→right: **cancel** (X glyph, dark grey), **accept** (checkmark glyph, white), **type** (I-beam/cursor glyph, dark grey, added later and occupying the pill's far-right slot; accept shifted one button-width+gap left of its old position to make room).
- `indicator_box()` — the rectangle *between* cancel and accept (not extending to the type button) where the active indicator animation renders.
- **Per-state accent colours** (`STATE_ACCENT`) drawn as a faint rim + blurred outer glow around the capsule, existing purely as a secondary/at-a-glance state cue (the indicator animation is the primary signal): idle=grey-blue `(120,130,145)`, listening=cyan `(90,200,255)`, thinking=purple `(170,130,255)`, speaking=amber `(255,180,90)`, working=green `(90,230,170)`. `STATE_ACCENT_STRENGTH` is `0.20` for idle (subtle) vs `0.85` for every active state.
- Font loading tries `segoeui.ttf` → `arial.ttf` → `DejaVuSans.ttf` → PIL's bitmap default, in that order.
- Transcript text: truncated via a binary search over `draw.textlength` against `CANVAS_W - 40` px, appending an ellipsis; drawn with a 5-direction shadow offset for legibility over arbitrary desktop content (it floats over the desktop, not a solid backdrop, so it needs its own contrast).
- `canvas_origin_for_bottom_centre(work_right, work_bottom, work_left, margin)` — computes screen placement so the **capsule** (not the padded canvas) sits at bottom-centre with `margin` clearance; accounts for the empty transcript space above the capsule so that space doesn't push the capsule upward from where it visually should sit.

**`Core/ui/pill/indicators.py`** — the two interchangeable center-of-pill visualizations, picked **at random per activation** by `random_indicator()` so both get exercised in real use rather than compared only from screenshots (deliberate A/B-in-production approach, called out explicitly in the module docstring). Both implement `.render(width, height, state, phase, level) -> PIL.Image`:
- **`BarsIndicator`** — the "Typeless" reference look: 11 vertical white bars (`bar_count=11, bar_w=2, gap=4`) that collapse to a dotted row at rest and grow with `level`. Uses a rolling `_history` array shifted left each frame so energy visibly "travels" across the bars rather than pumping in unison, with a taper function (`0.45 + 0.55·sin(...)`) so the row reads as a shape rather than a flat bar chart. Distinct per-state animation curves: `listening`/`speaking` drive off live `level` + jitter; `thinking` is a single travelling Gaussian bump; `working` is *two slower counter-travelling bumps* — deliberately visually distinct from `thinking` so a long-running task doesn't read as a hung prompt; `idle` is a very shallow (`0.06` amplitude) breathing motion so it doesn't look dead.
- **`RibbonIndicator`** — the "iOS Siri" reference look: layered translucent numpy-rendered ribbons of spectrum light (cyan → white → amber → deep red, left to right, via explicit RGB colour stops rather than an HSV hue sweep, because the docstring notes a pure hue ramp can't reproduce the desaturated-white middle section the reference has). Three `LAYERS` (frequency, amplitude, phase offset, speed, thickness) additively accumulate into RGBA arrays, normalized by accumulated weight so overlapping bands brighten toward white instead of saturating one channel, then Gaussian-blurred and alpha-composited over itself for bloom. Comment explicitly flags this as a deliberate departure from an earlier "one colour set, not a rainbow" design instruction — because the supplied Siri visual reference is genuinely a full spectrum, "the reference wins." `BarsIndicator` is kept as the monochrome fallback option if the rainbow choice is later judged wrong.
- Both handle the same five states (`idle`, `listening`, `thinking`, `speaking`, `working`); `level` is real audio amplitude (mic RMS while listening, TTS PCM while speaking) and `thinking`/`working` have no audio so they animate purely off `phase`.

**`Core/ui/pill/__init__.py`** — empty (just marks `ui.pill` as a package).

### 1.3 `--mock` mode

Entry point is `fred_popup.py` (repo root), which supports:

```
python fred_popup.py --mock
python fred_popup.py --mock --indicator bars   # or "ribbon"
```

Implementation is `run_mock()` in `fred_popup.py` (not in `Core/ui/`): it constructs a bare `PillWindow` directly (no `PillApp`, no orchestrator, no STT/TTS, no LLM, no mic), starts a driver thread that cycles through `["idle", "listening", "thinking", "speaking", "working"]` on a fixed dwell timer (default `dwell=4.0` seconds each), and feeds synthetic amplitude for the audio-reactive states shaped to look like real speech rather than a flat sine — `env = max(0, sin(t*0.7))**2` gated by an inner `abs(sin(t*3.1))` term to produce bursts and gaps. During the `thinking` phase it also sets a fixed mock transcript (`"mock: what is the weather tomorrow"`). `--indicator` forces one of the two indicator classes by matching against `ALL_INDICATORS` (`bars`/`ribbon`); omitted, it picks randomly same as production.

**Why it exists**: per the top-of-file comment in `fred_popup.py`, "booting the whole stack to look at an animation is a waste of a minute" — mock mode is the fast iteration loop for any pure visual work on the pill (colours, geometry, animation timing) without paying the cost of loading Whisper/Kokoro/the LLM or needing a working microphone.

### 1.4 `input_popup.py` — the type-a-message fallback

`Core/ui/pill/input_popup.py`'s `TypeInputPopup` is a **separate, plain Win32 child-control window**, not layered/PIL-drawn like the pill. The reasoning (stated directly in the module docstring): a bitmap-blitted layered window can't take keyboard input, so text entry needs a real native `EDIT` control, which means a conventional (non-layered) popup window using the same `win32gui`/`win32con` primitives `layered.py` already provides.

- Spawned by clicking the pill's third ("type") button (`pill_app.py`'s `_on_type_button`), positioned just above the capsule.
- Structure: one `STATIC` label control (pre-filled with the last exchange text, for context — "showing the last exchange for context") above one single-line `EDIT` control.
- **Lazy-create-on-first-show**: the window/controls are created once on first `show()` and then just shown/hidden thereafter — explicitly *not* the same pattern as anything else in the pill code, kept here because issuing `CreateWindowEx` inside a hot click handler would be wasted repeated work on every reopen.
- **Subclassing**: the `EDIT` control's window procedure is replaced (`SetWindowLong(..., GWL_WNDPROC, self._edit_proc)`) to intercept `WM_KEYDOWN` for `VK_RETURN` (submit: grabs the text, hides the popup, calls `on_submit(text)`) and `VK_ESCAPE` (just hides). This is necessary because a plain (non-dialog) single-line `EDIT` control neither submits on Enter nor notifies its parent of Escape by default — dialog-box keyboard semantics only exist inside a real dialog, which this isn't.
- **Click-outside-to-dismiss**: handled via `WM_ACTIVATE` — if the new activation state is `WA_INACTIVE` (i.e. focus moved elsewhere), the popup hides itself. This gives "click outside the box to dismiss" behavior without installing a global mouse hook.
- `pill_app.py`'s `_on_type_submit(text)` feeds the typed text into the **exact same turn pipeline** a hotkey release uses (`_run_turn` → `_turn_body`), just pre-loaded with the typed string instead of a live transcription — filler injection, streaming, TTS, and turn-locking all behave identically to a spoken turn.

### 1.5 pill_app.py's role as UI/window driver (not turn control)

`Core/ui/pill_app.py`'s `PillApp` class constructs and owns the `PillWindow` and `TypeInputPopup` instances (`self.window`, `self.type_popup`), and is the thing that calls `window.set_state`/`set_level`/`set_transcript`/`show`/`hide`/`set_indicator` at each point in a turn's lifecycle. From the UI-rendering perspective (as opposed to the turn-control perspective documented in `03_voice_pipeline.md`), the load-bearing details are:

- **`_mirror_window_to_bus`**: wraps `window.set_state`/`window.set_level` at construction time (not by adding a publish call at each of the ~8 call sites that change pill state) so that *every* future call to either setter also mirrors onto the `VoiceLineBus` file bus for the HUD to read. This wrapping-once approach means a call site added later can't silently forget to publish. Both wrapper functions are fire-and-forget: a bus-publish failure is caught and printed, never allowed to affect the pill's own on-screen update, since the pill is "the thing the user is actually looking at" and must never be degraded by a spectator feature failing.
- **New random indicator per activation** — `self.window.set_indicator(random_indicator())` is called at the top of every `_on_hold_start`, so consecutive turns visibly alternate between the Bars and Ribbon looks in real use.
- **`IDLE_LINGER = 0.7`** seconds — after a turn ends, `_to_idle_and_hide()` sets state to `idle`, sleeps 0.7s, *then* hides the window (unless a new activation started during the linger). Documented reason: without this pause, the popup vanishes the instant audio ends, which reads as a crash rather than a completion — the brief visible "done" beat is a deliberate UX signal.
- **`TRANSCRIPT_TTL = 2.5`** seconds — how long the "what you said" line stays visible above the capsule; deliberately *not* a confirmation gate (no mandatory pause before the model starts working) — the transcript is shown for legibility, cancel-if-misheard via the X button, not as a blocking approval step.
- Tool-in-progress captions (`_on_tool_event`) are shown on the pill via `window.set_transcript(f"{label}...", ttl=2.0)`, giving a visual cue for tool calls even without audio.
- Ambiguous-router-choice notices (`_on_ambiguous_choice`) get a longer TTL (3.5s, since there's more text) and are shown-only, never spoken.

For the actual turn state machine (hold-to-talk, wake-word, cancellation, the `_turn_seq` staleness-discard mechanism, the llama.cpp non-reentrancy hazard, filler/merged-stream TTS), see `03_voice_pipeline.md` — this document intentionally does not restate that content.

---

## 2. The browser-based HUD

Two files at the repo root (not under `Core/`): `hud/server.py` (Python stdlib HTTP server + telemetry) and `hud/index.html` (the actual "Iron Man arc reactor" page, self-contained single-file HTML/CSS/JS).

### 2.1 `hud/server.py` — loopback-only server design

Run as:
```
python hud/server.py              # real telemetry, 127.0.0.1:8777
python hud/server.py --mock       # scripted state loop + fake stats, :8778
```

**Why loopback-only (`127.0.0.1`, never `0.0.0.0`)**: stated directly in the module docstring — "this exposes live machine telemetry, and there is no reason for anything off this box to be able to read it." No auth layer exists or is needed because the bind itself is the security boundary.

**Dependencies**: stdlib (`http.server.ThreadingHTTPServer`) plus `psutil` (hard-required, `sys.exit`s with a pip-install message if missing) and optionally `sounddevice` (soft-required — `/devices` just reports itself unavailable if absent, doesn't crash the server). GPU numbers come from shelling out to `nvidia-smi`; if it's missing (no NVIDIA card, or not on PATH) every GPU field comes back `None` and the HUD renders `"--"` rather than failing.

**Threading model**: a single background `Telemetry` thread polls `POLL_SECONDS = 1.0` and snapshots CPU/RAM/GPU/disk/swap/power stats under a lock, so an incoming HTTP request never blocks waiting on `nvidia-smi` (which can itself take a few hundred ms). `ThreadingHTTPServer` handles concurrent requests without extra plumbing.

**Endpoints**:
- `GET /` or `/index.html` — serves `hud/index.html` verbatim, `Cache-Control: no-store`.
- `GET /state` — the polling endpoint the page hits every 1200ms client-side (`POLL_MS` in the JS). Returns the telemetry snapshot plus `state`/`level` (from the file bus, see below), `subsystems`, `turns` (radar-scope data), `diagnostics` (log lines), and `timer_s` (seconds remaining on the soonest running reminder/timer).
- `GET /devices` — mic/speaker catalog for the hover dropdown, filtered to the WASAPI host API only (PortAudio otherwise lists every physical device once per host API — MME, DirectSound, WASAPI, WDM-KS — which the code notes was confirmed live as ~35 entries for 4 real devices on a 2-mic/2-speaker machine; WASAPI is what Windows itself calls the "default device," so filtering to it removes the duplication). This WASAPI-index-finding logic is **deliberately duplicated** from `Core/audio/device_info.py` rather than imported, because the server is stdlib-only otherwise by design and doesn't import the `Core` package.
- `POST /command` — the text-console channel (see file-bus mechanism below).

### 2.2 The file-bus mechanism

State arrives as plain files under `~/voice-line/` (i.e. `Path.home() / "voice-line"`, referenced as `BUS_DIR`), written by FRED's main process (`Core/utils/voice_line.py`'s `VoiceLineBus`, called from `pill_app.py`) and **read-only from the server's side** — `hud/server.py` never writes the `state`/`waveform`/`systems` files, guaranteeing the HUD can never corrupt whatever owns the bus.

Files:
- `~/voice-line/state` — one word: `idle | listening | thinking | speaking | alert`.
- `~/voice-line/waveform` — one or more floats (space/comma separated) representing voice amplitude in `0..1`.
- `~/voice-line/systems` — JSON dict of which FRED models are resident (used by `read_subsystems()`).
- `~/voice-line/audio_devices.json` — currently-selected mic/speaker indices (published by `Core/audio/device_info.py`).
- `~/voice-line/command.json` / `~/voice-line/command_reply.json` — the one bidirectional exception (see below).

Neither `state` nor `waveform` has to exist: their absence reads as "idle," which is deliberately *the same code path* as a crashed/stopped producer — no special "offline" detection needed for that case.

**Two robustness rules**, both explicit in the docstring:
- **STOMP TOLERANCE** — a *fresh* waveform outranks the state file: if audio is demonstrably flowing right now (`wave_age <= STOMP_FRESH_SECONDS = 0.4` and `level > SPEAK_LEVEL_FLOOR = 0.02`), the HUD reports `speaking` even if the state file still says `thinking` or was never updated at all for this turn. Sound actually coming out of the speakers is the more trustworthy signal than whatever state string happened to be written last.
- **STALENESS** — a `state` file untouched for more than `STALE_SECONDS = 5.0` is ignored entirely and the HUD eases back to `idle`. This means a producer that dies mid-turn decays to calm rather than freezing the HUD forever in `"thinking"`.

**The console text box is the one write path**: `POST /command` → `submit_command(text)` writes `command.json` with a fresh `uuid4().hex` request id, then polls `command_reply.json` up to `COMMAND_TIMEOUT = 45.0` seconds (0.25s poll interval) waiting for a reply whose `id` matches. The fresh id per request exists specifically so a slow reply from a *stale* request (FRED restarted mid-wait, or two commands landed close together) is never mistaken for the answer to the current one. On the FRED side, `Core/ui/pill_app.py`'s `_hud_command_loop` (polling every `HUD_COMMAND_POLL = 0.4`s, discarding anything older than `HUD_COMMAND_MAX_AGE = 15.0`s as a stale leftover from a since-closed browser tab) is the only thing that reads `command.json` and writes `command_reply.json` — `_answer_hud_command` then runs the typed text through the same orchestrator a spoken turn uses and **speaks the reply through Kokoro same as a mic turn**, not just returning text. The HTTP response returns as soon as the reply text exists (`on_reply` fires before speaking starts), but the whole call is still held under `_turn_lock`, so a second command (typed or spoken) can't start until the HUD-typed one has actually finished talking — same serialization guarantee a mic turn gets.

### 2.3 Telemetry, radar, and diagnostic log

- `read_gpu()` shells to `nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,power.limit --format=csv,noheader,nounits`; individual fields that a given card/driver doesn't support come back as `"[N/A]"` and are parsed defensively per-field rather than failing the whole read.
- `read_timer()` opens the APScheduler jobstore SQLite DB (`Core/data/reminders.sqlite`) in `mode=ro` — explicitly **never** locks or writes it, since APScheduler is actively using that file; the HUD is "a spectator here." It reads only `MIN(next_run_time)` for jobs whose id starts with `timer`, and deliberately does **not** unpickle the job's own state BLOB (which would require every scheduled callable to be importable in this process) — costing only the timer's label, which a bare countdown doesn't need.
- `read_turns(events)` builds the radar-scope data by pairing `user_speech` events with the *next non-filler* `fred_speech` event from the day's session JSONL log (`Core/data/logs/sessions/session_<date>.jsonl`, tail-read up to `LOG_TAIL_BYTES = 64_000`) — filler is explicitly skipped because it's spoken before the model has produced anything, so pairing against it would report every turn as instant.
- `read_diagnostics(events)` filters the same log to a small allow-list of event types worth a HUD line (`tool_call`, `tool_event`, `ambiguous_choice`, `error`, `system`) — deliberately excluding bulky log content like full transcripts or health-check dumps as "just noise here."

### 2.4 `hud/index.html` — real-time state indicator and design

Self-contained single HTML file: inline CSS (custom properties drive per-state animation speed/glow), inline JS, fonts declared local-only (`Chakra Petch`/`JetBrains Mono` with system fallbacks) since the page must work with zero network access (loopback-only server, per above).

**State table** (`STATES` object in JS) drives every animation timing and glow intensity from one place:

| state | outer ring spin | inner ring spin | coil cycle | radar sweep | core pulse | glow | label |
|---|---|---|---|---|---|---|---|
| idle | 46s | 30s | 160s | 5.2s | 3.4s | 0.40 | STABLE |
| listening | 24s | 16s | 90s | 2.4s | 2.4s | 0.80 | RECEIVING |
| thinking | 8s | 5s | 32s | 1.0s | 1.0s | 0.70 | COMPUTING |
| speaking | 30s | 20s | 120s | 4.4s | 0.9s | 1.0 | TRANSMITTING |
| alert | 11s | 7s | 44s | 0.8s | 0.7s | 1.0 | FAULT |

`alert` additionally applies `filter: hue-rotate(158deg) saturate(1.35)` to the *entire stage* rather than swapping ~80 hardcoded `rgba()` colour values individually — a `ponytail:` comment in the source flags this explicitly as a deliberate corner-cut ("blanket hue-rotate... reads unmistakably as alarm anyway; give every colour a CSS variable and swap the palette if that bothers you" — i.e. the known ceiling/upgrade path is documented in the code itself).

The page polls `GET /state` every `POLL_MS = 1200` ms via `fetch`. `applyState()` only runs (and only re-triggers CSS animation-duration changes) when the state actually changes (`if (next === shownState) return`), avoiding animation restarts on every poll tick even though state is usually unchanged between polls.

**Live voice level** (`--lvl` CSS custom property) is eased per-`requestAnimationFrame` (not per-poll) toward `level` while `state === "speaking"`, with asymmetric attack/release rates (`dt * (target > cur ? 9 : 3.5)`) so the reactor core visibly swells and settles with actual speech loudness rather than snapping between the 1.2s poll samples.

**`?mockstate=<state>&level=<0..1>`** query params pin the HUD to one state regardless of the live bus — used for visually capturing/comparing each look without waiting for a real turn to occur. **`?still`** disables all CSS animations/transitions (`body.still * { animation:none!important; transition:none!important }`) so headless-Chrome screenshots capture a fair static frame instead of blank panels (entry animations use `fill-mode: both`, which holds their `opacity:0` starting frame until their delay elapses — a screenshot taken before that elapses without `?still` would literally show blank panels).

**`fit()`** computes `--hud-scale = min(innerWidth/1600, innerHeight/900)` and applies it as a `transform: scale()` on the whole 1600×900 `#stage` div, so the entire fixed-size layout scales uniformly to whatever window/monitor it's displayed on. Guards against being called before Chrome has composited a frame (`innerWidth`/`innerHeight` still 0 in a kiosk window at very early load) — this is the exact bug referenced by the user's stored memory item "HUD text box fixed — `--hud-scale` zero-collapse bug," fixed 2026-08-03 and reconfirmed working 2026-08-18.

**Screen Wake Lock**: `keepScreenAwake()` calls `navigator.wakeLock.request("screen")` whenever the document becomes visible, re-acquiring on `visibilitychange` (Chrome drops the lock on tab-hide or system suspend). The module comment explains *why this lives client-side rather than server-side*: the lock's lifetime is exactly the page's, so closing the HUD releases it automatically with no teardown code to forget; a `Win32 SetThreadExecutionState` call from `hud/server.py` was considered and rejected because the server runs whether or not the HUD window is actually open, and would have kept the screen on forever regardless. Secure-context requirement is satisfied by `127.0.0.1` (which is why the loopback bind matters for more than just privacy). A denied request (battery saver, policy) is silently accepted — "the HUD is still perfectly usable, the screen just sleeps as it did before," no error state shown.

**Tray-icon interaction model**: the HUD window is never auto-opened by FRED. `HudManager.start_server()` (below) brings the *server* up quietly and early at boot; the *window* only appears when the user clicks "Show HUD" from the system tray icon (`pill_app.py`'s `_start_tray`, where it's the default/left-click action on the tray icon, not buried behind a right-click submenu — explicitly because "burying that behind a right-click menu would make the HUD feel hidden rather than on call").

**Mute/lockdown/device controls**: the mute button in the top-right sends the same `"mute"`/`"unmute"` text a spoken command would, through the same `/command` channel, routed by FRED's deterministic (non-LLM) dispatcher for near-instant response; it's optimistic client-side (flips the icon immediately, reverts only on an actual fetch failure) and reconciled against the real state on the next poll. The lock icon reflects lockdown-mode status read-only from `subsystems.locked`. The mic/speaker dropdown is lazily fetched on first hover (not on page load) since the device list rarely changes mid-session.

### 2.5 `Core/utils/hud_manager.py` — how the main process manages the HUD subprocess

`HudManager` deliberately splits into two independently-lifecycled pieces, exactly mirroring the reasoning given in its own module docstring:

> The SERVER is cheap (stdlib http.server + a 1s telemetry poll) and has to be up early, because it is what records the session while you are not looking. The WINDOW is a whole Chrome, and the user asked not to see it unless they ask for it — so the browser is only launched on demand.

- **`start_server()`** — called once at boot (`pill_app.py`'s `_on_ready`). Runs `hud/server.py` as an entirely separate OS process via `subprocess.Popen` (not a thread in FRED), specifically because it blocks in `serve_forever()`, wants its own independent lifetime, and "a crash in the HUD should cost the HUD only" — never take the voice pipeline down with it. **Idempotent**: it first probes `GET /state` on `127.0.0.1:8777`; if something is already listening (e.g. a previous FRED process that didn't shut down cleanly, or a manually-launched server), it adopts/reuses that instance rather than fighting over the port. This mirrors the identical pattern used for the phone-command API on port 8779 (`pill_app.py`'s `_start_phone_api`), both citing the same underlying Windows quirk: `HTTPServer` sets `SO_REUSEADDR`, which lets a second process bind an already-bound port instead of failing cleanly — two live servers would then split incoming requests nondeterministically, with only one of them actually wired to FRED's bus, so detecting and skipping the duplicate is required, not optional.
- **`show()`** — the tray-click action. Ensures the server is up (calling `start_server()` again if the health probe fails), then launches Chrome in kiosk mode: `--kiosk --user-data-dir=<throwaway profile> --no-first-run --disable-features=Translate <URL>`. The profile directory is deliberately a **throwaway temp profile** (`%TEMP%/fred-hud-profile`), not the user's real Chrome profile — the docstring notes pointing at the real profile "would drag in extensions, sessions and a restore prompt after any hard shutdown." Re-focusing an already-open HUD window is left entirely to the OS window manager: launching Chrome again with the *same* profile directory raises the existing window rather than opening a second one (Chrome's own single-instance-per-profile behavior), so `HudManager` does no window-tracking of its own beyond `self.window` (the `Popen` handle for `stop()` purposes). If no local Chrome install is found (checks three standard install paths), it falls back to `os.startfile(URL)` (default browser) rather than failing outright.
- **`stop()`** — terminates window then server, in that order, "so the page isn't briefly showing a dead server."
- Every method is designed so nothing here can raise into FRED's main flow — "a missing Chrome or a busy port must never stop the voice assistant from starting."

---

## 3. Screen vision subsystem

Three files: `Core/vision/screen_watcher.py` (the child process's whole world), `Core/vision/watcher_manager.py` (the main process's coordination side), `Core/vision/screen_context.py` (the shared cache read/write, importable cheaply from either side). `Core/vision/__init__.py` is empty.

### 3.1 Why a separate OS process, not a thread

This is the single most important architectural decision in this subsystem, and `screen_watcher.py`'s module docstring states the concrete failure mode it avoids:

> `llama.cpp`'s `create_chat_completion()` is a single blocking C call with no cooperative cancellation point — this codebase already hit the failure mode of two inference calls racing on one model instance (documented in `orchestrator/pill_app.py`: `"Fatal Python error: Aborted"` from two concurrent `llama_decode` calls). A background analysis task sharing the main process's model would either have to be strictly serialized with real conversation turns (defeating the point — it would block your actual request) or risk that exact crash. A separate OS process sidesteps both: it can be `terminate()`'d instantly and safely from the main process, because it's a different memory space and a different CUDA context entirely. Nothing it does can touch the real conversation model.

Concretely: a background screen-description task competing for GPU/model access with an actual conversation turn is unacceptable on two counts — either it blocks the user's real request (bad UX) or, worse, the two `llama_decode` calls race and crash the whole process (a previously-confirmed crash mode). Spawning `Core/vision/screen_watcher.py`'s `run()`/`run_once()` via `multiprocessing.Process` gives the watcher its own memory space and its own separate CUDA context, so it can be killed with `terminate()` — instantly, non-blockingly, and safely — from the main process at any moment, with zero risk to anything the real conversation model is doing.

`screen_watcher.py` itself "knows nothing about the hotkey, the pill UI, or the orchestrator — it only screenshots, describes, and writes a result for someone else to read later." This is a deliberately narrow, self-contained module: its only interface to the rest of FRED is the file it writes (`screen_context.py`'s `write()`).

### 3.2 Why it's currently disabled

From `Core/config/settings.py`'s SCREEN WATCHER section:

```python
SCREEN_WATCHER_ENABLED = False
```

Comment above it: *"Off 2026-08-19 to rule it out as the source of a reported periodic GPU spike (the actual measured cadence in the logs didn't match the report[ed pattern]... automatic idle-loop capture in watcher_manager.start() — capture_now() (on-demand 'what's on my screen') is untouched, still works either way. Flip back to True once confirmed/no longer needed."* i.e. this flag was flipped off specifically as a **diagnostic isolation step** for an unrelated bug report (a periodic GPU spike), not because the feature itself was judged broken or unwanted — and the investigation had already found the watcher's own logged cadence didn't line up with the reported spike pattern, so this was disabling it to rule it out by elimination rather than a confirmed fix. On-demand capture (`capture_now()`, i.e. the voice/HUD "what's on my screen" tool) is completely unaffected by this flag and continues to work regardless — only the *automatic, idle-triggered* background loop is gated by it.

`watcher_manager.py`'s `ScreenWatcherManager.start()` no-ops entirely (returns immediately without starting the idle-watch thread) when this flag is `False`.

### 3.3 Why it's killed immediately on hotkey press

`ScreenWatcherManager.touch()` is called at the very start of `pill_app.py`'s `_on_hold_start` (before anything else in that method) *and* at the start of `_on_hold_end`. Its docstring:

> Call on every hotkey press AND release. Resets the idle clock, and — the safety-critical part — kills the watcher immediately if it's running, so it can never be mid-inference (competing for the GPU) at the exact moment a real conversation turn is about to start. Must return fast: this is called from the same code path as the hotkey callback, which Windows silently unhooks if it blocks past ~300ms... `terminate()` is non-blocking — it signals the OS to kill the process and returns immediately, it does not wait for the process to actually exit.

This is the same GPU-contention concern as §3.1, applied at the trigger level: a real conversation turn about to start must never have to share the GPU with a background screenshot-description cycle, so `touch()` unconditionally calls `terminate()` on any live watcher child process before doing anything else. Because it's called from inside the low-level keyboard hook's code path, it must return essentially instantly — `Process.terminate()` satisfies this because it only *signals* the OS to kill the process and returns immediately without waiting for actual process exit, matching the ~300ms hard limit before Windows silently unhooks a slow low-level keyboard hook callback (documented in `Core/input/hotkey.py`).

### 3.4 `screen_watcher.py` — the child process loop

`run()` is the child process's `multiprocessing.Process` entry point, blocking forever until externally `terminate()`'d:

```python
def run():
    from llm.llm_client import LLMClient   # imported here, not module-level
    llm = LLMClient(report_status=False)
    while True:
        try:
            _run_one_cycle(llm)
        except Exception as e:
            print(f"[screen_watcher] cycle failed: {e}")
        time.sleep(SCREEN_WATCHER_INTERVAL_SECONDS)
```

`llm_client` is imported **inside** `run()`, not at module load time — deliberately, so this import (and the CUDA context it establishes) only happens once the child process has actually started, not when the parent process merely imports `screen_watcher` as a module to hand its `run` function to `multiprocessing.Process`.

The Vision model is loaded **once** and kept resident for the entire process lifetime rather than reloaded every cycle — reasoned as: the watcher runs for several minutes at a stretch between hotkey presses, so reloading (~4s per load) every `SCREEN_WATCHER_INTERVAL_SECONDS` would be pure waste. Residency ends automatically when the whole process is `terminate()`'d — there's nothing to explicitly unload.

**One cycle** (`_run_one_cycle`):
1. `_main_process_has_a_model_loaded()` — a cross-process VRAM-collision guard: reads `LLM_STATUS_PATH` (a JSON file the main process's `LLMClient` writes on every load/unload with its currently-resident tier). **Fails safe**: any read problem (missing file, bad JSON) is treated as "assume something is loaded, skip this cycle" — the comment justifies this asymmetrically: "a missed screenshot costs nothing, a VRAM collision on this machine has crashed it before." This check gates *only the local Vision-tier fallback*, not the whole cycle — cloud vision calls need no local VRAM and are always attempted first regardless of this check (this was itself a prior bug: confirmed live 2026-08-10 that gating the *entire* cycle on this check left the on-demand cache 19+ hours stale, since the main process very often has some tier resident during normal use — fixed by narrowing the gate to only the local fallback path).
2. `_capture_screenshot_data_uri()` — screenshots via `mss` (primary monitor only, `sct.monitors[1]` — deliberately not `monitors[0]`, which is "all monitors combined" and unnecessary detail for a one-sentence description), downscales via `PIL.Image.thumbnail((1024, 1024))`, encodes as PNG → base64 data URI. **The image bytes never touch disk at any point** — captured straight to an in-memory `BytesIO` buffer. The module docstring is explicit that this is the highest-privacy-stakes part of the whole feature: "a screen can show email, chat, banking, anything. Only the resulting short TEXT description is ever persisted... the image itself lives only in this process's memory for the few seconds it takes to describe it."
3. Calls `llm.describe_image(image_uri, prompt, max_tokens=500, allow_local_fallback=local_ok, skip_cloud=force_local, thinking_signal_text=question)` — a separate LLM tier/call path from the main conversation model (documented more fully in the LLM-client doc; here it's enough to note the watcher uses its **own separate `LLMClient` instance** in its own process, entirely decoupled from whatever tier the main process currently has loaded).
4. On success, writes the description via `screen_context.write(...)`.

**Prompting**: `_DESCRIBE_PROMPT` (used when no specific question is asked) asks the model to name the application and general activity (e.g. "VS Code, editing a Python file"), and to **quote exact text/errors/numbers verbatim rather than summarizing** when visible — "that's usually the point of asking." `_prompt_for(question)` builds a different, question-targeted prompt when a real question was passed through. A comment records a confirmed real bug this fixed: previously `whats_on_screen()` took no arguments at all, so a real user question (e.g. "tell me if I've used correct English") never reached the vision model — it always got the generic describe prompt, and the answer was effectively a guess if that generic description happened not to mention the relevant detail.

`max_tokens=500` (up from `describe_image`'s own default of 200) — bumped specifically so a real quoted error/traceback requested by the new verbatim-quoting instruction doesn't get truncated mid-message.

`run_once(force_local=False, question="")` is the **on-demand** variant, called via a fresh one-shot `multiprocessing.Process` by `watcher_manager.capture_now()` — loads the model, runs exactly one cycle, exits, no sleep loop, no lingering residency. `force_local=True` is the forced-local retry path: the caller has already unloaded the main process's model specifically to make a local Vision-tier attempt safe, so the VRAM check is skipped entirely (nothing left to check) and cloud is *not* retried (it just failed moments earlier in the same logical call).

### 3.5 `watcher_manager.py` — main-process coordination

`ScreenWatcherManager` (constructed once in `PillApp.__init__`, `self.screen_watcher`) owns:
- `self._process` — the current child `multiprocessing.Process` handle, or `None`.
- `self._last_hotkey_activity` — a `time.monotonic()` timestamp, reset by `touch()`.
- A `threading.Lock()` guarding all of the above, since `touch()` (hotkey thread), `_loop()` (its own idle-watch thread), and `capture_now()` (called from tool-invocation context, itself potentially concurrent) all touch the same state.

Explicitly **independent of `ModelLifecycle`** — the class that manages models resident *in the main process*. The screen watcher's model lives in a completely separate process and is governed by wall-clock hotkey-idle time, not the main process's own idle-unload policy (documented in `03_voice_pipeline.md`).

**`start()`** — called once at app boot (`pill_app.py`'s `_on_ready`). No-ops if `SCREEN_WATCHER_ENABLED` is `False` (currently the case, per §3.2) or if already running. Otherwise starts `_loop()` on a daemon thread.

**`_loop()`** — coarse polling: `time.sleep(15)` between checks ("this only ever needs minute-scale precision"), then under the lock, computes `idle_minutes = (now - last_hotkey_activity) / 60` and spawns a new watcher process (`_spawn_locked`) only if nothing is already running *and* `idle_minutes >= SCREEN_WATCHER_IDLE_MINUTES` (**5 minutes**, from settings.py — reasoned as "short enough to actually be useful, long enough that normal pauses mid-conversation don't repeatedly spin it up only to kill it again seconds later").

**`touch()`** — see §3.3 above; called on both press and release, resetting the idle clock from the moment of *release* rather than *press* (per `pill_app.py`'s comment: "the 5-minute 'hasn't touched the hotkey' window should count from when he stopped talking to FRED, not when he started").

**`capture_now(timeout=12.0, force_local=False, question="")`** — the on-demand path used by the `whats_on_screen()` tool. Spawns `screen_watcher.run_once` as a fresh one-shot `multiprocessing.Process`, tracked in the same `self._process` slot as the background loop — meaning a hotkey press mid-wait kills this exactly the same way `touch()` kills the automatic loop: "a real conversation turn always wins the GPU over an on-demand screen check." It then polls `screen_context.read()` up to `timeout` seconds waiting for a write whose age indicates it landed *after* this call started (`(time.time() - age) >= start_wall`), returning `True` if a fresh capture landed in time. Returns `False` uniformly for three different cases — already-running (skipped), timed out, or `run_once`'s own VRAM safety check silently skipped its cycle — and the docstring is explicit that this ambiguity is fine: "either way the caller just falls back to whatever's already cached."

**`stop()`** — called on app shutdown; kills any live child process so nothing survives the main process exiting as an orphan.

### 3.6 `screen_context.py` — cache/staleness model

Deliberately tiny and dependency-light: only `json` + `pathlib` imports. The module docstring explains why this matters architecturally: this file is imported by the **on-demand tool in the main process** (`whats_on_screen()`), which must never need to pull in `mss` or `llama_cpp` just to read a cached string — those heavy imports live only in `screen_watcher.py`, the child-process side.

- `write(description)` — atomic write-then-rename (`tmp.write_text(...)` then `tmp.replace(SCREEN_CONTEXT_PATH)`), the same pattern used by every other small JSON state file in the codebase (`found_cache`, `proactive_state`) — guarantees a concurrent reader never observes a half-written file.
- `read()` — returns `(description, age_seconds)`, or `(None, None)` if nothing has ever been captured. Deliberately returns the raw **age** rather than a pre-computed staleness boolean, "so the caller can phrase staleness in its own words rather than getting a bare True/False" (i.e. the tool-facing text can say "as of about 4 minutes ago" rather than a flat yes/no).
- `is_fresh(age_seconds)` — `age_seconds is not None and age_seconds <= SCREEN_CONTEXT_MAX_AGE_SECONDS`.

From `settings.py`:
```python
SCREEN_CONTEXT_PATH = DATA_DIR / "screen_context.json"
SCREEN_CONTEXT_MAX_AGE_SECONDS = 300     # 5 minutes
```
Rationale for the 300s ceiling, per settings.py's comment: "a description older than this is treated as stale rather than shown as current — the watcher may have been killed mid-cycle by a hotkey press, or simply not run yet this session." Given `touch()` kills the watcher on every hotkey press/release, a stale cache is an expected, routine occurrence, not an error condition.

### 3.7 Full numeric/value reference (settings.py, SCREEN WATCHER section)

```python
SCREEN_WATCHER_IDLE_MINUTES = 5          # no-hotkey-activity threshold before the idle loop starts
SCREEN_WATCHER_INTERVAL_SECONDS = 300    # re-screenshot cadence while the idle loop is running
SCREEN_WATCHER_ENABLED = False           # automatic idle-loop capture off (2026-08-19, GPU-spike isolation); capture_now() unaffected
LLM_STATUS_PATH = DATA_DIR / "llm_status.json"        # cross-process "what tier is resident" coordination file
SCREEN_CONTEXT_PATH = DATA_DIR / "screen_context.json" # cached description + timestamp
SCREEN_CONTEXT_MAX_AGE_SECONDS = 300     # 5 minutes — staleness ceiling on the cached description
```

`SCREEN_WATCHER_INTERVAL_SECONDS`'s in-code comment adds one more piece of history worth preserving: it currently sits at 300s (5 minutes) rather than a faster cadence with the note *"inference EVERY cycle instead[,] is exactly the GPU load `CLOUD_VISION_PROVIDER` was added to avoid in the first place. Revert to 60 once `CEREBRAS_API_KEY` is restored."* — i.e. the interval was widened as a stopgap tied to a (at time of writing) unavailable/removed Cerebras API key for cloud-tier vision, with an explicit intended reversion to 60s once that's restored. This is a temporary value, not a permanent design choice, and should be revisited if rebuilding this subsystem with cloud vision access available.

### 3.8 HUD/pill state values recap

For completeness, the full set of state-machine values referenced throughout this document, gathered in one place:

- **Pill / bus states** (`Core/ui/pill/window.py` `TICK_MS` keys, `render.py` `STATE_ACCENT` keys): `idle`, `listening`, `thinking`, `speaking`, `working`.
- **HUD / voice-line bus states** (`hud/server.py` `VALID_STATES`, `hud/index.html` `STATES`): `idle`, `listening`, `thinking`, `speaking`, `alert` — note **`working` does not appear on the HUD/bus side**; the pill has a fifth state (`working`, used for longer background tasks, visually distinct from `thinking` via the counter-travelling-bumps animation in `BarsIndicator`) that has no corresponding bus/HUD representation, while the HUD has an `alert` state (red-shifted via hue-rotate, the HUD's "something failed" indicator, triggered by `VoiceLineBus.alert()` from `pill_app.py`'s turn-failure exception handler) that the pill itself does not render distinctly — the pill just drops back to idle on a failed turn, and the alert flash is a bus-only signal, held briefly so the HUD's ~1s poll can't miss it before idle overwrites it.

---

## Rebuild checklist for this scope

To reconstruct this subsystem from scratch, in dependency order:
1. `Core/ui/pill/layered.py` — Win32 ctypes primitives (no external logic to get right beyond the premultiplied-alpha detail and the create-time-not-post-hoc `WS_EX_TOPMOST` requirement).
2. `Core/ui/pill/render.py` — pure PIL frame composition; get `CANVAS_W/H`/`PILL_X/Y` geometry and `canvas_origin_for_bottom_centre` right first, since `window.py`'s hit-testing depends on `button_centres()` matching exactly what was drawn.
3. `Core/ui/pill/window.py` — the single-HWND + `WM_NCHITTEST` click-through design; get the render-loop's hidden-vs-visible branching right (this is what makes the pill cheap at rest).
4. `Core/ui/pill/indicators.py` — can be built/iterated independently and fast using `--mock` mode once `window.py`/`render.py` exist.
5. `Core/ui/pill/input_popup.py` — depends on `layered.register_class` only.
6. `fred_popup.py`'s `run_mock()` — build this early; it's the fast iteration loop for everything above.
7. `Core/vision/screen_context.py` — trivial, no dependencies beyond `config.settings`.
8. `Core/vision/screen_watcher.py` — depends on an `LLMClient` with a working `describe_image()` method (documented in the LLM-client doc) and `mss`/`PIL`.
9. `Core/vision/watcher_manager.py` — depends on `screen_watcher.py` existing as an importable module with `run`/`run_once` functions (needed for `multiprocessing.Process(target=...)`).
10. `hud/server.py` — stdlib-only except `psutil` (required) and `sounddevice` (optional); depends on the `~/voice-line/` bus files existing (produced by `Core/utils/voice_line.py`, documented in `03_voice_pipeline.md`) and, optionally, the APScheduler jobstore DB for the timer readout.
11. `hud/index.html` — single self-contained file, no build step; can be developed against `hud/server.py --mock` or `?mockstate=`/`?still` query params without a live FRED instance.
12. `Core/utils/hud_manager.py` — thin subprocess-management wrapper around `hud/server.py`; trivial once the server exists.

## What could not be confidently documented from source alone

- The exact visual reference images ("Typeless," "iOS 27 Siri") that `indicators.py`'s comments cite as design targets were not available to inspect — descriptions above are from the code's own stated intent, not a side-by-side visual comparison.
- `SCREEN_WATCHER_INTERVAL_SECONDS`'s note about `CEREBRAS_API_KEY` implies a `CLOUD_VISION_PROVIDER` setting and cloud-vision cost/latency tradeoff that lives in the LLM-client configuration, not in this file's scope — the numeric value (300s, intended revert to 60s) is documented above, but the full cloud-vision-provider rationale belongs in whichever document covers `Core/llm/llm_client.py`.
- The exact shape/contents of `~/voice-line/systems` (subsystem-status JSON) beyond the `llm`/`whisper`/`kokoro`/`muted`/`locked` keys observed in `pill_app.py`'s `_subsystem_status()` was not independently verified against `Core/utils/voice_line.py`'s writer side, which is out of this document's assigned scope.
