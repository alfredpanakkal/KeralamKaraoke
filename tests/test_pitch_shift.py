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
    assert key_a1.split("_")[1].isalnum() and len(key_a1.split("_")[1]) == 32
