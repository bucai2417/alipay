@echo off
title Alipay CodePay Server
cd /d "%~dp0"

set "PORT=5000"
if defined ALIPAY_PORT set "PORT=%ALIPAY_PORT%"
set "VENV=.venv"
set "PYEXE=%VENV%\Scripts\python.exe"

echo ============================================
echo   Alipay CodePay + Card Shop Launcher
echo   Folder: %CD%
echo ============================================
echo.

rem ---------- 1) port check ----------
netstat -ano | findstr /R /C:":%PORT% " | findstr /I "LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo [INFO] Port %PORT% is already in use. The server may already be running.
    echo        Opening http://127.0.0.1:%PORT% ...
    start "" "http://127.0.0.1:%PORT%"
    echo.
    echo To restart: close the existing server window, or run stop.bat first.
    echo.
    pause
    exit /b 1
)

rem ---------- 2) virtual env ----------
if exist "%PYEXE%" (
    echo [1/3] Virtual environment OK
    "%PYEXE%" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Existing .venv uses Python older than 3.10.
        echo         Delete the .venv folder and run start.bat again.
        pause
        exit /b 1
    )
    goto :deps
)

echo [1/3] Creating virtual environment...
set "PYCMD="
where py >nul 2>&1
if not errorlevel 1 (
    py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
    if not errorlevel 1 set "PYCMD=py -3"
)
if not defined PYCMD (
    where python >nul 2>&1
    if not errorlevel 1 (
        python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
        if not errorlevel 1 set "PYCMD=python"
    )
)
if not defined PYCMD goto :nopython

%PYCMD% -m venv "%VENV%"
if errorlevel 1 goto :err

:deps

rem ---------- 3) dependencies ----------
"%PYEXE%" -c "import flask, requests, qrcode, PIL, cryptography, rsa, alipay, bleach, waitress" >nul 2>&1
if errorlevel 1 (
    echo [2/3] Installing dependencies, first run may take a few minutes...
    "%PYEXE%" -m pip install --upgrade pip
    "%PYEXE%" -m pip install -r requirements.txt
    if errorlevel 1 goto :err
) else (
    echo [2/3] Dependencies OK
)

rem ---------- 4) start ----------
echo [3/3] Starting server: http://127.0.0.1:%PORT%
echo        Admin panel: http://127.0.0.1:%PORT%/admin/
echo        Press Ctrl+C to stop.
echo.
chcp 65001 >nul
"%PYEXE%" app.py
set "CODE=%ERRORLEVEL%"
echo.
if not "%CODE%"=="0" (
    echo [ERROR] Server exited with code %CODE%.
    echo         Common causes: port in use / missing dependencies / config error.
    echo         Log file: data\logs\app.log
) else (
    echo Server stopped.
)
echo.
pause
exit /b %CODE%

:nopython
echo [ERROR] Python 3.10 or newer was not found in PATH.
echo         Install Python 3.10+ and check "Add Python to PATH".
echo         Download: https://www.python.org/downloads/
echo.
pause
exit /b 1

:err
echo.
echo [ERROR] Setup failed. Check the messages above (network or permission issue).
echo         Try manually:
echo            python -m venv .venv
echo            .venv\Scripts\python.exe -m pip install -r requirements.txt
echo.
pause
exit /b 1
