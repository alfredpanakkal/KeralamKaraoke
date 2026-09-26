"""Pure logic: filename sanitisation, path construction, CLI argv building."""

import os
import re
import sys
from pathlib import Path

# Characters that are illegal in Windows filenames. Stripped rather than
# replaced so "My Song" -> "My_Song" stays readable.
_FORBIDDEN_CHARS = re.compile(r'[\\/\*?:"<>|]')

# Windows reserved device names (case-insensitive)
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitise_name(name: str) -> str:
    """Strip filesystem-forbidden chars and spaces, keeping the extension.

    >>> sanitise_name('My Song (Remix)!.mp3')
    'My_Song_(Remix)!.mp3'
    >>> sanitise_name('A/B*C?D"E<F>G|H.wav')
    'ABCDEFGH.wav'
    """
    root, ext = os.path.splitext(name)
    return _FORBIDDEN_CHARS.sub("", root).replace(" ", "_") + ext


def safe_upload_name(name: str) -> str:
    """A filename guaranteed safe as a direct child of its target dir.

    On top of sanitise_name: strips leading dots (traversal), trailing
    dots/spaces (Windows silently strips them, causing collisions),
    prefixes reserved device names (CON, NUL, COM1...) which Windows
    refuses to create, and caps the stem at 100 chars.
    """
    cleaned = sanitise_name(name).lstrip(".")
    root, ext = os.path.splitext(cleaned)
    root = root.rstrip(". ")[:100]
    ext = ext.rstrip(". ")
    if root.upper() in _WINDOWS_RESERVED:
        root = f"_{root}"
    cleaned = root + ext
    return cleaned.lstrip(".") or "upload"


MAX_UPLOAD_BYTES = 150 * 1024 * 1024


def validate_upload_size(size_bytes: int) -> tuple[bool, str]:
    """Guard against disk exhaustion and demucs timeout on huge files."""
    if size_bytes > MAX_UPLOAD_BYTES:
        mb = MAX_UPLOAD_BYTES // (1024 * 1024)
        return False, f"File exceeds the {mb} MB limit."
    return True, ""


# The models the UI offers: the label is what the user reads, the value is what
# Demucs is invoked with. The first entry is the default, so the app starts on
# plain htdemucs.
MODEL_DEFAULT = "htdemucs"
MODEL_OPTIONS = {
    "htdemucs (fast, good enough for karaoke)": "htdemucs",
    "htdemucs_ft (best quality, roughly 4x slower)": "htdemucs_ft",
}


def build_output_paths(
    song_name: str, base_dirs: dict, model: str = "htdemucs"
) -> dict:
    """Return per-song output paths so concurrent songs never collide.

    base_dirs keys: "separated", "output", "cache".
    """
    return {
        "instrumental": str(
            Path(base_dirs["separated"]) / model / f"{song_name}_no_vocals.wav"
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


STEMS = ("vocals", "drums", "bass", "other")


def build_stems_argv(input_path: str, separated_dir: str, model: str) -> list[str]:
    """Build argv for full 4-stem separation (vocals, drums, bass, other).

    Unlike the karaoke argv, there is no --two-stems flag, and the
    filename template is "{track}_{stem}.{ext}" so every stem is already
    named per-song (test_drums.wav, ...) and needs no copy afterwards.
    """
    return [
        sys.executable,
        "-m", "demucs",
        "-n", model,
        "-o", separated_dir,
        "--filename", "{track}_{stem}.{ext}",
        input_path,
    ]


def probe_audio(source) -> dict | None:
    """Return {"duration", "samplerate", "channels", "format"} for an audio
    file, or None when it cannot be read.

    `source` is a path (str/Path) or an open binary file object. Never raises:
    an unreadable file is reported as None so the caller can warn and let
    Demucs produce the authoritative error. May move a file object's read
    position; callers pass objects they do not read positionally.
    """
    try:
        import soundfile as sf
        info = sf.info(source)
    except Exception:
        return None
    return {
        "duration": float(info.duration),
        "samplerate": int(info.samplerate),
        "channels": int(info.channels),
        "format": str(info.format),
    }


def clear_generated_files(dirs) -> tuple[int, list[str]]:
    """Delete the contents of each directory in `dirs`, keeping the
    directories themselves.

    Recurses into subdirectories (e.g. separated/htdemucs/<song>/) and prunes
    the subdirectories it empties. Returns (files_deleted, failures) where
    each failure is a "<path>: <reason>" string. Never raises: a locked or
    undeletable file is reported, not propagated.
    """
    deleted = 0
    failures: list[str] = []
    for directory in dirs:
        root = Path(directory)
        if not root.is_dir():
            continue  # a directory the app has not created yet
        # Bottom-up: a subdirectory is emptied and pruned before its parent is
        # visited, so the only rmdir that can fail is one holding a locked
        # file, and that file is already in `failures`.
        for dirpath, _dirnames, filenames in os.walk(root, topdown=False):
            for name in filenames:
                path = Path(dirpath) / name
                try:
                    path.unlink()
                except OSError as exc:
                    failures.append(f"{path}: {exc.strerror or exc}")
                else:
                    deleted += 1
            if Path(dirpath) != root:
                try:
                    os.rmdir(dirpath)
                except OSError:
                    pass
    return deleted, failures
