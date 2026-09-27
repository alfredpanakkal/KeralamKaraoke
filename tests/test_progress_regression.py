"""Regression tests for progress-thread lifecycle (critical review findings)."""
from tests.test_app import FakeSessionState, FakeStreamlit, FakeUpload


class DeadThread:
    def is_alive(self):
        return False


def _make_fake(state, **kw):
    fake = FakeStreamlit(state, **kw)
    # _render_progress_ui needs st.progress; FakeStreamlit lacks it.
    fake.progress = lambda *a, **k: type("B", (), {"progress": lambda s, y: None})()
    return fake


def test_progress_thread_none_does_not_crash(monkeypatch):
    import app
    state = FakeSessionState({
        "current_file": FakeUpload.name,
        "mode": app.MODE_KARAOKE,
        "model": "htdemucs",
        "progress_tracker": app.ProgressTracker(),
        "progress_thread": None,
    })
    fake = _make_fake(state, uploaded=FakeUpload(), mode=app.MODE_KARAOKE)
    monkeypatch.setattr(app, "st", fake)
    app.main()  # must not raise AttributeError: 'NoneType' has no 'is_alive'


def test_dead_thread_with_done_result_still_consumed(monkeypatch):
    import app
    tracker = app.ProgressTracker()
    tracker.set_done(result="/fake/path_no_vocals.wav")
    state = FakeSessionState({
        "current_file": FakeUpload.name,
        "mode": app.MODE_KARAOKE,
        "model": "htdemucs",
        "song_name": "My_Song",
        "paths": {"instrumental": "/fake/x.wav", "final": "out.wav", "cache_dir": "cache"},
        "progress_tracker": tracker,
        "progress_thread": DeadThread(),
    })
    fake = _make_fake(state, uploaded=FakeUpload(), mode=app.MODE_KARAOKE)
    monkeypatch.setattr(app, "st", fake)
    # Avoid touching real files downstream: stub the render to observe consumption.
    monkeypatch.setattr(app, "_render_progress_ui", lambda tracker, mode: (True, None, "/fake/path_no_vocals.wav"))
    app.main()
    assert state.get("instrumental_path") == "/fake/path_no_vocals.wav"
