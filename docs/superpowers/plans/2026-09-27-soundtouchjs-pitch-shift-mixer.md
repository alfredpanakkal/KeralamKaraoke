# SoundTouchJS Pitch Shift in Stem Mixer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a −12..+12 semitone pitch control to the stem mixer. The four stems are summed into one buffer, that buffer is pitch-shifted with soundtouchjs, and both playback and download use the shifted result.

**Architecture:** Vendor soundtouchjs 0.3.0 into `static/soundtouchjs/`. In `_render_mixer`, sum the four stem buffers (honouring per-stem and master gains) into a single `AudioBuffer`, then feed it to `new PitchShifter(audioCtx, mixedBuffer, 4096, onEnd)`. The shifter's ScriptProcessorNode connects to `masterGain`. At 0 semitones, skip the shifter and use a plain `AudioBufferSourceNode`.

**Tech Stack:** Streamlit `components.html`, Web Audio API, soundtouchjs 0.3.0 (ES module), Python 3.11+

## Global Constraints

- Test command: `.\karaoke-env\Scripts\python.exe -m pytest tests/ -q -p no:cacheprovider` — must stay green, count may only go up.
- No new Python dependency. soundtouchjs is a static asset only.
- Do **not** change `requirements.txt`, the Demucs invocation, the pitch-shift
  cache, or the existing per-stem download links in `_render_stems`.
- `PitchShifter` constructor order is `(context, buffer, bufferSize, onEnd)`.
  The 3rd argument is a number.
- `pitchSemitones` is write-only. Track the current value in a JS variable.
- The library is an ES module — any `<script>` tag that loads it must be
  `type="module"`.
- Do not remove or rename existing element ids (`{key}_master`, `{key}_stems`,
  `{key}_play`, `{key}_pause`, `{key}_stop`, `{key}_download`, …); the Python
  string assertions in `tests/test_app.py` depend on them.
- The f-string in `_render_mixer` is brace-escaped. **Any literal `{` or `}` in
  the embedded JS/CSS must be doubled** or the Python file will not parse.
- UI copy is plain short English, no new emoji.
- One commit per task, imperative subject.

### A note on the automated tests in this plan

The tests here assert on the *generated HTML string*. They cannot execute
browser audio. They are structural guards — useful for catching accidental
deletion of the wiring, and nothing more. The behavioural verification is the
manual browser checklist in Task 6, which is mandatory before shipping.

---

## File structure

| File | Responsibility |
|------|----------------|
| `static/soundtouchjs/soundtouch.min.js` | Vendored library, loaded by the iframe |
| `static/soundtouchjs/soundtouch.src.js` | Readable copy; reference only, do not load at runtime |
| `app.py` (`_render_mixer`) | The entire mixer: markup, summing, pitch, transport, download |
| `tests/test_app.py` | Structural assertions on the rendered HTML |

`app.py` is already ~790 lines and `_render_mixer` alone is ~310. This change
adds roughly 120 lines of JS to it. Splitting the mixer's JS into
`static/mixer.js` and passing data in via a data attribute would keep `app.py`
from growing further, but it splits related logic across two files and changes
how the component is built. Out of scope here; noted for a future refactor.

---

### Task 1: Vendor soundtouchjs

**Files:**
- Add: `static/soundtouchjs/soundtouch.min.js` (already downloaded)
- Add: `static/soundtouchjs/soundtouch.src.js` (already downloaded)
- Modify: `.gitignore` — ensure `static/soundtouchjs/` is **not** ignored

**Interfaces:**
- Produces: `/app/static/soundtouchjs/soundtouch.min.js`, an ES module
  exporting `{ PitchShifter, SoundTouch, WebAudioBufferSource, getWebAudioNode, … }`

- [ ] **Step 1: Confirm the files are on disk and are ES modules**

```powershell
Get-ChildItem static\soundtouchjs\
Select-String -Path static\soundtouchjs\soundtouch.min.js -Pattern "export\{"
```
Expected: both files listed; the export line is found.

- [ ] **Step 2: Confirm `static/soundtouchjs` is not gitignored**

