```markdown

\# Karaoke Track Generator — Full Plan



\*\*Scope:\*\* Audio-only output (instrumental `.wav`). Video/visuals deferred to later phase.

\*\*Hardware:\*\* Laptop, RTX 4050 (6GB VRAM), 16GB system RAM — confirmed sufficient for this scope, no GPU bottleneck expected.

\*\*Route chosen:\*\* Plain Python script + Streamlit web GUI. ComfyUI rejected for this scope — too many moving parts for a 2-step audio task.



\---



\## 1. Goal



Input: any song file (`.mp3`/`.wav`)

Output: instrumental-only `.wav`, optional pitch shift, downloadable from a browser GUI.



No lyrics, no visuals, no video compositing in this phase — cut for speed, revisit later once core pipeline proven.



\---



\## 2. Architecture



```

\[User uploads song via browser GUI]

&#x20;       ↓

\[Demucs — vocal/instrumental separation, GPU-accelerated]

&#x20;       ↓

\[Optional pitch shift — librosa, CPU]

&#x20;       ↓

\[Playback preview + download button]

```



Two Python tools do all the work:

\- \*\*Demucs\*\* (Meta AI, open source) — separates vocals from instrumental

\- \*\*librosa\*\* — pitch shifting

\- \*\*Streamlit\*\* — wraps both in a browser-based GUI, no command-line needed after setup



\---



\## 3. Setup (one-time)



```bash

\# Create isolated environment — keeps this project's packages separate from other Python work

python -m venv karaoke-env



\# Activate it

source karaoke-env/bin/activate      # Windows: karaoke-env\\Scripts\\activate



\# Install everything needed

pip install demucs librosa soundfile streamlit

```



\*\*Why a virtual environment:\*\* keeps package versions isolated per project. Without it, updating a package for one project can silently break another. Standard practice — use it for every Python project going forward, not just this one.



\---



\## 4. Core Script Logic (what the GUI runs under the hood)



Two building blocks, in order:



\### Step A — Vocal Separation

```bash

demucs -n htdemucs --two-stems=vocals --device cuda -o separated --filename "{stem}.{ext}" "song.mp3"

```

\- `-n htdemucs` → explicitly uses the `htdemucs` model (also dictates the subfolder name in the output directory).

\- `--two-stems=vocals` → outputs just `vocals.wav` + `no\_vocals.wav` (instrumental) — faster than full 4-stem separation.

\- `--device cuda` → uses your 4050 GPU, much faster than CPU.

\- `-o separated --filename "{stem}.{ext}"` → forces a predictable output path. Because Demucs hardcodes the model name into the directory structure, the final output will land at `separated/htdemucs/no\_vocals.wav`.



\### Step B — Pitch Shift (optional)

```python

import librosa, soundfile as sf

y, sr = librosa.load("separated/htdemucs/no\_vocals.wav", sr=None)

y\_shifted = librosa.effects.pitch\_shift(y, sr=sr, n\_steps=2)  # +2 semitones example

sf.write("karaoke\_output.wav", y\_shifted, sr)

```

CPU-only, no GPU competition with Demucs.



\---



\## 5. Full GUI Application (`app.py`)



```python

import streamlit as st

import os

import re

import subprocess

import shutil

import librosa

import soundfile as sf



\# ── PAGE CONFIG ──────────────────────────────

st.set\_page\_config(page\_title="Karaoke Maker", page\_icon="🎤")

st.title("🎤 Karaoke Track Generator")

st.write("Upload a song, get instrumental-only wav back.")



\# ── FOLDERS ──────────────────────────────────

UPLOAD\_FOLDER = "uploads"

OUTPUT\_FOLDER = "karaoke\_out"

os.makedirs(UPLOAD\_FOLDER, exist\_ok=True)

os.makedirs(OUTPUT\_FOLDER, exist\_ok=True)



\# ── UI: FILE UPLOAD ──────────────────────────

uploaded\_file = st.file\_uploader("Choose a song file", type=\["mp3", "wav"])



\# ── UI: PITCH SHIFT SLIDER ───────────────────

pitch\_shift = st.slider(

&#x20;   "Pitch shift (semitones)",

&#x20;   min\_value=-6, max\_value=6, value=0,

&#x20;   help="0 = no change. Positive = higher pitch, negative = lower."

)



\# ── PROCESS BUTTON ───────────────────────────

if uploaded\_file is not None:

&#x20;   if st.button("Generate Karaoke Track"):



&#x20;       # Save uploaded file to disk (Demucs needs a real file path, not a memory object)

&#x20;       input\_path = os.path.join(UPLOAD\_FOLDER, uploaded\_file.name)

