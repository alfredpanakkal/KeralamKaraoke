from pathlib import Path
import io
import os
import stat
import sys

import pytest
import soundfile as sf

import karaoke.core


def test_core_imports():
    assert hasattr(karaoke.core, "sanitise_name")
    assert hasattr(karaoke.core, "build_output_paths")
    assert hasattr(karaoke.core, "build_demucs_argv")


def test_sanitise_name_removes_forbidden_chars():
    assert karaoke.core.sanitise_name("A/B*C?D\"E<F>G|H") == "ABCDEFGH"
    assert karaoke.core.sanitise_name("normal_name.wav") == "normal_name.wav"


def test_sanitise_name_replaces_spaces_but_keeps_other_punctuation():
    # Parens, exclamation and dots are legal in filenames and stay put.
    assert karaoke.core.sanitise_name("My Song (Remix)!.mp3") == "My_Song_(Remix)!.mp3"


def test_sanitise_name_preserves_extension():
    assert karaoke.core.sanitise_name("track.mp3").endswith(".mp3")
    assert karaoke.core.sanitise_name("track.WAV").endswith(".WAV")


def test_model_options_map_the_two_exact_labels():
    assert karaoke.core.MODEL_OPTIONS == {
        "htdemucs (fast, good enough for karaoke)": "htdemucs",
        "htdemucs_ft (best quality, roughly 4x slower)": "htdemucs_ft",
    }


def test_model_default_is_htdemucs():
    assert karaoke.core.MODEL_DEFAULT == "htdemucs"
    # The model the UI starts on must be one a user can still get back to.
    assert karaoke.core.MODEL_DEFAULT in karaoke.core.MODEL_OPTIONS.values()


def test_model_option_values_are_non_empty_strings():
    for value in karaoke.core.MODEL_OPTIONS.values():
        assert isinstance(value, str) and value.strip()


def test_the_first_model_option_is_the_default():
    # The selector offers list(MODEL_OPTIONS) with no index, so entry order
    # is the out-of-the-box choice.
    labels = list(karaoke.core.MODEL_OPTIONS)
    assert karaoke.core.MODEL_OPTIONS[labels[0]] == karaoke.core.MODEL_DEFAULT


def test_build_output_paths_creates_per_song_dirs():
    base = {"separated": "separated", "output": "karaoke_out", "cache": "karaoke_cache"}
    paths = karaoke.core.build_output_paths("my_song", base)
    assert "my_song" in paths["instrumental"]
    assert paths["final"].endswith("my_song_karaoke.wav")
    assert "my_song" in paths["cache_dir"]


def test_build_output_paths_isolates_different_songs():
    base = {"separated": "separated", "output": "karaoke_out", "cache": "karaoke_cache"}
    a = karaoke.core.build_output_paths("song_a", base)
    b = karaoke.core.build_output_paths("song_b", base)
    # Every path must differ, or song B overwrites song A.
    assert a["instrumental"] != b["instrumental"]
    assert a["final"] != b["final"]
    assert a["cache_dir"] != b["cache_dir"]


def test_build_demucs_argv_uses_correct_flags():
    argv = karaoke.core.build_demucs_argv("/path/input.mp3", "separated", "htdemucs")
    # Invoked via the current interpreter, not a bare "demucs" on PATH --
    # the bare command breaks when the venv isn't activated.
    assert "python" in argv[0].lower()
    assert argv[1] == "-m" and argv[2] == "demucs"
    assert "-n" in argv and "htdemucs" in argv
    assert "--two-stems=vocals" in argv
    assert "--filename" in argv and "{stem}.{ext}" in argv
    assert "-o" in argv and "separated" in argv
    assert "/path/input.mp3" in argv
    # Device must NOT be hardcoded -- demucs auto-detects cuda/mps/cpu.
    assert "--device" not in argv


