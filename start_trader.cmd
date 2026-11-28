@echo off
REM ============================================================
REM  trading-bot web terminal launcher  (usage: start_trader.cmd [port])
REM
REM  RESTART BEHAVIOUR: kills anything already listening on the port
REM  before launching, so after every code change you just re-run
REM  this script - no need to find and kill the old process.
REM
REM  - serves http://127.0.0.1:PORT (default 9000) in a minimised
REM    background window
REM  - every stdout/stderr line (incl. tracebacks) is logged to
REM    logs\web_<timestamp>.log for troubleshooting
REM ============================================================
setlocal
cd /d "%~dp0"
set PYTHONPATH=%~dp0src
REM lab mode: this laptop's dashboard lists every strategy, promoted or not.
REM The server never sets this, so demo/live there list only promoted ones.
set BOT_LAB=1

set HOST=127.0.0.1
set PORT=9000
if not "%~1"=="" set PORT=%~1
set PY=%~dp0.venv\Scripts\python.exe

if not exist "%PY%" (
    echo [error] venv python not found: %PY%
    echo         create it first:  python -m venv .venv
    pause
    exit /b 1
)

if not exist logs mkdir logs

REM ---- restart: kill anything listening on the port ----
set KILLED=0
for /f "tokens=5" %%P in ('netstat -aon ^| findstr ":%PORT% " ^| findstr LISTENING') do (
    set KILLED=1
    echo [restart] stopping old instance, PID %%P
    taskkill /PID %%P /F >nul 2>&1
)
if "%KILLED%"=="1" powershell -NoProfile -Command "Start-Sleep -Seconds 1" >nul 2>&1

REM ---- timestamped log file ----
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set TS=%%i
set LOGFILE=%~dp0logs\web_%TS%.log

REM ---- keep only the 10 newest logs ----
powershell -NoProfile -Command "$f = Get-ChildItem '%~dp0logs' -Filter 'web_*.log' | Sort-Object Name -Descending; if ($f.Count -gt 10) { $f | Select-Object -Skip 10 | Remove-Item -Force }" >nul 2>&1

echo [start] trading-bot web terminal
echo [start] url : http://%HOST%:%PORT%/
echo [start] log : %LOGFILE%

REM ---- launch detached, everything logged ----
start "trading-bot web %PORT%" /MIN cmd /c ""%PY%" -u -m bot web --host %HOST% --port %PORT% >> "%LOGFILE%" 2>&1"

REM ---- give the server a moment, then open the browser ----
powershell -NoProfile -Command "Start-Sleep -Seconds 2" >nul 2>&1
start "" http://%HOST%:%PORT%/

endlocal
