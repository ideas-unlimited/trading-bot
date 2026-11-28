@echo off
REM ============================================================
REM  demo-bots launcher  (usage: start_bots.cmd [interval_seconds])
REM
REM  Runs the five research books as $1,000 demo wallets, each with
REM  $1,000, on Binance 4h bars.  NOTHING IS ORDERED: the wallets are
REM  simulated, and every number is reproducible from the cached bars.
REM
REM  - A  trend gated 3%   (SMA 30/50 + Damiani/STC, 3% risk)
REM  - B  trend max 5%     (same signals at 5% risk)
REM  - C  plain cross 3%   (no filters)
REM  - D  room gate 3%     (plain cross + measured-move room, median channel)
REM  - E  trail 1.5 ATR 3% (gated entries, Chandelier trail ends the trade)
REM
REM  Each cycle: delta-sync bars -> replay each book -> write
REM  runs\autobooks\status.json + per-book trades.csv/equity.csv and
REM  append the block to logsots_<timestamp>.log
REM
REM  Restart-safe: the replay is deterministic, so re-running this
REM  script cannot corrupt a wallet.  'since deploy' is measured from
REM  the first cycle, which is the honest dry-run number.
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

REM ---- is a loop already running?  one instance is enough ----
set RUNNING=0
for /f "tokens=*" %%P in ('powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*bot auto*' } | Select-Object -ExpandProperty ProcessId"') do (
    set RUNNING=1
    echo [restart] stopping old demo-bot loop, PID %%P
    taskkill /PID %%P /F >nul 2>&1
)
if "%RUNNING%"=="1" powershell -NoProfile -Command "Start-Sleep -Seconds 1" >nul 2>&1

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set TS=%%i
set LOGFILE=%~dp0logs\bots_%TS%.log

powershell -NoProfile -Command "$f = Get-ChildItem '%~dp0logs' -Filter 'bots_*.log' | Sort-Object Name -Descending; if ($f.Count -gt 10) { $f | Select-Object -Skip 10 | Remove-Item -Force }" >nul 2>&1

echo [start] demo books A/B/C/D/E, $1,000 each, 4h, binance
echo [start] every %INTERVAL%s  log: %LOGFILE%

powershell -NoProfile -Command "Start-Process -FilePath '%PY%' -ArgumentList '-u','-m','bot','auto','--interval','%INTERVAL%' -WorkingDirectory '%~dp0' -WindowStyle Hidden -RedirectStandardOutput '%LOGFILE%' -RedirectStandardError '%LOGFILE%.err'"

powershell -NoProfile -Command "Start-Sleep -Seconds 6" >nul 2>&1
echo [ok] first cycle written to: runs\autobooks\status.json

endlocal