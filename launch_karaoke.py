#!/usr/bin/env python3
"""Cross-platform launcher for Karaoke Track Generator."""

import os
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / "karaoke-env"
REQUIREMENTS = ROOT / "requirements.txt"
APP = ROOT / "app.py"


def run(cmd, cwd=None, check=True):
    print(f"$ {cmd}")
    return subprocess.run(cmd, cwd=cwd or ROOT, shell=True, check=check)


def ensure_venv():
    if not VENV_DIR.exists():
        print("Creating virtual environment...")
        venv.create(VENV_DIR, with_pip=True)


def get_python():
    if sys.platform == "win32":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def get_pip():
    py = get_python()
    return f'"{py}" -m pip'


def main():
    os.chdir(ROOT)
    ensure_venv()

    pip = get_pip()
    print("Installing/updating dependencies...")
    run(f"{pip} install -q -r {REQUIREMENTS}", check=False) or run(f"{pip} install -r {REQUIREMENTS}")

    print("\nStarting Karaoke Track Generator...")
    print("Opening http://localhost:8501 in your browser...")
    print("Press Ctrl+C to stop the server.\n")

    streamlit = VENV_DIR / ("Scripts" if sys.platform == "win32" else "bin") / "streamlit"
    run(f'"{streamlit}" run "{APP}"')


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
    except Exception as e:
        print(f"\nError: {e}")
        input("Press Enter to exit...")