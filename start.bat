@echo off
rem Launcher for the New Product Pre-sales Process Data Admin System
rem NOTE: keep this file pure ASCII (no Chinese) to avoid cmd encoding issues.

set "ROOT=%~dp0"
set "PY=C:\Users\dell\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
set "NODE_DIR=C:\Users\dell\.workbuddy\binaries\node\versions\22.22.2-2"
set "NPM=%NODE_DIR%\npm.cmd"

echo ============================================================
echo   Data Admin System - New Product Pre-sales Process
echo   Backend : FastAPI  http://127.0.0.1:8000
echo   Frontend: Vite dev http://127.0.0.1:5173
echo ============================================================

if not exist "%PY%" (
    echo [ERROR] Python venv not found: %PY%
    echo Please install dependencies first, see README.md
    pause
    exit /b 1
)

rem ---- frontend deps (first run only) ----
if not exist "%ROOT%frontend\node_modules" (
    echo [INFO] First run: installing frontend dependencies, npm install ...
    pushd "%ROOT%frontend"
    call "%NPM%" install
    popd
)

rem ---- start backend (new window) ----
echo [START] Backend FastAPI on port 8000 ...
start "backend-8000" /D "%ROOT%backend" cmd /k ""%PY%" run.py"

rem wait for backend
timeout /t 3 /nobreak >nul

rem ---- clear stale vite dep cache (prevents dev-server startup crash) ----
if exist "%ROOT%frontend\node_modules\.vite" rd /s /q "%ROOT%frontend\node_modules\.vite"

rem ---- start frontend dev server (new window) ----
echo [START] Frontend Vite dev on port 5173 ...
start "frontend-5173" /D "%ROOT%frontend" cmd /k ""%NPM%" run dev"

rem wait for frontend then open browser
timeout /t 8 /nobreak >nul
start http://127.0.0.1:5173

echo.
echo [DONE] System opened in browser: http://127.0.0.1:5173
echo To stop: close the backend-8000 and frontend-5173 windows
echo.
pause
