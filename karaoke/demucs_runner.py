"""Subprocess wrapper for the Demucs CLI."""

import math
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import torch
from demucs.api import Separator
from karaoke.core import STEMS, build_output_paths, build_stems_argv

TIMEOUT_SECONDS = 1800


class ProgressTracker:
    """Thread-safe progress tracking for Demucs separation."""

    def __init__(self):
        self._lock = threading.Lock()
        self._data = {
            "percent": 0.0,
            "status": "initializing",
            "eta_seconds": None,
            "current_model": 0,
            "total_models": 1,
            "current_segment": 0,
            "total_segments": 1,
            "current_shift": 0,
            "total_shifts": 1,
            "done": False,
            "error": None,
            "result": None,
        }
        self._start_time = None
        self._last_update = None

    def update(self, **kwargs):
        with self._lock:
            self._data.update(kwargs)
            self._last_update = time.time()

    def get(self):
        with self._lock:
            return dict(self._data)

    def set_done(self, result=None, error=None):
        with self._lock:
            self._data["done"] = True
            self._data["percent"] = 100.0
            self._data["status"] = "done" if error is None else "error"
            self._data["error"] = error
            self._data["result"] = result
            self._last_update = time.time()

    def start(self, total_models=1, total_shifts=1):
        with self._lock:
            self._start_time = time.time()
            self._last_update = self._start_time
            self._data["status"] = "separating"
            self._data["total_models"] = total_models
            self._data["total_shifts"] = total_shifts

    def get_eta(self):
        with self._lock:
            if self._start_time is None or self._data["percent"] <= 0:
                return None
            elapsed = time.time() - self._start_time
            if self._data["percent"] >= 100:
                return 0
            total_estimated = elapsed / (self._data["percent"] / 100)
            return max(0, total_estimated - elapsed)


def _make_progress_callback(tracker: ProgressTracker, total_segments: int):
    """Create a callback function for Demucs Separator."""

    def callback(info: dict):
        try:
            model_idx = info.get("model_idx_in_bag", 0)
            shift_idx = info.get("shift_idx", 0)
            segment_offset = info.get("segment_offset", 0)
            state = info.get("state", "start")
            audio_length = info.get("audio_length", 1)
            models = info.get("models", 1)

            # Calculate total segments from audio_length and segment size
            # Default segment is ~7.8s at 44100Hz = ~344064 frames
            segment_size = 44100 * 7.8  # Default segment length in frames
            total_segments_calc = max(1, math.ceil(audio_length / segment_size))

            if state == "start":
                # Update segment progress within current model+shift
                tracker.update(
                    current_model=model_idx + 1,
                    total_models=models,
                    current_shift=shift_idx + 1,
                    total_shifts=1,  # shifts is always 1 for htdemucs
                    current_segment=min(
                        math.ceil(segment_offset / segment_size), total_segments_calc
                    ),
                    total_segments=total_segments_calc,
                    status="separating",
                )
            elif state == "end":
                # Segment completed
                tracker.update(
                    current_model=model_idx + 1,
                    total_models=models,
                    current_shift=shift_idx + 1,
                    total_shifts=1,
                    current_segment=min(
                        math.ceil(segment_offset / segment_size), total_segments_calc
                    ),
                    total_segments=total_segments_calc,
                    status="separating",
                )

            # Calculate overall percentage
            # Work units = models * shifts * segments_per_model
            # Current unit = model_idx * shifts * segments + shift_idx * segments + segment_idx
            segments_per_model = total_segments_calc
            current_unit = (
                model_idx * 1 * segments_per_model
                + shift_idx * 1 * segments_per_model
                + min(math.ceil(segment_offset / segment_size), segments_per_model)
            )
            total_units = models * 1 * segments_per_model
            percent = min(100.0, (current_unit / total_units) * 100)

            eta = tracker.get_eta()
            tracker.update(percent=percent, eta_seconds=eta)

        except Exception:
            # Don't let callback errors break separation
            pass

    return callback


def _run_demucs_python_api(
    input_path: str,
    separated_dir: str,
    model: str,
    two_stems: bool,
    progress_tracker: ProgressTracker | None = None,
) -> tuple[int, str, str]:
    """Run Demucs using Python API with progress tracking.

    Returns (returncode, stdout, stderr) for compatibility.
    """
    try:
        # Initialize separator
        separator = Separator(model=model, progress=False)

        # Get model info
        total_models = len(separator.model.models) if hasattr(separator.model, "models") else 1
        total_shifts = 1  # htdemucs uses shifts=1 by default

        if progress_tracker:
            progress_tracker.start(total_models=total_models, total_shifts=total_shifts)

        # Set up callback
        callback = None
        if progress_tracker:
            # Estimate segments (will be refined in callback)
            callback = _make_progress_callback(progress_tracker, total_segments=100)
            separator.update_parameter(callback=callback)

        # Run separation
        origin, stems = separator.separate_audio_file(Path(input_path))

        if two_stems:
            # Karaoke mode: combine non-vocal stems into instrumental
            # Demucs separates into: vocals, drums, bass, other
            # Instrumental = drums + bass + other
            instrumental = stems["drums"] + stems["bass"] + stems["other"]

            # Save as no_vocals.wav equivalent
            song_name = Path(input_path).stem
            paths = build_output_paths(
                song_name,
                {"separated": separated_dir, "output": "", "cache": ""},
                model=model,
            )
            output_path = Path(paths["instrumental"])
            output_path.parent.mkdir(parents=True, exist_ok=True)

            # Save using torchaudio
            import torchaudio

            torchaudio.save(
                str(output_path), instrumental.cpu(), separator.samplerate, bits_per_sample=16
            )

            if progress_tracker:
                progress_tracker.set_done(result=str(output_path))

            return 0, f"Saved to {output_path}", ""

        else:
            # Stems mode: save all stems with track_stem.wav naming
            song_name = Path(input_path).stem
            out_dir = Path(separated_dir) / model
            out_dir.mkdir(parents=True, exist_ok=True)

            import torchaudio

            stem_paths = {}
            for stem_name in STEMS:
                out_path = out_dir / f"{song_name}_{stem_name}.wav"
                torchaudio.save(
                    str(out_path), stems[stem_name].cpu(), separator.samplerate, bits_per_sample=16
                )
                stem_paths[stem_name] = str(out_path)

            if progress_tracker:
                progress_tracker.set_done(result=stem_paths)

            return 0, "Stems saved", ""

    except Exception as e:
        error_msg = str(e)
        if progress_tracker:
            progress_tracker.set_done(error=error_msg)
        return 1, "", error_msg


