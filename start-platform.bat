@echo off
REM ============================================================
REM  TGJU Platform - ONE-CLICK launcher (Windows)
REM  Double-click this file. It:
REM    1. finds or creates a Python virtual environment
REM    2. auto-installs all requirements on first run (or when missing)
REM    3. starts the platform and opens the dashboard in your browser
REM  No command-line knowledge needed.
REM ============================================================
setlocal EnableExtensions
cd /d "%~dp0"
title TGJU Platform

set "PYEXE="
if defined TGJU_PYTHON set "PYEXE=%TGJU_PYTHON%"

REM ---- 1. find a system python (3.11+ preferred) ---------------
if not defined PYEXE (
  where py >nul 2>nul && set "PYEXE=py -3"
)
if not defined PYEXE (
  where python >nul 2>nul && set "PYEXE=python"
)
if not defined PYEXE (
  echo.
  echo  [!] Python was not found on this computer.
  echo      Please install Python 3.11+ from https://www.python.org/downloads/
  echo      IMPORTANT: tick "Add Python to PATH" during install, then run this file again.
  echo.
  pause
  exit /b 1
)

REM ---- 2. create venv if missing -------------------------------
if not exist ".venv\Scripts\python.exe" (
  echo  [*] First run detected - creating isolated environment...
  %PYEXE% -m venv .venv
  if errorlevel 1 (
    echo  [!] Could not create the environment. Run this file as Administrator once, or check disk space.
    pause
    exit /b 1
  )
)

set "VPY=.venv\Scripts\python.exe"

REM ---- 3. install/refresh requirements automatically -----------
echo  [*] Checking required packages...
"%VPY%" -c "import fastapi, uvicorn, yaml" >nul 2>nul
if errorlevel 1 (
  echo  [*] Installing requirements - please wait ^(one time only^)...
  "%VPY%" -m pip install --upgrade pip --quiet
  "%VPY%" -m pip install -r requirements.txt --quiet
  if errorlevel 1 (
    echo  [!] Package installation failed. Check your internet connection and run this file again.
    pause
    exit /b 1
  )
  echo  [OK] Packages installed.
)

REM ---- 4. start platform + open browser ------------------------
echo  [OK] Starting TGJU platform...
echo  [i] Dashboard will open at: http://127.0.0.1:8791
start "" "http://127.0.0.1:8791" >nul 2>&1
cd tgju
"%VPY%" tgju_platform.py
if errorlevel 1 (
  echo.
  echo  [!] The platform exited with an error. Press any key to close.
  pause >nul
)
