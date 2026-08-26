# Core/input/wakeword_train.py
#
# Trains the "Hey FRED" wake model wakeword.py loads at runtime. Not
# something FRED itself ever imports — a standalone pipeline, run by
# hand:
#
#   Core/venv/Scripts/python.exe -m input.wakeword_train
#
# All data lives under Core/data/wakeword_training/ (gitignored — see
# .gitignore's note there), the trained model gets copied to
# Core/models/wakeword/hey_fred.onnx (also gitignored, same convention
# as Kokoro/Vosk's model files: real files, not source, don't belong
# in git). Every step below is idempotent (skips work whose output
# already exists), so re-running this after adding new data — e.g. a
# real room recording as better negatives — only does the new part.
#
# WHY IT LOOKS LIKE THIS (decisions made building it, 2026-08-09):
#
#   - Positive "Hey FRED" clips come from Kokoro (Core/audio/
#     tts_kokoro.py), not openWakeWord's own recommended generator
#     (piper-sample-generator + a Piper voice checkpoint). That
#     generator needs `piper-phonemize`, which has never had a Windows
#     wheel (github.com/rhasspy/piper-phonemize/issues/34, still open).
#     Kokoro was already a working local dependency, so it was used
#     instead — see PIPER_SAMPLE_GENERATOR_DIR below for how train.py's
#     own unconditional import of a `generate_samples` module is
#     satisfied without ever calling the real (Windows-broken) one.
#
#   - Negatives are DEMAND (real recorded ambient noise: kitchen,
#     living room, office, cafeteria, street traffic — zenodo.org/
#     records/1227121) plus numpy-synthesized white/pink/brown noise.
#     NOT openWakeWord's own standard recipe (ACAV100M ~17GB + FMA
#     ~8GB) — that's sized for a production model, not a same-day v1.
#     Explicitly a placeholder: a real room recording (planned for
#     2026-08-10) will replace/supplement DEMAND as the main negative
#     signal, since it matches the actual deployment room/mic.
#
#   - No room-impulse-response reverb augmentation (rir_paths=[] in
#     the config) — a quality nice-to-have, skipped for the same
#     same-day-v1 reason as above.
#
#   - Runs on CPU. This venv's torch build has no CUDA (llama-cpp-
#     python's CUDA wheel is separate and doesn't imply torch has one)
#     — acceptable since the model itself is tiny (a few hundred
#     thousand params, not an LLM).

import os
import shutil
import sys

import numpy as np
import soundfile as sf
import yaml

CORE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKSPACE = os.path.join(CORE, "data", "wakeword_training")
sys.path.insert(0, CORE)

# torch's ONNX exporter prints Unicode (a checkmark) in its verbose
# progress output; Windows' console defaults to cp1252, which can't
# encode it, crashing the export after it already succeeded. Force
# UTF-8 so this and any other stray Unicode print doesn't take the
# whole run down at the very last step.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# The scipy.special.sph_harm shim — openwakeword.data imports the
# `acoustics` package for one colored-noise helper this pipeline
# doesn't use (numpy already generates the synthetic noise below), and
# acoustics' unrelated `directivity` submodule imports a scipy.special
# name that was renamed upstream (sph_harm -> sph_harm_y). Never
# actually called, just needs to not raise ImportError at import time.
import scipy.special as _sp  # noqa: E402
_sp.sph_harm = getattr(_sp, "sph_harm_y", lambda *a, **k: None)

# The torchaudio.load shim — this venv's torchaudio (2.11) routes
# .load()/.info() through torchcodec by default, which needs FFmpeg's
# native shared libraries; not installed, and not worth installing
# system-wide for one WAV read this venv's soundfile (already a
# working dependency, via kokoro-onnx) already does natively. .info()'s
# result is discarded immediately by its one caller in openwakeword's
# data.py (assigned then overwritten by mutagen.File on the very next
# line) so it only needs to not raise.
def _load_via_soundfile(path, **kwargs):
    import torch
    data, sample_rate = sf.read(str(path), dtype="float32", always_2d=True)  # (frames, channels)
    return torch.from_numpy(data.T), sample_rate  # -> (channels, frames)


import torchaudio  # noqa: E402
torchaudio.load = _load_via_soundfile
torchaudio.info = lambda path: None

# The speechbrain stub — openwakeword.data imports read_audio and
# reverberate from real speechbrain, but only calls them from functions
# this pipeline never reaches (background/foreground clip mixing, RIR
# convolution — RIR_paths is empty here, see write_config). The real
# package is otherwise fine, but importing it at all registers a lazy-
# loading system for its optional integrations (k2_fsa, needing the
# `k2` package, not installed) that PyTorch's own frame-inspection
# machinery (torch._dynamo, imported transitively via torchmetrics ->
# transformers) incidentally touches and crashes on — nothing to do
# with anything this script actually uses speechbrain for. Injecting
# fake modules into sys.modules before openwakeword.data ever imports
# the real thing means Python never executes speechbrain's own
# __init__.py at all, sidestepping the whole lazy-loader landmine.
import types  # noqa: E402

