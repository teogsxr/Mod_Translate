@echo off
setlocal
cd /d "%~dp0"
echo Crimson Desert Italian Voice Mod - controllo prerequisiti
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\verifica_prerequisiti.ps1"
echo.
pause
