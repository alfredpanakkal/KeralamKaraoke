import array
import io
import os
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
import soundfile as sf


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


def _wav_bytes(seconds, rate=8000):
    """Real wav bytes, in memory.

    8-bit mono keeps a 90-second fixture at 720 KB instead of 5.7 MB, which is
    what makes a duration long enough to move the estimate off its floor
    affordable in a test.
    """
    samples = array.array("h", [0]) * int(seconds * rate)
    buffer = io.BytesIO()
    sf.write(buffer, samples, rate, format="WAV", subtype="PCM_U8")
    return buffer.getvalue()


# 0.5 s at 8 kHz, 8-bit mono: a real header to read, for every test that just
# needs an upload that is readable audio.
SHORT_AUDIO = _wav_bytes(0.5)

# 90.0 s at 8 kHz, 8-bit mono: 720,044 bytes, so the caption reads
# "0.7 MB ... 90s of audio ... about 2 min to process".
LONG_AUDIO = _wav_bytes(90)


class FakeUpload(io.BytesIO):
    """Just enough UploadedFile for main(): a name, a size, and real audio.

    Streamlit hands the script an io.BytesIO subclass, and app.py reads its
    audio header straight off that object, so the double is one too -- a name
    and a size alone would probe as unreadable and warn on every run.

    `payload` overrides the audio, for the file that is not audio at all.
    """
    name = "My_Song.mp3"

    def __init__(self, payload=None):
        audio = SHORT_AUDIO if payload is None else payload
        super().__init__(audio)
        self.size = len(audio)

    def getbuffer(self):
        # Only the Generate branch may touch the buffer, so a copy there is a
        # real cost. The probe has to read the upload itself.
        raise AssertionError("getbuffer() is the Generate branch's, not the probe's")


class _FakeStatus:
    """Minimal stand-in for st.status context manager."""

    def __init__(self, parent):
        self.parent = parent

    def update(self, label=None, state=None, expanded=None, **kwargs):
        if label:
            self.parent.events.append(f"status_update:{label}")
        if state:
            self.parent.events.append(f"status_state:{state}")


class _FakeContainer:
    """Minimal stand-in for st.container context manager."""

    def __init__(self, parent):
        self.parent = parent

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


class FakeStreamlit:
    """The smallest Streamlit double that can drive app.main().

    Only the framework boundary is faked: the script itself runs for real
    against real files, so a stale path left in the session raises for real.
    `sidebar` is this same object -- the sidebar is a view onto the one script,
    so one recorder covers both.
    """

    def __init__(self, session_state, uploaded, mode, clicked=(), generate=False,
                 model_label=None):
        self.session_state = session_state
        self.sidebar = self
        self.uploaded = uploaded
        self.mode = mode
        self.clicked = set(clicked)
        self.generate = generate
        # None means "the user left the selector on its first option".
        self.model_label = model_label
        self.events = []  # what the script called, in order
        self.reports = []  # (level, text, session_state as the user sees it)
        # Kept out of `events` so an ordering assertion never trips over a word
        # in the copy itself.
        self.captions = []
        self.spinners = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    def _report(self, level, text):
        # Snapshot the session at report time: the ordering test reads it.
        self.reports.append((level, text, dict(self.session_state)))

    def set_page_config(self, **kwargs):
        pass

    def title(self, text, **kwargs):
        pass

    def write(self, text):
        pass

    def subheader(self, text, **kwargs):
        self.events.append(f"subheader:{text}")

    def caption(self, text):
        self.captions.append(text)

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

    def selectbox(self, label, options, index=0, help=None):
        self.events.append(f"selectbox:{label}")
        if self.model_label is not None:
            return self.model_label
        # New quality labels: "Fast (htdemucs)" or "Best (htdemucs_ft — 4× slower)"
        if "Fast" in str(options[0]):
            return options[index]
        return options[index]

    def slider(self, label, min_value, max_value, value, help=None):
        return 0

    def segmented_control(self, label, options, default=None, **kwargs):
        self.events.append(f"segmented_control:{label}")
        # Return the mode that was passed to the fake, mapping to the option
        for opt in options:
            if self.mode in opt:
                return opt
        if default is not None:
            return default
        return options[0]

    def button(self, label, **kwargs):
        self.events.append(f"button:{label}")
        if label in ("Generate", "Create karaoke track", "Split into stems"):
            return self.generate
        return label in self.clicked

    @contextmanager
    def spinner(self, text):
        self.events.append("spinner")
        self.spinners.append(text)
        yield

    @contextmanager
    def status(self, label, expanded=True, state="running", **kwargs):
        self.events.append(f"status:{label}")
        yield _FakeStatus(self)

    def toast(self, text, icon=None, **kwargs):
        self.events.append(f"toast:{text}")

    def container(self, border=False, **kwargs):
        self.events.append(f"container:border={border}")
        return _FakeContainer(self)

    def audio(self, data):
        self.events.append("audio")

    def download_button(self, label, **kwargs):
        self.events.append(f"download:{label}")

    def markdown(self, body, unsafe_allow_html=False, **kwargs):
        # Extract link text from the anchor tag for event recording
        import re
        match = re.search(r'<a [^>]*>([^<]+)</a>', body)
        if match:
            self.events.append(f"markdown:{match.group(1)}")

    def subheader(self, text, **kwargs):
        self.events.append(f"subheader:{text}")

    def header(self, text, **kwargs):
        self.events.append(f"header:{text}")

    @contextmanager
    def expander(self, label, expanded=False, icon=None, **kwargs):
        self.events.append(f"expander:{label}")
        yield


