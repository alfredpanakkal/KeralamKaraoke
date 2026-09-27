# KeralamKaraoke — Remaining Improvements Plan

## Context

`KeralamKaraoke` is a single-user, local Streamlit app that turns an uploaded mp3/wav
into a karaoke track (vocals removed, optional −6..+6 semitone pitch shift) or into
four stems (vocals, drums, bass, other). It shells out to Demucs
(`sys.executable -m demucs`) and caches pitch shifts on disk, keyed by a blake2b hash
of the instrumental plus the semitone count.

Baseline at plan start: commit `5752357` on `master`, 38/38 tests passing, CI green,
working tree clean.

This plan covers the polish items that survived review. Already-done work is **not**
repeated here: CI is green, the 150 MB limit, `safe_upload_name`, model-aware output
paths, the atomic pitch cache with failure cleanup, the timeout `RuntimeError`,
`LICENSE`, and bounded dependencies all exist and are covered by tests.

Work happens on branch `polish/remaining-improvements`. Do not push.

## Global Constraints

These bind every task. A task that cannot satisfy one is BLOCKED, not "adapted".

1. **Test command** — from the repo root, the full suite must be green:
   `.\karaoke-env\Scripts\python.exe -m pytest tests/ -q -p no:cacheprovider`
   38 tests pass at plan start; the count may only go up. Shell is PowerShell on
   Windows; the app targets Windows.
2. **No new runtime dependencies.** `soundfile>=0.14`, `streamlit>=1.64`,
   `librosa>=0.11` are already installed and available.
3. **`karaoke/core.py` must stay free of heavy module-level imports.** Import
   `soundfile` lazily inside the function that needs it — precedent:
   `karaoke/pitch_shift.py::_shift_audio`.
4. **Do not change** the 150 MB upload limit (`MAX_UPLOAD_BYTES`), the −6..+6
   semitone range, the blake2b cache-key format, or `sanitise_name` /
   `safe_upload_name` behaviour. Existing tests assert all four.
5. **Single-user local app** — no auth, no multi-user locking, no database, no
   background jobs.
6. **Session state must never outlive the files it points at.** Any code that
   deletes files under `uploads/`, `karaoke_out/`, `separated/`, or `karaoke_cache/`
   must purge the song keys and every `_dl_bytes:*` key in the same breath, or the
   next rerun raises `FileNotFoundError`.
7. **UI copy is plain, short English.** No emoji beyond the ones already in
   `app.py` (`🎤`). No jargon in user-facing strings.
8. **Tests verify real behaviour**, not mock behaviour. Use real files under
   `tmp_path`, real `soundfile` writes; monkeypatch only genuine boundaries
   (`st.session_state`, subprocess).
9. **Scope discipline** — implement the task as written, follow the existing module
   responsibilities, and do not restructure files beyond the task.
10. **Commits** — one commit per task, imperative subject (`feat:`, `fix:`, `docs:`).
    Never commit `.superpowers/`, `karaoke_cache/`, `separated/`, `karaoke_out/`,
    `uploads/`, or `karaoke-env/` (all gitignored). Do not push.
11. **TDD** for every task that adds logic: capture RED output before the
    implementation exists, then GREEN. Task 7 is docs-only and adds no tests.

## Definition of Done (every task)

- Full suite green via the Global Constraints command, output pristine (no warnings).
- New logic covered by tests that fail without the change.
- `README.md` updated in the same task whenever user-facing behaviour changes.
- Self-reviewed; report written; work committed on this branch.

---

## Task 1: Anchor runtime paths to the app directory

**Why:** `app.py:12-15` builds `Path("uploads")` and friends relative to the current
working directory. Launching the app from anywhere other than the repo root scatters
output across the filesystem and can write into an unexpected place.

**Files:** `app.py`, `tests/test_app.py`

**Requirements:**

1. In `app.py`, replace the four bare `Path(...)` constants with a `ROOT` anchor
   placed directly after the imports:

   ```python
   ROOT = Path(__file__).resolve().parent
   UPLOAD_DIR = ROOT / "uploads"
   OUTPUT_DIR = ROOT / "karaoke_out"
   SEPARATED_DIR = ROOT / "separated"
   CACHE_DIR = ROOT / "karaoke_cache"
   ```

2. Leave `MODEL`, `BASE_DIRS`, and the `mkdir` loop (`app.py:59-60`) otherwise
   untouched. `BASE_DIRS` already stringifies whatever the constants are, so it
   picks up absolute paths for free.

3. The four directory **names** must not change — `.gitignore` matches them by name.

