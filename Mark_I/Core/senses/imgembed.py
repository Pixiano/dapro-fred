"""Image embedder for screen memory (Phase B): a small SigLIP-class model (transformers, fp16, CUDA) that puts a screenshot and a text query into one vector space.

Loaded lazily on the first image, unloaded after screen_img_idle_s without use (idle_check(), called by the screen memory's tick), so it costs VRAM only while visits are being stored.
GPU only: no CPU fallback (without CUDA, load() raises and the screen memory keeps text vectors only). Callers run it through gpu.run at BACKGROUND priority.

Sockets
  in : ImageEmbedder(model=None, clock=time.monotonic)      model defaults to config.screen_img_model
       embed_image(rgb ndarray HxWx3 uint8) -> unit float32 vector;  embed_text(query) -> unit float32 vector (same space: dot product = similarity)
       shared() -> the process-wide ImageEmbedder;  idle_check() unloads when idle;  unload();  loaded -> bool;  model -> the model id (vectors are only compared within one id)
  out: vectors only; the pixels are dropped after the call. Settings: screen_img_model, screen_img_idle_s.
Self-check: python senses/imgembed.py (loads the real model on the GPU if CUDA is free enough: a few seconds, ~0.4 GB; skipped without CUDA)
"""
import sys
import threading
import time
from pathlib import Path

import numpy as np

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import log


class ImageEmbedder:
    def __init__(self, model: str | None = None, clock=time.monotonic):
        self.model, self.clock = model or config.get().screen_img_model, clock
        self._m = self._p = None
        self._used = 0.0
        self._lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self._m is not None

    def _load(self):
        if self._m is None:
            import torch
            if not torch.cuda.is_available():
                raise RuntimeError("no CUDA: image embedder is GPU only")
            from transformers import AutoModel, AutoProcessor
            from transformers.utils import logging as hf_logging
            hf_logging.set_verbosity_error()                    # no progress bars or config warnings on the console
            hf_logging.disable_progress_bar()
            self._m = AutoModel.from_pretrained(self.model, dtype=torch.float16).cuda().eval()
            self._p = AutoProcessor.from_pretrained(self.model)
            log.session("imgembed_loaded", model=self.model)

    def _feat(self, out):
        return out if hasattr(out, "shape") else out.pooler_output

    def embed_image(self, rgb) -> np.ndarray:
        import torch
        from PIL import Image
        with self._lock:
            self._load()
            self._used = self.clock()
            x = self._p(images=Image.fromarray(np.ascontiguousarray(rgb)), return_tensors="pt")
            with torch.no_grad():
                v = self._feat(self._m.get_image_features(pixel_values=x["pixel_values"].cuda().half()))
            return torch.nn.functional.normalize(v.float(), dim=-1)[0].cpu().numpy()

    def embed_text(self, query: str) -> np.ndarray:
        import torch
        with self._lock:
            self._load()
            self._used = self.clock()
            x = self._p(text=[query.lower()], padding="max_length", max_length=64, truncation=True, return_tensors="pt")      # SigLIP was trained on lower-case, padded to 64
            with torch.no_grad():
                v = self._feat(self._m.get_text_features(input_ids=x["input_ids"].cuda()))
            return torch.nn.functional.normalize(v.float(), dim=-1)[0].cpu().numpy()

    def unload(self):
        with self._lock:
            if self._m is not None:
                import gc
                import torch
                self._m = self._p = None
                gc.collect()
                torch.cuda.empty_cache()
                log.session("imgembed_unloaded")

    def idle_check(self):
        if self._m is not None and self.clock() - self._used >= config.get().screen_img_idle_s:
            self.unload()


_shared = None


def shared() -> ImageEmbedder:
    """The one process-wide instance (the screen memory and the screen_recall skill share one loaded model)."""
    global _shared
    if _shared is None:
        try:
            from transformers import AutoModel, AutoProcessor   # noqa: F401  import once on the main thread: done first on a worker thread while torch loads elsewhere it fails
        except Exception:
            pass
        _shared = ImageEmbedder()
    return _shared


def _selfcheck():
    try:
        import torch
        if not torch.cuda.is_available():
            print("imgembed skipped (no CUDA)")
            return
        free, _ = torch.cuda.mem_get_info()
        if free < 1.5e9:
            print("imgembed skipped (GPU is busy: under 1.5 GB free)")
            return
    except ImportError:
        print("imgembed skipped (no torch)")
        return
    from PIL import Image, ImageDraw
    now = [0.0]
    e = ImageEmbedder(clock=lambda: now[0])
    red = Image.new("RGB", (1280, 720), (30, 30, 70))
    ImageDraw.Draw(red).rectangle((300, 150, 900, 550), fill=(210, 40, 40))
    page = Image.new("RGB", (1280, 720), "white")
    d = ImageDraw.Draw(page)
    for i in range(14):
        d.rectangle((80, 60 + i * 44, 1100 - (i % 3) * 200, 80 + i * 44), fill=(60, 60, 60))
    assert not e.loaded
    vi = [e.embed_image(np.asarray(x)) for x in (red, page)]
    vt = [e.embed_text(q) for q in ("a red square on a dark blue background", "a white document page with lines of text")]
    assert e.loaded and abs(float(vi[0] @ vi[0]) - 1) < 1e-3 and vi[0].shape == vt[0].shape
    assert vi[0] @ vt[0] > vi[1] @ vt[0] and vi[1] @ vt[1] > vi[0] @ vt[1], (vi[0] @ vt[0], vi[1] @ vt[0], vi[1] @ vt[1], vi[0] @ vt[1])      # text finds its image
    now[0] += 10
    e.idle_check()
    assert e.loaded
    now[0] += 100
    e.idle_check()
    assert not e.loaded
    print("imgembed ok")


if __name__ == "__main__":
    _selfcheck()