```powershell
git check-ignore -v static/soundtouchjs/soundtouch.min.js
```
Expected: no output (not ignored). Note that `static/` currently shows as
untracked in `git status`, and `static/stems/` holds generated audio — if
`static/stems` is ignored, confirm the vendored library is not swept up by the
same rule.

- [ ] **Step 3: Stage only the library, not generated stems**

```powershell
git add static/soundtouchjs/soundtouch.min.js static/soundtouchjs/soundtouch.src.js
git status --short
```
Expected: only the two `soundtouchjs` files staged. If `static/stems/*` also
appears, unstage it with `git restore --staged static/stems`.

- [ ] **Step 4: Commit**

```powershell
git commit -m "feat: vendor soundtouchjs 0.3.0 for client-side pitch shifting"
```

---

### Task 2: Load the module and add the pitch slider

**Files:**
- Modify: `app.py:_render_mixer` (markup around lines 476–497; script tag at 498)
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: the static module from Task 1
- Produces: DOM ids `{key}_pitch` and `{key}_pitch_val`; a module-scoped
  `PitchShifter` binding

- [ ] **Step 1: Write the failing test**

Append to `tests/test_app.py`:

```python
def test_mixer_loads_soundtouchjs_as_a_module():
    from unittest.mock import patch
    from app import _render_mixer

    urls = {s: f"/app/static/stems/t/{s}.wav" for s in ("vocals", "drums", "bass", "other")}
    with patch("streamlit.components.v1.html") as mock_html:
        _render_mixer(urls, "t")
    html = mock_html.call_args[0][0]

    assert 'type="module"' in html
    assert "/app/static/soundtouchjs/soundtouch.min.js" in html
    # A bare <script src> would fail: the file is an ES module.
    assert '<script src="/app/static/soundtouchjs' not in html


def test_mixer_renders_pitch_slider():
    from unittest.mock import patch
    from app import _render_mixer

    urls = {s: f"/app/static/stems/t/{s}.wav" for s in ("vocals", "drums", "bass", "other")}
    with patch("streamlit.components.v1.html") as mock_html:
        _render_mixer(urls, "t")
    html = mock_html.call_args[0][0]

    assert 'id="mixer_t_pitch"' in html
    assert 'min="-12"' in html
    assert 'max="12"' in html
```

- [ ] **Step 2: Run and watch it fail**

```powershell
.\karaoke-env\Scripts\python.exe -m pytest tests/test_app.py -q -p no:cacheprovider -k "soundtouch or pitch_slider"
```
Expected: FAIL — no module tag, no pitch slider.

- [ ] **Step 3: Add the pitch row to the markup**

In `_render_mixer`, immediately before the existing master row, insert a
dedicated row. A separate row (not a second pair of controls crammed into the
master row) keeps the 80px label column aligned:

```python
        <div class="master-row">
          <span class="master-label">Pitch</span>
          <input type="range" class="stem-slider" id="{key}_pitch" min="-12" max="12" value="0" step="1" disabled>
          <span class="stem-value" id="{key}_pitch_val">0 st</span>
        </div>
```

It ships `disabled`; Task 4 enables it once loading succeeds.

- [ ] **Step 4: Convert the script tag to a module import**

Change line 498's `<script>` to `<script type="module">` and put the import at
the very top of it, above the existing IIFE:

```html
      <script type="module">
        import {{ PitchShifter }} from '/app/static/soundtouchjs/soundtouch.min.js';

        (function() {{
```

The doubled braces are mandatory — this is a Python f-string.

- [ ] **Step 5: Run the tests**

```powershell
.\karaoke-env\Scripts\python.exe -m pytest tests/test_app.py -q -p no:cacheprovider -k "soundtouch or pitch_slider"
```
Expected: PASS.

- [ ] **Step 6: Run the full suite**

```powershell
.\karaoke-env\Scripts\python.exe -m pytest tests/ -q -p no:cacheprovider
```
Expected: all green.

- [ ] **Step 7: Commit**

```powershell
git add app.py tests/test_app.py
git commit -m "feat: add pitch slider and module import to stem mixer"
```

---

### Task 3: Sum the stems into one buffer

**Files:**
- Modify: `app.py:_render_mixer` (JS)
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `buffers` (stem → AudioBuffer), `gainNodes`, `masterGain`
- Produces: `function rebuildMixedBuffer(): AudioBuffer` — a stereo buffer at
  the context sample rate, honouring every slider's current value

