#!/bin/bash
# TGJU Platform launcher — works on Linux, macOS, and git-bash (Windows)
set -e
cd "$(dirname "$0")"

# Find python
PY="${TGJU_PYTHON:-}"
if [ -z "$PY" ] && [ -x ".venv/bin/python" ]; then PY=".venv/bin/python"; fi
if [ -z "$PY" ] && [ -x ".venv/Scripts/python.exe" ]; then PY=".venv/Scripts/python.exe"; fi
if [ -z "$PY" ]; then PY="python"; fi

# Create venv if missing
if [ ! -x ".venv/bin/python" ] && [ ! -x ".venv/Scripts/python.exe" ]; then
  echo "  [1/3] Creating virtual environment..."
  "$PY" -m venv .venv
  if [ -x ".venv/bin/python" ]; then PY=".venv/bin/python"; else PY=".venv/Scripts/python.exe"; fi
fi

# Install deps if fastapi missing
if ! "$PY" -c "import fastapi" 2>/dev/null; then
  echo "  [2/3] Installing dependencies..."
  "$PY" -m pip install -r requirements.txt --quiet
fi

# Run
echo "  [3/3] Starting TGJU Platform..."
echo "         Open http://127.0.0.1:8791 in your browser"
cd tgju
exec "$PY" tgju_platform.py
