# Karaoke Track Generator

Browser GUI that strips vocals out of a song using Meta's [Demucs](https://github.com/facebookresearch/demucs), with optional pitch shifting — or splits a track into individual **vocals / drums / bass / other** stems.

Upload an `.mp3` or `.wav`, get back `.wav` files you can preview in the browser and download.

## Requirements

- Python 3.11+
- An NVIDIA GPU makes this practical (separation takes 1-3 min on a laptop GPU, far longer on CPU). Demucs auto-detects CUDA / Apple MPS / CPU, so it runs without a GPU — just slowly.

## Setup

```bash
python -m venv karaoke-env

# Windows
karaoke-env\Scripts\activate
# macOS / Linux
source karaoke-env/bin/activate

pip install -r requirements.txt
```

### GPU acceleration (NVIDIA, Windows)

`pip install -r requirements.txt` gives you a **CPU-only torch** — it works,
but separation takes 10-30 min per song instead of ~17 s. For CUDA:

```bash
pip install torch --index-url https://download.pytorch.org/whl/cu124
```

Verify it took effect (should print `True` and your GPU name):

```bash
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

This installs torch 2.6.0+cu124, which is what the project is tested against.

Separations are capped at 30 minutes (`demucs_runner.TIMEOUT_SECONDS`); exceeding it means torch is probably CPU-only — re-check the CUDA install above.

## Run

```bash
streamlit run app.py
```

Opens at <http://localhost:8501>.

The first run downloads the Demucs model weights (~80 MB) into `~/.cache/torch` — expect a pause before the first song processes.

## How it works

Two modes, selected in the UI:

1. **Karaoke** — Demucs splits the track into vocals and everything else (`--two-stems=vocals`), then librosa applies an optional −6 to +6 semitone pitch shift, duration preserved.
2. **Stems** — Demucs' default 4-stem mode splits the track into vocals, drums, bass, and other. Each stem gets its own player and download button. (No pitch shift in this mode.)

## Output layout

| Path | Contents |
|---|---|
| `karaoke_out/<song>_karaoke.wav` | Final karaoke download, matches the current slider value |
| `karaoke_cache/<song>/` | Cached pitch-shift variants, keyed on audio content + semitones |
| `separated/htdemucs/<song>_<stem>.wav` | Stems mode output (per-song named by demucs) |
| `separated/htdemucs/` | Demucs working directory; karaoke-mode flat files copied out per-song |

Each song gets its own cache and output names, so processing a second song never overwrites the first.

## Notes

- Demucs reuses one flat filename (`no_vocals.wav`) internally. This project copies the result to a per-song path after separation to avoid collisions.
- Pitch-shift results are cached by content hash, so revisiting a slider value is instant and the cache survives restarts.
- Separation quality varies by song. Dense mixes with heavy reverb or layered vocals separate less cleanly than vocal-forward tracks.

## Tests

```bash
python -m pytest tests/ -v
```

The suite covers filename sanitising, per-song path isolation, the Demucs argv it builds, subprocess error handling, and pitch-shift cache behaviour. It does not invoke Demucs or librosa, so it runs in under a second.
