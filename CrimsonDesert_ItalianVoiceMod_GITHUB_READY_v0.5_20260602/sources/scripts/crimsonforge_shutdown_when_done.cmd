@echo off
setlocal
cd /d "%~dp0"
echo Monitor CrimsonForge: il PC si spegnera' quando i mancanti arrivano a 0.
echo Per annullare uno spegnimento gia' programmato: shutdown /a
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0crimsonforge_shutdown_when_done.ps1" %*
echo.
pause