4. Add `tests/test_app.py::test_runtime_dirs_are_anchored_to_app_dir` asserting, for
   each of the four constants: it is absolute, its `.parent` equals
   `Path(app.__file__).resolve().parent`, and its `.name` is one of
   `{"uploads", "karaoke_out", "separated", "karaoke_cache"}`.

5. TDD: the new test must fail before the change (run it and record RED).

**Out of scope:** touching `karaoke/demucs_runner.py` or `karaoke/pitch_shift.py` —
they receive paths as arguments.

## Task 2: Add a non-blocking audio probe

**Why:** the app reports nothing about the file until Demucs runs, and a corrupt or
non-audio file surfaces as a separation failure with no context. A cheap header read
lets the UI warn early and supply a duration for the estimate in Task 5.

**Files:** `karaoke/core.py`, `tests/test_core.py`

**Requirements:**

1. Add to `karaoke/core.py`:

   ```python
   def probe_audio(source) -> dict | None:
       """Return {"duration", "samplerate", "channels", "format"} for an audio
       file, or None when it cannot be read.

       `source` is a path (str/Path) or an open binary file object. Never raises:
       an unreadable file is reported as None so the caller can warn and let
       Demucs produce the authoritative error. May move a file object's read
       position; callers pass objects they do not read positionally.
       """
       try:
           import soundfile as sf
           info = sf.info(source)
       except Exception:
           return None
       return {
           "duration": float(info.duration),
           "samplerate": int(info.samplerate),
           "channels": int(info.channels),
           "format": str(info.format),
       }
   ```

   The broad `except` is deliberate — soundfile raises several unrelated types
   (`LibsndfileError`, `RuntimeError`, `TypeError`) and a probe must never take the
   app down. Keep the docstring's warning about read position.

2. Tests in `tests/test_core.py`:
   - a real wav written with `soundfile.write(path, [0.0] * 800, 8000)` probes to
     `duration == 0.1` (within `1e-6`), `samplerate == 8000`, `channels == 1`
   - the same wav in a `io.BytesIO` probes to the same values (file-object source)
   - a file containing `b"not audio at all"` probes to `None`
   - a path that does not exist probes to `None`
   - the returned dict has exactly the four keys

3. TDD: record RED before implementing.

**Out of scope:** rejecting uploads based on the probe. It never blocks.

## Task 3: Add a "Clear generated files" control

**Why:** `karaoke_cache/` and `separated/` grow without bound and nothing in the UI
reclaims them. Deleting files naively would leave `st.session_state` pointing at
deleted paths — Global Constraint 6.

**Files:** `karaoke/core.py`, `app.py`, `tests/test_core.py`, `tests/test_app.py`

**Requirements:**

1. In `karaoke/core.py`:

   ```python
   def clear_generated_files(dirs) -> tuple[int, list[str]]:
       """Delete the contents of each directory in `dirs`, keeping the
       directories themselves.

       Recurses into subdirectories (e.g. separated/htdemucs/<song>/) and prunes
       the subdirectories it empties. Returns (files_deleted, failures) where
       each failure is a "<path>: <reason>" string. Never raises: a locked or
       undeletable file is reported, not propagated.
       """
   ```

2. In `app.py`, split the purge half out of `reset_song_state` so there is exactly
   one purge path:

   ```python
   def purge_song_state() -> None:
       for key in SONG_KEYS:
           st.session_state.pop(key, None)
       for key in [k for k in st.session_state if k.startswith("_dl_bytes:")]:
           st.session_state.pop(key, None)


   def reset_song_state(uploaded_name: str, mode: str) -> None:
       purge_song_state()
       st.session_state.current_file = uploaded_name
       st.session_state.mode = mode
   ```

   `reset_song_state` keeps its current signature and behaviour.

3. Add a sidebar button labelled **"Clear generated files"**, created before the
   file uploader so its click is handled before the results render. On click:
   - call `clear_generated_files([UPLOAD_DIR, OUTPUT_DIR, SEPARATED_DIR, CACHE_DIR])`
   - call `purge_song_state()` **before** the deletion result is shown
   - `st.sidebar.success(f"Deleted {deleted} file(s)")` when there are no failures;
     otherwise `st.sidebar.warning` naming the failure count and the first three
     failures, plus `f"and {len(failures) - 3} more"` when more remain
   - say plainly that the uploaded copy is deleted too — the button removes the
     copy in `uploads/`, not the user's original file

   If stale audio players still render after the purge, `st.rerun()` is acceptable —
   add it only if you can show it is needed.

