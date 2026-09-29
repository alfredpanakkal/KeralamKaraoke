import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import karaoke.demucs_runner


def test_run_demucs_captures_output():
    completed = MagicMock(returncode=0, stdout="ok", stderr="")
    with patch.object(subprocess, "run", return_value=completed) as mock_run:
        code, out, err = karaoke.demucs_runner.run_demucs(["demucs", "dummy"])
    assert (code, out, err) == (0, "ok", "")
    mock_run.assert_called_once()


def test_separate_vocals_raises_with_stderr_on_failure():
    with patch.object(karaoke.demucs_runner, "run_demucs", return_value=(1, "", "boom")):
        with pytest.raises(RuntimeError, match="boom"):
            karaoke.demucs_runner.separate_vocals("song.mp3", "separated", "htdemucs")


def test_separate_vocals_copies_to_per_song_path(tmp_path):
    """The whole point: demucs reuses one flat filename, we must not."""
    produced = tmp_path / "separated" / "htdemucs" / "no_vocals.wav"
    produced.parent.mkdir(parents=True)
    produced.write_bytes(b"instrumental-audio")

    with patch.object(karaoke.demucs_runner, "run_demucs", return_value=(0, "", "")):
        dest = karaoke.demucs_runner.separate_vocals(
            str(tmp_path / "mysong.mp3"), str(tmp_path / "separated"), "htdemucs"
        )

    # Destination is per-song, not the shared flat name.
    assert Path(dest).name == "mysong_no_vocals.wav"
    assert Path(dest) != produced
    assert Path(dest).read_bytes() == b"instrumental-audio"


def test_separate_vocals_second_song_does_not_clobber_first(tmp_path):
    separated = tmp_path / "separated"
    produced = separated / "htdemucs" / "no_vocals.wav"

    with patch.object(karaoke.demucs_runner, "run_demucs", return_value=(0, "", "")):
        produced.parent.mkdir(parents=True)
        produced.write_bytes(b"song-one")
        first = karaoke.demucs_runner.separate_vocals(
            str(tmp_path / "one.mp3"), str(separated), "htdemucs"
        )
        produced.write_bytes(b"song-two")
        second = karaoke.demucs_runner.separate_vocals(
            str(tmp_path / "two.mp3"), str(separated), "htdemucs"
        )

    assert Path(first).read_bytes() == b"song-one"
    assert Path(second).read_bytes() == b"song-two"


def test_separate_vocals_raises_when_output_missing(tmp_path):
    """Exit code 0 but no file means something went wrong -- don't pass it on."""
    with patch.object(karaoke.demucs_runner, "run_demucs", return_value=(0, "", "")):
        with pytest.raises(RuntimeError, match="does not exist"):
            karaoke.demucs_runner.separate_vocals(
                str(tmp_path / "gone.mp3"), str(tmp_path / "separated"), "htdemucs"
            )


def test_separate_stems_returns_all_four_stems(tmp_path):
    out_dir = tmp_path / "separated" / "htdemucs"
    out_dir.mkdir(parents=True)
    for stem in ("vocals", "drums", "bass", "other"):
        (out_dir / f"mysong_{stem}.wav").write_bytes(stem.encode())

    with patch.object(karaoke.demucs_runner, "run_demucs", return_value=(0, "", "")):
        stems = karaoke.demucs_runner.separate_stems(
            str(tmp_path / "mysong.mp3"), str(tmp_path / "separated"), "htdemucs"
        )

    assert set(stems) == {"vocals", "drums", "bass", "other"}
    assert Path(stems["drums"]).read_bytes() == b"drums"


def test_separate_stems_raises_with_stderr_on_failure():
    with patch.object(karaoke.demucs_runner, "run_demucs", return_value=(1, "", "boom")):
        with pytest.raises(RuntimeError, match="boom"):
            karaoke.demucs_runner.separate_stems("song.mp3", "separated", "htdemucs")


def test_separate_stems_raises_when_a_stem_is_missing(tmp_path):
    out_dir = tmp_path / "separated" / "htdemucs"
    out_dir.mkdir(parents=True)
    (out_dir / "song_vocals.wav").write_bytes(b"vocals")

    with patch.object(karaoke.demucs_runner, "run_demucs", return_value=(0, "", "")):
        with pytest.raises(RuntimeError, match="missing"):
            karaoke.demucs_runner.separate_stems(
                str(tmp_path / "song.mp3"), str(tmp_path / "separated"), "htdemucs"
            )


def test_run_demucs_wraps_timeout_in_runtimeerror():
    timeout = subprocess.TimeoutExpired(cmd="demucs", timeout=1800)
    with patch.object(subprocess, "run", side_effect=timeout):
        with pytest.raises(RuntimeError, match="timed out"):
            karaoke.demucs_runner.run_demucs(["demucs"])


def test_separate_vocals_respects_model_name(tmp_path):
    produced = tmp_path / "separated" / "htdemucs_ft" / "no_vocals.wav"
    produced.parent.mkdir(parents=True)
    produced.write_bytes(b"ft-audio")

    with patch.object(karaoke.demucs_runner, "run_demucs", return_value=(0, "", "")):
        dest = karaoke.demucs_runner.separate_vocals(
            str(tmp_path / "song.mp3"), str(tmp_path / "separated"), "htdemucs_ft"
        )
    assert "htdemucs_ft" in dest


def test_separate_stems_validates_the_tracker_result(tmp_path):
    """The tracker path must apply the same missing-stem check as the CLI path.

    A partial write (e.g. disk full on the last stem) leaves all four paths in
    the tracker dict, so the check has to touch the filesystem, not the dict.
    """
    out_dir = tmp_path / "separated" / "htdemucs"
    out_dir.mkdir(parents=True)
    result = {}
    for stem in ("vocals", "drums", "bass"):  # 'other' is claimed but never written
        path = out_dir / f"song_{stem}.wav"
        path.write_bytes(stem.encode())
        result[stem] = str(path)
    result["other"] = str(out_dir / "song_other.wav")

    class FakeTracker:
        def get(self):
            return {"result": result}

    with patch.object(
        karaoke.demucs_runner, "_run_demucs_python_api", return_value=(0, "", "")
    ):
        with pytest.raises(RuntimeError, match="missing"):
            karaoke.demucs_runner.separate_stems(
                str(tmp_path / "song.mp3"),
                str(tmp_path / "separated"),
                "htdemucs",
                progress_tracker=FakeTracker(),
            )


def test_progress_percent_does_not_count_shifts_as_extra_work():
    """shifts is always 1 for htdemucs, so shift_idx must add no work unit.

    With it counted, a single-model four-segment track would report 100%
    before the first segment finished whenever shift_idx came back as 1.
    """
    tracker = karaoke.demucs_runner.ProgressTracker()
    callback = karaoke.demucs_runner._make_progress_callback(tracker, total_segments=4)
    segment_size = 44100 * 7.8

    info = {
        "model_idx_in_bag": 0,
        "shift_idx": 1,  # demucs still reports a shift index even at shifts=1
        "segment_offset": 0,
        "state": "start",
        "audio_length": segment_size * 4,
        "models": 1,
    }
    callback(info)
    assert tracker.get()["percent"] == 0.0

    info["segment_offset"] = segment_size * 2
    callback(info)
    assert tracker.get()["percent"] == pytest.approx(50.0)
