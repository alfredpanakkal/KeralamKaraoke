@echo off
title Karaoke Track Generator

cd /d "%~dp0"

if not exist "karaoke-env" (
    echo Creating virtual environment...
    python -m venv karaoke-env
    if errorlevel 1 (
        echo Failed to create virtual environment.
        echo Make sure Python 3.11+ is installed and in PATH.
        pause
        exit /b 1
    )
)

call karaoke-env\Scripts\activate.bat

echo Checking dependencies...
pip install -q -r requirements.txt 2>nul
if errorlevel 1 (
    echo Installing dependencies...
    pip install -r requirements.txt
    if errorlevel 1 (
        echo Failed to install dependencies.
        pause
        exit /b 1
    )
)

echo.
echo Starting Karaoke Track Generator...
echo Opening http://localhost:8501 in your browser...
echo.
echo Press Ctrl+C to stop the server.
echo.

streamlit run app.py

pause