def test_build_stems_argv_has_no_two_stems_and_per_song_names():
    argv = karaoke.core.build_stems_argv("/path/input.mp3", "separated", "htdemucs")
    assert "python" in argv[0].lower()
    assert argv[1] == "-m" and argv[2] == "demucs"
    assert "-n" in argv and "htdemucs" in argv
    # Full 4-stem mode: no --two-stems restriction.
    assert not any(flag.startswith("--two-stems") for flag in argv)
    # Stems must be named per-song so tracks cannot clobber each other.
    assert "--filename" in argv and "{track}_{stem}.{ext}" in argv
    assert "-o" in argv and "separated" in argv
    assert "/path/input.mp3" in argv
    assert "--device" not in argv


def test_safe_upload_name_blocks_traversal():
    for evil in ["../../evil.mp3", "..\\..\\evil.mp3", "/etc/passwd",
                 "..", "....mp3", "", "normal song.mp3"]:
        safe = karaoke.core.safe_upload_name(evil)
        assert safe, "must never be empty"
        assert "/" not in safe and "\\" not in safe
        assert not safe.startswith(".")


def test_safe_upload_name_resolves_inside_target_dir(tmp_path):
    for evil in ["../../evil.mp3", "..\\x.mp3", "a/b.mp3"]:
        resolved = (tmp_path / karaoke.core.safe_upload_name(evil)).resolve()
        assert resolved.parent == tmp_path.resolve()


def test_safe_upload_name_keeps_normal_names_readable():
    assert karaoke.core.safe_upload_name("My Song.mp3") == "My_Song.mp3"


def test_build_output_paths_respects_model_name():
    paths = karaoke.core.build_output_paths(
        "song", {"separated": "sep", "output": "out", "cache": "c"},
        model="htdemucs_ft",
    )
    assert "htdemucs_ft" in paths["instrumental"]
    assert paths["instrumental"] == str(Path("sep") / "htdemucs_ft" / "song_no_vocals.wav")


def test_build_output_paths_defaults_to_htdemucs():
    paths = karaoke.core.build_output_paths(
        "song", {"separated": "sep", "output": "out", "cache": "c"},
    )
    assert "htdemucs" in paths["instrumental"]


def test_safe_upload_name_handles_windows_reserved_names():
    assert karaoke.core.safe_upload_name("CON.mp3") == "_CON.mp3"
    assert karaoke.core.safe_upload_name("nul.wav") == "_nul.wav"
    assert karaoke.core.safe_upload_name("com1.mp3") == "_com1.mp3"
    # non-reserved passes through untouched
    assert karaoke.core.safe_upload_name("constant.mp3") == "constant.mp3"


def test_safe_upload_name_strips_trailing_dots_and_caps_length():
    a = karaoke.core.safe_upload_name("song.mp3.")
    assert a == "song.mp3"
    long_name = "x" * 300 + ".mp3"
    assert len(karaoke.core.safe_upload_name(long_name)) <= 104  # 100-stem cap + ext


def test_upload_too_large_is_rejected():
    limit = 150 * 1024 * 1024
    ok, msg = karaoke.core.validate_upload_size(limit + 1)
    assert not ok and "150" in msg
    ok, _ = karaoke.core.validate_upload_size(limit)
    assert ok


def test_probe_audio_reports_header_of_a_real_wav(tmp_path):
    path = tmp_path / "song.wav"
    sf.write(path, [0.0] * 800, 8000)
    facts = karaoke.core.probe_audio(path)
    assert abs(facts["duration"] - 0.1) < 1e-6
    assert facts["samplerate"] == 8000
    assert facts["channels"] == 1
    assert facts["format"] == "WAV"


def test_probe_audio_accepts_an_open_file_object(tmp_path):
    # Streamlit's UploadedFile is an io.BytesIO subclass, so the UI probes the
    # upload without copying it to disk first.
    path = tmp_path / "song.wav"
    sf.write(path, [0.0] * 800, 8000)
    facts = karaoke.core.probe_audio(io.BytesIO(path.read_bytes()))
    assert abs(facts["duration"] - 0.1) < 1e-6
    assert facts["samplerate"] == 8000
    assert facts["channels"] == 1


