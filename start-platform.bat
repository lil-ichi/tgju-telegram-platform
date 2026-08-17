@echo off
REM ============================================================
REM  TGJU Telegram Platform launcher (Windows) — portable
REM  Resolution order:
REM    1. %TGJU_PYTHON%   (explicit override, if set)
REM    2. .venv\Scripts\python.exe inside this repo (if present)
REM    3. python on PATH
REM ============================================================
setlocal
cd /d "%~dp0"

set "PYEXE="
if defined TGJU_PYTHON set "PYEXE=%TGJU_PYTHON%"
if not defined PYEXE if exist ".venv\Scripts\python.exe" set "PYEXE=.venv\Scripts\python.exe"
if not defined PYEXE set "PYEXE=python"

cd /d "%~dp0tgju"
"%PYEXE%" tgju_platform.py
if errorlevel 1 (
  echo.
  echo [start-platform] TGJU platform exited with error code %errorlevel%
  pause
)