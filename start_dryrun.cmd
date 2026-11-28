@echo off
REM ============================================================
REM  dry-run sleeve runner  (usage: start_dryrun.cmd [interval_seconds])
REM
REM  Replays EVERY active promotion manifest as a $1,000 simulated
REM  wallet on closed 4h bars, one folder per sleeve.  Same Engine as
REM  the backtest, so stop, target and time exit all fire.
REM  NO ORDERS: no exchange key is read.
REM
REM  The set of sleeves follows promotions/<env>/*.json: promoting or
REM  demoting one changes the next cycle without editing this script.
REM  Default poll is 15 minutes; each sleeve reads its manifest risk profile.
REM
REM  State: runs/<env>/<strategy>/dryrun/status.json
REM         plus <SYMBOL>.trades.csv and <SYMBOL>.equity.csv.
REM  The replay is deterministic, so restarting is safe: deploy
REM  equity stays pinned to the first cycle.
REM ============================================================
setlocal
cd /d "%~dp0"
set PYTHONPATH=%~dp0src
set PY=%~dp0.venv\Scripts\python.exe
set INTERVAL=900
if not "%~1"=="" set INTERVAL=%~1

if not exist "%PY%" (
    echo [error] venv python not found: %PY%
    pause
    exit /b 1
)

if not exist logs mkdir logs

REM ---- one loop is enough: stop an existing dry-run run -------
set RUNNING=0
for /f "tokens=*" %%P in ('powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*bot dryrun*' } | Select-Object -ExpandProperty ProcessId"') do (
    set RUNNING=1
    echo [restart] stopping old dry-run loop, PID %%P
    taskkill /PID %%P /F >nul 2>&1
)
if "%RUNNING%"=="1" powershell -NoProfile -Command "Start-Sleep -Seconds 1" >nul 2>&1

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set TS=%%i
set LOGFILE=%~dp0logs\dryrun_%TS%.log

powershell -NoProfile -Command "$f = Get-ChildItem '%~dp0logs' -Filter 'dryrun_*.log' | Sort-Object Name -Descending; if ($f.Count -gt 10) { $f | Select-Object -Skip 10 | Remove-Item -Force }" >nul 2>&1

echo [start] active manifests  4h  binance, $1,000 simulated per sleeve
echo [start] every %INTERVAL%s  log: %LOGFILE%

powershell -NoProfile -Command "Start-Process -FilePath '%PY%' -ArgumentList '-u','-m','bot','dryrun','--all','--poll','%INTERVAL%' -WorkingDirectory '%~dp0' -WindowStyle Hidden -RedirectStandardOutput '%LOGFILE%' -RedirectStandardError '%LOGFILE%.err'"

powershell -NoProfile -Command "Start-Sleep -Seconds 6" >nul 2>&1
echo [ok] status: runs\%BOT_ENV%\<strategy>\dryrun\status.json

endlocal