_sb = types.ModuleType("speechbrain")
_sb_dataio = types.ModuleType("speechbrain.dataio")
_sb_dataio_dataio = types.ModuleType("speechbrain.dataio.dataio")
_sb_dataio_dataio.read_audio = lambda *a, **k: (_ for _ in ()).throw(
    NotImplementedError("stub — this pipeline's code path never calls speechbrain.read_audio")
)
_sb_processing = types.ModuleType("speechbrain.processing")
_sb_processing_signal = types.ModuleType("speechbrain.processing.signal_processing")
_sb_processing_signal.reverberate = lambda *a, **k: (_ for _ in ()).throw(
    NotImplementedError("stub — RIR_paths is empty, reverberate is never called")
)
for _name, _mod in (
    ("speechbrain", _sb),
    ("speechbrain.dataio", _sb_dataio),
    ("speechbrain.dataio.dataio", _sb_dataio_dataio),
    ("speechbrain.processing", _sb_processing),
    ("speechbrain.processing.signal_processing", _sb_processing_signal),
):
    sys.modules[_name] = _mod

# The trim_mmap shim — openwakeword.data.trim_mmap keeps its mmap_mode='r'
# handle on the ORIGINAL file open across the entire trim, then calls
# os.remove() on that same still-open file. POSIX allows deleting an
# open file (the classic unlink-while-held pattern); Windows doesn't,
# so this always crashes here with WinError 32. compute_features_from_
# generator (openwakeword/utils.py) imports trim_mmap locally on every
# call, so patching openwakeword.data.trim_mmap before training starts
# is enough — no need to touch utils.py's own import.
def _close_memmap(m):
    """del + gc.collect() alone doesn't reliably release a numpy
    memmap's underlying Windows file handle (a known numpy gotcha —
    __del__ timing isn't guaranteed, and there's no public .close()).
    Reaching into the private ._mmap and closing it directly is more
    reliable than hoping garbage collection gets there in time."""
    inner = getattr(m, "_mmap", None)
    if inner is not None:
        try:
            inner.close()
        except Exception:
            pass


def _trim_mmap_windows_safe(mmap_path):
    import gc
    import time
    from numpy.lib.format import open_memmap

    mmap_file1 = np.load(mmap_path, mmap_mode="r")
    i = -1
    while np.all(mmap_file1[i, :, :] == 0):
        i -= 1
    n_new = mmap_file1.shape[0] + i + 1

    output_file2 = mmap_path[:-4] + "2.npy"
    mmap_file2 = open_memmap(
        output_file2, mode="w+", dtype=np.float32,
        shape=(n_new, mmap_file1.shape[1], mmap_file1.shape[2]),
    )
    for start in range(0, mmap_file1.shape[0], 1024):
        end = min(start + 1024, n_new)
        if start >= n_new:
            break
        mmap_file2[start:end] = mmap_file1[start:end].copy()
        mmap_file2.flush()

    # The part the original is missing: release both mmap handles
    # before touching the file on disk, so Windows will actually allow
    # the remove/rename below.
    _close_memmap(mmap_file1)
    _close_memmap(mmap_file2)
    del mmap_file1, mmap_file2
    gc.collect()

    # Even with both handles explicitly closed, Windows can take the OS
    # a moment to fully release the file after an mmap unmap — retry
    # rather than assume the first attempt lands.
    for attempt in range(10):
        try:
            os.remove(mmap_path)
            break
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(0.3)
    os.rename(output_file2, mmap_path)


