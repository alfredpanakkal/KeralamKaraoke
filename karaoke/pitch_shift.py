"""Pitch shifting with content-addressed file caching.

The cache is keyed on the instrumental's content hash plus the semitone
count, so dragging the slider back to a value you already visited is
instant, and the cache survives an app restart.
"""

import hashlib
import shutil
from pathlib import Path

# librosa pulls in a large dependency tree. Importing it lazily keeps the
# zero-shift path (a plain file copy) usable without it.
def _shift_audio(path: str, semitones: int) -> None:
    import librosa
    import soundfile as sf

    audio, sr = librosa.load(path, sr=None)
    sf.write(path, librosa.effects.pitch_shift(audio, sr=sr, n_steps=semitones), sr)


def _cache_key(instrumental_path: str, semitones: int) -> str:
    digest = hashlib.blake2b(
        Path(instrumental_path).read_bytes(), digest_size=16
    ).hexdigest()
    return f"{Path(instrumental_path).stem}_{digest}_{semitones:+d}.wav"


def pitch_shift_cached(
    instrumental_path: str,
    semitones: int,
    cache_dir: str,
    shifter=None,
) -> str:
    """Return the path to the pitch-shifted file, computing it only if needed.

    `shifter` is injectable for testing; it defaults to the librosa path.
    """
    cache_path = Path(cache_dir) / _cache_key(instrumental_path, semitones)
    if cache_path.exists():
        return str(cache_path)

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if semitones == 0:
        shutil.copy2(instrumental_path, cache_path)
        return str(cache_path)

    # Write to a temp name first so an interrupted shift cannot leave a
    # truncated file that later reads as a valid cache hit. Keep the .wav
    # suffix so soundfile can infer the output format.
    tmp_path = cache_path.with_name(cache_path.stem + ".tmp.wav")
    shutil.copy2(instrumental_path, tmp_path)
    try:
        (shifter or _shift_audio)(str(tmp_path), semitones)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    tmp_path.replace(cache_path)
    return str(cache_path)
