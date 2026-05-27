@echo off
setlocal
cd /d "%~dp0"
echo Crimson Desert Italian Voice Mod - diagnostica Xbox / Microsoft Store
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0diagnostica_xbox_compatibilita.ps1"
echo.
pause
