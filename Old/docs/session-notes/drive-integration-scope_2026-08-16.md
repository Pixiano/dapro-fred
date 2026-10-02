# DaPro Drive → FRED — scoping notes, 2026-08-16

**Status: discussion, nothing built.** Written from a conversation on
2026-08-15/16. Deliberately NOT added to `Core/config/settings.py`'s
`DOCS_FILES`, so `ask_about_myself` can never read this and tell Vatsal
FRED has capabilities it doesn't have — that tool exists to stop exactly
that failure.

---

## The idea

FRED reads recent photos and videos off DaPro Drive and answers "what
have I been up to" — optionally running media through a vision model.

DaPro Drive: self-hosted Nextcloud + MariaDB in Docker. Auto-uploads the
camera roll, tags photos by face and location, spills to a second drive
when the primary fills, reachable anywhere over Tailscale with scoped
public links for friends/family.

---

## Two findings that reshape it

**1. FRED already has cloud vision.** `describe_image()` exists, and
`CLOUD_VISION_PROVIDER` points at Cerebras `gemma-4-31b` with automatic
local fallback (added 2026-08-09 to stop the local Vision tier fighting
the main model for the card). So this needs no new inference path and
**no Gemini** — adding a second provider means a second key and a second
set of data-processing terms to vet, for a capability already wired and
already vetted.

**2. Nextcloud has already done the expensive part.** It tags faces and
locations itself. FRED should read what Nextcloud already computed, not
re-run face recognition or geotagging. Combined with EXIF timestamps,
"who / where / when" is available with zero inference.

---

## Phase 1 — metadata only, no vision at all

WebDAV listing (`requests` is already a dependency) + existing face and
location tags + EXIF dates, grouped by day:

> "Last Saturday: 40 photos and 2 videos, mostly around <location>, with
> <tagged people>. Sunday: nothing until evening, then 12 photos."

No image leaves the machine. No model runs. For "what have I been up to",
the answer is usually *when, where and who* — not what objects were in
frame — so this probably covers most of the value on its own.

Ship this before anything else.

## Phase 2 — vision, only for the fourth axis

Vision adds only *what were you doing* — "gym" vs "restaurant" vs
"wedding", where location and faces don't already imply it.

It changes the cost shape completely: metadata over a thousand files is
one listing; vision over a thousand files is a thousand inference calls.
That is a batch job with incremental "already described" state — the same
shape as the Drive's existing duplicate finder — not a tool call. Cap it
at recent media, never the library.

## Video

**A video model is not needed for this.** Sample a frame every few
seconds, describe each with the existing vision path, aggregate.

And videos have an audio track: thirty seconds transcribed by Whisper —
already loaded half the day — usually beats thirty sampled frames for
inferring activity. Voices, background noise, someone naming the place.

Real video VLMs earn their keep on temporal questions ("did he pick it up
before or after she left"), not "was I at the gym."

If a genuine video VLM is ever wanted, the envelope is ~4-5GB quantized:
with Standard resident (~9.9GB of 16311 MiB) there is roughly 5GB of real
headroom, which is why the 6.5GB gemma-4-12B Vision tier already can't
coexist and why `watcher_manager` does its unload dance. Candidates as of
2026-08: Qwen2.5-VL 7B (native video, dynamic FPS sampling), MiniCPM-V
2.6, InternVL 8B-class — all needing a re-check, this space moves monthly.
Caveat that matters more than the model choice: llama.cpp multimodal is
image-oriented, so "video" locally usually means frame sampling anyway,
and a truly video-native path means a second inference stack (vLLM /
transformers) on a card that already can't hold what it has.

---

## Privacy boundary

`SENSITIVE_LOCAL_ONLY = False` (2026-08-04, Vatsal's explicit call)
already sends personal content to these providers, terms vetted. The
precedent is set and this doesn't relitigate it.

A screenshot of the desktop and a face-tagged, geotagged personal photo
library are still different in degree. If a boundary is ever wanted, the
natural one falls out of the design above for free: **metadata always
local, images cloud only on request** — the Phase 1 path simply never
calls `describe_image()`.

---

## Triage

Per the MVP plan's own rule: this serves **v1.1 item #2** (account
integrations, which lists Drive) and folds into it rather than becoming a
new line. Its credentials-vault prerequisite applies — a Nextcloud app
password is exactly the kind of credential that item exists to protect,
and this project has leaked keys twice.

Cloud vision needs nothing from item #3 ("extreme of extreme" tier); it
is already its own path.

The "infer and file my activities into memory" half is Phase 19 territory
and should stay out of scope until the memory rebuild happens — otherwise
it writes inferred life events into a store still full of undistilled
per-turn transcripts.
