import pytest
import re
from pathlib import Path

import numpy as np
import soundfile as sf

import karaoke.pitch_shift


def _write_wav(path, seconds=1.0, sr=44100):
    sf.write(path, np.zeros(int(sr * seconds)), sr)
    return path


def test_pitch_shift_imports():
    assert hasattr(karaoke.pitch_shift, "pitch_shift_cached")


def test_zero_shift_returns_a_copy_not_the_original(tmp_path):
    src = _write_wav(tmp_path / "in.wav")
    out = karaoke.pitch_shift.pitch_shift_cached(str(src), 0, str(tmp_path / "cache"))
    assert out != str(src)
    assert Path(out).exists()
    assert Path(out).read_bytes() == Path(src).read_bytes()


def test_shift_is_computed_only_once_per_value(tmp_path):
    """The bug this cache exists to prevent: recomputing on every slider tick."""
    src = _write_wav(tmp_path / "in.wav")
    calls = []

    def fake_shifter(path, semitones):
        calls.append(semitones)

    cache = str(tmp_path / "cache")
    first = karaoke.pitch_shift.pitch_shift_cached(str(src), 2, cache, shifter=fake_shifter)
    second = karaoke.pitch_shift.pitch_shift_cached(str(src), 2, cache, shifter=fake_shifter)
    karaoke.pitch_shift.pitch_shift_cached(str(src), -2, cache, shifter=fake_shifter)

    assert first == second
    assert len(calls) == 2, "expected one compute for +2 and one for -2, nothing more"
    assert sorted(calls) == [-2, 2]


def test_different_semitones_produce_different_cache_entries(tmp_path):
    src = _write_wav(tmp_path / "in.wav")
    cache = str(tmp_path / "cache")
    up = karaoke.pitch_shift.pitch_shift_cached(str(src), 3, cache, shifter=lambda p, s: None)
    down = karaoke.pitch_shift.pitch_shift_cached(str(src), -3, cache, shifter=lambda p, s: None)
    assert up != down


def test_different_songs_do_not_share_cache_entries(tmp_path):
    cache = str(tmp_path / "cache")
    a = _write_wav(tmp_path / "a.wav")
    b = _write_wav(tmp_path / "b.wav")
    out_a = karaoke.pitch_shift.pitch_shift_cached(str(a), 0, cache)
    out_b = karaoke.pitch_shift.pitch_shift_cached(str(b), 0, cache)
    assert out_a != out_b


def test_no_partial_file_left_behind_on_success(tmp_path):
    src = _write_wav(tmp_path / "in.wav")
    cache = str(tmp_path / "cache")
    karaoke.pitch_shift.pitch_shift_cached(str(src), 1, cache, shifter=lambda p, s: None)
    assert list(Path(cache).glob("*.tmp.wav")) == []


def test_cache_key_is_blake2b_and_content_sensitive(tmp_path):
    a = tmp_path / "a.wav"
    b = tmp_path / "b.wav"
    a.write_bytes(b"same-bytes")
    b.write_bytes(b"other-bytes")
    key_a1 = karaoke.pitch_shift._cache_key(str(a), 2)
    key_a2 = karaoke.pitch_shift._cache_key(str(a), 2)
    key_b = karaoke.pitch_shift._cache_key(str(b), 2)
    assert key_a1 == key_a2          # deterministic
    assert key_a1 != key_b           # content-sensitive
    assert re.fullmatch(r".*_[0-9a-f]{32}_[+-]\d+\.wav", key_a1)


def test_failed_shift_leaves_no_orphaned_tmp(tmp_path):
    src = _write_wav(tmp_path / "in.wav")
    cache = str(tmp_path / "cache")

    def crash(p, s):
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        karaoke.pitch_shift.pitch_shift_cached(str(src), 2, cache, shifter=crash)
    assert list(Path(cache).glob("*")) == []


def test_shift_audio_preserves_stereo(tmp_path, monkeypatch):
    """librosa.load defaults to mono=True, which would silently collapse the
    stereo image of every shifted track. mono=False is the fix, plus the
    transpose, because librosa returns (channels, samples) while soundfile
    writes (samples, channels).

    librosa and soundfile are stubbed: asserting on the real filter chain
    would need the full resampling stack for a wiring check.
    """
    import sys
    from unittest.mock import MagicMock

    fake_librosa = MagicMock()
    stereo = np.arange(6, dtype=np.float32).reshape(2, 3)  # (channels, samples)
    fake_librosa.load.return_value = (stereo.copy(), 22050)
    fake_librosa.effects.pitch_shift.side_effect = lambda audio, sr, n_steps: audio
    fake_sf = MagicMock()
    monkeypatch.setitem(sys.modules, "librosa", fake_librosa)
    monkeypatch.setitem(sys.modules, "soundfile", fake_sf)

    karaoke.pitch_shift._shift_audio(str(tmp_path / "x.wav"), 2)

    _, load_kwargs = fake_librosa.load.call_args
    assert load_kwargs.get("mono") is False
    written = fake_sf.write.call_args[0][1]
    assert written.shape == (3, 2)  # back to (samples, channels) for soundfile
