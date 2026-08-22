@echo off
cd /d "%~dp0"
echo === Running containers ===
docker ps --format "table {{.ID}}\t{{.Names}}\t{{.Status}}"
echo.
echo === DOME logs ===
docker logs --tail 100 dome-bot
pause
