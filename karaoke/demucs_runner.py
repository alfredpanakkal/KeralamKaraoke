"""Subprocess wrapper for the Demucs CLI."""

import shutil
import subprocess
import sys
from pathlib import Path

from karaoke.core import build_demucs_argv, build_output_paths

TIMEOUT_SECONDS = 1800


def run_demucs(argv: list[str]) -> tuple[int, str, str]:
    """Run the demucs CLI, capturing output. Returns (returncode, stdout, stderr)."""
    result = subprocess.run(
        argv, capture_output=True, text=True, timeout=TIMEOUT_SECONDS
    )
    return result.returncode, result.stdout, result.stderr


def separate_vocals(input_path: str, separated_dir: str, model: str) -> str:
    """Separate vocals, then copy the result to a per-song path.

    Demucs always writes to <separated_dir>/<model>/no_vocals.wav, a single
    flat filename it reuses for every track. Copying to a per-song name is
    what stops song B from clobbering song A.

    Returns the per-song instrumental path. Raises RuntimeError on failure.
    """
    song_name = Path(input_path).stem
    paths = build_output_paths(
        song_name, {"separated": separated_dir, "output": "", "cache": ""}
    )
    returncode, _stdout, stderr = run_demucs(
        build_demucs_argv(input_path, separated_dir, model)
    )
    if returncode != 0:
        raise RuntimeError(f"Demucs failed (exit code {returncode}):\n{stderr}")

    produced = Path(separated_dir) / model / "no_vocals.wav"
    if not produced.exists():
        raise RuntimeError(
            f"Demucs reported success but {produced} does not exist."
        )

    dest = Path(paths["instrumental"])
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(produced, dest)
    return str(dest)
