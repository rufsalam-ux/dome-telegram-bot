@echo off
cd /d "%~dp0"
echo Starting DOME Control Center...
python -m tools.dome_admin
pause
