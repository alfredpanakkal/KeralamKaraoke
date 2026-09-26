import os
import time
from pathlib import Path

import pytest


class FakeSessionState(dict):
    """Minimal session_state replacement supporting both dict and attribute access."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)

    def __setattr__(self, name, value):
        self[name] = value

    def __delattr__(self, name):
        try:
            del self[name]
        except KeyError:
            raise AttributeError(name)


def test_import_app_runs_without_streamlit():
    """Importing app.py should not crash outside Streamlit runtime."""
    import app

    # main() runs at import; with no uploaded file it should just return
    assert hasattr(app, "_cached_bytes")
    assert hasattr(app, "reset_song_state")


def test_cached_bytes_rereads_on_mtime_change(tmp_path, monkeypatch):
    import app

    # Provide a fake session_state dict
    fake_state = FakeSessionState()
    monkeypatch.setattr(app.st, "session_state", fake_state)

    f = tmp_path / "a.wav"
    f.write_bytes(b"v1")

    # First read
    result1 = app._cached_bytes(f)
    assert result1 == b"v1"

    # Bump mtime and change content
    os.utime(f, None)
    time.sleep(0.01)  # ensure mtime changes
    f.write_bytes(b"v2")
    os.utime(f, (time.time() + 2, time.time() + 2))

    # Second read should pick up new content
    result2 = app._cached_bytes(f)
    assert result2 == b"v2"


def test_reset_song_state_clears_dl_bytes_keeps_other(monkeypatch):
    import app

    state = FakeSessionState({"_dl_bytes:x": (1, b""), "unrelated": 1})
    monkeypatch.setattr(app.st, "session_state", state)

    app.reset_song_state("song.mp3", app.MODE_KARAOKE)

    # _dl_bytes:* keys should be gone
    assert "_dl_bytes:x" not in state
    # unrelated keys preserved
    assert state["unrelated"] == 1
    # new session keys set
    assert state["current_file"] == "song.mp3"
    assert state["mode"] == app.MODE_KARAOKE


def test_mode_constants_are_distinct():
    import app

    assert app.MODE_KARAOKE != app.MODE_STEMS