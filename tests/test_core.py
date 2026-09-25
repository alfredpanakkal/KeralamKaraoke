def test_core_imports():
    import karaoke.core
    assert hasattr(karaoke.core, "sanitise_name")
    assert hasattr(karaoke.core, "build_output_paths")