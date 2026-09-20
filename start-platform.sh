#!/bin/bash
# TGJU Platform launcher — thin wrapper around tgju/tgju_start.py
#
#   ./start-platform.sh              # start (foreground) + health check
#   ./start-platform.sh start -d     # start detached
#   ./start-platform.sh status       # pid, port, /healthz, paths
#   ./start-platform.sh logs -f      # tail the app log
#   ./start-platform.sh stop         # graceful stop
#   ./start-platform.sh doctor       # environment self-check
#
# The Python launcher does the heavy lifting (creates .venv, installs
# requirements.txt, resolves the port, waits for /healthz), so any Python
# 3.11+ interpreter works here.
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

PY="${TGJU_PYTHON:-}"
if [ -z "$PY" ] && [ -x ".venv/bin/python" ]; then PY="$ROOT/.venv/bin/python"; fi
if [ -z "$PY" ] && [ -x ".venv/Scripts/python.exe" ]; then PY="$ROOT/.venv/Scripts/python.exe"; fi
if [ -z "$PY" ]; then PY="$(command -v python3 || command -v python || true)"; fi

if [ -z "$PY" ]; then
  echo "  [!] Python 3.11+ not found. Install it (with the venv module) or set TGJU_PYTHON."
  exit 1
fi

exec "$PY" "$ROOT/tgju/tgju_start.py" "$@"
