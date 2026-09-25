# Karaoke Track Generator

Browser GUI that strips vocals out of a song using Meta's [Demucs](https://github.com/facebookresearch/demucs), with optional pitch shifting.

Upload an `.mp3` or `.wav`, get back an instrumental-only `.wav` you can preview in the browser and download.

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

## Run

```bash
streamlit run app.py
```

Opens at <http://localhost:8501>.

The first run downloads the Demucs model weights (~80 MB) into `~/.cache/torch` — expect a pause before the first song processes.

## How it works

1. **Separate** — Demucs splits the track into vocals and everything else. `--two-stems=vocals` is faster than full 4-stem separation.
2. **Pitch shift** *(optional)* — librosa shifts the instrumental by −6 to +6 semitones, duration preserved.

## Output layout

| Path | Contents |
|---|---|
| `karaoke_out/<song>_karaoke.wav` | Final download, matches the current slider value |
| `karaoke_cache/<song>/` | Cached pitch-shift variants, keyed on audio content + semitones |
| `separated/htdemucs/` | Demucs working directory (reused per run) |

Each song gets its own cache and output name, so processing a second song never overwrites the first.

## Notes

- Demucs reuses one flat filename (`no_vocals.wav`) internally. This project copies the result to a per-song path after separation to avoid collisions.
- Pitch-shift results are cached by content hash, so revisiting a slider value is instant and the cache survives restarts.
- Separation quality varies by song. Dense mixes with heavy reverb or layered vocals separate less cleanly than vocal-forward tracks.

## Tests

```bash
python -m pytest tests/ -v
```

The suite covers filename sanitising, per-song path isolation, the Demucs argv it builds, subprocess error handling, and pitch-shift cache behaviour. It does not invoke Demucs or librosa, so it runs in under a second.
