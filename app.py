"""Streamlit UI for the Karaoke Track Generator."""

import shutil
from pathlib import Path

import streamlit as st

from karaoke.core import (
    MODEL_OPTIONS,
    STEMS,
    build_output_paths,
    clear_generated_files,
    safe_upload_name,
    validate_upload_size,
)
from karaoke.demucs_runner import separate_stems, separate_vocals
from karaoke.pitch_shift import pitch_shift_cached

ROOT = Path(__file__).resolve().parent
UPLOAD_DIR = ROOT / "uploads"
OUTPUT_DIR = ROOT / "karaoke_out"
SEPARATED_DIR = ROOT / "separated"
CACHE_DIR = ROOT / "karaoke_cache"

MODE_KARAOKE = "Karaoke (remove vocals)"
MODE_STEMS = "Stems (vocals, drums, bass, other)"

BASE_DIRS = {
    "separated": str(SEPARATED_DIR),
    "output": str(OUTPUT_DIR),
    "cache": str(CACHE_DIR),
}

# Session keys that describe the *current* song. Cleared wholesale when the
# user picks a different file, mode, or model, otherwise song A's output keeps
# rendering under song B's name.
SONG_KEYS = (
    "current_file",
    "model",
    "song_name",
    "instrumental_path",
    "stems_paths",
    "paths",
)


def purge_song_state() -> None:
    """Forget the current song and its cached download bytes.

    The files those keys point at may be deleted in the same breath, so
    nothing that reads them can be left behind in the session.
    """
    for key in SONG_KEYS:
        st.session_state.pop(key, None)
    for key in [k for k in st.session_state if k.startswith("_dl_bytes:")]:
        st.session_state.pop(key, None)


def reset_song_state(uploaded_name: str, mode: str, model: str | None = None) -> None:
    """Start a new song, and a new model if one was given."""
    purge_song_state()
    st.session_state.current_file = uploaded_name
    st.session_state.mode = mode
    if model is not None:
        st.session_state.model = model


def _cached_bytes(path: Path) -> bytes:
    """Read a file once per (path, mtime); Streamlit reruns often."""
    key = f"_dl_bytes:{path}"
    mtime = path.stat().st_mtime
    cached = st.session_state.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    data = path.read_bytes()
    st.session_state[key] = (mtime, data)
    return data


def main() -> None:
    st.set_page_config(page_title="Karaoke Maker", page_icon="🎤")
    st.title("🎤 Karaoke Track Generator")
    st.write("Upload a song, get an instrumental-only wav or individual stems back.")

    for folder in (UPLOAD_DIR, OUTPUT_DIR, SEPARATED_DIR, CACHE_DIR):
        folder.mkdir(parents=True, exist_ok=True)

    # Created above the uploader so a click is handled before the results
    # below re-read a path this run has just deleted.
    st.sidebar.caption(
        "Deletes everything in uploads, karaoke_out, separated, and "
        "karaoke_cache, including the copy the app made of your upload. "
        "Your original file is not touched."
    )
    if st.sidebar.button("Clear generated files"):
        deleted, failures = clear_generated_files(
            [UPLOAD_DIR, OUTPUT_DIR, SEPARATED_DIR, CACHE_DIR]
        )
        # Purge before reporting: nothing below may read a deleted path.
        purge_song_state()
        if failures:
            more = f", and {len(failures) - 3} more" if len(failures) > 3 else ""
            st.sidebar.warning(
                f"Deleted {deleted} file(s), but {len(failures)} could not be "
                f"deleted: {'; '.join(failures[:3])}{more}"
            )
        else:
            st.sidebar.success(f"Deleted {deleted} file(s)")

    uploaded = st.file_uploader("Choose a song file", type=["mp3", "wav"])
    mode = st.radio(
        "Separation mode",
        options=[MODE_KARAOKE, MODE_STEMS],
        horizontal=True,
    )
    model_label = st.selectbox(
        "Demucs model",
        list(MODEL_OPTIONS),
        help=(
            "The first run with any model downloads its weights. htdemucs_ft "
            "runs four models instead of one, so it takes about four times as "
            "long."
        ),
    )
    model = MODEL_OPTIONS[model_label]
    semitones = st.slider(
        "Pitch shift (semitones)",
        min_value=-6,
        max_value=6,
        value=0,
        help="0 = no change. Karaoke mode only. Positive = higher, negative = lower.",
    )

    if uploaded is None:
        st.info("Upload a song to begin.")
        return

    ok, msg = validate_upload_size(uploaded.size)
    if not ok:
        st.error(msg)
        return

    if (
        st.session_state.get("current_file") != uploaded.name
        or st.session_state.get("mode") != mode
        or st.session_state.get("model") != model
    ):
        reset_song_state(uploaded.name, mode, model)

    if st.button("Generate", type="primary"):
        try:
            safe_name = safe_upload_name(uploaded.name)
            input_path = UPLOAD_DIR / safe_name
            input_path.write_bytes(uploaded.getbuffer())

            song_name = Path(safe_name).stem
            st.session_state.song_name = song_name
            st.session_state.paths = build_output_paths(
                song_name, BASE_DIRS, model=model
            )

            if mode == MODE_KARAOKE:
                with st.spinner("Separating vocals from instrumental... (1-3 min)"):
                    st.session_state.instrumental_path = separate_vocals(
                        str(input_path), str(SEPARATED_DIR), model
                    )
            else:
                with st.spinner("Separating stems... (1-3 min)"):
                    st.session_state.stems_paths = separate_stems(
                        str(input_path), str(SEPARATED_DIR), model
                    )
        except RuntimeError as exc:
            st.error(f"Separation failed: {exc}")
            st.stop()
        except Exception as exc:  # noqa: BLE001 - surface anything unexpected
            st.error(f"Unexpected error: {exc}")
            st.stop()

    if st.session_state.get("mode") == MODE_STEMS:
        _render_stems()
        return

    if "instrumental_path" not in st.session_state:
        st.info("Click **Generate** to separate this song.")
        return

    instrumental_path = st.session_state.instrumental_path
    paths = st.session_state.paths
    song_name = st.session_state.song_name

    # Cached: only recomputed when this (song, semitones) pair is new.
    with st.spinner("Applying pitch shift..." if semitones else "Preparing track..."):
        shifted = pitch_shift_cached(instrumental_path, semitones, paths["cache_dir"])
        final = Path(paths["final"])
        final.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(shifted, final)

    st.success("Done! Karaoke track ready below.")
    st.audio(str(final))

    st.download_button(
        label="Download Karaoke Track",
        data=_cached_bytes(final),
        file_name=f"{song_name}_karaoke.wav",
        mime="audio/wav",
    )


def _render_stems() -> None:
    stems_paths = st.session_state.get("stems_paths")
    if not stems_paths:
        st.info("Click **Generate** to separate this song into stems.")
        return

    song_name = st.session_state.song_name
    st.success("Done! Stems ready below.")
    for stem in STEMS:
        path = Path(stems_paths[stem])
        st.subheader(stem.capitalize())
        st.audio(str(path))
        st.download_button(
            label=f"Download {stem}",
            data=_cached_bytes(path),
            file_name=f"{song_name}_{stem}.wav",
            mime="audio/wav",
            key=f"dl_{stem}",
        )


main()