- [ ] **Step 1: Write the failing test**

```python
def test_mixer_sums_stems_into_one_buffer(monkeypatch):
    html = _rendered_mixer_html(monkeypatch)

    assert "function rebuildMixedBuffer" in html
    assert "createBuffer(2, length, sr)" in html
```

> **Deviation from the plan, applied during execution:** the first draft also
> asserted `html.index("rebuildMixedBuffer") < html.index("new PitchShifter")`.
> That makes Task 3 depend on Task 4's implementation and leaves the suite red
> in between, which breaks the "each task ends green" rule. The ordering
> assertion moved to Task 4's test, where both symbols exist.

- [ ] **Step 2: Run and watch it fail**

Expected: FAIL — neither function exists yet.

- [ ] **Step 3: Implement the sum**

Add inside the IIFE, after `buffers` is populated:

```javascript
          // Sum the stems into one buffer, honouring per-stem and master gains.
          // This is what makes master-only pitch shifting possible: PitchShifter
          // plays a single buffer, so we hand it the sum rather than a node.
          function rebuildMixedBuffer() {{
            const first = buffers[STEMS[0]];
            const sr = first.sampleRate;
            // Longest stem wins; stems from one Demucs run are equal length,
            // but a truncated file should not clip the mix.
            const length = Math.max(...STEMS.map(s => buffers[s].length));
            const mixed = audioCtx.createBuffer(2, length, sr);

            for (let stem of STEMS) {{
              const data = buffers[stem].getChannelData(0);
              const gain = parseFloat(stemSliders[stem].value) / 100;
              for (let c = 0; c < 2; c++) {{
                const src = buffers[stem].numberOfChannels > c
                  ? buffers[stem].getChannelData(c)
                  : data;
                const dst = mixed.getChannelData(c);
                for (let i = 0; i < data.length; i++) {{
                  dst[i] += src[i] * gain;
                }}
              }}
            }}

            const master = parseFloat(masterSlider.value) / 100;
            for (let c = 0; c < 2; c++) {{
              const dst = mixed.getChannelData(c);
              for (let i = 0; i < dst.length; i++) dst[i] *= master;
            }}

            // Guard against clipping once summed levels exceed 1.0.
            for (let c = 0; c < 2; c++) {{
              const dst = mixed.getChannelData(c);
              for (let i = 0; i < dst.length; i++) {{
                if (dst[i] > 1) dst[i] = 1;
                else if (dst[i] < -1) dst[i] = -1;
              }}
            }}
            return mixed;
          }}
```

Clamping matters: four stems at 100% routinely sum past 1.0, and an unclamped
sum wraps around into loud distortion. Master gain is applied during the sum, so
`masterGain` is bypassed on the pitch path (Task 4).

- [ ] **Step 4: Run the tests**

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add app.py tests/test_app.py
git commit -m "feat: sum stems into a single mixed buffer"
```

---

### Task 4: Play the summed buffer through PitchShifter

**Files:**
- Modify: `app.py:_render_mixer` (JS)
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `rebuildMixedBuffer()` from Task 3, `{key}_pitch` slider
- Produces: `startPlayback(offsetSeconds)`, `stopPlayback()`,
  `currentPitchSemitones`; enables the pitch slider after load

- [ ] **Step 1: Write the failing tests**

```python
def test_mixer_applies_pitch_via_pitchshifter():
    from unittest.mock import patch
    from app import _render_mixer

    urls = {s: f"/app/static/stems/t/{s}.wav" for s in ("vocals", "drums", "bass", "other")}
    with patch("streamlit.components.v1.html") as mock_html:
        _render_mixer(urls, "t")
    html = mock_html.call_args[0][0]

    assert "new PitchShifter" in html
    assert ".pitchSemitones" in html
    # Constructor order is (context, buffer, bufferSize, onEnd).
    assert "new PitchShifter(audioCtx, mixed, 4096" in html


