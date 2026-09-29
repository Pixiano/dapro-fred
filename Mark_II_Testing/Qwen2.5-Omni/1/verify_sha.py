import hashlib, sys, pathlib
from huggingface_hub import HfApi
root = pathlib.Path(sys.argv[1])
info = HfApi().model_info("Qwen/Qwen2.5-Omni-7B", revision="ae9e1690543ffd5c0221dc27f79834d0294cba00", files_metadata=True)
bad = 0; n = 0
for s in info.siblings:
    if not s.lfs:
        continue
    p = root / s.rfilename
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 24), b""):
            h.update(chunk)
    ok = h.hexdigest() == s.lfs.sha256
    n += 1; bad += not ok
    print(("OK  " if ok else "BAD ") + s.rfilename, flush=True)
print(f"{n - bad}/{n} LFS files match")