&#x20;       with open(input\_path, "wb") as f:

&#x20;           f.write(uploaded\_file.getbuffer())



&#x20;       # Sanitize song name for final output file (avoids OS path errors)

&#x20;       raw\_song\_name = os.path.splitext(uploaded\_file.name)\[0]

&#x20;       song\_name = re.sub(r'\[\\\\/\*?:"<>|]', "", raw\_song\_name).replace(" ", "\_")



&#x20;       # ── STEP 1: Run Demucs ───────────────

&#x20;       # -n htdemucs explicitly sets the model. Demucs CLI always creates 

&#x20;       # a subfolder named after the model inside the -o directory.

&#x20;       SEPARATED\_DIR = "separated"

&#x20;       MODEL\_NAME = "htdemucs"

&#x20;       with st.spinner("Separating vocals from instrumental... (can take 1-3 min)"):

&#x20;           result = subprocess.run(

&#x20;               \["demucs", "-n", MODEL\_NAME, "--two-stems=vocals", "--device", "cuda",

&#x20;                "-o", SEPARATED\_DIR, "--filename", "{stem}.{ext}", input\_path],

&#x20;               capture\_output=True, text=True

&#x20;           )



&#x20;       if result.returncode != 0:

&#x20;           # Show actual error instead of failing silently

&#x20;           st.error("Demucs failed. Error details below:")

&#x20;           st.code(result.stderr)

&#x20;           st.stop()



&#x20;       # Demucs appends the model name to the output directory internally

&#x20;       instrumental\_path = os.path.join(SEPARATED\_DIR, MODEL\_NAME, "no\_vocals.wav")



&#x20;       # Cache Demucs's output path in session state — moving the pitch

&#x20;       # slider re-runs this whole script top to bottom (how Streamlit

&#x20;       # works), but we don't want to re-run the 1-3 min separation step

&#x20;       # just because the slider moved. Store the path once, reuse it.

&#x20;       st.session\_state.instrumental\_path = instrumental\_path

&#x20;       st.session\_state.song\_name = song\_name



\# ── STEP 2: Pitch Shift (outside the button block — runs on every ─────

\#    slider move too, not just button clicks, since it reads from

\#    session\_state instead of re-triggering Demucs) ─────────────────

if uploaded\_file is not None:

&#x20;   if "instrumental\_path" in st.session\_state:

&#x20;       instrumental\_path = st.session\_state.instrumental\_path

&#x20;       song\_name = st.session\_state.song\_name

&#x20;       final\_output\_path = os.path.join(OUTPUT\_FOLDER, f"{song\_name}\_karaoke.wav")



&#x20;       if pitch\_shift != 0:

&#x20;           with st.spinner("Applying pitch shift..."):

&#x20;               y, sr = librosa.load(instrumental\_path, sr=None)

&#x20;               y\_shifted = librosa.effects.pitch\_shift(y, sr=sr, n\_steps=pitch\_shift)

&#x20;               sf.write(final\_output\_path, y\_shifted, sr)

&#x20;       else:

&#x20;           shutil.copy(instrumental\_path, final\_output\_path)



&#x20;       st.success("Done! Karaoke track ready below.")



&#x20;       # ── PLAYBACK + DOWNLOAD ──────────────

&#x20;       st.audio(final\_output\_path)



&#x20;       with open(final\_output\_path, "rb") as f:

&#x20;           st.download\_button(

&#x20;               label="Download Karaoke Track",

&#x20;               data=f,

&#x20;               file\_name=f"{song\_name}\_karaoke.wav",

&#x20;               mime="audio/wav"

&#x20;           )

```



\*\*Run it:\*\*

```bash

streamlit run app.py

```

Opens automatically at `http://localhost:8501`.



\---



\## 6. Code Walkthrough (learning notes)



| Line/Block | What it does | Why it matters |

|---|---|---|

| `st.file\_uploader` | Accepts drag-and-drop file | Returns file as memory object, not disk path — must be written to disk before Demucs (a CLI tool) can use it |

| `re.sub(...)` | Sanitizes filename | Strips special characters like `?`, `!`, `/` from the user's uploaded filename to prevent OS-level path errors when saving the final output. |

| `capture\_output=True, text=True` | Captures Demucs's terminal output | Without this, errors vanish silently — GUI would just hang or fail with no explanation |

| `if result.returncode != 0` | Checks if Demucs actually failed | `subprocess.run` doesn't raise an error by default — you must check the return code yourself, or failures go unnoticed |

