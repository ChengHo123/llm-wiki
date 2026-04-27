@echo off
setlocal

title LLM Wiki - Startup
cd /d "%~dp0"

echo ========================================
echo  LLM Wiki - Starting Services
echo ========================================
echo.

:: Check if Docker Desktop is running
docker info >nul 2>&1
if %errorlevel% neq 0 (
    echo [*] Docker not running. Starting Docker Desktop...
    start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"

    echo [*] Waiting for Docker to be ready...
    :wait_docker
    timeout /t 5 /nobreak >nul
    docker info >nul 2>&1
    if %errorlevel% neq 0 goto wait_docker
    echo [OK] Docker is ready.
    echo.
)

echo [*] Starting all services...
docker compose up -d

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Failed to start services. Check docker compose logs for details.
    pause
    exit /b 1
)

echo.
echo ========================================
echo  Services started successfully!
echo ========================================
echo.
echo   Frontend   : http://localhost:3000
echo   Backend    : http://localhost:8000
echo   LiteLLM    : http://localhost:4000
echo   pgAdmin    : http://localhost:5050
echo   Ngrok UI   : http://localhost:4040
echo.
echo [*] Checking service status...
echo.
docker compose ps

echo.
echo Press any key to close this window...
pause >nul
