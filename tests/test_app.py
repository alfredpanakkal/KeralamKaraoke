import os
import time
from contextlib import contextmanager
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


class FakeUpload:
    """Just enough UploadedFile for main(): a name and a size.

    getbuffer() is deliberately absent -- the click path must never need to
    write the upload out again.
    """

    name = "My_Song.mp3"
    size = 4 * 1024 * 1024


class FakeStreamlit:
    """The smallest Streamlit double that can drive app.main().

    Only the framework boundary is faked: the script itself runs for real
    against real files, so a stale path left in the session raises for real.
    `sidebar` is this same object -- the sidebar is a view onto the one script,
    so one recorder covers both.
    """

    def __init__(self, session_state, uploaded, mode, clicked=(), generate=False):
        self.session_state = session_state
        self.sidebar = self
        self.uploaded = uploaded
        self.mode = mode
        self.clicked = set(clicked)
        self.generate = generate
        self.events = []  # what the script called, in order
        self.reports = []  # (level, text, session_state as the user sees it)

    def _report(self, level, text):
        # Snapshot the session at report time: the ordering test reads it.
        self.reports.append((level, text, dict(self.session_state)))

    def set_page_config(self, **kwargs):
        pass

    def title(self, text):
        pass

    def write(self, text):
        pass

    def caption(self, text):
        pass

    def info(self, text):
        pass

    def success(self, text):
        self._report("success", text)

    def warning(self, text):
        self._report("warning", text)

    def error(self, text):
        self._report("error", text)

    def stop(self):
        raise AssertionError("st.stop() was reached")

    def file_uploader(self, label, type=None):
        self.events.append("file_uploader")
        return self.uploaded

    def radio(self, label, options, horizontal=False):
        return self.mode

    def slider(self, label, min_value, max_value, value, help=None):
        return 0

    def button(self, label, **kwargs):
        self.events.append(f"button:{label}")
        if label == "Generate":
            return self.generate
        return label in self.clicked

    @contextmanager
    def spinner(self, text):
        self.events.append("spinner")
        yield

    def audio(self, data):
        self.events.append("audio")

    def download_button(self, label, **kwargs):
        self.events.append(f"download:{label}")


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


def test_purge_song_state_clears_song_and_dl_bytes(monkeypatch):
    import app

    state = FakeSessionState({
        "_dl_bytes:x": (1, b""),
        "song_name": "old_song",
        "unrelated": 1,
    })
    monkeypatch.setattr(app.st, "session_state", state)

    app.purge_song_state()

    assert "_dl_bytes:x" not in state
    assert "song_name" not in state
    assert state["unrelated"] == 1

    # reset_song_state purges through the same path, then re-seeds the song.
    app.reset_song_state("song.mp3", app.MODE_KARAOKE)
    assert state["current_file"] == "song.mp3"
    assert state["mode"] == app.MODE_KARAOKE


CLEAR_BUTTON = "Clear generated files"


def _click_clear_generated(monkeypatch, tmp_path, mode):
    """Run app.main() with the sidebar's clear button clicked, for real.

    The four runtime dirs are redirected into tmp_path and the session is
    seeded as a finished song would leave it, so any path the render path
    still holds has genuinely been deleted. Returns (fake, roots).
    """
    import app

    roots = {
        name: tmp_path / name
        for name in ("uploads", "karaoke_out", "separated", "karaoke_cache")
    }
    for root in roots.values():
        root.mkdir()

    instrumental = roots["separated"] / "htdemucs" / "My_Song" / "no_vocals.wav"
    instrumental.parent.mkdir(parents=True)
    instrumental.write_bytes(b"separated audio")
    final = roots["karaoke_out"] / "My_Song_karaoke.wav"
    final.write_bytes(b"karaoke audio")
    (roots["uploads"] / "My_Song.mp3").write_bytes(b"the app's copy")
    cache = roots["karaoke_cache"] / "My_Song"
    cache.mkdir()
    (cache / "shift_0.wav").write_bytes(b"cached shift")

    monkeypatch.setattr(app, "UPLOAD_DIR", roots["uploads"])
    monkeypatch.setattr(app, "OUTPUT_DIR", roots["karaoke_out"])
    monkeypatch.setattr(app, "SEPARATED_DIR", roots["separated"])
    monkeypatch.setattr(app, "CACHE_DIR", roots["karaoke_cache"])

    state = FakeSessionState({
        "current_file": FakeUpload.name,
        "mode": mode,
        "song_name": "My_Song",
        "instrumental_path": str(instrumental),
        "paths": {
            "instrumental": str(instrumental),
            "final": str(final),
            "cache_dir": str(cache),
        },
        f"_dl_bytes:{final}": (1.0, b"karaoke audio"),
        "unrelated": "kept",
    })
    fake = FakeStreamlit(
        state, uploaded=FakeUpload(), mode=mode, clicked=(CLEAR_BUTTON,)
    )
    monkeypatch.setattr(app, "st", fake)
    app.main()
    return fake, roots


