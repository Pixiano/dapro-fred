# install_runtime_model's non-trivial bit, added 2026-08-11: os.replace
# survives the destination being memory-mapped by a running FRED
# (onnxruntime opens external-data files with delete-sharing) —
# confirmed live against the actual running process, not reproducible
# here since plain Python open() doesn't request that same share mode
# (and, unhelpfully, fails a naive rename-over-open-file test the same
# way a plain lock would, even though the real target survives it).
# What IS testable portably is the atomicity/content-swap itself.

import os

from input import wakeword_train


def test_replace_file_swaps_content(tmp_path):
    src = tmp_path / "src.bin"
    dst = tmp_path / "dst.bin"
    src.write_bytes(b"new content")
    dst.write_bytes(b"old content")

    wakeword_train._replace_file(str(src), str(dst))

    assert dst.read_bytes() == b"new content"
    assert not os.path.exists(str(dst) + ".tmp")


def test_replace_file_is_atomic_no_partial_write(tmp_path):
    src = tmp_path / "src.bin"
    dst = tmp_path / "dst.bin"
    src.write_bytes(b"x" * 1000)

    wakeword_train._replace_file(str(src), str(dst))

    assert dst.read_bytes() == b"x" * 1000