def test_mixer_zero_pitch_skips_the_shifter():
    from unittest.mock import patch
    from app import _render_mixer

    urls = {s: f"/app/static/stems/t/{s}.wav" for s in ("vocals", "drums", "bass", "other")}
    with patch("streamlit.components.v1.html") as mock_html:
        _render_mixer(urls, "t")
    html = mock_html.call_args[0][0]

    # A 0-semitone shift is a no-op; taking the plain source path keeps the
    # default state free of a deprecated ScriptProcessorNode.
    assert "currentPitchSemitones !== 0" in html


def test_mixer_keeps_existing_transport_controls():
    from unittest.mock import patch
    from app import _render_mixer

    urls = {s: f"/app/static/stems/t/{s}.wav" for s in ("vocals", "drums", "bass", "other")}
    with patch("streamlit.components.v1.html") as mock_html:
        _render_mixer(urls, "t")
    html = mock_html.call_args[0][0]

    for suffix in ("_play", "_pause", "_stop", "_download", "_master", "_status"):
        assert f'id="mixer_t{suffix}"' in html
```

- [ ] **Step 2: Run and watch them fail**

Expected: FAIL — no `PitchShifter` usage.

- [ ] **Step 3: Replace the transport with the single-buffer version**

Add state next to the existing `let` declarations:

```javascript
          let mixedBuffer = null;
          let currentPitchSemitones = 0;
          let pitchShifter = null;
          let playbackSource = null;
          let startedAt = 0;
          let pausedOffset = 0;
```

Replace `createSource`, `startAll`, `pauseAll` and `stopAll` with:

```javascript
          function disposePlayback() {{
            if (playbackSource) {{
              try {{ playbackSource.stop(0); }} catch (e) {{ /* already stopped */ }}
              playbackSource.disconnect();
              playbackSource = null;
            }}
            if (pitchShifter) {{
              try {{ pitchShifter.disconnect(); }} catch (e) {{ /* not connected */ }}
              pitchShifter = null;
            }}
          }}

          function startPlayback(offset) {{
            disposePlayback();
            if (!mixedBuffer) mixedBuffer = rebuildMixedBuffer();
            if (!mixedBuffer) return;

            if (currentPitchSemitones !== 0) {{
              try {{
                pitchShifter = new PitchShifter(audioCtx, mixedBuffer, 4096, onPlaybackEnded);
                pitchShifter.pitchSemitones = currentPitchSemitones;
                pitchShifter.connect(masterGain);
              }} catch (err) {{
                console.warn("PitchShifter failed, playing unshifted", err);
                pitchShifter = null;
              }}
            }}

            if (pitchShifter) {{
              masterGain.gain.value = 1;   // master gain already applied in the sum
            }} else {{
              playbackSource = audioCtx.createBufferSource();
              playbackSource.buffer = mixedBuffer;
              playbackSource.connect(masterGain);
              masterGain.gain.value = 1;
            }}

            if (pitchShifter) pitchShifter.percentagePlayed =
              (offset / mixedBuffer.duration) * 100;
            else playbackSource.start(0, offset);

            startedAt = audioCtx.currentTime - offset;
            pausedOffset = offset;
            isPlaying = true;
            playBtn.disabled = true;
            pauseBtn.disabled = false;
            stopBtn.disabled = false;
            updateStatus("Playing…");
          }}

          function pausePlayback() {{
            if (!isPlaying) return;
            pausedOffset = audioCtx.currentTime - startedAt;
            audioCtx.suspend();
            isPlaying = false;
            playBtn.disabled = false;
            pauseBtn.disabled = true;
            updateStatus(`Paused at ${{formatTime(pausedOffset)}}`);
          }}

          function stopPlayback() {{
            disposePlayback();
            audioCtx.resume();
            isPlaying = false;
            pausedOffset = 0;
            startedAt = 0;
            playBtn.disabled = false;
            pauseBtn.disabled = true;
            stopBtn.disabled = true;
            updateStatus(mixedBuffer
              ? `Ready — ${{formatTime(mixedBuffer.duration)}}`
              : "Ready");
          }}

          function onPlaybackEnded() {{
            stopPlayback();
          }}
