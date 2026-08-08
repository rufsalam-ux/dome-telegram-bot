@echo off
setlocal
cd /d "%~dp0"
echo [1/5] Stopping every old DOME/Bilingua container...
for /f "delims=" %%i in ('docker ps -a --format "{{.Names}}" ^| findstr /i "dome bilingua"') do docker rm -f %%i >nul 2>&1
echo [2/5] Removing old DOME/Bilingua images...
for /f "tokens=3" %%i in ('docker images --format "{{.Repository}} {{.Tag}} {{.ID}}" ^| findstr /i "dome bilingua"') do docker rmi -f %%i >nul 2>&1
echo [3/5] Clearing only generated slide cache (profiles/progress remain)...
if exist storage\slide-cache rmdir /s /q storage\slide-cache
echo [4/5] Building v30 without Docker cache...
docker compose build --no-cache
if errorlevel 1 goto :error
echo [5/5] Starting v30...
docker compose up
exit /b 0
:error
echo BUILD FAILED. Copy the red error text and send it.
pause
exit /b 1