# compute_features_from_generator (openwakeword/utils.py) keeps its
# own write-mode memmap (`fp`) open on its stack frame for the entire
# duration of its call to trim_mmap — so no amount of retrying inside
# trim_mmap can help; `fp`'s handle can't release until this function
# itself returns, which can't happen until trim_mmap (called
# synchronously, before the return) finishes. Same fix as trim_mmap
# above, one level up: explicitly close `fp` before handing off.
# train.py does `from openwakeword.utils import
# compute_features_from_generator` at module level, so — like
# trim_mmap — this has to be patched before run_training()'s
# runpy.run_path executes that import.
def _compute_features_from_generator_windows_safe(generator, n_total, clip_duration, output_file, device="cpu", ncpu=1):
    from numpy.lib.format import open_memmap
    from openwakeword.data import trim_mmap
    from openwakeword.utils import AudioFeatures
    from tqdm import tqdm

    F = AudioFeatures(device=device)
    n_feature_cols = F.get_embedding_shape(clip_duration / 16000)
    output_shape = (n_total, n_feature_cols[0], n_feature_cols[1])
    fp = open_memmap(output_file, mode="w+", dtype=np.float32, shape=output_shape)

    row_counter = 0
    audio_data = next(generator)
    batch_size = audio_data.shape[0]
    if batch_size > n_total:
        raise ValueError(
            f"The value of 'n_total' ({n_total}) is less than the batch size ({batch_size})."
            " Please increase 'n_total' to be >= batch size."
        )

    features = F.embed_clips(audio_data, batch_size=batch_size)
    fp[row_counter:row_counter + features.shape[0], :, :] = features
    row_counter += features.shape[0]
    fp.flush()

    for audio_data in tqdm(generator, total=n_total // batch_size, desc="Computing features"):
        if row_counter >= n_total:
            break
        features = F.embed_clips(audio_data, batch_size=batch_size, ncpu=ncpu)
        if row_counter + features.shape[0] > n_total:
            features = features[0:n_total - row_counter]
        fp[row_counter:row_counter + features.shape[0], :, :] = features
        row_counter += features.shape[0]
        fp.flush()

    _close_memmap(fp)
    del fp
    import gc
    gc.collect()

    trim_mmap(output_file)


# Force single-process DataLoader — train.py builds its DataLoaders
# with num_workers=os.cpu_count()//2, which on Windows means spawning
# worker PROCESSES (not threads: Windows has no fork). That needs the
# whole dataset/generator graph pickled, which fails outright (train.py
# stores label transforms as lambdas — never picklable) — and even
# past that, each worker re-executes train.py fresh via runpy, without
# any of this file's monkeypatches applied, since Windows spawn re-runs
# whatever it recorded as the entry script, not this wrapper. None of
# this matters at this dataset's size (a few hundred clips): forcing
# num_workers=0 runs loading in the main process, sidestepping the
# entire class of problems. prefetch_factor is dropped too — PyTorch
# raises ValueError if it's set without num_workers > 0.
import torch  # noqa: E402
_RealDataLoader = torch.utils.data.DataLoader


def _SingleProcessDataLoader(*args, **kwargs):
    kwargs["num_workers"] = 0
    kwargs.pop("prefetch_factor", None)
    return _RealDataLoader(*args, **kwargs)


torch.utils.data.DataLoader = _SingleProcessDataLoader

import openwakeword.utils  # noqa: E402
openwakeword.utils.compute_features_from_generator = _compute_features_from_generator_windows_safe

import openwakeword.data  # noqa: E402
openwakeword.data.trim_mmap = _trim_mmap_windows_safe

# openwakeword's train.py does `from generate_samples import
# generate_samples` unconditionally in its __main__ block, regardless
# of whether --generate_clips is passed. PIPER_SAMPLE_GENERATOR_DIR
# holds a stub satisfying that import (see its own file for why the
# real one can't be used on Windows) — positive clips are generated by
# generate_positive_clips() below instead.
PIPER_SAMPLE_GENERATOR_DIR = os.path.join(WORKSPACE, "piper-sample-generator")
sys.path.insert(0, PIPER_SAMPLE_GENERATOR_DIR)

MODEL_NAME = "hey_fred"
OUTPUT_DIR = os.path.join(WORKSPACE, "output")
MODEL_DIR = os.path.join(OUTPUT_DIR, MODEL_NAME)
POS_TRAIN = os.path.join(MODEL_DIR, "positive_train")
POS_TEST = os.path.join(MODEL_DIR, "positive_test")
NEG_TRAIN = os.path.join(MODEL_DIR, "negative_train")
NEG_TEST = os.path.join(MODEL_DIR, "negative_test")

DEMAND_RAW_DIR = os.path.join(WORKSPACE, "demand_raw")
DEMAND_DIR = os.path.join(WORKSPACE, "demand_noise")
SYNTH_DIR = os.path.join(WORKSPACE, "synth_noise")

DEMAND_BASE_URL = "https://zenodo.org/records/1227121/files"
DEMAND_ENVS = ["DKITCHEN_16k", "DLIVING_16k", "OOFFICE_16k", "PCAFETER_16k", "STRAFFIC_16k"]
# Held out entirely for negative_test + the false-positive validation
# set — never seen during training. If tomorrow's room recording gets
# added as its own negative source, consider holding IT out instead
# (a real recording of the actual deployment room is a better
# validation signal than a DEMAND environment).
HELD_OUT_ENV = "DLIVING_16k.wav"

ROOM_RECORDING_DIR = os.path.join(WORKSPACE, "room_recording")  # 2026-08-10, not yet present

# Real recorded "Hey Fred" utterances — the positive-side counterpart
# to ROOM_RECORDING_DIR above. Added 2026-08-11: every positive clip
# generate_positive_clips() makes is synthetic Kokoro TTS, so the model
# has never actually heard Vatsal's voice/mic/room — which is exactly
# what the near-miss scores in wakeword_log.jsonl point back to (real
# attempts landing at 0.14-0.28, well under threshold, while synthetic-
# trained positives fire at 0.5-0.98). Drop WAV/MP3 recordings of
# "Hey Fred" in here and rerun; ingest_real_positive_clips() picks them
# up regardless of whether POS_TRAIN already has Kokoro clips in it.
REAL_POSITIVE_DIR = os.path.join(WORKSPACE, "real_positive")
REAL_POSITIVE_TEST_FRACTION = 0.15

# Real false-fire captures — passively collected during normal use
# (input/wakeword_capture.py), reclassified 2026-08-25/26: the wake
# word fired but either nothing meaningful followed, or the followup
# was cancelled/too garbled to trust as a real command. These are
# genuine hard negatives (real mic/room audio that already fooled the
# model once), not synthetic — a stronger signal than
# NEGATIVE_SPEECH_DIR's TTS-generated negatives. Whole-file, no
# segmenting: unlike DEMAND_DIR's few long environment recordings (see
# _segment_audio_file's own docstring on the 5-vs-200 imbalance that
# caused), this is already thousands of individual short files.
REAL_NEGATIVE_DIR = os.path.join(WORKSPACE, "real_negative")
REAL_NEGATIVE_TEST_FRACTION = 0.15

TARGET_SR = 16000
POSITIVE_PHRASINGS = ["Hey FRED.", "Hey, Fred.", "Hey Fred!"]
POSITIVE_SPEEDS = [0.9, 1.0, 1.1]
POSITIVE_TEST_PHRASING = "Fred, hey."  # held out from train entirely
POSITIVE_TEST_SPEED = 1.0
POSITIVE_TEST_VOICE_FRACTION = 0.2

NOISE_SR = 16000
NOISE_CLIP_SECONDS = 30
NOISE_PER_COLOR = 6


# =========================================================
# STEP 1 — DEMAND negative noise (real recorded ambient audio)
# =========================================================

def download_demand():
    import requests
    import zipfile

    os.makedirs(DEMAND_RAW_DIR, exist_ok=True)
    os.makedirs(DEMAND_DIR, exist_ok=True)

    for env in DEMAND_ENVS:
        wav_path = os.path.join(DEMAND_DIR, f"{env}.wav")
        if os.path.exists(wav_path):
            continue

        zip_path = os.path.join(DEMAND_RAW_DIR, f"{env}.zip")
        if not os.path.exists(zip_path):
            url = f"{DEMAND_BASE_URL}/{env}.zip?download=1"
            print(f"[wakeword_train] downloading {env}...")
            with requests.get(url, stream=True, timeout=60) as r:
                r.raise_for_status()
                with open(zip_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1 << 20):
                        f.write(chunk)

        with zipfile.ZipFile(zip_path) as z:
            for name in z.namelist():
                if name.endswith("ch01.wav"):  # 16-channel array, mono is enough
                    with z.open(name) as src, open(wav_path, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    print(f"[wakeword_train] extracted {wav_path}")


# =========================================================
# STEP 2 — synthesized noise (zero-download supplement)
# =========================================================

def _white(n):
    return np.random.normal(0, 1, n)


def _pink(n):
    # Voss-McCartney-ish: sum of octave-spaced white noise sources,
    # each updated at half the rate of the last — cheap and good
    # enough for an augmentation negative, not a synthesis-quality
    # target.
    n_rows = 16
    array = np.random.randn(n_rows, n // n_rows + 1)
    cum = np.cumsum(array, axis=1)
    pink = cum[-1]
    for row in range(n_rows - 1):
        step = 2 ** row
        pink += np.repeat(cum[row][::step], step)[: len(pink)]
    return pink[:n]


def _brown(n):
    return np.cumsum(np.random.normal(0, 1, n))


_NOISE_GENERATORS = {"white": _white, "pink": _pink, "brown": _brown}


def generate_synthetic_noise():
    os.makedirs(SYNTH_DIR, exist_ok=True)
    for color, fn in _NOISE_GENERATORS.items():
        for i in range(NOISE_PER_COLOR):
            path = os.path.join(SYNTH_DIR, f"{color}_{i}.wav")
            if os.path.exists(path):
                continue
            samples = fn(NOISE_SR * NOISE_CLIP_SECONDS).astype(np.float32)
            samples /= np.max(np.abs(samples)) + 1e-9
            samples *= 0.3  # quiet-ish; augment_clips handles loudness variety
            sf.write(path, samples, NOISE_SR)


# =========================================================
# STEP 3 — positive "Hey FRED" clips via Kokoro
# =========================================================

def _resample(samples, sr):
    if sr == TARGET_SR:
        return samples
    from scipy.signal import resample_poly
    return resample_poly(samples, TARGET_SR, sr).astype(np.float32)


def _load_mono_16k_int16(path):
    """
    Read any format soundfile/libsndfile supports (confirmed 2026-08-10:
    libsndfile 1.2.2 here reads MP3 fine — real phone/recorder input is
    the actual target, not just the WAVs this pipeline generates itself)
    and return int16 mono at TARGET_SR, downmixing and resampling as
    needed. The two real-audio ingestion points (segmenting negatives,
    building the false-positive validation set) used to just assert
    sr == 16000 and crash on anything else — fine for openWakeWord's own
    16kHz WAV output, not for a voice memo recorded at 44.1kHz stereo.
    """
    audio, sr = sf.read(path, dtype="float32", always_2d=True)  # (frames, channels)
    if audio.shape[1] > 1:
        audio = audio.mean(axis=1)
    else:
        audio = audio[:, 0]
    audio = _resample(audio, sr)
    return np.clip(audio * 32767, -32768, 32767).astype(np.int16)


def generate_positive_clips():
    if os.path.isdir(POS_TRAIN) and len(os.listdir(POS_TRAIN)) > 0:
        return
    os.makedirs(POS_TRAIN, exist_ok=True)
    os.makedirs(POS_TEST, exist_ok=True)

    from audio.tts_kokoro import KokoroTTS

    tts = KokoroTTS()
    voices = [v for v in tts.available_voices() if v[:2] in ("af", "am", "bf", "bm")]
    rng = np.random.default_rng(0)
    rng.shuffle(voices)
    n_test_voices = max(1, int(len(voices) * POSITIVE_TEST_VOICE_FRACTION))
    test_voices = set(voices[:n_test_voices])

    for voice in voices:
        if voice in test_voices:
            samples, sr = tts.kokoro.create(
                POSITIVE_TEST_PHRASING, voice=voice, speed=POSITIVE_TEST_SPEED, lang="en-us"
            )
            sf.write(os.path.join(POS_TEST, f"{voice}_test.wav"), _resample(samples, sr), TARGET_SR)
            continue

        for phrase in POSITIVE_PHRASINGS:
            for speed in POSITIVE_SPEEDS:
                samples, sr = tts.kokoro.create(phrase, voice=voice, speed=speed, lang="en-us")
                name = f"{voice}_{phrase.strip('.!,').replace(' ', '_')}_{speed}.wav"
                sf.write(os.path.join(POS_TRAIN, name), _resample(samples, sr), TARGET_SR)


def ingest_real_positive_clips():
    """
    Mixes real recorded "Hey Fred" utterances (REAL_POSITIVE_DIR) into
    POS_TRAIN/POS_TEST. Runs unconditionally, not gated behind
    generate_positive_clips()'s own "already populated" skip — dropping
    new recordings in and re-running should always pick them up.
    Per-file idempotent (skip if the destination already exists) like
    _segment_audio_file, so a re-run only adds what's new.
    """
    if not os.path.isdir(REAL_POSITIVE_DIR):
        return
    names = sorted(f for f in os.listdir(REAL_POSITIVE_DIR) if not f.startswith("."))
    if not names:
        return
    os.makedirs(POS_TRAIN, exist_ok=True)
    os.makedirs(POS_TEST, exist_ok=True)

    rng = np.random.default_rng(2)
    rng.shuffle(names)
    n_test = max(1, int(len(names) * REAL_POSITIVE_TEST_FRACTION))

    for i, name in enumerate(names):
        dst_dir = POS_TEST if i < n_test else POS_TRAIN
        dst_name = f"real_{os.path.splitext(name)[0]}.wav"
        dst = os.path.join(dst_dir, dst_name)

        # Check BOTH splits, not just the one this run's shuffle picked.
        # names is re-shuffled from scratch every call with a fixed seed,
        # but numpy's permutation for a given seed depends on the list's
        # length — so adding or removing files in REAL_POSITIVE_DIR
        # between runs can flip which split an ALREADY-ingested file
        # lands in this time. Checking only `dst` would then write a
        # second copy into the other split instead of recognizing the
        # file as already ingested, silently putting the same clip in
        # both train and test (inflated eval numbers, no error). Whatever
        # split it went into the first time is authoritative.
        if os.path.exists(dst) or os.path.exists(os.path.join(POS_TRAIN, dst_name)) \
                or os.path.exists(os.path.join(POS_TEST, dst_name)):
            continue
        audio = _load_mono_16k_int16(os.path.join(REAL_POSITIVE_DIR, name))
        sf.write(dst, audio, TARGET_SR)


def ingest_real_negative_clips():
    """
    Mixes real false-fire captures (REAL_NEGATIVE_DIR) into NEG_TRAIN/
    NEG_TEST — same idempotent-per-file, both-splits-checked shape as
    ingest_real_positive_clips() above. Files in REAL_NEGATIVE_DIR are
    expected to already be trigger-only clips (~2.5s, same convention
    REAL_POSITIVE_DIR's real captures use) by the time this runs — see
    that directory's own prep step, NOT this function, for the actual
    trigger-only slicing. An earlier version of this function copied
    the full trigger+gap+followup capture in whole (2.5-32s each) and
    that broke training badly enough that even the ORIGINAL, unrelated,
    previously-fine real_positive/take_*.wav scripted clips started
    scoring near-zero — confirmed live 2026-08-26. Fixed upstream, not
    here, by only ever populating REAL_NEGATIVE_DIR with pre-trimmed
    clips in the first place.
    """
    if not os.path.isdir(REAL_NEGATIVE_DIR):
        return
    names = sorted(f for f in os.listdir(REAL_NEGATIVE_DIR) if f.endswith(".wav"))
    if not names:
        return

    rng = np.random.default_rng(3)
    rng.shuffle(names)
    n_test = max(1, int(len(names) * REAL_NEGATIVE_TEST_FRACTION))

    for i, name in enumerate(names):
        dst_dir = NEG_TEST if i < n_test else NEG_TRAIN
        dst_name = f"realneg_{os.path.splitext(name)[0]}.wav"
        dst = os.path.join(dst_dir, dst_name)

        if os.path.exists(dst) or os.path.exists(os.path.join(NEG_TRAIN, dst_name)) \
                or os.path.exists(os.path.join(NEG_TEST, dst_name)):
            continue
        audio = _load_mono_16k_int16(os.path.join(REAL_NEGATIVE_DIR, name))
        sf.write(dst, audio, TARGET_SR)


# =========================================================
# STEP 3.5 — negative SPEECH clips via Kokoro
#
# Found live 2026-08-09: a model trained only against DEMAND (ambient
# noise, no clear speech) + synthesized colored noise learned "human
# voice = positive" rather than "the phrase 'hey fred' = positive" —
# it scored 0.88-0.997 on ordinary unrelated sentences ("The weather
# today is quite nice."), not just the wake phrase. Every negative
# example up to that point was non-speech, so the model had no signal
# to separate "hey fred" from "any talking at all". This step gives it
# that signal: phonetically-similar decoys (openwakeword's own
# generate_adversarial_texts, via the `pronouncing` package — the
# actual mechanism openWakeWord's own recipe uses this for) plus a
# pool of ordinary unrelated sentences, both spoken by the same
# Kokoro voices the positive clips use.
# =========================================================

NEGATIVE_SPEECH_DIR = os.path.join(WORKSPACE, "negative_speech")
N_ADVERSARIAL_PHRASES = 60
NEGATIVE_SPEECH_VOICES = ("af_heart", "af_bella", "am_michael", "am_eric", "bf_emma", "bm_george")
NEGATIVE_SPEECH_SPEEDS = (0.9, 1.0, 1.1)

# Ordinary sentences unrelated to the wake phrase — broad "this is
# just talking" coverage, distinct from the phonetically-adversarial
# set above which only covers the narrow region near "hey fred" itself.
GENERIC_NEGATIVE_SENTENCES = [
    "The weather today is quite nice.",
    "Can you turn off the lights please.",
    "I need to check my email before the meeting.",
    "What time does the store close tonight.",
    "Let's grab dinner after the movie.",
    "The meeting has been moved to three o'clock.",
    "I think it's going to rain later.",
    "Did you finish reading that book yet.",
    "Traffic was terrible on the way home.",
    "We should plan a trip for next summer.",
    "The coffee machine is broken again.",
    "I left my phone in the other room.",
    "How was your weekend, anything fun.",
    "The package should arrive tomorrow morning.",
    "Turn up the volume a little bit.",
    "Set an alarm for seven in the morning.",
    "What's the score of the game right now.",
    "I can't remember where I put my keys.",
]

# Words that rhyme with or sound close to "Fred" — generate_adversarial_texts
# above targets the whole phrase "hey fred" phonetically, but bare
# single-syllable near-rhymes (which is what a mis-hearing actually
# sounds like) are a narrower, tighter negative than anything in that
# set or GENERIC_NEGATIVE_SENTENCES. Added 2026-08-11 after a live
# false positive (score 0.76) on unrelated speech the model had never
# been trained against. Spoken bare, same as a single-word utterance
# would be, not embedded in a sentence — matches "hey fred"'s own
# clip length better than a full sentence would.
PHONETIC_NEIGHBOR_WORDS = [
    "Red", "Bread", "Bred", "Bled", "Dead", "Dread", "Fed", "Fled", "Led",
    "Sled", "Sped", "Spread", "Shed", "Shred", "Tread", "Wed", "Ted", "Ned",
    "Head", "Said", "Friend", "Fresh", "Front", "Frank", "Fright", "Fret",
    "Free", "French", "Freddy", "Fridge", "Friday", "Thread", "Instead",
]


def generate_negative_speech_clips():
    if os.path.isdir(NEGATIVE_SPEECH_DIR) and len(os.listdir(NEGATIVE_SPEECH_DIR)) > 0:
        return
    os.makedirs(NEGATIVE_SPEECH_DIR, exist_ok=True)

    from audio.tts_kokoro import KokoroTTS
    from openwakeword.data import generate_adversarial_texts

    tts = KokoroTTS()
    tts._ensure_model()  # available_voices() would do this too, but we already know the voice list

    adversarial_phrases = generate_adversarial_texts(
        input_text="hey fred", N=N_ADVERSARIAL_PHRASES, include_partial_phrase=0.2, include_input_words=0.1,
    )
    all_phrases = list(adversarial_phrases) + GENERIC_NEGATIVE_SENTENCES + PHONETIC_NEIGHBOR_WORDS

    for phrase in all_phrases:
        for voice in NEGATIVE_SPEECH_VOICES:
            for speed in NEGATIVE_SPEECH_SPEEDS:
                samples, sr = tts.kokoro.create(phrase, voice=voice, speed=speed, lang="en-us")
                safe_name = "".join(c if c.isalnum() else "_" for c in phrase)[:40]
                name = f"{safe_name}_{voice}_{speed}.wav"
                sf.write(os.path.join(NEGATIVE_SPEECH_DIR, name), _resample(samples, sr), TARGET_SR)


# =========================================================
# STEP 4 — assemble negative_train / negative_test
# =========================================================

NEGATIVE_SEGMENT_SECONDS = 2.5  # roughly matches the positive clips' own duration


def _segment_audio_file(src_path, dst_dir, prefix):
    """
    Split one long recording into many short clips. augment_clips (see
    train.py) treats EACH FILE in negative_train/ as exactly one
    training example — it takes one window per path, it doesn't tile
    a long file into many windows itself. Copying the DEMAND WAVs in
    wholesale (the first version of this function did) meant ~5
    negative examples (one per environment) against ~200 positive
    ones: the model just learned to always say yes. Confirmed live
    2026-08-09 — a trained model scored 0.7-0.998 on EVERY negative
    test clip, not just the positive ones.
    """
    audio = _load_mono_16k_int16(src_path)

    clip_len = int(NEGATIVE_SEGMENT_SECONDS * TARGET_SR)
    n_written = 0
    for i, start in enumerate(range(0, len(audio) - clip_len, clip_len)):
        dst = os.path.join(dst_dir, f"{prefix}_{i:04d}.wav")
        if not os.path.exists(dst):
            sf.write(dst, audio[start:start + clip_len], TARGET_SR)
        n_written += 1
    return n_written


def assemble_negatives():
    if os.path.isdir(NEG_TRAIN) and len(os.listdir(NEG_TRAIN)) > 0:
        return
    os.makedirs(NEG_TRAIN, exist_ok=True)
    os.makedirs(NEG_TEST, exist_ok=True)

    for name in os.listdir(DEMAND_DIR):
        dst_dir = NEG_TEST if name == HELD_OUT_ENV else NEG_TRAIN
        _segment_audio_file(os.path.join(DEMAND_DIR, name), dst_dir, os.path.splitext(name)[0])

    for name in os.listdir(SYNTH_DIR):
        _segment_audio_file(os.path.join(SYNTH_DIR, name), NEG_TRAIN, os.path.splitext(name)[0])

    # Tomorrow's room recording (2026-08-10), if present by the time
    # this runs — real ambient audio from the actual deployment room,
    # a better negative signal than any generic dataset. Added to
    # negative_train wholesale; HELD_OUT_ENV above still covers
    # negative_test/false-positive validation, so there's no need to
    # also hold out a slice of this.
    if os.path.isdir(ROOM_RECORDING_DIR):
        for name in os.listdir(ROOM_RECORDING_DIR):
            _segment_audio_file(os.path.join(ROOM_RECORDING_DIR, name), NEG_TRAIN, os.path.splitext(name)[0])

    # Negative speech clips (see generate_negative_speech_clips) are
    # already single-phrase, already the right size — no segmenting
    # needed, just a train/test split like the positive clips get.
    speech_names = sorted(os.listdir(NEGATIVE_SPEECH_DIR))
    rng = np.random.default_rng(1)
    rng.shuffle(speech_names)
    n_test = max(1, int(len(speech_names) * 0.15))
    for i, name in enumerate(speech_names):
        dst_dir = NEG_TEST if i < n_test else NEG_TRAIN
        shutil.copy(os.path.join(NEGATIVE_SPEECH_DIR, name), os.path.join(dst_dir, name))

    ingest_real_negative_clips()


# =========================================================
# STEP 5 — false-positive validation set
# =========================================================

def build_false_positive_validation():
    """
    A long, continuous raw-embedding stream for train.py's
    false_positive_validation_data_path — a DIFFERENT shape than the
    per-clip negative_features_test.npy augment_clips produces
    (train.py slides a window over this file itself internally), so
    it's built separately, from the same held-out environment
    recording negative_test is segmented from. Reads the ORIGINAL
    whole file from DEMAND_DIR, not NEG_TEST — that directory holds
    the short segmented clips assemble_negatives() splits it into
    (see that function's docstring for why), not the long continuous
    recording this needs.
    """
    out_path = os.path.join(MODEL_DIR, "false_positive_validation.npy")
    if os.path.exists(out_path):
        return out_path

    from openwakeword.utils import AudioFeatures

    held_out_path = os.path.join(DEMAND_DIR, HELD_OUT_ENV)
    audio = _load_mono_16k_int16(held_out_path)

    features = AudioFeatures(device="cpu")
    embeddings = features._get_embeddings(audio)
    np.save(out_path, embeddings)
    return out_path


# =========================================================
# STEP 6 — train
# =========================================================

def write_config(fp_val_path):
    config = {
        "target_phrase": ["hey fred"],
        "model_name": MODEL_NAME,
        "output_dir": OUTPUT_DIR,
        "piper_sample_generator_path": PIPER_SAMPLE_GENERATOR_DIR,
        # Unused — clips are pre-generated (steps 1-3 above), so
        # --generate_clips is never passed. Left present because
        # train.py reads these config keys unconditionally regardless.
        "n_samples": 0,
        "n_samples_val": 0,
        "tts_batch_size": 1,
        "custom_negative_phrases": [],
        "rir_paths": [],
        "background_paths": [],
        "background_paths_duplication_rate": [],
        "false_positive_validation_data_path": fp_val_path,
        "augmentation_rounds": 1,
        "augmentation_batch_size": 16,
        "model_type": "dnn",
        "layer_size": 128,
        "steps": 20000,
        "max_negative_weight": 1500,
        "target_false_positives_per_hour": 0.2,
        "batch_n_per_class": {"positive": 200, "adversarial_negative": 200},
        "feature_data_files": {},
    }
    config_path = os.path.join(WORKSPACE, "training_config.yaml")
    with open(config_path, "w") as f:
        yaml.dump(config, f)
    return config_path


def run_training(config_path, overwrite_features=False):
    """
    train.py does the real work (feature extraction, training, ONNX
    export) then unconditionally tries a bonus TFLite conversion this
    pipeline has no use for (wakeword.py's runtime Model loads the
    ONNX directly) — needs onnx_tf, which needs TensorFlow, a multi-
    GB dependency purely for a format nothing here reads. Rather than
    install that, tolerate a failure at that specific tail end: the
    ONNX export (export_model) always runs and completes BEFORE the
    TFLite step (convert_onnx_to_tflite) even starts, so if the .onnx
    is already on disk when something raises, the run got everything
    that's actually needed and the failure is the known, harmless one.
    """
    import runpy
    train_py = os.path.join(CORE, "venv", "Lib", "site-packages", "openwakeword", "train.py")
    argv = [train_py, "--training_config", config_path, "--augment_clips", "--train_model"]
    if overwrite_features:
        argv.append("--overwrite")
    sys.argv = argv

    expected_onnx = os.path.join(OUTPUT_DIR, MODEL_NAME + ".onnx")
    try:
        runpy.run_path(train_py, run_name="__main__")
    except Exception as e:
        if os.path.exists(expected_onnx):
            print(f"[wakeword_train] training/export succeeded; ignoring a post-export failure (likely the unused TFLite conversion step): {e}")
        else:
            raise


def _replace_file(src, dst):
    """
    Copy-then-atomic-rename instead of shutil.copy straight onto dst.
    Confirmed live 2026-08-11: FRED running at the same time keeps
    hey_fred.onnx.data memory-mapped for its whole process lifetime, so
    shutil.copy's open(dst, 'wb') hit OSError [Errno 22] trying to
    truncate a file another process has mapped — leaving a NEW .onnx
    graph paired with the OLD .onnx.data on disk (only the graph copy
    had run first), a silently broken runtime pair. os.replace() only
    swaps the directory entry rather than truncating the live file, so
    it isn't blocked by the mmap the same way, and it's atomic — a
    reader never sees a half-written file either.
    """
    tmp = dst + ".tmp"
    shutil.copy(src, tmp)
    os.replace(tmp, dst)


def install_runtime_model():
    """Copy the trained .onnx to the runtime location, matching the
    Kokoro/Vosk convention of keeping installed models separate from
    build output. torch's newer ONNX exporter splits large-enough
    models into two files — the graph (.onnx) and its weights, as
    external data (.onnx.data), which the graph references by
    filename — so both have to travel together or onnxruntime fails
    to find the weights when loading the "installed" copy."""
    runtime_dir = os.path.join(CORE, "models", "wakeword")
    os.makedirs(runtime_dir, exist_ok=True)

    src = os.path.join(OUTPUT_DIR, MODEL_NAME + ".onnx")
    dst = os.path.join(runtime_dir, MODEL_NAME + ".onnx")

    src_data = src + ".data"
    if os.path.exists(src_data):
        # Data before graph: if FRED is running and blocks one of these,
        # leaving the OLD graph paired with the NEW data is safe (same
        # architecture every run, only the learned weights differ) —
        # the reverse order risks a NEW graph over OLD data instead,
        # which is what actually broke this run.
        _replace_file(src_data, dst + ".data")
    _replace_file(src, dst)

    return dst


def main(overwrite_features=False):
    print("[wakeword_train] downloading DEMAND negatives...")
    download_demand()
    print("[wakeword_train] generating synthetic noise...")
    generate_synthetic_noise()
    print("[wakeword_train] generating positive clips via Kokoro...")
    generate_positive_clips()
    print("[wakeword_train] ingesting real recorded positive clips (if any)...")
    ingest_real_positive_clips()
    print("[wakeword_train] generating negative speech clips via Kokoro...")
    generate_negative_speech_clips()
    print("[wakeword_train] assembling negative_train/negative_test...")
    assemble_negatives()
    print("[wakeword_train] building false-positive validation set...")
    fp_val = build_false_positive_validation()
    print("[wakeword_train] writing training config...")
    config_path = write_config(fp_val)
    print("[wakeword_train] training (this takes a while on CPU)...")
    run_training(config_path, overwrite_features=overwrite_features)
    installed = install_runtime_model()
    print(f"[wakeword_train] done — model installed at {installed}")


if __name__ == "__main__":
    main(overwrite_features="--overwrite" in sys.argv)
