@echo off
setlocal
cd /d "%~dp0"
echo Crimson Desert Italian Voice Mod - diagnostica compatibilita
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\diagnostica_compatibilita.ps1"
echo.
pause
