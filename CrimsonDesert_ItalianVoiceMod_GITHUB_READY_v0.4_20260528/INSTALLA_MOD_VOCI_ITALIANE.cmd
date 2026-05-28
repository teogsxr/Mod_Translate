@echo off
setlocal
cd /d "%~dp0"
echo Crimson Desert Italian Voice Mod - installazione voci italiane
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\install_patch.ps1"
echo.
pause
