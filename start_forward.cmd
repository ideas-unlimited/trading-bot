@echo off
REM ============================================================
REM  forward sleeve runner  (usage: start_forward.cmd [interval_seconds])
REM
REM  Replays ONE promoted sleeve as a $1,000 simulated wallet on
REM  closed 4h bars.  Same Engine as the backtest, so stop, target
REM  and time exit all fire.  NO ORDERS: no exchange key is read.
REM
REM  Default: capitulation_dip_uni / UNI-USDT / 4h, 5% risk,
REM  50% position cap, every 15 minutes.  Outside BOT_LAB the
REM  command refuses unless promotions/paper names the sleeve.
REM
REM  State: runs/paper/forward/status.json plus trades/equity CSVs.
REM  The replay is deterministic, so restarting is safe: deploy
REM  equity stays pinned to the first cycle.
REM ============================================================
setlocal
cd /d "%~dp0"
set PYTHONPATH=%~dp0src
set PY=%~dp0.venv\Scripts\python.exe
set INTERVAL=900
if not "%~1"=="" set INTERVAL=%~1
set STRATEGY=capitulation_dip_uni
set SYMBOL=UNI/USDT

if not exist "%PY%" (
    echo [error] venv python not found: %PY%
    pause
    exit /b 1
)

if not exist logs mkdir logs

REM ---- one loop is enough: stop an existing forward run -------
set RUNNING=0
for /f "tokens=*" %%P in ('powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*bot forward*' } | Select-Object -ExpandProperty ProcessId"') do (
    set RUNNING=1
    echo [restart] stopping old forward loop, PID %%P
    taskkill /PID %%P /F >nul 2>&1
)
if "%RUNNING%"=="1" powershell -NoProfile -Command "Start-Sleep -Seconds 1" >nul 2>&1

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set TS=%%i
set LOGFILE=%~dp0logs\forward_%TS%.log

powershell -NoProfile -Command "$f = Get-ChildItem '%~dp0logs' -Filter 'forward_*.log' | Sort-Object Name -Descending; if ($f.Count -gt 10) { $f | Select-Object -Skip 10 | Remove-Item -Force }" >nul 2>&1

echo [start] %STRATEGY%  %SYMBOL%  4h  binance, $1,000 simulated
echo [start] every %INTERVAL%s  log: %LOGFILE%

powershell -NoProfile -Command "Start-Process -FilePath '%PY%' -ArgumentList '-u','-m','bot','forward','--strategy','%STRATEGY%','--symbol','%SYMBOL%','--interval','4h','--risk-per-trade','0.05','--max-position-pct','0.5','--poll','%INTERVAL%' -WorkingDirectory '%~dp0' -WindowStyle Hidden -RedirectStandardOutput '%LOGFILE%' -RedirectStandardError '%LOGFILE%.err'"

powershell -NoProfile -Command "Start-Sleep -Seconds 6" >nul 2>&1
echo [ok] first cycle written to: runs\paper\forward\status.json

endlocal
