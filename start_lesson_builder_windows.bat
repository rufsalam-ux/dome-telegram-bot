@echo off
cd /d "%~dp0"
echo DOME Lesson Builder is now inside Control Center.
start "" http://127.0.0.1:8766/builder
python -m tools.dome_admin
pause
