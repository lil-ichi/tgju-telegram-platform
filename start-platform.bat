@echo off
setlocal
cd /d "%~dp0"
title TGJU Platform

REM -- find python --
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
  echo  [!] Python not found. Install Python 3.11+ and add it to PATH.
  pause
  exit /b 1
)

REM -- create venv if missing --
if not exist ".venv\Scripts\python.exe" (
  echo  [1/3] Creating virtual environment...
  %PY% -m venv .venv
)

REM -- install deps --
".venv\Scripts\python.exe" -c "import fastapi" >nul 2>nul
if errorlevel 1 (
  echo  [2/3] Installing dependencies...
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet
)

REM -- run --
echo  [3/3] Starting TGJU Platform...
echo         Open http://127.0.0.1:8791 in your browser
cd tgju
".venv\Scripts\python.exe" tgju_platform.py
pause
