@echo off
REM  stops the trading-bot web terminal (usage: stop_trader.cmd [port])
set PORT=9000
if not "%~1"=="" set PORT=%~1
set FOUND=0
for /f "tokens=5" %%P in ('netstat -aon ^| findstr ":%PORT% " ^| findstr LISTENING') do (
    set FOUND=1
    echo [stop] killing PID %%P on port %PORT%
    taskkill /PID %%P /F >nul 2>&1
)
if "%FOUND%"=="0" echo [stop] nothing listening on port %PORT%
