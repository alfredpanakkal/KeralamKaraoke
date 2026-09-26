"""Streamlit UI for the Karaoke Track Generator."""

import base64
import shutil
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

            # Advisory: an estimate from a laptop GPU, so word it as one.
            eta = f"about {est_minutes} min" if est_minutes else "1-3 min"

            if mode == MODE_KARAOKE:
                with st.spinner(f"Separating vocals from instrumental... ({eta})"):
                    st.session_state.instrumental_path = separate_vocals(
                        str(input_path), str(SEPARATED_DIR), model
                    )
            else:
                with st.spinner(f"Separating stems... ({eta})"):
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