| `st.session\_state` | Caches variable across script reruns | Streamlit reruns the whole script on every UI interaction (like moving a slider). Caching the separation path prevents accidentally re-triggering the 1-3 min Demucs step just to change the pitch |

| `st.spinner(...)` | Loading indicator | Demucs takes real time (1-3 min); without feedback, GUI looks frozen |

| `st.audio(...)` | In-browser playback | Lets you verify quality before downloading — catch bad separations early |

| `st.download\_button` | Serves file for download | Standard Streamlit pattern for delivering generated files |



\---



\## 7. Known Risks / Things to Watch



1\. \*\*File overwrites\*\* — using flat output names (`.../htdemucs/no\_vocals.wav`) means running a second song overwrites the first. Fine for one-off testing; if batching multiple songs, confirm each download completes before starting the next. No history/gallery in current version.

2\. \*\*Separation quality varies by song\*\* — dense mixes (heavy layered vocals, lots of reverb) separate worse than clean vocal-forward tracks. Listen-check every output before trusting it, especially early on.

3\. \*\*First run downloads the Demucs model\*\* (`htdemucs` weights, a few hundred MB) — expect a delay on the very first song processed, faster after that since it's cached locally.

4\. \*\*GPU memory\*\* — not a real concern at this scope (audio-only, no video diffusion), but if you ever run Demucs alongside another GPU-heavy app simultaneously, watch for slowdowns.

5\. \*\*Streamlit Download Button State\*\* — Clicking download triggers a script rerun. Because we save files to disk (`OUTPUT\_FOLDER`), this is handled gracefully. If we ever switch to purely in-memory (`io.BytesIO`), state could wipe on click.



\---



\## 7b. External Review — Fact-Checked



Script got reviewed by another AI. Checked each claim before applying:



| Claim | Verdict | Action |

|---|---|---|

| Demucs sanitizes filenames, breaks folder path guess | Partially right concern, but their fix (`-n current\_track`) is \*\*wrong\*\* — `-n` selects the \*pretrained model\*, not output naming. | Used real fix: `-o` + `--filename "{stem}.{ext}"` to force a flat output path |

| Demucs hardcodes model name into output path despite `--filename` flag | \*\*Correct.\*\* Demucs CLI logic creates a subfolder matching the model name. | Applied fix: explicitly added `-n htdemucs` and updated path to `os.path.join(SEPARATED\_DIR, "htdemucs", "no\_vocals.wav")` |

| Slider re-triggers full Demucs run every move | Correct, real bug | Applied — `st.session\_state` caches the instrumental path, pitch shift now reruns independently of separation |

| Use `tempfile` for cleanup | Reasonable, optional | Not applied yet — current folders fine for single-user local tool |

| Demucs Python API vs `subprocess` | Valid alternative, not required | Sticking with `subprocess` — simpler to read/debug at current skill level |

| `librosa` pitch artifacts vs `pyrubberband` | Accurate — phase vocoding can sound metallic | Noted as risk, `pyrubberband` is the upgrade path if it becomes an issue |

| Sanitize `song\_name` before saving final output | Accurate edge case (e.g., `My Song (Remix)!.mp3` could cause path errors) | Applied — `re.sub` strips special characters and replaces spaces with underscores |



\*\*Lesson here:\*\* verify specific flag/API claims from any AI review — the fix mattered more than the diagnosis. Getting the bug right and the patch wrong is worse than not knowing about the bug, since it looks fixed but isn't.



\---



\## 8. Build Order



1\. Set up virtual environment, install packages

2\. Run Demucs from command line once, manually, on 1 test song — confirm output quality and exact path before touching the GUI at all

3\. Build `app.py`, run `streamlit run app.py`

4\. Test full GUI flow: upload → process → preview → download, on same test song

5\. Test pitch shift slider at a few values, listen for artifacts (ensure Demucs isn't re-running)

6\. Test with 2-3 more songs (different genres/mix density) to gauge separation quality range

7\. \*\*Decision point:\*\* if quality/speed acceptable across test songs, this is your working tool — start using it. If separation quality inconsistent, may need to try Demucs's other models (`htdemucs\_ft` — fine-tuned variant, slower but sometimes cleaner) before scaling usage.



\---



\## 9. Deferred to Later (Video Phase)



Not part of this plan — noted here only so scope stays clear:

\- Lyric timing/alignment (Whisper)

\- Background visual generation

\- Video compositing (audio + visuals + subtitles → mp4)

\- Metadata/upload templating



When you're ready for video, that's a separate follow-up plan — different tools (ComfyUI becomes worth the setup cost at that point), different hardware considerations (6GB VRAM becomes a real constraint for video diffusion, not here).

```

