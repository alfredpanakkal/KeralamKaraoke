"""Streamlit UI for the Karaoke Track Generator."""

import shutil
from pathlib import Path

import streamlit as st

from karaoke.core import build_output_paths, sanitise_name
from karaoke.demucs_runner import separate_vocals
from karaoke.pitch_shift import pitch_shift_cached

UPLOAD_DIR = Path("uploads")
OUTPUT_DIR = Path("karaoke_out")
SEPARATED_DIR = Path("separated")
CACHE_DIR = Path("karaoke_cache")
MODEL = "htdemucs"

BASE_DIRS = {
    "separated": str(SEPARATED_DIR),
    "output": str(OUTPUT_DIR),
    "cache": str(CACHE_DIR),
}

# Session keys that describe the *current* song. Cleared wholesale when the
# user picks a different file, otherwise song A's output keeps rendering
# under song B's name.
SONG_KEYS = ("current_file", "song_name", "instrumental_path", "paths")


def reset_song_state(uploaded_name: str) -> None:
    for key in SONG_KEYS:
        st.session_state.pop(key, None)
    st.session_state.current_file = uploaded_name


def main() -> None:
    st.set_page_config(page_title="Karaoke Maker", page_icon="🎤")
    st.title("🎤 Karaoke Track Generator")
    st.write("Upload a song, get an instrumental-only wav back.")

    for folder in (UPLOAD_DIR, OUTPUT_DIR, SEPARATED_DIR, CACHE_DIR):
        folder.mkdir(parents=True, exist_ok=True)

    uploaded = st.file_uploader("Choose a song file", type=["mp3", "wav"])
    semitones = st.slider(
        "Pitch shift (semitones)",
        min_value=-6,
        max_value=6,
        value=0,
        help="0 = no change. Positive = higher pitch, negative = lower.",
    )

    if uploaded is None:
        st.info("Upload a song to begin.")
        return

    if st.session_state.get("current_file") != uploaded.name:
        reset_song_state(uploaded.name)

    if st.button("Generate Karaoke Track", type="primary"):
        input_path = UPLOAD_DIR / uploaded.name
        input_path.write_bytes(uploaded.getbuffer())

        song_name = Path(sanitise_name(uploaded.name)).stem
        st.session_state.song_name = song_name
        st.session_state.paths = build_output_paths(song_name, BASE_DIRS)

        with st.spinner("Separating vocals from instrumental... (1-3 min)"):
            try:
                st.session_state.instrumental_path = separate_vocals(
                    str(input_path), str(SEPARATED_DIR), MODEL
                )
            except RuntimeError as exc:
                st.error(f"Separation failed: {exc}")
                st.stop()
            except Exception as exc:  # noqa: BLE001 - surface anything unexpected
                st.error(f"Unexpected error: {exc}")
                st.stop()

    if "instrumental_path" not in st.session_state:
        st.info("Click **Generate Karaoke Track** to separate this song.")
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
        data=final.read_bytes(),
        file_name=f"{song_name}_karaoke.wav",
        mime="audio/wav",
    )


main()