def test_probe_audio_returns_none_for_a_non_audio_file(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_bytes(b"not audio at all")
    assert karaoke.core.probe_audio(path) is None


def test_probe_audio_returns_none_for_a_missing_file(tmp_path):
    assert karaoke.core.probe_audio(tmp_path / "nope.wav") is None


def test_probe_audio_returns_exactly_the_four_expected_keys(tmp_path):
    path = tmp_path / "song.wav"
    sf.write(path, [0.0] * 800, 8000)
    facts = karaoke.core.probe_audio(path)
    assert set(facts) == {"duration", "samplerate", "channels", "format"}


def test_estimate_seconds_per_minute_is_the_measured_gpu_rate():
    assert karaoke.core.ESTIMATE_SECONDS_PER_MINUTE == 45


@pytest.mark.parametrize(
    ("seconds", "minutes"),
    [(0, 1), (-5, 1), (30, 1), (90, 2), (3600, 80)],
)
def test_estimate_minutes(seconds, minutes):
    # A one-minute floor whatever the header says, so a short or unreadable
    # duration never reads as "0 min to process".
    assert karaoke.core.estimate_minutes(seconds) == minutes


def test_clear_generated_files_empties_the_roots_and_keeps_them(tmp_path):
    uploads = tmp_path / "uploads"
    cache = tmp_path / "karaoke_cache"
    uploads.mkdir()
    (uploads / "My_Song.mp3").write_bytes(b"upload")
    (uploads / "notes.txt").write_bytes(b"scratch")
    # per-song cache dir, the shape separated/ and karaoke_cache/ actually take
    (cache / "My_Song").mkdir(parents=True)
    (cache / "My_Song" / "shift_0.wav").write_bytes(b"cached")

    deleted, failures = karaoke.core.clear_generated_files([uploads, cache])

    assert (deleted, failures) == (3, [])
    # The roots survive: the app reuses them on the next run.
    assert uploads.is_dir() and cache.is_dir()
    assert list(uploads.iterdir()) == []
    assert list(cache.iterdir()) == []  # the emptied subdirectory is pruned


def test_clear_generated_files_skips_a_root_that_does_not_exist(tmp_path):
    present = tmp_path / "karaoke_out"
    present.mkdir()
    (present / "My_Song_karaoke.wav").write_bytes(b"out")
    missing = tmp_path / "never_created"

    deleted, failures = karaoke.core.clear_generated_files([missing, present])

    assert (deleted, failures) == (1, [])
    assert present.is_dir() and list(present.iterdir()) == []
    assert not missing.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="POSIX deletes read-only files")
def test_clear_generated_files_reports_an_undeletable_file_instead_of_raising(tmp_path):
    root = tmp_path / "karaoke_cache"
    root.mkdir()
    locked = root / "locked.wav"
    locked.write_bytes(b"x")
    os.chmod(locked, stat.S_IREAD)  # Windows refuses to unlink a read-only file
    try:
        deleted, failures = karaoke.core.clear_generated_files([root])
    finally:
        os.chmod(locked, stat.S_IWRITE)

    assert deleted == 0  # the file is still there, so it was not deleted
    assert len(failures) == 1
    assert failures[0].startswith(f"{locked}: ")  # "<path>: <reason>"
    assert locked.exists()
    locked.unlink()


def test_cache_limit_bytes_is_two_gigabytes():
    # A deliberate budget, so pin it: 2 GB is a few full-length songs, not an
    # unbounded accumulation.
    assert karaoke.core.CACHE_LIMIT_BYTES == 2 * 1024 ** 3


def _write_cache_entry(root: Path, relative: str, payload: bytes, mtime: float) -> Path:
    """Write one cache file and pin its mtime.

    The mtime is set explicitly because eviction order is the whole contract:
    without this, ordering would depend on filesystem timestamp resolution and
    on how fast the test happens to run.
    """
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    os.utime(path, (mtime, mtime))
    return path


def test_enforce_cache_limit_keeps_a_cache_at_or_under_the_limit(tmp_path):
    root = tmp_path / "karaoke_cache"
    first = _write_cache_entry(root, "Song_a/shift_0.wav", b"a" * 100, 1000.0)
    second = _write_cache_entry(root, "Song_b/shift_-3.wav", b"b" * 100, 2000.0)

    # 200 bytes on disk against a 200 limit: exactly at it is not over it, so
    # there is nothing to free.
    freed = karaoke.core.enforce_cache_limit(str(root), limit_bytes=200)

    assert freed == 0
    assert first.is_file() and second.is_file()


