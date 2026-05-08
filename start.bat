@echo off
REM ─────────────────────────────────────────────────────────────────────────
REM  Bank Statement → Excel  |  Windows launcher
REM  Double-click this file to open the app in your browser.
REM  Python 3.11+ must be installed. Get it from https://www.python.org/
REM ─────────────────────────────────────────────────────────────────────────

echo Starting Bank Statement to Excel...
echo.

REM ── Find Python ───────────────────────────────────────────────────────────
REM Windows often installs the "py" launcher even when "python" isn't on PATH.
REM Try py first, then python, then python3.

set PYTHON=
py --version >nul 2>&1
if not errorlevel 1 (
    set PYTHON=py
    goto :found_python
)
python --version >nul 2>&1
if not errorlevel 1 (
    set PYTHON=python
    goto :found_python
)
python3 --version >nul 2>&1
if not errorlevel 1 (
    set PYTHON=python3
    goto :found_python
)

echo ERROR: Python was not found.
echo.
echo Please install Python 3.11 or later from https://www.python.org/
echo During installation, tick "Add Python to PATH" on the first screen.
echo.
pause
exit /b 1

:found_python
echo Found Python: %PYTHON%
echo.

REM ── Install dependencies ──────────────────────────────────────────────────
REM Safe to run repeatedly — skips anything already installed.
echo Installing dependencies (fast after the first time)...
%PYTHON% -m pip install -e ".[ui]" --quiet
if errorlevel 1 (
    echo.
    echo ERROR: Dependency installation failed. Check your internet connection.
    pause
    exit /b 1
)

echo.
echo Opening the app in your browser...
echo (Close this window or press Ctrl+C to stop the app.)
echo.

REM Use "py -m streamlit" so it always uses the same Python we found above.
%PYTHON% -m streamlit run app.py --server.headless false
pause
