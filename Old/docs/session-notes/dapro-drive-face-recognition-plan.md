# Face recognition / people-grouping for DaPro Drive (Recognize app trial)

## Context

Vatsal wants DaPro Drive (self-hosted Nextcloud) to replicate Google Photos' "group photos by person" feature. After researching options (Nextcloud's native Recognize app, standalone Immich/PhotoPrism, and a DIY face-embedding script), the decision is:

1. Try Nextcloud's **Recognize** app first — it's the only option with zero new infrastructure, installs directly into the existing Docker/Nextcloud stack.
2. Trial it on a **small subset (~10-20%) of the library first**, not the whole thing, to judge clustering quality before committing.
3. Only if Recognize's output disappoints, revisit a **DIY approach** later (insightface + HDBSCAN + real folders via hardlinks) — not part of this plan, noted as a future option.

Vatsal also runs a separate GPU-using process, **FRED** (`Project_FRED`, launched via `C:\Users\Dhiraj Vatsal\Desktop\fred.bat` → `pythonw.exe fred_popup.py`), which may be active overnight using ~10-12% GPU. Two constraints from Vatsal, folded into this plan:
1. Recognize's face-scanning should only run in a **2:00-4:00 AM window**, using the best-quality GPU model/settings available (not the fastest/lowest-quality one) since it's time-boxed anyway.
2. FRED must be stopped 10 minutes before the window (1:50 AM) so it isn't competing for GPU, and restarted 5 minutes after the scan actually stops — whether that's an early finish or the hard 4:00 AM cutoff.

Built `D:\DaPro_Drive\scripts\Start-NightlyRecognizeWindow.ps1` for this: stops FRED (matched by command line, not just process name, since `pythonw.exe` alone isn't a unique match), waits for 2:00 AM, runs the face-scan capped at 4:00 AM, then restarts FRED 5 minutes after the scan stops. **Not yet wired to a Scheduled Task** — it has a placeholder for the actual `occ recognize:*` scan subcommand and the quality/model config key, both unverifiable until Recognize is actually installed tomorrow. Registering the nightly trigger is the last step of tomorrow's session, after the real command is confirmed working manually once.

Vatsal has an RTX 5060 Ti (16GB VRAM) and wants to use it. Verified directly on the host tonight:
- `nvidia-smi` confirms the GPU, driver 596.36, CUDA 13.2 available.
- `docker info` already lists an `nvidia` runtime alongside the default ones — nvidia-container-toolkit is already set up for Docker Desktop on this machine.
- `D:\DaPro_Drive\Dockerfile` currently only extends `nextcloud:latest` with ffmpeg (for video thumbnails) — no CUDA/cuDNN libraries installed in the image yet.
- `D:\DaPro_Drive\docker-compose.yml` does not currently request GPU access for the `nextcloud` service.
- Confirmed via `occ app:list` that neither `recognize` nor `memories` apps are installed yet.

This is D:\DaPro_Drive, the hand-built reference instance (not a skill-provisioned replica) — changes here are direct owner-operator actions via `occ`/`docker compose`, same pattern as everything else done on it this session.

## Approach

### 1. Give the `nextcloud` container GPU access
Edit `D:\DaPro_Drive\docker-compose.yml`, add a GPU reservation to the `nextcloud` service (Docker Compose's `deploy.resources.reservations.devices` with `driver: nvidia`, or equivalent `runtime: nvidia` + `NVIDIA_VISIBLE_DEVICES=all`). Recreate the container (`docker compose up -d`) and verify GPU visibility inside it (`docker exec dapro-drive-nextcloud-1 nvidia-smi`).

### 2. Install Recognize + Memories
```
docker exec dapro-drive-nextcloud-1 php occ app:install recognize
docker exec dapro-drive-nextcloud-1 php occ app:install memories
```
Memories is required because Recognize's face clusters are surfaced through Memories' "People" view — the base Photos app doesn't show them properly (known display bugs upstream).

### 3. Get GPU-mode inference actually working (main open unknown)
Recognize supports native/WASM/GPU TensorFlow.js backends. GPU mode needs CUDA 450.80.02+ and cuDNN reachable *inside* the container, not just driver passthrough from the host.
- Check Settings > Administration > Recognize after install for a backend-mode toggle/status.
- If it needs cuDNN libraries the current image doesn't have, extend `D:\DaPro_Drive\Dockerfile` (same pattern as the existing ffmpeg line) to install the matching cuDNN version, then `docker compose build`.
- If GPU mode turns out to be too fiddly to get clean, fall back to CPU/WASM for the trial specifically — the trial's purpose is judging clustering *quality*, not speed, so CPU is an acceptable fallback just for the subset test.

### 4. Scope the first scan to a small subset
Check `docker exec dapro-drive-nextcloud-1 php occ list | grep recognize` for any path- or user-scoped scan subcommand. If Recognize has no native way to limit scanning to a subset, use a naturally small existing folder as the stand-in test set instead of enabling it system-wide — the just-merged `VatsalDaPro/files/Overflow/Phone_11_import` (316 files) is a ready-made ~small subset for exactly this.

### 5. Review and decide
Run the scan, check the People view in Memories: are faces correctly grouped without heavy manual merge/split correction needed? That's the quality bar for deciding whether to let it loose on the full library.

### 6. If quality is good
Let Recognize's normal background cron pick up the rest of the library (or trigger a manual full scan) — no separate app needed, it's designed to run continuously as new photos land.

### 7. If quality disappoints
Stop here. DIY (insightface + HDBSCAN clustering + real folders via hardlinks, incremental sqlite index of embeddings, same script pattern as `tag_photo_locations.py`) becomes a separate future plan — not built as part of this one.

### 8. Wire up the nightly 2:00-4:00 AM window (once Recognize is confirmed working manually)
- Find and set Recognize's best-quality/model config (check admin UI and `occ config:app:get recognize` for the actual key — not guessed in advance).
- Replace the placeholder `occ recognize:scan-faces` line in `Start-NightlyRecognizeWindow.ps1` with the real, confirmed scan subcommand.
- Register a Scheduled Task triggering `Start-NightlyRecognizeWindow.ps1` daily at 1:50 AM (same pattern as the existing `DaProDrive_NextcloudCron` / `DaProDrive_PhotoLocationTagger` tasks).
- Test one full cycle manually (or wait for the next natural 2 AM window) before trusting it unattended: confirm FRED actually stops at 1:50, the scan runs, and FRED comes back 5 minutes after the scan ends (early or at the 4 AM cutoff).

## Files touched
- `D:\DaPro_Drive\docker-compose.yml` — GPU reservation on the `nextcloud` service
- `D:\DaPro_Drive\Dockerfile` — possibly extended with cuDNN, only if GPU mode needs it beyond driver passthrough (same pattern as the existing ffmpeg addition)
- `D:\DaPro_Drive\scripts\Start-NightlyRecognizeWindow.ps1` — already created; needs its scan-command placeholder replaced with the real one (step 8)
- No other application code changes — this is entirely Nextcloud app installation + Docker config + one orchestration script

## Verification
- `docker exec dapro-drive-nextcloud-1 nvidia-smi` shows the GPU visible inside the container
- `occ app:list` shows `recognize` and `memories` as enabled
- Recognize's admin settings confirm which backend (native/wasm/gpu) is actually active
- Memories' People view shows real face clusters for the test subset — manually reviewed for correctness
- `docker compose ps` still shows both containers healthy throughout, no regression to the running Drive