def test_enforce_cache_limit_deletes_oldest_first_and_keeps_the_newest(tmp_path):
    """Eviction order is the contract: the entry the render path is about to
    read is the newest one, so a sweep that keeps the newest cannot take it.

    Break it catches: sorting by size or by name instead of by mtime, keeping
    the oldest rather than the newest, and stopping one file late.
    """
    root = tmp_path / "karaoke_cache"
    oldest = _write_cache_entry(root, "Song_a/shift_-6.wav", b"o" * 400, 1000.0)
    middle = _write_cache_entry(root, "Song_a/shift_+1.wav", b"m" * 300, 2000.0)
    newest = _write_cache_entry(root, "Song_b/shift_+6.wav", b"n" * 200, 3000.0)

    freed = karaoke.core.enforce_cache_limit(str(root), limit_bytes=700)

    # 900 bytes on disk against a 700 limit: deleting the oldest leaves 500,
    # which is within the limit, so exactly one file goes.
    assert freed == 400
    assert not oldest.exists()
    assert middle.is_file()
    assert newest.is_file()


def test_enforce_cache_limit_sweeps_nested_per_song_subdirectories(tmp_path):
    """The cache is a directory per song, so a top-level-only sweep would free
    nothing at all.

    Break it catches: globbing `root/*` instead of recursing.
    """
    root = tmp_path / "karaoke_cache"
    nested = _write_cache_entry(root, "Song_a/shift_-6.wav", b"x" * 100, 1000.0)
    top = _write_cache_entry(root, "loose.wav", b"y" * 100, 2000.0)

    freed = karaoke.core.enforce_cache_limit(str(root), limit_bytes=100)

    assert freed == 100
    assert not nested.exists()  # the older file, one level down, went first
    assert top.is_file()
    # Only files are evicted, so the emptied per-song directory is still there
    # for the next run to write into.
    assert (root / "Song_a").is_dir()


def test_enforce_cache_limit_of_zero_sweeps_the_whole_cache(tmp_path):
    root = tmp_path / "karaoke_cache"
    _write_cache_entry(root, "Song_a/shift_-6.wav", b"x" * 100, 1000.0)
    _write_cache_entry(root, "Song_b/shift_+6.wav", b"y" * 100, 2000.0)

    freed = karaoke.core.enforce_cache_limit(str(root), limit_bytes=0)

    assert freed == 200
    assert list(root.rglob("*.wav")) == []


def test_enforce_cache_limit_on_a_cache_that_does_not_exist(tmp_path):
    """Never raises: a cache the app has not filled yet is not an error.

    Break it catches: creating the cache as a side effect of walking it, and
    raising on the missing root instead of reporting nothing to do.
    """
    missing = tmp_path / "karaoke_cache"

    assert karaoke.core.enforce_cache_limit(str(missing)) == 0
    assert not missing.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="POSIX deletes read-only files")
def test_enforce_cache_limit_skips_an_undeletable_file_and_keeps_sweeping(tmp_path):
    """Reclaiming space must never break playback, so a file the OS refuses to
    delete is skipped -- and only bytes actually freed are reported.

    Break it catches: letting the OSError escape, counting a skipped file's
    size as freed, and abandoning the sweep at the first failure.
    """
    root = tmp_path / "karaoke_cache"
    locked = _write_cache_entry(root, "Song_a/shift_-6.wav", b"x" * 100, 1000.0)
    os.chmod(locked, stat.S_IREAD)  # Windows refuses to unlink a read-only file
    deletable = _write_cache_entry(root, "Song_b/shift_+6.wav", b"y" * 100, 2000.0)
    try:
        freed = karaoke.core.enforce_cache_limit(str(root), limit_bytes=0)
    finally:
        os.chmod(locked, stat.S_IWRITE)

    assert freed == 100
    assert locked.exists()  # skipped, so its 100 bytes were never freed
    assert not deletable.exists()  # the sweep carried on past the failure
    locked.unlink()
