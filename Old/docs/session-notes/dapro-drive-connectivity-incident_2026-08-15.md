# DaPro Drive connectivity incident — 2026-08-15

**Symptom:** iPhone auto-upload to DaPro Drive (Nextcloud) stuck in queue, spinning with no error.

**Setup:** Nextcloud runs as `dapro-drive-nextcloud-1` / `dapro-drive-db-1` (docker compose at `D:\DaPro_Drive\docker-compose.yml`, data at `D:\DaPro_Drive\data`). The iOS app connects over Tailscale at `100.69.112.52:8080`, not localhost.

## Two unrelated root causes, stacked

1. **Docker Desktop wouldn't start.** `%APPDATA%\Docker\settings-store.json` had a UTF-8 BOM prefix (bytes `EF BB BF` before the `{`). Docker's JSON parser doesn't skip it -> `invalid character 'ï' looking for beginning of value` -> crash on launch. **Fix:** rewrite the file as plain UTF-8 without BOM, relaunch Docker Desktop.

2. **Tailscale daemon wedged.** `tailscaled` would start, create its network adapter, then hang 20s+ on:
   ```
   error configuring DNS registration: opening SYSTEM\CurrentControlSet\Services\Tcpip6\Parameters\Interfaces\{GUID}: timeout waiting for registry key
   ```
   That hang blocks the whole IPN backend, so `tailscale status`/`up` just return `EOF` — looks identical to "daemon not running," but killing/restarting the process or service does nothing (confirmed: even a full reboot didn't fix it, since the new adapter GUID hit the same registry hang). **Real fix:** Device Manager -> Network adapters -> uninstall the Tailscale adapter -> reboot. Tailscale recreates a clean adapter with no stale registry entry, and it connects immediately after.

**Lesson:** don't stop at the first fix and declare victory — verify end-to-end (Docker up -> Tailscale interface has an IP -> `curl` to the Tailscale IP returns 200) before assuming uploads will resume. These two causes had nothing to do with each other.

**Also learned:** restarting Windows services/processes from a non-elevated automation shell fails silently or misleadingly (`Access is denied`, `Cannot open service`, or `-Verb RunAs` UAC prompts getting cancelled with no visible dialog roughly half the time). When a fix needs admin rights, handing the exact command to a human to run in their own elevated terminal is more reliable than repeatedly proxying elevation requests.

## Healthcheck script

`D:\DaPro_Drive\healthcheck.ps1` (run elevated): checks Docker -> containers -> Tailscale IP -> Nextcloud reachability, auto-restarting Docker/Tailscale-service if down, logging to `D:\DaPro_Drive\healthcheck.log`. Deliberately does **not** attempt to fix a wedged adapter (cause #2 above) — logs `MANUAL FIX NEEDED` instead, since that always needs the manual Device Manager step. Not scheduled — run on demand.