4. Tests:
   - `tests/test_core.py`: two files in one root plus one file inside a nested
     subdirectory, across two of the supplied roots, returns the right count, both
     roots still exist, and both are empty
   - a root that does not exist is skipped without raising, and the count reflects
     only what was really deleted
   - `tests/test_app.py::test_purge_song_state_clears_song_and_dl_bytes`: seed
     `_dl_bytes:x`, one `SONG_KEYS` entry, and an unrelated key; assert the first
     two are gone, the unrelated key survives, and `reset_song_state` still sets
     `current_file` and `mode`

5. TDD: record RED before implementing.

## Task 4: Add a Demucs model selector

**Why:** `app.py:16` hard-codes `MODEL = "htdemucs"`, so a user cannot reach
`htdemucs_ft` even though the backend already accepts any model. Grok's plan is right
that the copy must be honest about the cost: `htdemucs_ft` is a bag of four models,
roughly 4x the processing time and a much larger first-run weight download.

**Files:** `karaoke/core.py`, `app.py`, `tests/test_core.py`, `tests/test_app.py`

**Requirements:**

1. In `karaoke/core.py`, next to the other model-independent constants:

   ```python
   MODEL_DEFAULT = "htdemucs"
   MODEL_OPTIONS = {
       "htdemucs (fast, good enough for karaoke)": "htdemucs",
       "htdemucs_ft (best quality, roughly 4x slower)": "htdemucs_ft",
   }
   ```

   Use those exact label strings. `build_output_paths`' own `model="htdemucs"`
   default is untouched.

2. In `app.py`, delete `MODEL = "htdemucs"` and add a `st.selectbox` labelled
   `"Demucs model"` over `list(MODEL_OPTIONS)`, placed next to the mode radio, with
   `help` text stating: the first run with any model downloads its weights, and
   `htdemucs_ft` runs four models instead of one.

3. Resolve the selection once: `model = MODEL_OPTIONS[label]`, and pass `model` to
   `build_output_paths`, `separate_vocals`, and `separate_stems`. The default
   selection must be `htdemucs`, so existing behaviour is unchanged out of the box.

4. Switching model must invalidate the current song's results, exactly as switching
   file or mode does today:
   - add `"model"` to `SONG_KEYS`
   - give `reset_song_state` a third optional parameter
     `model: str | None = None`, set `st.session_state.model = model` when it is not
     `None`
   - extend the reset trigger (`app.py:85-90`) to also fire when
     `st.session_state.get("model") != model`, and pass `model` to
     `reset_song_state` at that call site

5. Tests:
   - `tests/test_core.py`: `MODEL_OPTIONS` maps the two exact labels to `htdemucs`
     and `htdemucs_ft`; `MODEL_DEFAULT == "htdemucs"`; every value is a non-empty
     string
   - `tests/test_app.py::test_reset_song_state_replaces_previous_model`: seed
     `model="htdemucs_ft"`, call `reset_song_state(..., model=MODEL_DEFAULT)`, assert
     the stored value is `htdemucs`
   - `tests/test_app.py::test_reset_song_state_omits_model_when_not_given`: calling it
     without `model` leaves no `model` key behind

6. TDD: record RED before implementing.

## Task 5: Show file size, duration, and a processing estimate

**Why:** a user with a 6-minute file learns nothing about the wait until the spinner
is already running. The probe from Task 2 supplies the duration.

**Files:** `karaoke/core.py`, `app.py`, `tests/test_core.py`

**Requirements:**

1. In `karaoke/core.py`:

   ```python
   # Rough processing throughput of a mid-range CUDA GPU, measured on an
   # RTX 4050: about 45 seconds of audio per minute of processing. CPU-only is
   # several times slower. The copy is advisory, never a promise.
   ESTIMATE_SECONDS_PER_MINUTE = 45


   def estimate_minutes(duration_seconds: float) -> int:
       """Rough minutes to process `duration_seconds` of audio; at least 1."""
       if duration_seconds <= 0:
           return 1
       return max(1, round(duration_seconds / ESTIMATE_SECONDS_PER_MINUTE))
   ```

2. In `app.py`, after the size check and before the reset trigger, probe the upload
   and show one caption line. Pass the `UploadedFile` straight to `probe_audio` — it
   is an `io.BytesIO` subclass, and wrapping it in `BytesIO(uploaded.getbuffer())`
   would copy up to 150 MB on every rerun. Do not do that.

   ```python
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
   ```

3. Use `est_minutes` in the two separation spinner labels in place of the hard-coded
   `"(1-3 min)"`, falling back to the current text when it is `None`.

