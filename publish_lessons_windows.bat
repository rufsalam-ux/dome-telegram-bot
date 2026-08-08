@echo off
cd /d "%~dp0"
echo Publishing DOME lesson/content changes to GitHub...
git add content config
set MSG=DOME lesson content update
if not "%~1"=="" set MSG=%~1
git commit -m "%MSG%"
if errorlevel 1 echo No new content to commit, or commit failed.
git push origin main
pause
