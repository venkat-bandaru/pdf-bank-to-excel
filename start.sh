#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  Bank Statement → Excel  |  Mac / Linux launcher
#  Double-click this file (or run it in Terminal) to open the app in your
#  browser. Python 3.11+ must be installed.
#  Mac: install from https://www.python.org/ or via Homebrew (brew install python)
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

echo "Starting Bank Statement to Excel..."
echo ""

# Find a suitable Python 3.11+ interpreter
PYTHON=""
for cmd in python3.12 python3.11 python3 python; do
    if command -v "$cmd" &>/dev/null; then
        version=$("$cmd" -c 'import sys; print(sys.version_info >= (3,11))' 2>/dev/null)
        if [ "$version" = "True" ]; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo "ERROR: Python 3.11 or later was not found."
    echo ""
    echo "Mac:   install from https://www.python.org/  or  brew install python"
    echo "Linux: sudo apt install python3.11   (Ubuntu/Debian)"
    echo ""
    read -rp "Press Enter to close..."
    exit 1
fi

echo "Using $($PYTHON --version)"
echo ""
echo "Installing dependencies (fast after the first time)..."
"$PYTHON" -m pip install -e ".[ui]" --quiet

echo ""
echo "Opening the app in your browser..."
echo "(Press Ctrl+C in this window to stop the app.)"
echo ""

"$PYTHON" -m streamlit run app.py --server.headless false
