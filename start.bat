@echo off
REM POCKET launcher — starts backend (:8001) and frontend (:5174), then waits until the
REM backend actually answers before opening the browser. A cold start can take ~20-30s while
REM RapidOCR/ONNX initialise; opening the UI early shows a login "500" that is really just
REM "backend not up yet".
setlocal
title POCKET Launcher
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python not found in PATH.
  pause
  exit /b 1
)

if not exist .venv (
  echo Creating virtual environment...
  python -m venv .venv
  .venv\Scripts\python -m pip install --upgrade pip -q
  .venv\Scripts\python -m pip install -r requirements.txt -q
)

if not exist frontend\node_modules (
  echo Installing frontend dependencies...
  pushd frontend && npm install --no-audit --no-fund && popd
)

echo Starting backend on http://127.0.0.1:8001 ...
start "POCKET backend" cmd /c ".venv\Scripts\python -m uvicorn backend.main:app --host 127.0.0.1 --port 8001"

echo Waiting for the backend to answer (this can take ~30s on first run)...
powershell -NoProfile -Command "$ok=$false; for($i=0; $i -lt 60; $i++){ try { $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 http://127.0.0.1:8001/health; if ($r.StatusCode -eq 200) { $ok=$true; break } } catch {} Start-Sleep -Seconds 1 }; if ($ok) { exit 0 } else { exit 1 }"
if errorlevel 1 (
  echo.
  echo WARNING: the backend did not answer on http://127.0.0.1:8001 within 60 seconds.
  echo Check the "POCKET backend" window for the traceback before using the app; the UI will
  echo report "backend unreachable" until the API is running.
) else (
  echo Backend is up: http://127.0.0.1:8001/health
)

echo Starting frontend on http://localhost:5174 ...
start "POCKET frontend" cmd /c "cd frontend && npm run dev"

timeout /t 3 /nobreak >nul
start http://localhost:5174

echo.
echo POCKET is starting. Login: inspector / inspector123
echo Close the two spawned windows to stop.
endlocal
