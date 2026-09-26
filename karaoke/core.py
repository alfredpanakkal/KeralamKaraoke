"""Pure logic: filename sanitisation, path construction, CLI argv building."""

import os
import re
import sys
from pathlib import Path

# Characters that are illegal in Windows filenames. Stripped rather than
# replaced so "My Song" -> "My_Song" stays readable.
_FORBIDDEN_CHARS = re.compile(r'[\\/\*?:"<>|]')


def sanitise_name(name: str) -> str:
    """Strip filesystem-forbidden chars and spaces, keeping the extension.

    >>> sanitise_name('My Song (Remix)!.mp3')
    'My_Song_(Remix)!.mp3'
    >>> sanitise_name('A/B*C?D"E<F>G|H.wav')
    'ABCDEFGH.wav'
    """
    root, ext = os.path.splitext(name)
    return _FORBIDDEN_CHARS.sub("", root).replace(" ", "_") + ext


def build_output_paths(song_name: str, base_dirs: dict) -> dict:
    """Return per-song output paths so concurrent songs never collide.

    base_dirs keys: "separated", "output", "cache".
    """
    return {
        "instrumental": str(
            Path(base_dirs["separated"]) / "htdemucs" / f"{song_name}_no_vocals.wav"
        ),
        "final": str(Path(base_dirs["output"]) / f"{song_name}_karaoke.wav"),
        "cache_dir": str(Path(base_dirs["cache"]) / song_name),
    }


def build_demucs_argv(input_path: str, separated_dir: str, model: str) -> list[str]:
    """Build argv for the demucs CLI.

    Invoked as ``sys.executable -m demucs`` rather than the bare ``demucs``
    command: the latter only works when the virtualenv is on PATH, while
    this works however app.py was launched.

    --device is deliberately omitted: demucs already auto-detects
    cuda -> mps -> cpu, and hardcoding "cuda" turns a working CPU
    fallback into a hard failure.

    --filename "{stem}.{ext}" is required. The default is
    "{track}/{stem}.{ext}", which would nest output one level deeper
    per song; this flattens it so the output path is predictable.
    """
    return [
        sys.executable,
        "-m", "demucs",
        "-n", model,
        "--two-stems=vocals",
        "-o", separated_dir,
        "--filename", "{stem}.{ext}",
        input_path,
    ]