```

`pausePlayback` uses `suspend()` because a ScriptProcessorNode cannot be
stopped and resumed mid-stream without losing its internal filter state;
suspending the context freezes it cleanly and `resume()` continues.

- [ ] **Step 4: Rewire the buttons and the pitch slider**

Replace the old transport listeners with:

```javascript
          playBtn.addEventListener("click", () => {{
            if (audioCtx && audioCtx.state === "suspended") audioCtx.resume();
            startPlayback(pausedOffset);
          }});
          pauseBtn.addEventListener("click", pausePlayback);
          stopBtn.addEventListener("click", stopPlayback);

          // 'change' fires on release, not on every tick of a drag. Re-shifting
          // on each tick would restart the shifter mid-drag and stutter.
          pitchSlider.addEventListener("change", () => {{
            currentPitchSemitones = parseInt(pitchSlider.value, 10);
            pitchVal.textContent = (currentPitchSemitones > 0 ? "+" : "") +
                                   currentPitchSemitones + " st";
            const wasPlaying = isPlaying;
            const offset = wasPlaying ? audioCtx.currentTime - startedAt : pausedOffset;
            if (wasPlaying) pausePlayback();
            startPlayback(offset);
          }});
```

- [ ] **Step 5: Rebuild the sum when a volume slider moves**

`updateGains` currently writes straight to `gainNodes`, which are no longer on
the pitch path. Change it to mark the sum dirty and restart if playing:

```javascript
          function updateGains() {{
            masterVal.textContent = masterSlider.value + "%";
            STEMS.forEach(stem => {{
              stemVals[stem].textContent = stemSliders[stem].value + "%";
            }});
            mixedBuffer = null;   // rebuilt lazily on next playback
          }}
```

Re-summing a four-minute song on every `input` tick would jank the UI, so the
buffer is invalidated rather than rebuilt, and `startPlayback` rebuilds it. The
cost is that a volume drag does not change the sound until playback restarts —
call this out in Task 7's README note.

- [ ] **Step 6: Enable the pitch slider on successful load**

At the end of `loadAudio`, after `enableUI(true)`:

```javascript
              // Only offer pitch once we know the module loaded and playback works.
              pitchSlider.disabled = false;
              pitchSlider.value = "0";
              currentPitchSemitones = 0;
              pitchVal.textContent = "0 st";
              mixedBuffer = rebuildMixedBuffer();
```

and reference the new elements near the other `getElementById` lookups:

```javascript
          const pitchSlider = document.getElementById(KEY + "_pitch");
          const pitchVal = document.getElementById(KEY + "_pitch_val");
```

Add `pitchSlider.disabled = !enable;` to `enableUI` as well.

- [ ] **Step 7: Run the new tests, then the full suite**

```powershell
.\karaoke-env\Scripts\python.exe -m pytest tests/test_app.py -q -p no:cacheprovider -k "pitch or transport"
.\karaoke-env\Scripts\python.exe -m pytest tests/ -q -p no:cacheprovider
```
Expected: PASS, then all green.

- [ ] **Step 8: Commit**

```powershell
git add app.py tests/test_app.py
git commit -m "feat: play summed buffer through soundtouchjs PitchShifter"
```

---

### Task 5: Download the pitch-shifted buffer

**Files:**
- Modify: `app.py:_render_mixer` (`downloadMix`)
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `mixedBuffer`, `currentPitchSemitones`
- Produces: `<song>_mix.wav` encoded from the shifted buffer

- [ ] **Step 1: Write the failing test**

```python
def test_mixer_download_uses_the_shifted_buffer():
    from unittest.mock import patch
    from app import _render_mixer

    urls = {s: f"/app/static/stems/t/{s}.wav" for s in ("vocals", "drums", "bass", "other")}
    with patch("streamlit.components.v1.html") as mock_html:
        _render_mixer(urls, "t")
    html = mock_html.call_args[0][0]

    # The download must consume the same buffer playback uses, not re-render
    # an unshifted OfflineAudioContext mix.
    assert "bufferToWav(" in html
    assert "OfflineAudioContext" not in html
