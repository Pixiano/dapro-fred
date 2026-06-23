# Core/utils/gpu_bootstrap.py

import ctypes
import os
import sys
from pathlib import Path

_DONE = False

# Load order matters: each name depends only on the ones before it.
_GGML_LOAD_ORDER = [
    "ggml-base.dll",
    "ggml.dll",
    "ggml-cpu.dll",
    "ggml-cuda.dll",
    "mtmd.dll",
    "llama.dll",
]


def ensure_cuda_dlls():
    """
    Makes llama_cpp's CUDA backend actually loadable on Windows.

    Two separate problems, both worked around here:

    1. The CUDA 12 runtime (cudart/cublas/nvrtc) ships as pip packages
       (nvidia-cuda-runtime-cu12, etc.) rather than a system-wide
       install, so its DLL directories aren't on PATH by default —
       register them via os.add_dll_directory.

    2. llama_cpp's own loader (_ctypes_extensions.py) loads llama.dll
       with winmode=ctypes.RTLD_GLOBAL. Under that flag, the ggml
       backend DLLs (ggml.dll, ggml-cuda.dll) fail to resolve their
       sibling dependencies even though they sit in the same folder —
       a real Windows/ctypes quirk with this DLL layout, reproduced
       and confirmed independent of any CUDA_PATH conflict. Loading
       the same DLLs first with the *default* winmode works fine, and
       Windows then just reuses those already-loaded handles when
       llama_cpp's RTLD_GLOBAL load runs afterward, instead of
       repeating the load steps that fail.

    Must run before the first `import llama_cpp`.
    """

    global _DONE

    if _DONE or sys.platform != "win32":
        return

    _add_cuda_runtime_dirs()
    _preload_ggml_stack()

    _DONE = True


def _add_cuda_runtime_dirs():

    site_packages = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"

    dll_dirs = [
        site_packages / "cuda_runtime" / "bin",
        site_packages / "cublas" / "bin",
        site_packages / "cuda_nvrtc" / "bin",
    ]

    for dll_dir in dll_dirs:
        if dll_dir.exists():
            try:
                os.add_dll_directory(str(dll_dir))
            except OSError:
                pass


def _preload_ggml_stack():

    # Computed directly from site-packages, NOT via `import llama_cpp` —
    # that import is exactly what's broken until this preload runs.
    lib_dir = Path(sys.prefix) / "Lib" / "site-packages" / "llama_cpp" / "lib"

    if not lib_dir.exists():
        return

    try:
        os.add_dll_directory(str(lib_dir))
    except OSError:
        pass

    for filename in _GGML_LOAD_ORDER:
        dll_path = lib_dir / filename

        if dll_path.exists():
            try:
                ctypes.CDLL(str(dll_path))
            except OSError:
                # If a piece is genuinely missing/incompatible, let
                # llama_cpp's own loader raise the real error.
                pass
