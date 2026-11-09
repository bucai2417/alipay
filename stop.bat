@echo off
title Stop Alipay CodePay Server
cd /d "%~dp0"

set "PORT=5000"
if defined ALIPAY_PORT set "PORT=%ALIPAY_PORT%"
set "FOUND="

echo ============================================
echo   Stop project server on port %PORT%
echo ============================================
echo.

for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%PORT% " ^| findstr /I "LISTENING"') do (
    set "FOUND=1"
    echo Found PID %%P listening on port %PORT%
    wmic process where "ProcessId=%%P" get CommandLine 2>nul | findstr /I "app.py" >nul
    if errorlevel 1 (
        echo   Skipped: command line does not contain app.py.
    ) else (
        taskkill /PID %%P /F >nul 2>&1
        if errorlevel 1 (
            echo   Failed. Run this script as Administrator.
        ) else (
            echo   Stopped.
        )
    )
    echo.
)

if not defined FOUND (
    echo Port %PORT% is not in use.
    echo.
)

pause
exit /b 0