```

- [ ] **Step 2: Run and watch it fail**

Expected: FAIL — `downloadMix` still uses `OfflineAudioContext`.

- [ ] **Step 3: Replace `downloadMix`**

```javascript
          async function downloadMix() {{
            if (!mixedBuffer) mixedBuffer = rebuildMixedBuffer();
            if (!mixedBuffer) {{
              updateStatus("Nothing to download yet");
              return;
            }}

            downloadBtn.disabled = true;
            const previousLabel = downloadBtn.textContent;
            downloadBtn.textContent = "Rendering…";

            try {{
              let outBuffer = mixedBuffer;
              const pitch = currentPitchSemitones;

              if (pitch !== 0) {{
                updateStatus("Rendering pitch shift…");
                // ScriptProcessorNode is not available in OfflineAudioContext,
                // so render the shift by pulling through PitchShifter's own
                // source/filter and collecting its output.
                outBuffer = await renderShifted(mixedBuffer, pitch);
              }}

              updateStatus("Encoding WAV…");
              const blob = new Blob([bufferToWav(outBuffer)], {{ type: "audio/wav" }});
              const url = URL.createObjectURL(blob);
              const a = document.createElement("a");
              a.href = url;
              a.download = "{song_name}_mix.wav";
              a.click();
              // Revoke on the next tick; revoking synchronously can cancel
              // the download in some browsers.
              setTimeout(() => URL.revokeObjectURL(url), 1000);
              updateStatus("Mix downloaded");
            }} catch (err) {{
              updateStatus("Download failed: " + err.message);
              console.error(err);
            }} finally {{
              downloadBtn.disabled = false;
              downloadBtn.textContent = previousLabel;
            }}
          }}
```

- [ ] **Step 4: Add `renderShifted`**

`PitchShifter` has no pull-based render entry point, so drive its underlying
`SimpleFilter` directly — it is exported for exactly this:

```javascript
          // Render a pitch-shifted copy of `buffer` without touching the live
          // audio graph. Duration is preserved because SoundTouch trades rate
          // against tempo.
          //
          // extract() drives the whole pipeline itself: it calls
          // fillOutputBuffer(), which refills the input from the source via
          // SimpleFilter.fillInputBuffer() and runs the DSP as needed. Calling
          // fillInputBuffer() from here as well would double-feed the input
          // FIFO and corrupt the stream, so we only ever call extract().
          async function renderShifted(buffer, semitones) {{
            const soundtouch = new SoundTouch();
            soundtouch.pitchSemitones = semitones;

            const source = new WebAudioBufferSource(buffer);
            const filter = new SimpleFilter(source, soundtouch);

            const sr = buffer.sampleRate;
            const chunk = 4096;
            // SoundTouch introduces a fixed algorithmic delay and may emit
            // slightly more frames than it consumes. Over-allocate, then trim.
            const capacity = buffer.length + Math.ceil(sr * 0.1);
            const out = audioCtx.createBuffer(2, capacity, sr);
            const outL = out.getChannelData(0);
            const outR = out.getChannelData(1);
            const scratch = new Float32Array(chunk * 2);
            let written = 0;

            while (written < capacity) {{
              const frames = filter.extract(scratch, chunk);
              if (frames === 0) break;          // source exhausted
              for (let i = 0; i < frames && written < capacity; i++) {{
                outL[written] = scratch[i * 2];
                outR[written] = scratch[i * 2 + 1];
                written++;
              }}
              // Yield periodically so a long song does not freeze the UI.
              if (written % (chunk * 64) === 0) {{
                await new Promise(r => setTimeout(r, 0));
              }}
            }}

            // Trim the algorithmic-delay tail by matching the source length,
            // so the downloaded file is the same length as the input.
            const finalLength = Math.min(written, buffer.length);
            return trimBuffer(out, finalLength);
          }}

          // Return the first `length` frames of `buffer` as a new AudioBuffer.
          function trimBuffer(buffer, length) {{
            const trimmed = audioCtx.createBuffer(
              buffer.numberOfChannels, length, buffer.sampleRate);
            for (let c = 0; c < buffer.numberOfChannels; c++) {{
              trimmed.getChannelData(c).set(buffer.getChannelData(c).subarray(0, length));
            }}
            return trimmed;
          }}
```

- [ ] **Step 5: Import the extra symbols at the top**

The module import in Task 2 only pulled in `PitchShifter`. `renderShifted`
needs three more. Extend it, and delete the now-unused
`const STEMS = {{...}}` / `SoundTouchModule` indirection if you added one while
drafting:

```javascript
        import {{
          PitchShifter, SoundTouch, SimpleFilter, WebAudioBufferSource
        }} from '/app/static/soundtouchjs/soundtouch.min.js';
