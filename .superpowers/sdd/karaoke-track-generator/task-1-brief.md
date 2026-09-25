# Task 1 Brief: Project scaffolding

**Task:** Create project scaffolding — requirements.txt, .gitignore, module skeletons, test stubs

**Global Constraints:**
- Python 3.11.9
- Virtual environment: karaoke-env
- Audio-only .wav output (no video/lyrics)
- Code must be importable/testable without Streamlit
- Per-song output isolation

**Files to Create:**
- `requirements.txt`
- `.gitignore`
- `karaoke/__init__.py`
- `karaoke/core.py` (stub)
- `karaoke/demucs_runner.py` (stub)
- `karaoke/pitch_shift.py` (stub)
- `app.py` (stub)
- `tests/__init__.py`
- `tests/test_core.py` (stub)
- `tests/test_demucs_runner.py` (stub)
- `tests/test_pitch_shift.py` (stub)

**Interfaces to Produce:**
- Empty module skeletons with docstrings
- `karaoke.core`: sanitise_name, build_output_paths, build_demucs_argv
- `karaoke.demucs_runner`: run_demucs, separate_vocals
- `karaoke.pitch_shift`: pitch_shift_cached
- `app.py`: main() entry point

**Test Requirements:**
- `tests/test_core.py::test_core_imports` - failing test for module imports
- Test must fail initially (ModuleNotFoundError or AttributeError)
- After implementation, test must pass

**Step-by-Step (from plan):**
1. Write failing test for module imports
2. Run test to verify it fails
3. Write minimal skeletons (exact code from plan)
4. Run test to verify it passes
5. Write .gitignore (exact content from plan)
6. Commit with message: "chore: scaffold project structure with modules and tests"

**Report File:** .superpowers/sdd/karaoke-track-generator/task-1-report.md

**Exact Code from Plan (must use verbatim):**

karaoke/__init__.py:
```python
"""Karaoke track generator core modules."""
```

karaoke/core.py:
```python
"""Pure logic: filename sanitisation, path construction, CLI argv building."""
def sanitise_name(name: str) -> str: ...
def build_output_paths(song_name: str, base_dirs: dict) -> dict: ...
def build_demucs_argv(input_path: str, separated_dir: str, model: str) -> list[str]: ...
```

karaoke/demucs_runner.py:
```python
"""Subprocess wrapper for Demucs CLI."""
def run_demucs(argv: list[str]) -> tuple[int, str, str]: ...
def separate_vocals(input_path: str, separated_dir: str, model: str) -> str: ...
```

karaoke/pitch_shift.py:
```python
"""Pitch shift with file-based caching."""
def pitch_shift_cached(instrumental_path: str, semitones: int, cache_dir: str) -> str: ...
```

app.py:
```python
"""Streamlit UI entry point."""
def main(): ...
if __name__ == "__main__": main()
```

requirements.txt:
```
streamlit>=1.38
demucs>=4.0
librosa>=0.10
soundfile>=0.12
pytest>=8.0
```

.gitignore:
```gitignore
# Python
__pycache__/
*.py[cod]
*.so
.venv/
venv/
karaoke-env/

# Project outputs
uploads/
separated/
karaoke_out/
karaoke_cache/

# Demucs model cache (can be large)
.demucs/

# IDE
.vscode/
.idea/
```

tests/test_core.py:
```python
def test_core_imports():
    import karaoke.core
    assert hasattr(karaoke.core, "sanitise_name")
    assert hasattr(karaoke.core, "build_output_paths")
```