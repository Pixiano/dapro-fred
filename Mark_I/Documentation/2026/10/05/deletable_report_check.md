# Check of the "deletable space" report, FRED part (2026-10-05)

Source: `Projects\Claude\miscellaneous\deletable_report_2026-10-05.md`. Only the Project_FRED lines were checked; the other projects were not. Nothing was deleted or changed. Sizes measured with `du` on 2026-10-05 (GiB; the report's units are not stated).

| Claim in the report | Measured | Verdict |
|---|---|---|
| Old\Wispr 2.3 (fred_vid.mp4 1.5, IMG_2698.MOV 0.8), dated 2026-08-17 | 2.30; files 1.62 and 0.84 GB, dated 2026-08-17 | correct |
| Old\Core\data 3.1, modified 2026-10-02 | 3.11, modified 2026-10-02 | correct |
| Old\Core\models 2.2, modified 2026-08-28 | 2.19, modified 2026-08-28 | correct |
| Old\Legacy\FRED 10.2, last touched 2026-06-22 | 10.61; folder dated 2026-06-22, no newer file inside | date correct, size about 0.4 low |
| Old\Core\venv 8.18 | 8.34 | about right |
| Mark_II_Testing 33.5 = Realtime_Venus 9.2 + MiniCPM-o-2.6 8.8 + Qwen2.5-Omni 8.1 + Assembled_FRED 7.3 | 4.78 + 8.84 + 3.81 + 7.34 = 24.8 | **overstated by about 8.7 GB**: Realtime_Venus and Qwen2.5-Omni are about half the stated size (a `compress.log` sits in the folder, so they may have been compressed since the report's source measurement) |
| Mark_II_Testing holds four trials | six folders: MiniCPM-o-4.5 and Qwen2.5-Omni-FREDtools are also there (both about 0 GB) | incomplete |
| Old\Core\venv: "rebuildable, delete freely, holds no work" | `Old\FRED.bat` starts `Core\venv\Scripts\pythonw.exe`; `Core\config`, `web\phone_api.py` and others also name it | **misleading**: rebuildable, but it is the live FRED's only interpreter; deleting it stops FRED launching until it is rebuilt. FRED is down on purpose until the revamp is done, so it is safe only if you accept a rebuild before the next start |
| Project_FRED total 60.4 | not measured (whole-tree scan too slow on the venvs) | unverified |

Not verified: the Tier 1 / Tier 2 totals (33, 52, 70 to 80 GB), which mix the other projects. The 33.5 figure feeds Tier 2, so the FRED share of "about 52 GB" is high by about 8.7 GB.

What to do for FRED if space is wanted, in safe order: Old\Wispr (raw footage, 2.3 GB), then Mark_II_Testing (24.8 GB, trials; archive first as the report says), then Legacy\FRED (10.6 GB). Keep Core\venv, Core\data and Core\models.