```

All four names are used directly in `renderShifted` and `startPlayback`; there
is no namespaced module object.

- [ ] **Step 6: Run the tests, then the full suite**

Expected: PASS, then all green.

- [ ] **Step 7: Commit**

```powershell
git add app.py tests/test_app.py
git commit -m "feat: download the pitch-shifted mix"
```

---

### Task 6: Browser verification (mandatory)

**Files:** none — verification only.

**Interfaces:**
- Verifies: everything built in Tasks 1–5

- [ ] **Step 1: Start the app**

```powershell
.\karaoke-env\Scripts\python.exe -m streamlit run app.py
```

- [ ] **Step 2: Generate stems and check the console**

Upload a song, choose Stems, generate. Open DevTools → Console. Expected: no
`Uncaught SyntaxError`, no failed import of `soundtouch.min.js`, no
`PitchShifter failed` warning.

- [ ] **Step 3: Check the pitch slider is enabled and correct**

Expected: slider reads `0 st`, min `−12`, max `+12`, not greyed out.

- [ ] **Step 4: Confirm the 0-semitone path sounds right**

Play. Expected: audible, starts from 0, matches the pre-change mixer.

- [ ] **Step 5: Confirm pitch changes audibly and preserves duration**

Set `+3`, play from 0. Then `−3`. Expected: obviously higher / lower; the
track does not speed up or slow down; duration unchanged.

- [ ] **Step 6: Confirm pause and stop**

Pause mid-track → status shows the position; Play resumes from there. Stop →
returns to 0, Play re-enabled.

- [ ] **Step 7: Confirm volume sliders**

Move a per-stem slider and the master slider. Expected: labels update, and the
change is audible on the next playback start (the sum is rebuilt lazily).

- [ ] **Step 8: Confirm the download matches the pitch**

Set `+5`, Download Mix, open the file. Expected: plays back at +5. Repeat at 0
and at `−5`.

- [ ] **Step 9: Confirm offline operation**

Disconnect the network, reload the page, generate again. Expected: pitch
slider still works — nothing is fetched from a CDN.

- [ ] **Step 10: Confirm graceful degradation**

Temporarily rename `static/soundtouchjs/soundtouch.min.js`, reload. Expected:
the module import fails, the pitch slider stays disabled, and volume /
playback / download still work. Restore the file afterwards.

---

### Task 7: Document the behaviour

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: shipped behaviour from Tasks 1–6
- Produces: accurate user-facing docs

- [ ] **Step 1: Update the Stems description**

In the "How to use" section, the Stems bullet currently reads *"one player and
download per stem"*. Add: the mixer has a **Pitch** slider (−12 to +12
semitones) that shifts the whole mix; the downloaded mix includes the pitch.

- [ ] **Step 2: Add the technical caveat**

Under "Technical details", state plainly that pitch is applied to the summed
mix, so it takes effect when playback starts, and that volume changes are
picked up on the next play.

- [ ] **Step 3: Update the test count**

The Tests section says "79 tests". Set it to whatever `pytest --collect-only -q`
actually reports, so the number is not a guess.

- [ ] **Step 4: Commit**

```powershell
git add README.md
git commit -m "docs: describe stem mixer pitch shifting"
```

---

## Execution order

Tasks 1 → 2 → 3 → 4 → 5 → 6 → 7. Tasks 2–5 each end green; Task 6 is the
behavioural gate and must pass before Task 7 ships the documentation.

## Known risks

| Risk | Mitigation |
|------|------------|
| `renderShifted` is the least verifiable part — pure DSP pulled through `SimpleFilter` with no reference output | Task 6 step 8; if the tail handling is wrong the file will be audibly clipped, which is easy to spot |
| Four-stem summing can exceed full scale | Clamped in `rebuildMixedBuffer` |
| ScriptProcessorNode is deprecated | Confined to the non-zero-pitch path; 0 semitones uses a plain source node |
| Volume changes are not heard until playback restarts | Documented in Task 7; a live gain path would require per-stem shifters, which is out of scope |
