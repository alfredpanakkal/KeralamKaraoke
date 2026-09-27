# Karaoke Track Generator

Turn any song into a karaoke track — or split it into **vocals / drums / bass / other** stems — with a browser app. Upload an `.mp3` or `.wav`, listen in the browser, download the result.

![Karaoke separation in progress](docs/images/hero_karaoke_progress.png)

## Easy install

1. On the GitHub page click **Code → Download ZIP**, then extract it (right-click → **Extract All**).
2. Open the folder and double-click:
   - **Windows:** `launch_karaoke.bat`
   - **macOS / Linux:** `launch_karaoke.sh`
3. Wait. The first start sets up everything (a few minutes), and the first song downloads the AI model (~80 MB) — **both happen once; don't close the window**.
4. Your browser opens to <http://localhost:8501>. Upload a song and go.

<details>
<summary><strong>NVIDIA GPU? Do this once for ~5x speed.</strong></summary>

The launcher installs CPU-only PyTorch. On an NVIDIA PC run this once, after step 3 has finished:

```bash
karaoke-env\Scripts\pip install torch --index-url https://download.pytorch.org/whl/cu124
```

Check it worked (should print `True` and your GPU name):

```bash
karaoke-env\Scripts\python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

</details>

## How to use

**1. Upload a song** — click **Browse files** and pick an `.mp3` or `.wav`.

**2. Choose and generate** — pick **Karaoke** (instrumental, with optional pitch-shift slider) or **Stems** (vocals/drums/bass/other), leave the model on `htdemucs`, and click **Generate**. Watch the progress bar and ETA.

![Karaoke track ready to play and download](docs/images/karaoke_result.png)

**3. Play and download** — preview each track with the player, then click **Download**.

![Stems mode: one player and download per stem](docs/images/stems_result.png)

## FAQ

- **Slow on an NVIDIA PC?** See the GPU box under *Easy install*.
- **Where do files go?** Folders are created next to `app.py`: `uploads/`, `karaoke_out/`, `separated/`, `karaoke_cache/`.
- **Stops with a timeout on a long song?** Separation is capped at 30 minutes. `htdemucs_ft` takes ~4x longer, so treat time estimates as a floor.

<details>
<summary><strong>Technical details</strong></summary>

Powers [Demucs](https://github.com/facebookresearch/demucs) (vocal/stem separation) and librosa (pitch shift) behind a Streamlit GUI.

- **Karaoke** — Demucs `--two-stems=vocals`, then an optional −6 to +6 semitone pitch shift (duration preserved).
- **Stems** — Demucs' default 4-stem split, one player per stem (no pitch shift in this mode).
- **Model** — `htdemucs` is the fast default; `htdemucs_ft` runs four models for higher quality.
- Separation runs at roughly 45 seconds of audio per minute of processing on an RTX 4050 with CUDA; CPU-only is several times slower.
- Pitch-shift variants are cached by content hash below `karaoke_cache/` (2 GB cap, oldest evicted first).
- Each song gets its own cache and output names, so a second song never overwrites the first.
  | Path | Contents |
  |---|---|
  | `uploads/<song>.<ext>` | Copy of your uploaded file |
  | `karaoke_out/<song>_karaoke.wav` | Final karaoke download, matching the current slider value |
  | `karaoke_cache/<song>/` | Cached pitch-shift variants |
  | `separated/<model>/` | Demucs working dir + stems output |
- The sidebar's **Clear generated files** empties all four directories; your original file is untouched.
- Requirements: Python 3.11+. An NVIDIA GPU makes it practical; CPU works but is slow.
- First run with a model downloads its weights to `~/.cache/torch` (~80 MB for `htdemucs`).

</details>

## Manual install (developers)

```bash
python -m venv karaoke-env

# Windows
karaoke-env\Scripts\activate
# macOS / Linux
source karaoke-env/bin/activate

pip install -r requirements.txt
streamlit run app.py
```

## Tests

```bash
python -m pytest tests/ -v
```

79 tests covering filename sanitising, per-song path isolation, Demucs argv, error handling, pitch-shift cache, and the async progress flow. The suite does not invoke Demucs or librosa, so it runs in a few seconds.
