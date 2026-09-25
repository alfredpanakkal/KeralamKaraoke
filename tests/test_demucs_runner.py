def test_demucs_runner_imports():
    import karaoke.demucs_runner
    assert hasattr(karaoke.demucs_runner, "run_demucs")
    assert hasattr(karaoke.demucs_runner, "separate_vocals")