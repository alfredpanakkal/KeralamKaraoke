#!/usr/bin/env bash
# Karaoke Track Generator - macOS/Linux launcher

set -e

cd "$(dirname "$0")"

if [[ ! -d "karaoke-env" ]]; then
    echo "Creating virtual environment..."
    python3 -m venv karaoke-env
fi

source karaoke-env/bin/activate

echo "Checking dependencies..."
pip install -q -r requirements.txt 2>/dev/null || pip install -r requirements.txt

echo
echo "Starting Karaoke Track Generator..."
echo "Opening http://localhost:8501 in your browser..."
echo
echo "Press Ctrl+C to stop the server."
echo

streamlit run app.py