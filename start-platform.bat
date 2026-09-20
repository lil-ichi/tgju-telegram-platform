@echo off
REM TGJU Platform launcher - thin wrapper around tgju\tgju_start.py
REM
REM   start-platform.bat              start (foreground) + health check
REM   start-platform.bat start -d     start detached (background)
REM   start-platform.bat status       pid, port, /healthz, paths
REM   start-platform.bat logs -f      tail the app log
REM   start-platform.bat stop         graceful stop
REM   start-platform.bat doctor       environment self-check
REM
REM The Python launcher creates .venv, installs requirements.txt, resolves the
REM port and waits for /healthz, so any Python 3.11+ interpreter works here.
setlocal
cd /d "%~dp0"
title TGJU Platform

set "PY="
if defined TGJU_PYTHON if exist "%TGJU_PYTHON%" set "PY=%TGJU_PYTHON%"
if not defined PY if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY (
  where py >nul 2>nul
  if not errorlevel 1 set "PY=py -3"
)
if not defined PY (
  where python >nul 2>nul
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  echo  [!] Python not found. Install Python 3.11+ and add it to PATH.
  pause
  exit /b 1
)

%PY% "tgju\tgju_start.py" %*
if errorlevel 1 (
  echo.
  echo  [!] The launcher reported a problem - see the messages above.
  pause
)