4. Tests in `tests/test_core.py` for `estimate_minutes`: `0 -> 1`, `-5 -> 1`,
   `30 -> 1`, `90 -> 2`, `3600 -> 80`, and `ESTIMATE_SECONDS_PER_MINUTE == 45`.

5. TDD: record RED before implementing.

**Out of scope:** a progress bar, `st.status`, or any timing prediction beyond this
estimate.

## Task 6: Evict the pitch-shift cache past a size limit

**Why:** the pitch cache is content-addressed and never pruned, so a session that
drags the slider across many semitones accumulates full-length wavs indefinitely.

**Files:** `karaoke/core.py`, `app.py`, `tests/test_core.py`

**Requirements:**

1. In `karaoke/core.py`:

   ```python
   CACHE_LIMIT_BYTES = 2 * 1024 ** 3


   def enforce_cache_limit(root: str, limit_bytes: int = CACHE_LIMIT_BYTES) -> int:
       """Delete least-recently-modified files under `root` until the total is
       within `limit_bytes`. Returns bytes freed. Never raises: an undeletable
       file is skipped. Newest files are kept — sorting is by
       (mtime, str(path)) so ties are deterministic."""
   ```

   Collect `(mtime, path, size)` for every file under `root` via `rglob`, sort
   ascending, then delete oldest-first while the running total exceeds the limit,
   accumulating freed bytes. Wrap each `unlink` in its own `try/except OSError`.

2. In `app.py`, call it in the karaoke render path against the **top-level**
   `CACHE_DIR` — not the per-song `paths["cache_dir"]`, which would make the limit
   per-song. Place the call **after** the `shutil.copy2` that consumes
   `pitch_shift_cached`'s return value, not immediately after the call itself: on a
   cache hit the returned file's mtime was never refreshed, so a sweep placed there
   could delete the very file the copy is about to read. Show a `st.caption` naming
   the freed amount only when it is greater than zero.

   The walk runs on every rerun of the pitch-slider path; that is deliberate and
   cheap (a few hundred `stat` calls). Leave a one-line comment saying so, so nobody
   "optimises" it into a per-rerun regression.

3. Tests in `tests/test_core.py`, using `os.utime` to make mtimes explicit:
   - a cache under the limit returns `0` and deletes nothing
   - a cache over the limit deletes oldest first, keeps the newest, and returns the
     sum of the deleted files' sizes
   - nested per-song subdirectories are swept
   - `limit_bytes=0` sweeps everything
   - `CACHE_LIMIT_BYTES == 2 * 1024 ** 3`

4. TDD: record RED before implementing.

**Out of scope:** a background sweeper, an age-based TTL, or eviction of
`separated/`.

## Task 7: Update README and mark PLAN.MD historical

**Why:** Tasks 1-6 all change user-facing behaviour, and `PLAN.MD` still documents
the pre-`712e2b5` / `e90b1e0` filename sanitisation as if it were current. (Corrected
by the controller during Task 7: the plan originally cited `5752357` here, but that is
the LICENSE/CI commit and sits *after* both sanitisation commits, so it is not a
boundary.)

**Files:** `README.md`, `PLAN.MD`

**Requirements:**

1. `PLAN.MD`: add a banner as the very first line of the file, before its existing
   title, stating that this is the original implementation plan, that `README.md`
   documents current behaviour, and    that its §7b describes filename sanitisation as
   it stood before commits `712e2b5` and `e90b1e0`.

2. `README.md`, updating the existing text in place — keep its current structure and
   do not reorganise it:
   - output directories are created next to `app.py` regardless of the directory the
     app is launched from
   - the model selector, and that `htdemucs_ft` costs roughly 4x the time plus a much
     larger first-run weight download
   - the "Clear generated files" button, including that it deletes the copy in
     `uploads/` and not the user's original file
   - pitch-shift cache entries are evicted oldest-first once `karaoke_cache/` exceeds
     2 GB
   - the unreadable-audio warning is advisory and does not block an upload
   - the "~N min" figure is an estimate from a 45 s-of-audio-per-minute heuristic
     measured on an RTX 4050, and CPU-only runs are several times slower
   - a line in Limitations that CI runs on `windows-latest` because two `skipif`
     tests exercise read-only-file deletion, which POSIX does not reproduce
     (corrected by the controller during Task 7: the plan originally attributed this
     to the filename tests asserting Windows path semantics; those are pure-function
     tests that pass on Linux and so do not force the runner)

3. No tests. Verify only that the README's claims match the code you just shipped.

**Out of scope:** new README sections beyond the bullets above, badges, or screenshots.
