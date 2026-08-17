#!/bin/bash
# TGJU Telegram Platform launcher (git-bash / Linux/macOS) — portable
# Resolution order:
#   1. $TGJU_PYTHON   (explicit override, if set)
#   2. .venv/bin/python or .venv/Scripts/python.exe inside this repo
#   3. python on PATH
set -e
cd "$(dirname "$0")"

PYEXE="${TGJU_PYTHON:-}"
if [ -z "$PYEXE" ] && [ -x ".venv/bin/python" ]; then PYEXE=".venv/bin/python"; fi
if [ -z "$PYEXE" ] && [ -x ".venv/Scripts/python.exe" ]; then PYEXE=".venv/Scripts/python.exe"; fi
if [ -z "$PYEXE" ]; then PYEXE="python"; fi

cd platform
exec "$PYEXE" tgju_platform.py