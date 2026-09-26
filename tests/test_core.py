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
