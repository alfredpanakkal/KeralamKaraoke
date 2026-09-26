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
