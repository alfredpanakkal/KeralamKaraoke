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
    Render progress UI and check if separation is complete.
    Returns (is_done, error, result)
    """
    data = tracker.get()

    # Progress bar
    progress_bar = st.progress(data["percent"] / 100.0)

    # Status text
    status_parts = []
    if data["total_models"] > 1:
        status_parts.append(f"Model {data['current_model']}/{data['total_models']}")
    if data["total_segments"] > 1:
        status_parts.append(f"Segment {data['current_segment']}/{data['total_segments']}")
    status_parts.append(data["status"].capitalize())

    status_text = " • ".join(status_parts)
    st.caption(status_text)

    # ETA
    if data["eta_seconds"] is not None and data["eta_seconds"] > 1:
        eta_min = int(data["eta_seconds"] // 60)
        eta_sec = int(data["eta_seconds"] % 60)
        if eta_min > 0:
            st.caption(f"⏱️ ETA: ~{eta_min}m {eta_sec}s")
        else:
            st.caption(f"⏱️ ETA: ~{eta_sec}s")

    # Check completion
    if data["done"]:
        progress_bar.progress(1.0)
        if data["error"]:
            st.error(f"Separation failed: {data['error']}")
            return True, data["error"], None
        else:
            st.success("Separation complete!")
            return True, None, data["result"]

    return False, None, None


def _safe_rerun():
    """Call st.rerun() if available (not in test environment)."""
    if hasattr(st, "rerun"):
        st.rerun()


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
        purge_progress_state()
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

    if mode == MODE_KARAOKE:
        semitones = st.slider(
            "Pitch shift (semitones)",
            min_value=-6,
            max_value=6,
            value=0,
            help="0 = no change. Karaoke mode only. Positive = higher, negative = lower.",
        )
    else:
        semitones = 0   # unused in stems mode, keep defined so later refs don't break

    if uploaded is None:
        st.info("Upload a song to begin.")
        return

    ok, msg = validate_upload_size(uploaded.size)
    if not ok:
        st.error(msg)
        return

    # The upload itself, not a copy of it: an UploadedFile is an io.BytesIO, so
    # the header read costs a seek and nothing else, on every rerun.
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

    # Initialize progress tracker if not exists
    if "progress_tracker" not in st.session_state:
        st.session_state.progress_tracker = ProgressTracker()

    # Check if we have a running separation thread (async mode)
    # A finished thread may exit before the next poll, so also enter when the
    # tracker is done but its result has not been consumed yet. The thread key
    # is popped (not nulled) on completion so a later rerun never calls
    # None.is_alive().
    _thread = st.session_state.get("progress_thread")
    _thread_alive = _thread is not None and _thread.is_alive()
    _tracker_done_unconsumed = (
        st.session_state.progress_tracker.get().get("done")
        and "progress_result" not in st.session_state
        and "progress_error" not in st.session_state
    )
    if _thread_alive or _tracker_done_unconsumed:
        # Separation in progress - show progress UI
        done, error, result = _render_progress_ui(st.session_state.progress_tracker, mode)
        if done:
            # Separation complete - store result and clear thread
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
            # Still running - auto-refresh every 500ms
            time.sleep(0.5)
            _safe_rerun()
        return

    # Check if we have a completed result from previous run
    if "progress_result" in st.session_state:
        # Result already stored in session_state by the thread
        pass

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

            # Check if we're in test environment (no st.rerun support)
            in_test_env = not hasattr(st, "rerun")

            if in_test_env:
                # Synchronous execution for test compatibility
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
                # Async execution with progress tracking (production)
                # Create fresh progress tracker
                st.session_state.progress_tracker = ProgressTracker()
                purge_progress_state()  # Clear old progress keys
                st.session_state.progress_tracker = ProgressTracker()

                # Advisory: an estimate from a laptop GPU, so word it as one.
                eta = f"about {est_minutes} min" if est_minutes else "1-3 min"

                # Start separation in background thread
                two_stems = (mode == MODE_KARAOKE)
                tracker = st.session_state.progress_tracker
                thread = threading.Thread(
                    target=_run_separation_async,
                    args=(str(input_path), str(SEPARATED_DIR), model, two_stems, tracker),
                    daemon=True,
                )
                st.session_state.progress_thread = thread
                thread.start()

                # Force rerun to show progress UI
                _safe_rerun()

        except RuntimeError as exc:
            st.error(f"Separation failed: {exc}")
            st.stop()
        except Exception as exc:  # noqa: BLE001 - surface anything unexpected
            st.error(f"Unexpected error: {exc}")
            st.stop()

    # Show results if separation completed
    if mode == MODE_STEMS:
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
        # The copy is already made, so evicting now cannot take away a file the
        # render path still has to read. Top-level CACHE_DIR, not the per-song
        # cache_dir: the limit is a budget for the whole cache, not for one song.
        # Walking on every rerun is deliberate — a few hundred stat calls.
        freed = enforce_cache_limit(str(CACHE_DIR))

    if freed > 0:
        st.caption(f"Freed {freed / (1024 * 1024):.1f} MB from the pitch-shift cache.")

    st.success("Done! Karaoke track ready below.")
    st.audio(str(final))

    st.download_button(
        label="Download Karaoke Track",
        data=_cached_bytes(final),
        file_name=f"{song_name}_karaoke.wav",
        mime="audio/wav",
    )


def _render_mixer(stems_paths: dict[str, str], song_name: str) -> None:
    """Render a live Web Audio API mixer for the 4 stems."""
    # Encode each stem as base64 for embedding in the HTML
    stem_data = {}
    for stem in STEMS:
        path = Path(stems_paths[stem])
        b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        stem_data[stem] = f"data:audio/wav;base64,{b64}"

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
          const STEM_DATA = {stem_data};

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

    _render_mixer(stems_paths, song_name)

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