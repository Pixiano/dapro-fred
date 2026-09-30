"""Download (or re-download) a candidate's base weights at its pinned commit, then SHA-check them.

Every fix, loader, live script and benchmark adapter lives in git, outside
`models/`, so deleting a base to save disk loses nothing but the weights:

    <candidate venv>\\Scripts\\python.exe fetch_model.py qwen      # restore + verify -> plug and play
    <candidate venv>\\Scripts\\python.exe fetch_model.py qwen --verify-only

Resumable (plain HTTP; Xet ignored partials on 2026-09-28) and retried.
"""

import argparse
import hashlib
import os
import time
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
from huggingface_hub import HfApi, snapshot_download  # noqa: E402

HERE = Path(__file__).resolve().parent
# name: (repo, pinned commit, folder the hub paths land in, only this hub subfolder or None)
BASES = {
    "venus": ("inclusionAI/Realtime-Venus", "ad42af5f6c77936377626e016cccb7147b6a723f",
              "Realtime_Venus/1/models", "Realtime-Venus-Omni"),
    "qwen": ("Qwen/Qwen2.5-Omni-7B", "ae9e1690543ffd5c0221dc27f79834d0294cba00",
             "Qwen2.5-Omni/1/models/Qwen2.5-Omni-7B", None),
    "minicpmo26": ("openbmb/MiniCPM-o-2_6", "06849bfd36da94da1f5a88fa17e8ba63f08a4c4d",
                   "MiniCPM-o-2.6/1/models/MiniCPM-o-2_6", None),
    "minicpmo45": ("openbmb/MiniCPM-o-4_5", "503e754207c94da6bb26850b4469f367c9ea3582",
                   "MiniCPM-o-4.5/1/models/MiniCPM-o-4_5", None),
}


def verify(repo, rev, root, sub):
    info = HfApi().model_info(repo, revision=rev, files_metadata=True)
    bad = n = 0
    for s in info.siblings:
        if not s.lfs or (sub and not s.rfilename.startswith(sub + "/")):
            continue
        h = hashlib.sha256()
        with open(root / s.rfilename, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 24), b""):
                h.update(chunk)
        ok = h.hexdigest() == s.lfs.sha256
        n, bad = n + 1, bad + (not ok)
        print(("OK  " if ok else "BAD ") + s.rfilename, flush=True)
    print(f"{n - bad}/{n} LFS files match")
    return bad == 0 and n > 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name", choices=BASES)
    ap.add_argument("--verify-only", action="store_true")
    a = ap.parse_args()
    repo, rev, folder, sub = BASES[a.name]
    root = HERE / folder
    if not a.verify_only:
        for attempt in range(1, 11):
            try:
                snapshot_download(repo, revision=rev, local_dir=root, max_workers=4,
                                  allow_patterns=[sub + "/*"] if sub else None)
                break
            except Exception as e:  # dropped connection: resume from the partials
                print(f"attempt {attempt} failed ({e}); retrying in 30 s", flush=True)
                time.sleep(30)
        else:
            raise SystemExit("download failed 10 times")
    raise SystemExit(0 if verify(repo, rev, root, sub) else 1)


if __name__ == "__main__":
    main()