def _song_keys_left(state):
    """Every SONG_KEYS entry and _dl_bytes:* key still present in `state`."""
    import app

    return [
        key for key in state
        if key in app.SONG_KEYS or key.startswith("_dl_bytes:")
    ]


def _deleted_path_keys_left(state):
    """Session keys that still point at a file the click deleted.

    current_file is not one of them: it names the file the uploader is holding,
    which the reset trigger re-seeds from the upload on every rerun, clear or
    not. mode is not in SONG_KEYS at all.
    """
    return [key for key in _song_keys_left(state) if key != "current_file"]


def test_clear_button_empties_the_roots_and_purges_the_session(monkeypatch, tmp_path):
    """Global Constraint 6, end to end through the real script.

    Break it catches: the click handler losing purge_song_state(), or the purge
    landing after the results. Either leaves instrumental_path in the session,
    and the render path then reads a file this run deleted -- for real, which is
    how this test fails: a FileNotFoundError, not an assertion.
    """
    import app

    fake, roots = _click_clear_generated(monkeypatch, tmp_path, app.MODE_KARAOKE)

    for name, root in roots.items():
        assert root.is_dir(), f"{name} must survive for the next run"
        assert list(root.iterdir()) == [], f"{name} should be empty"
    assert _deleted_path_keys_left(fake.session_state) == []
    assert fake.session_state["unrelated"] == "kept"
    # Nothing stale is rendered: the deleted track gets no player this run.
    assert "audio" not in fake.events
    # The click reported once, and claimed no failures.
    assert [level for level, _text, _state in fake.reports] == ["success"]


def test_clear_click_is_handled_before_the_results_render(monkeypatch, tmp_path):
    """The brief's two ordering rules: the button is created above the
    uploader, and the purge happens before the deletion result is shown.

    Break it catches: the sidebar block moving below the uploader, so the click
    is handled after the results instead of before them; and
    purge_song_state() slipping to after the message.
    """
    import app

    fake, _roots = _click_clear_generated(monkeypatch, tmp_path, app.MODE_KARAOKE)

    events = fake.events
    assert events.index(f"button:{CLEAR_BUTTON}") < events.index("file_uploader")
    # Purged by the time the result is shown, so no statement left in the
    # handler can read a path the deletion already removed.
    _level, _text, at_report = fake.reports[0]
    assert _song_keys_left(at_report) == []


def test_mode_constants_are_distinct():
    import app

    assert app.MODE_KARAOKE != app.MODE_STEMS


def test_runtime_dirs_are_anchored_to_app_dir():
    """Runtime dirs must not depend on the process working directory."""
    import app

    app_root = Path(app.__file__).resolve().parent
    for directory in (app.UPLOAD_DIR, app.OUTPUT_DIR, app.SEPARATED_DIR, app.CACHE_DIR):
        assert directory.is_absolute()
        assert directory.parent == app_root
        assert directory.name in {"uploads", "karaoke_out", "separated", "karaoke_cache"}