def run_demucs(argv: list[str]) -> tuple[int, str, str]:
    """Run the demucs CLI, capturing output. Returns (returncode, stdout, stderr).

    Raises RuntimeError on timeout so the UI shows a clear message
    instead of a bare subprocess exception.

    This is the legacy CLI-based function kept for backward compatibility with tests.
    """
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, timeout=TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"Demucs timed out after {TIMEOUT_SECONDS} seconds. "
            "Try a shorter song or check that the GPU is being used."
        ) from exc
    return result.returncode, result.stdout, result.stderr


def _run_demucs_legacy(
    input_path: str,
    separated_dir: str,
    model: str,
    two_stems: bool,
) -> tuple[int, str, str]:
    """Legacy CLI-based separation for backward compatibility with tests."""
    from karaoke.core import build_demucs_argv, build_stems_argv
    
    if two_stems:
        argv = build_demucs_argv(input_path, separated_dir, model)
    else:
        argv = build_stems_argv(input_path, separated_dir, model)
    
    return run_demucs(argv)


def separate_vocals(
    input_path: str,
    separated_dir: str,
    model: str,
    progress_tracker: ProgressTracker | None = None,
) -> str:
    """Separate vocals, then copy the result to a per-song path.

    Demucs always writes to <separated_dir>/<model>/no_vocals.wav, a single
    flat filename it reuses for every track. Copying to a per-song name is
    what stops song B from clobbering song A.

    Returns the per-song instrumental path. Raises RuntimeError on failure.
    """
    song_name = Path(input_path).stem
    paths = build_output_paths(
        song_name, {"separated": separated_dir, "output": "", "cache": ""}, model=model
    )

    if progress_tracker is not None:
        # Use Python API with progress tracking
        returncode, _stdout, stderr = _run_demucs_python_api(
            input_path, separated_dir, model, two_stems=True, progress_tracker=progress_tracker
        )
        if returncode != 0:
            raise RuntimeError(f"Demucs failed (exit code {returncode}):\n{stderr}")
        dest = Path(paths["instrumental"])
        if not dest.exists():
            raise RuntimeError(
                f"Demucs reported success but {dest} does not exist."
            )
        return str(dest)

    # Legacy CLI path (for tests)
    returncode, _stdout, stderr = _run_demucs_legacy(
        input_path, separated_dir, model, two_stems=True
    )
    if returncode != 0:
        raise RuntimeError(f"Demucs failed (exit code {returncode}):\n{stderr}")

    # Demucs writes to flat filename; copy to per-song path
    produced = Path(separated_dir) / model / "no_vocals.wav"
    if not produced.exists():
        raise RuntimeError(
            f"Demucs reported success but {produced} does not exist."
        )
    dest = Path(paths["instrumental"])
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(produced, dest)

    return str(dest)


def separate_stems(
    input_path: str,
    separated_dir: str,
    model: str,
    progress_tracker: ProgressTracker | None = None,
) -> dict[str, str]:
    """Separate a track into vocals/drums/bass/other stems.

    Uses --filename "{track}_{stem}.{ext}", so demucs names every stem
    per-song; no copy step is needed. Returns {stem_name: file_path}.
    Raises RuntimeError on failure or if any expected stem is missing.
    """
    if progress_tracker is not None:
        # Use Python API with progress tracking
        returncode, _stdout, stderr = _run_demucs_python_api(
            input_path, separated_dir, model, two_stems=False, progress_tracker=progress_tracker
        )
        if returncode != 0:
            raise RuntimeError(f"Demucs failed (exit code {returncode}):\n{stderr}")
        # Result is already stored in progress_tracker by _run_demucs_python_api
        result = progress_tracker.get().get("result")
        if result:
            return result
        # Fallback: check filesystem
        track = Path(input_path).stem
        out_dir = Path(separated_dir) / model
        stems: dict[str, str] = {}
        missing: list[str] = []
        for stem in STEMS:
            produced = out_dir / f"{track}_{stem}.wav"
            if produced.exists():
                stems[stem] = str(produced)
            else:
                missing.append(stem)
        if missing:
            raise RuntimeError(
                f"Demucs reported success but stems are missing: {', '.join(missing)}"
            )
        return stems

    # Legacy CLI path (for tests)
    returncode, _stdout, stderr = _run_demucs_legacy(
        input_path, separated_dir, model, two_stems=False
    )
    if returncode != 0:
        raise RuntimeError(f"Demucs failed (exit code {returncode}):\n{stderr}")

    track = Path(input_path).stem
    out_dir = Path(separated_dir) / model
    stems: dict[str, str] = {}
    missing: list[str] = []
    for stem in STEMS:
        produced = out_dir / f"{track}_{stem}.wav"
        if produced.exists():
            stems[stem] = str(produced)
        else:
            missing.append(stem)
    if missing:
        raise RuntimeError(
            f"Demucs reported success but stems are missing: {', '.join(missing)}"
        )
    return stems