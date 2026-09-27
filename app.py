"""Streamlit UI for the Karaoke Track Generator."""

import base64
import shutil
import threading
import time
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from karaoke.core import (
    MODEL_OPTIONS,
    STEMS,
    build_output_paths,
    clear_generated_files,
    enforce_cache_limit,
    estimate_minutes,
    probe_audio,
    safe_upload_name,
    validate_upload_size,
)
from karaoke.demucs_runner import ProgressTracker, separate_stems as _separate_stems, separate_vocals as _separate_vocals
from karaoke.pitch_shift import pitch_shift_cached

# For test compatibility: tests monkeypatch these module-level functions
separate_vocals = _separate_vocals
separate_stems = _separate_stems

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
    "staged_stem_urls",
)

# Progress tracking keys
PROGRESS_KEYS = (
    "progress_tracker",
    "progress_thread",
    "progress_result",
    "progress_error",
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


def purge_progress_state() -> None:
    """Clear progress tracking state."""
    for key in PROGRESS_KEYS:
        st.session_state.pop(key, None)
    # Also clear any progress-related keys
    for key in list(st.session_state.keys()):
        if key.startswith("progress_"):
            st.session_state.pop(key, None)


def reset_song_state(uploaded_name: str, mode: str, model: str | None = None) -> None:
    """Start a new song, and a new model if one was given."""
    purge_song_state()
    purge_progress_state()
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


def _stage_stems_static(stems_paths: dict[str, str], song_name: str) -> dict[str, str]:
    """
    Copy stem WAVs to static/stems/<song>/ for HTTP serving (via enableStaticServing).
    Returns {stem: url_path}. Clears previous song's folder to avoid stale files.
    """
    static_root = ROOT / "static" / "stems"
    # Clear entire static/stems to avoid accumulating old songs
    if static_root.exists():
        shutil.rmtree(static_root, ignore_errors=True)
    static_root.mkdir(parents=True, exist_ok=True)

    song_dir = static_root / song_name
    song_dir.mkdir(parents=True, exist_ok=True)

    urls = {}
    for stem, src_path in stems_paths.items():
        dest = song_dir / f"{stem}.wav"
        shutil.copy2(src_path, dest)
        urls[stem] = f"/app/static/stems/{song_name}/{stem}.wav"
    return urls


def _run_separation_sync(
    input_path: str,
    separated_dir: str,
    model: str,
    two_stems: bool,
) -> tuple[str | dict[str, str] | None, str | None]:
    """Run separation synchronously (for test compatibility).
    
    Uses the module-level functions that tests can monkeypatch.
    """
    try:
        if two_stems:
            result = separate_vocals(input_path, separated_dir, model)
        else:
            result = separate_stems(input_path, separated_dir, model)
        return result, None
    except Exception as e:
        return None, str(e)


def _run_separation_async(
    input_path: str,
    separated_dir: str,
    model: str,
    two_stems: bool,
    tracker: ProgressTracker,
):
    """Run separation in background thread, storing result in tracker."""
    try:
        if two_stems:
            result = _separate_vocals(input_path, separated_dir, model, progress_tracker=tracker)
        else:
            result = _separate_stems(input_path, separated_dir, model, progress_tracker=tracker)
        tracker.set_done(result=result)
    except Exception as e:
        tracker.set_done(error=str(e))


def _render_progress_ui(tracker: ProgressTracker, mode: str) -> tuple[bool, str | None, any]:
    """
    Render progress UI inside a status container and check if separation is complete.
    Returns (is_done, error, result)
    """
    data = tracker.get()

    status_parts = []
    if data["total_models"] > 1:
        status_parts.append(f"Model {data['current_model']}/{data['total_models']}")
    if data["total_segments"] > 1:
        status_parts.append(f"Segment {data['current_segment']}/{data['total_segments']}")
    status_parts.append(data["status"].capitalize())

    status_text = " • ".join(status_parts)

    eta_text = ""
    if data["eta_seconds"] is not None and data["eta_seconds"] > 1:
        eta_min = int(data["eta_seconds"] // 60)
        eta_sec = int(data["eta_seconds"] % 60)
        if eta_min > 0:
            eta_text = f"  ETA ~{eta_min}m {eta_sec}s"
        else:
            eta_text = f"  ETA ~{eta_sec}s"

    # Use st.status for a collapsible progress block
    # Note: we create the status container on each rerun, but that's fine —
    # Streamlit manages its state via the widget key internally.
    with st.status(f"Separating…{eta_text}", expanded=True) as status:
        st.progress(data["percent"] / 100.0)
        st.caption(status_text)

        if data["done"]:
            status.update(label="Separation complete", state="complete")
            if data["error"]:
                st.error(f"Separation failed: {data['error']}")
                return True, data["error"], None
            return True, None, data["result"]

    return False, None, None


def _safe_rerun():
    """Call st.rerun() if available (not in test environment)."""
    if hasattr(st, "rerun"):
        st.rerun()


def main() -> None:
    st.set_page_config(page_title="HELM - Karaoke", page_icon=":material/graphic_eq:")
    st.title("HELM — Karaoke", icon=":material/graphic_eq:")
    st.caption("Upload a song. Get the karaoke track, or split it into stems.")

    for folder in (UPLOAD_DIR, OUTPUT_DIR, SEPARATED_DIR, CACHE_DIR):
        folder.mkdir(parents=True, exist_ok=True)

    STATIC_STEMS_DIR = ROOT / "static" / "stems"
    STATIC_STEMS_DIR.mkdir(parents=True, exist_ok=True)

    # Sidebar: storage actions (destructive, so tucked away)
    with st.sidebar:
        st.header("Storage", icon=":material/storage:")
        with st.expander("Advanced", icon=":material/tune:"):
            st.caption(
                "Deletes everything in uploads, karaoke_out, separated, karaoke_cache, "
                "and static/stems, including the copy the app made of your upload. "
                "Your original file is not touched."
            )
            if st.button("Clear generated files", icon=":material/delete_forever:"):
                deleted, failures = clear_generated_files(
                    [UPLOAD_DIR, OUTPUT_DIR, SEPARATED_DIR, CACHE_DIR, STATIC_STEMS_DIR]
                )
                purge_song_state()
                purge_progress_state()
                if failures:
                    more = f", and {len(failures) - 3} more" if len(failures) > 3 else ""
                    st.warning(
                        f"Deleted {deleted} file(s), but {len(failures)} could not be "
                        f"deleted: {'; '.join(failures[:3])}{more}"
                    )
                else:
                    st.success(f"Deleted {deleted} file(s)")

        st.caption("Powered by Demucs · Meta AI")

    # Main input flow
    uploaded = st.file_uploader("Drop an mp3 or wav", type=["mp3", "wav"])

    with st.container(border=True):
        mode = st.segmented_control(
            "Mode",
            options=[MODE_KARAOKE, MODE_STEMS],
            default=MODE_KARAOKE,
        )

        quality_label = st.selectbox(
            "Quality",
            ["Fast (htdemucs)", "Best (htdemucs_ft — 4× slower)"],
            index=0,
            help=(
                "Fast uses htdemucs (single model). Best uses htdemucs_ft "
                "(four models) for higher quality but takes roughly four times "
                "as long."
            ),
        )
        model = "htdemucs_ft" if "Best" in quality_label else "htdemucs"

        if mode == MODE_KARAOKE:
            semitones = st.slider(
                "Pitch",
                min_value=-6,
                max_value=6,
                value=0,
                help="Semitones, -6 to +6. Karaoke mode only.",
            )
            st.caption("semitones, -6 to +6")
        else:
            semitones = 0

    if uploaded is None:
        return

    ok, msg = validate_upload_size(uploaded.size)
    if not ok:
        st.error(msg)
        return

    facts = probe_audio(uploaded)
    est_minutes = estimate_minutes(facts["duration"]) if facts else None
    if facts is None:
        st.warning(
            "Could not read this file as audio. If separation fails, try a "
            "different mp3 or wav."
        )
    else:
        st.caption(
            f"{uploaded.size / (1024 * 1024):.1f} MB · "
            f"{facts['duration']:.0f}s of audio · about {est_minutes} min to process"
        )

    if (
        st.session_state.get("current_file") != uploaded.name
        or st.session_state.get("mode") != mode
        or st.session_state.get("model") != model
    ):
        reset_song_state(uploaded.name, mode, model)

    if "progress_tracker" not in st.session_state:
        st.session_state.progress_tracker = ProgressTracker()

    _thread = st.session_state.get("progress_thread")
    _thread_alive = _thread is not None and _thread.is_alive()
    _tracker_done_unconsumed = (
        st.session_state.progress_tracker.get().get("done")
        and "progress_result" not in st.session_state
        and "progress_error" not in st.session_state
    )
    if _thread_alive or _tracker_done_unconsumed:
        done, error, result = _render_progress_ui(st.session_state.progress_tracker, mode)
        if done:
            if error:
                st.session_state.progress_error = error
            else:
                st.session_state.progress_result = result
                if mode == MODE_KARAOKE:
                    st.session_state.instrumental_path = result
                else:
                    st.session_state.stems_paths = result
            st.session_state.pop("progress_thread", None)
            _safe_rerun()
        else:
            time.sleep(0.5)
            _safe_rerun()
        return

    # A finished separator leaves its outcome in session state; errors must be
    # surfaced here, otherwise a failed run silently resets to the idle UI and
    # looks like the app did nothing.
    if progress_error := st.session_state.pop("progress_error", None):
        st.error(f"Separation failed: {progress_error}")

    cta_label = "Create karaoke track" if mode == MODE_KARAOKE else "Split into stems"
    if st.button(cta_label, icon=":material/music_note:", type="primary", width="stretch"):
        try:
            safe_name = safe_upload_name(uploaded.name)
            input_path = UPLOAD_DIR / safe_name
            input_path.write_bytes(uploaded.getbuffer())

            song_name = Path(safe_name).stem
            st.session_state.song_name = song_name
            st.session_state.paths = build_output_paths(
                song_name, BASE_DIRS, model=model
            )

            in_test_env = not hasattr(st, "rerun")

            if in_test_env:
                two_stems = (mode == MODE_KARAOKE)
                spinner_msg = (
                    f"Separating vocals from instrumental... (about {est_minutes} min)"
                    if two_stems and est_minutes
                    else f"Separating stems... (about {est_minutes} min)"
                    if not two_stems and est_minutes
                    else "Separating vocals from instrumental... (1-3 min)"
                    if two_stems
                    else "Separating stems... (1-3 min)"
                )
                with st.spinner(spinner_msg):
                    result, error = _run_separation_sync(
                        str(input_path), str(SEPARATED_DIR), model, two_stems
                    )
                if error:
                    st.error(f"Separation failed: {error}")
                    st.stop()
                else:
                    if mode == MODE_KARAOKE:
                        st.session_state.instrumental_path = result
                    else:
                        st.session_state.stems_paths = result
            else:
                st.session_state.progress_tracker = ProgressTracker()
                purge_progress_state()
                st.session_state.progress_tracker = ProgressTracker()

                two_stems = (mode == MODE_KARAOKE)
                tracker = st.session_state.progress_tracker
                thread = threading.Thread(
                    target=_run_separation_async,
                    args=(str(input_path), str(SEPARATED_DIR), model, two_stems, tracker),
                    daemon=True,
                )
                st.session_state.progress_thread = thread
                thread.start()

                _safe_rerun()

        except RuntimeError as exc:
            st.error(f"Separation failed: {exc}")
            st.stop()
        except Exception as exc:  # noqa: BLE001
            st.error(f"Something went wrong: {exc}")
            st.stop()

    if mode == MODE_STEMS:
        _render_stems()
        return

    if "instrumental_path" not in st.session_state:
        st.info("Click **Create karaoke track** to separate this song.")
        return

    instrumental_path = st.session_state.instrumental_path
    paths = st.session_state.paths
    song_name = st.session_state.song_name

    with st.spinner("Applying pitch shift..." if semitones else "Preparing track..."):
        shifted = pitch_shift_cached(instrumental_path, semitones, paths["cache_dir"])
        final = Path(paths["final"])
        final.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(shifted, final)
        freed = enforce_cache_limit(str(CACHE_DIR))

    if freed > 0:
        st.caption(f"Freed {freed / (1024 * 1024):.1f} MB from the pitch-shift cache.")

    st.toast("Track ready", icon=":material/check:")

    with st.container(border=True):
        st.subheader("Your track")
        st.audio(str(final))
        st.download_button(
            label="Download karaoke track",
            icon=":material/download:",
            data=_cached_bytes(final),
            file_name=f"{song_name}_karaoke.wav",
            mime="audio/wav",
        )


def _render_mixer(stem_urls: dict[str, str], song_name: str) -> None:
    """Render a live Web Audio API mixer for the 4 stems using HTTP-served URLs."""
    # Unique component key to avoid collisions on reruns
    key = f"mixer_{song_name}"

    html = f"""
    <div id="{key}" style="font-family: system-ui, sans-serif;">
      <style>
        .mixer-container {{ display: flex; flex-direction: column; gap: 12px; max-width: 600px; }}
        .mixer-header {{ display: flex; justify-content: space-between; align-items: center; }}
        .mixer-title {{ font-weight: 600; font-size: 1.1rem; }}
        .stem-row {{ display: flex; align-items: center; gap: 12px; }}
        .stem-label {{ width: 80px; font-weight: 500; text-transform: capitalize; }}
        .stem-slider {{ flex: 1; }}
        .stem-value {{ width: 45px; text-align: right; font-variant-numeric: tabular-nums; }}
        .transport {{ display: flex; gap: 8px; align-items: center; padding-top: 8px; border-top: 1px solid #e0e0e0; }}
        .transport-btn {{ padding: 6px 16px; border: none; border-radius: 4px; background: #0066cc; color: white; cursor: pointer; font-size: 0.9rem; }}
        .transport-btn:disabled {{ background: #ccc; cursor: not-allowed; }}
        .transport-btn.secondary {{ background: #666; }}
        .download-btn {{ padding: 6px 16px; border: none; border-radius: 4px; background: #28a745; color: white; cursor: pointer; font-size: 0.9rem; }}
        .download-btn:disabled {{ background: #ccc; cursor: not-allowed; }}
        .status {{ font-size: 0.85rem; color: #666; min-height: 1.2em; }}
        .master-row {{ display: flex; align-items: center; gap: 12px; padding-top: 8px; border-top: 1px solid #e0e0e0; }}
        .master-label {{ width: 80px; font-weight: 500; }}
      </style>

      <div class="mixer-container">
        <div class="mixer-header">
          <span class="mixer-title">Live Stem Mixer</span>
          <span class="status" id="{key}_status">Loading audio…</span>
        </div>

        <div class="master-row">
          <span class="master-label">Master</span>
          <input type="range" class="stem-slider" id="{key}_master" min="0" max="150" value="100" step="1">
          <span class="stem-value" id="{key}_master_val">100%</span>
        </div>

        <div id="{key}_stems"></div>

        <div class="transport">
          <button class="transport-btn" id="{key}_play" disabled>▶ Play</button>
          <button class="transport-btn" id="{key}_pause" disabled>⏸ Pause</button>
          <button class="transport-btn secondary" id="{key}_stop" disabled>⏹ Stop</button>
          <button class="download-btn" id="{key}_download" disabled>Download Mix</button>
        </div>
      </div>

      <script>
        (function() {{
          const KEY = "{key}";
          const STEMS = {list(STEMS)};
          const STEM_DATA = {stem_urls};

          // Audio context and nodes
          let audioCtx = null;
          let buffers = {{}};
          let sources = {{}};
          let gainNodes = {{}};
          let masterGain = null;
          let startTime = 0;
          let pauseTime = 0;
          let isPlaying = false;
          let loadedCount = 0;

          const statusEl = document.getElementById(KEY + "_status");
          const playBtn = document.getElementById(KEY + "_play");
          const pauseBtn = document.getElementById(KEY + "_pause");
          const stopBtn = document.getElementById(KEY + "_stop");
          const downloadBtn = document.getElementById(KEY + "_download");
          const masterSlider = document.getElementById(KEY + "_master");
          const masterVal = document.getElementById(KEY + "_master_val");
          const stemsContainer = document.getElementById(KEY + "_stems");

          // Create stem rows
          STEMS.forEach(stem => {{
            const row = document.createElement("div");
            row.className = "stem-row";
            row.innerHTML = `
              <span class="stem-label">${{stem}}</span>
              <input type="range" class="stem-slider" id="${{KEY}}_${{stem}}" min="0" max="150" value="100" step="1" disabled>
              <span class="stem-value" id="${{KEY}}_${{stem}}_val">100%</span>
            `;
            stemsContainer.appendChild(row);
          }});

          const stemSliders = {{}};
          const stemVals = {{}};
          STEMS.forEach(stem => {{
            stemSliders[stem] = document.getElementById(KEY + "_" + stem);
            stemVals[stem] = document.getElementById(KEY + "_" + stem + "_val");
          }});

          function updateStatus(msg) {{
            statusEl.textContent = msg;
          }}

          function enableUI(enable) {{
            playBtn.disabled = !enable || isPlaying;
            pauseBtn.disabled = !enable || !isPlaying;
            stopBtn.disabled = !enable || !isPlaying;
            downloadBtn.disabled = !enable;
            masterSlider.disabled = !enable;
            STEMS.forEach(stem => stemSliders[stem].disabled = !enable);
          }}

          function formatTime(seconds) {{
            const m = Math.floor(seconds / 60);
            const s = Math.floor(seconds % 60);
            return `${{m}}:${{s.toString().padStart(2, '0')}}`;
          }}

          async function loadAudio() {{
            try {{
              audioCtx = new (window.AudioContext || window.webkitAudioContext)();
              masterGain = audioCtx.createGain();
              masterGain.connect(audioCtx.destination);
              masterGain.gain.value = masterSlider.value / 100;

              // Load all stems in parallel
              await Promise.all(STEMS.map(async stem => {{
                const response = await fetch(STEM_DATA[stem]);
                const arrayBuffer = await response.arrayBuffer();
                buffers[stem] = await audioCtx.decodeAudioData(arrayBuffer);
                loadedCount++;
                updateStatus(`Loaded ${{loadedCount}}/${{STEMS.length}} stems…`);
              }}));

              updateStatus(`Ready — ${{formatTime(buffers[STEMS[0]].duration)}}`);
              enableUI(true);
            }} catch (err) {{
              updateStatus("Error loading audio: " + err.message);
              console.error(err);
            }}
          }}

          function createSource(stem) {{
            const src = audioCtx.createBufferSource();
            src.buffer = buffers[stem];
            const gain = audioCtx.createGain();
            gain.gain.value = stemSliders[stem].value / 100;
            src.connect(gain);
            gain.connect(masterGain);
            sources[stem] = src;
            gainNodes[stem] = gain;
            return src;
          }}

          function startAll(offset = 0) {{
            if (isPlaying) return;
            STEMS.forEach(stem => {{
              const src = createSource(stem);
              src.start(0, offset);
            }});
            startTime = audioCtx.currentTime - offset;
            isPlaying = true;
            playBtn.disabled = true;
            pauseBtn.disabled = false;
            stopBtn.disabled = false;
            updateStatus("Playing…");
          }}

          function pauseAll() {{
            if (!isPlaying) return;
            pauseTime = audioCtx.currentTime - startTime;
            STEMS.forEach(stem => {{
              if (sources[stem]) sources[stem].stop(0);
            }});
            isPlaying = false;
            playBtn.disabled = false;
            pauseBtn.disabled = true;
            updateStatus(`Paused at ${{formatTime(pauseTime)}}`);
          }}

          function stopAll() {{
            STEMS.forEach(stem => {{
              if (sources[stem]) sources[stem].stop(0);
            }});
            isPlaying = false;
            pauseTime = 0;
            startTime = 0;
            playBtn.disabled = false;
            pauseBtn.disabled = true;
            stopBtn.disabled = true;
            updateStatus(`Ready — ${{formatTime(buffers[STEMS[0]]?.duration || 0)}}`);
          }}

          function updateGains() {{
            if (masterGain) masterGain.gain.value = masterSlider.value / 100;
            masterVal.textContent = masterSlider.value + "%";
            STEMS.forEach(stem => {{
              if (gainNodes[stem]) gainNodes[stem].gain.value = stemSliders[stem].value / 100;
              stemVals[stem].textContent = stemSliders[stem].value + "%";
            }});
          }}

          // Download mix using OfflineAudioContext
          async function downloadMix() {{
            downloadBtn.disabled = true;
            downloadBtn.textContent = "Rendering…";
            updateStatus("Rendering mix…");

            try {{
              const duration = buffers[STEMS[0]].duration;
              const offlineCtx = new OfflineAudioContext(2, duration * offlineCtx.sampleRate, offlineCtx.sampleRate);
              const offlineMaster = offlineCtx.createGain();
              offlineMaster.connect(offlineCtx.destination);
              offlineMaster.gain.value = masterSlider.value / 100;

              await Promise.all(STEMS.map(async stem => {{
                const src = offlineCtx.createBufferSource();
                src.buffer = buffers[stem];
                const gain = offlineCtx.createGain();
                gain.gain.value = stemSliders[stem].value / 100;
                src.connect(gain);
                gain.connect(offlineMaster);
                src.start(0);
              }}));

              const renderedBuffer = await offlineCtx.startRendering();

              // Convert to WAV
              const wav = bufferToWav(renderedBuffer);
              const blob = new Blob([wav], {{ type: "audio/wav" }});
              const url = URL.createObjectURL(blob);
              const a = document.createElement("a");
              a.href = url;
              a.download = "{song_name}_mix.wav";
              a.click();
              URL.revokeObjectURL(url);

              updateStatus("Mix downloaded");
            }} catch (err) {{
              updateStatus("Download failed: " + err.message);
              console.error(err);
            }} finally {{
              downloadBtn.disabled = false;
              downloadBtn.textContent = "Download Mix";
            }}
          }}

          // WAV encoding helper
          function bufferToWav(buffer) {{
            const numChannels = buffer.numberOfChannels;
            const sampleRate = buffer.sampleRate;
            const length = buffer.length * numChannels * 2; // 16-bit
            const arrayBuffer = new ArrayBuffer(44 + length);
            const view = new DataView(arrayBuffer);

            // RIFF header
            writeString(view, 0, "RIFF");
            view.setUint32(4, 36 + length, true);
            writeString(view, 8, "WAVE");
            writeString(view, 12, "fmt ");
            view.setUint32(16, 16, true); // PCM chunk size
            view.setUint16(20, 1, true); // PCM format
            view.setUint16(22, numChannels, true);
            view.setUint32(24, sampleRate, true);
            view.setUint32(28, sampleRate * numChannels * 2, true); // byte rate
            view.setUint16(32, numChannels * 2, true); // block align
            view.setUint16(34, 16, true); // bits per sample
            writeString(view, 36, "data");
            view.setUint32(40, length, true);

            // Interleave channels
            const offset = 44;
            const channelData = [];
            for (let c = 0; c < numChannels; c++) {{
              channelData.push(buffer.getChannelData(c));
            }}
            let pos = offset;
            for (let i = 0; i < buffer.length; i++) {{
              for (let c = 0; c < numChannels; c++) {{
                const sample = Math.max(-1, Math.min(1, channelData[c][i]));
                view.setInt16(pos, sample * 0x7FFF, true);
                pos += 2;
              }}
            }}
            return arrayBuffer;
          }}

          function writeString(view, offset, str) {{
            for (let i = 0; i < str.length; i++) {{
              view.setUint8(offset + i, str.charCodeAt(i));
            }}
          }}

          // Event listeners
          masterSlider.addEventListener("input", updateGains);
          STEMS.forEach(stem => stemSliders[stem].addEventListener("input", updateGains));

          playBtn.addEventListener("click", () => startAll(pauseTime));
          pauseBtn.addEventListener("click", pauseAll);
          stopBtn.addEventListener("click", stopAll);
          downloadBtn.addEventListener("click", downloadMix);

          // Handle audio context suspension (browser autoplay policy)
          document.addEventListener("click", () => {{
            if (audioCtx && audioCtx.state === "suspended") {{
              audioCtx.resume();
            }}
          }}, {{ once: true }});

          // Start loading
          loadAudio();
        }})();
      </script>
    </div>
    """

    components.html(html, height=520, scrolling=False)


def _render_stems() -> None:
    stems_paths = st.session_state.get("stems_paths")
    if not stems_paths:
        st.info("Click **Generate** to separate this song into stems.")
        return

    song_name = st.session_state.song_name
    st.success("Done! Stems ready below.")

    # Stage stems to static dir once per song for HTTP serving
    if "staged_stem_urls" not in st.session_state:
        st.session_state.staged_stem_urls = _stage_stems_static(stems_paths, song_name)
    stem_urls = st.session_state.staged_stem_urls

    _render_mixer(stem_urls, song_name)

    for stem in STEMS:
        path = Path(stems_paths[stem])
        st.subheader(stem.capitalize())
        st.audio(str(path))
        # Use markdown anchor with download attribute (same-origin, forces download)
        url = stem_urls[stem]
        st.markdown(
            f'<a href="{url}" download="{song_name}_{stem}.wav" '
            f'style="display:inline-block;padding:6px 16px;background:#28a745;color:white;'
            f'text-decoration:none;border-radius:4px;font-size:0.9rem;">'
            f'Download {stem}</a>',
            unsafe_allow_html=True,
        )


main()