def test_import_app_runs_without_streamlit():
    """Importing app.py should not crash outside Streamlit runtime."""
    import app

    # main() runs at import; with no uploaded file it should just return
    assert hasattr(app, "_cached_bytes")
    assert hasattr(app, "reset_song_state")


def test_restaging_refreshes_static_file_validators(monkeypatch, tmp_path):
    """Re-staging the same song must rewrite the static file with a new mtime.

    Browsers revalidate cached stems against ETag/Last-Modified, both derived
    from the file's (mtime, size). If re-staging ever left the mtime unchanged,
    a reprocessed song would serve stale audio from the browser cache.
    """
    import app

    monkeypatch.setattr(app, "ROOT", tmp_path)
    stems_paths = {}
    for stem in ("vocals", "drums", "bass", "other"):
        src = tmp_path / f"{stem}.wav"
        src.write_bytes(b"RIFF")
        stems_paths[stem] = str(src)

    app._stage_stems_static(stems_paths, "Song")
    dest = tmp_path / "static" / "stems" / "Song" / "vocals.wav"
    first_mtime = dest.stat().st_mtime_ns

    # Simulate reprocessing: new source bytes with a different mtime
    src = tmp_path / "vocals.wav"
    src.write_bytes(b"RIFF_new")
    older = (time.time_ns() // 1_000_000_000 - 10) * 1_000_000_000
    os.utime(src, ns=(older, older))

    app._stage_stems_static(stems_paths, "Song")
    second_mtime = dest.stat().st_mtime_ns

    assert second_mtime != first_mtime


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
    """Session keys that still point at a file that is no longer there.

    current_file and model are not one of them: they name the file the uploader
    is holding and the model the selector is on, which the reset trigger
    re-seeds on every rerun, clear or not. mode is not in SONG_KEYS at all.
    """
    return [
        key for key in _song_keys_left(state)
        if key not in ("current_file", "model")
    ]


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


FT_LABEL = "Best (htdemucs_ft — 4× slower)"


def test_reset_song_state_replaces_previous_model(monkeypatch):
    import app
    from karaoke.core import MODEL_DEFAULT

    state = FakeSessionState({"model": "htdemucs_ft"})
    monkeypatch.setattr(app.st, "session_state", state)

    app.reset_song_state("song.mp3", app.MODE_KARAOKE, model=MODEL_DEFAULT)

    assert state["model"] == "htdemucs"


def test_reset_song_state_omits_model_when_not_given(monkeypatch):
    """The two-argument form must leave no model behind for a stale one to
    be read as the current choice."""
    import app

    state = FakeSessionState()
    monkeypatch.setattr(app.st, "session_state", state)

    app.reset_song_state("song.mp3", app.MODE_KARAOKE)

    assert "model" not in state


def _run_main_with_model(monkeypatch, seeded_model, chosen_label):
    """Run app.main() for real with a song already separated under
    `seeded_model`, and the selector left on `chosen_label` (None = its first
    option). Returns the fake, whose session_state is what the user is left
    with.

    Nothing is generated, so no file is written; the seeded result paths point
    at files that do not exist, so if the reset trigger fails to fire the
    render path reads one for real and this raises FileNotFoundError.
    """
    import app

    state = FakeSessionState({
        "current_file": FakeUpload.name,
        "mode": app.MODE_KARAOKE,
        "model": seeded_model,
        "song_name": "My_Song",
        "instrumental_path": f"separated/{seeded_model}/My_Song_no_vocals.wav",
        "paths": {
            "instrumental": f"separated/{seeded_model}/My_Song_no_vocals.wav",
            "final": "karaoke_out/My_Song_karaoke.wav",
            "cache_dir": "karaoke_cache/My_Song",
        },
    })
    fake = FakeStreamlit(
        state, uploaded=FakeUpload(), mode=app.MODE_KARAOKE, model_label=chosen_label
    )
    monkeypatch.setattr(app, "st", fake)
    app.main()
    return fake


def test_switching_model_discards_the_song_separated_with_the_old_one(monkeypatch):
    """Changing model invalidates the current song exactly as changing file
    or mode does."""
    fake = _run_main_with_model(monkeypatch, "htdemucs", FT_LABEL)

    assert fake.session_state["model"] == "htdemucs_ft"
    assert _deleted_path_keys_left(fake.session_state) == []
    # The stale track is not re-rendered from the old model's output path.
    assert "audio" not in fake.events


def test_the_model_selector_starts_on_htdemucs(monkeypatch):
    """Out of the box the app must still run plain htdemucs."""
    fake = _run_main_with_model(monkeypatch, "htdemucs_ft", None)

    assert fake.session_state["model"] == "htdemucs"


class WritableUpload(FakeUpload):
    """The Generate branch writes the upload out before separating it --
    FakeUpload refuses getbuffer, because no other path may need to."""

    def getbuffer(self):
        return b"not really an mp3"


class LongUpload(WritableUpload):
    """90 seconds of real audio, so the estimate is 2 minutes and not the
    one-minute floor a short clip would report."""

    def __init__(self):
        super().__init__(payload=LONG_AUDIO)


class UnreadableUpload(WritableUpload):
    """Real bytes that are not audio, so the probe reports None."""

    def __init__(self):
        super().__init__(payload=b"not audio at all")


def _redirect_runtime_dirs(monkeypatch, tmp_path):
    """Point every path the Generate branch writes to into tmp_path.

    BASE_DIRS is redirected too: it is frozen from the real paths at import, so
    patching the four constants alone would still write into the real
    karaoke_out/.
    """
    import app

    names = ("uploads", "karaoke_out", "separated", "karaoke_cache")
    roots = {name: tmp_path / name for name in names}
    monkeypatch.setattr(app, "UPLOAD_DIR", roots["uploads"])
    monkeypatch.setattr(app, "OUTPUT_DIR", roots["karaoke_out"])
    monkeypatch.setattr(app, "SEPARATED_DIR", roots["separated"])
    monkeypatch.setattr(app, "CACHE_DIR", roots["karaoke_cache"])
    monkeypatch.setattr(app, "BASE_DIRS", {
        "separated": str(roots["separated"]),
        "output": str(roots["karaoke_out"]),
        "cache": str(roots["karaoke_cache"]),
    })
    return roots


def _click_generate(monkeypatch, tmp_path, mode, upload=None):
    """Run app.main() for real with the Generate button clicked and the
    selector on FT_LABEL. The demucs runner is replaced by a recorder that
    writes the files the real one would, so everything downstream of the
    subprocess -- the pitch-shift cache, the final copy, the players -- runs
    for real. Returns (fake, calls, roots), where calls are the
    (input_path, separated_dir, model) triples the script passed to the runner.
    """
    import app
    from karaoke.core import STEMS

    roots = _redirect_runtime_dirs(monkeypatch, tmp_path)
    calls = []

    def record(input_path, separated_dir, model):
        calls.append((input_path, separated_dir, model))
        # Demucs writes into a directory named after the model, one file per
        # stem. Written for real, so the render path has real files to read.
        out = Path(separated_dir) / model
        out.mkdir(parents=True, exist_ok=True)

        def write(name):
            path = out / name
            path.write_bytes(b"separated audio")
            return path

        if mode == app.MODE_STEMS:
            return {stem: str(write(f"My_Song_{stem}.wav")) for stem in STEMS}
        return str(write("no_vocals.wav"))

    monkeypatch.setattr(app, "separate_vocals", record)
    monkeypatch.setattr(app, "separate_stems", record)

    fake = FakeStreamlit(
        FakeSessionState(),
        uploaded=WritableUpload() if upload is None else upload,
        mode=mode,
        generate=True,
        model_label=FT_LABEL,
    )
    monkeypatch.setattr(app, "st", fake)
    app.main()
    return fake, calls, roots


def test_the_chosen_model_reaches_demucs_and_the_output_paths(monkeypatch, tmp_path):
    """The resolved model must be what reaches the runner and the per-song
    paths -- not the label the user picked, and not a hard-coded default."""
    import app

    fake, calls, roots = _click_generate(monkeypatch, tmp_path, app.MODE_KARAOKE)

    assert calls == [(
        str(roots["uploads"] / "My_Song.mp3"),
        str(roots["separated"]),
        "htdemucs_ft",
    )]
    assert fake.session_state["paths"]["instrumental"] == str(
        roots["separated"] / "htdemucs_ft" / "My_Song_no_vocals.wav"
    )
    # A model-specific working directory, so one model's output can never be
    # read back as another's, and the whole karaoke render path ran for real.
    assert (roots["separated"] / "htdemucs_ft" / "no_vocals.wav").is_file()
    assert (roots["karaoke_out"] / "My_Song_karaoke.wav").is_file()
    assert fake.events[-1] == "download:Download karaoke track"


def test_the_chosen_model_reaches_the_stems_runner(monkeypatch, tmp_path):
    import app
    from karaoke.core import STEMS

    fake, calls, roots = _click_generate(monkeypatch, tmp_path, app.MODE_STEMS)

    assert calls == [(
        str(roots["uploads"] / "My_Song.mp3"),
        str(roots["separated"]),
        "htdemucs_ft",
    )]
    assert fake.session_state["paths"]["instrumental"] == str(
        roots["separated"] / "htdemucs_ft" / "My_Song_no_vocals.wav"
    )
    # Stems mode now uses markdown anchors with download attribute instead of
    # download_button (to avoid MessageSizeError). Verify the download links render.
    assert [e for e in fake.events if e.startswith("markdown:Download ")] == [
        f"markdown:Download {stem}" for stem in STEMS
    ]


# Both separation spinners, by the mode that reaches them; each must carry the
# estimate.
SEPARATION_LABELS = {
    "karaoke": "Separating vocals from instrumental...",
    "stems": "Separating stems...",
}


@pytest.mark.parametrize("mode_key", sorted(SEPARATION_LABELS))
def test_the_caption_reports_the_size_duration_and_estimate(monkeypatch, tmp_path,
                                                            mode_key):
    """Every figure in the caption is real: the size comes off the upload, the
    duration out of its header.

    The 90-second fixture puts the estimate at 2 minutes, so this fails if the
    caption quotes a fixed number or the one-minute floor. FakeUpload.getbuffer
    raises, so it also fails if the probe ever copies the upload to read it.
    """
    import app

    mode = app.MODE_KARAOKE if mode_key == "karaoke" else app.MODE_STEMS
    fake, _calls, _roots = _click_generate(
        monkeypatch, tmp_path, mode, upload=LongUpload()
    )

    assert "0.7 MB · 90s of audio · about 2 min to process" in fake.captions
    assert fake.spinners[0] == f"{SEPARATION_LABELS[mode_key]} (about 2 min)"


def test_an_unreadable_upload_warns_and_still_separates(monkeypatch, tmp_path):
    """The probe never blocks: a file it cannot read warns and the run carries
    on, with the spinner back to the wording it had before the estimate."""
    import app

    fake, calls, _roots = _click_generate(
        monkeypatch, tmp_path, app.MODE_KARAOKE, upload=UnreadableUpload()
    )

    warned = [text for level, text, _state in fake.reports if level == "warning"]
    assert len(warned) == 1
    assert "Could not read this file as audio" in warned[0]
    # No estimate to show, so no file-facts caption either.
    assert not any("min to process" in caption for caption in fake.captions)
    # The separation still ran: the warning is advisory.
    assert len(calls) == 1
    assert fake.spinners[0] == "Separating vocals from instrumental... (1-3 min)"


# --------------------------------------------------------------------------
# Stem mixer pitch shifting
#
# These assert on the *generated HTML string*. They cannot execute browser
# audio, so they guard the wiring from being deleted, nothing more. The
# behavioural check is the manual browser pass in the implementation plan.
# --------------------------------------------------------------------------

MIXER_STEMS = ("vocals", "drums", "bass", "other")


def _rendered_mixer_html(monkeypatch, song="t"):
    """Call _render_mixer with components.html stubbed; return the HTML."""
    from unittest.mock import patch

    import app

    urls = {s: f"/app/static/stems/{song}/{s}.wav" for s in MIXER_STEMS}
    with patch("streamlit.components.v1.html") as mock_html:
        app._render_mixer(urls, song)
    return mock_html.call_args[0][0]


def test_mixer_loads_soundtouchjs_as_a_module(monkeypatch):
    """soundtouchjs ships as an ES module, so a bare <script src> yields nothing.

    If someone "simplifies" this back to a plain script tag the import silently
    fails in the browser and pitch shifting disappears with no Python error.
    """
    html = _rendered_mixer_html(monkeypatch)

    assert 'type="module"' in html
    assert "/app/static/soundtouchjs/soundtouch.min.js" in html
    assert '<script src="/app/static/soundtouchjs' not in html


def test_mixer_renders_pitch_slider(monkeypatch):
    html = _rendered_mixer_html(monkeypatch)

    assert 'id="mixer_t_pitch"' in html
    assert 'id="mixer_t_pitch_val"' in html
    assert 'min="-12"' in html
    assert 'max="12"' in html
    # Ships disabled: it is enabled only once the module import has succeeded.
    pitch_input = html[html.index('id="mixer_t_pitch"') :]
    pitch_input = pitch_input[: pitch_input.index(">")]
    assert "disabled" in pitch_input
