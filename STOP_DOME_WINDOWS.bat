@echo off
cd /d "%~dp0"
docker compose down --remove-orphans
docker rm -f dome-bot >nul 2>&1
echo DOME stopped.
pause
