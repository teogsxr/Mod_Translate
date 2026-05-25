@echo off
setlocal

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo ERRORE: Python virtualenv non trovato in:
    echo %CD%\.venv\Scripts\python.exe
    echo.
    pause
    exit /b 1
)

if not exist "main.py" (
    echo ERRORE: main.py non trovato in:
    echo %CD%
    echo.
    pause
    exit /b 1
)

echo Avvio CrimsonForge...
echo Cartella: %CD%
echo.

".venv\Scripts\python.exe" "main.py"

echo.
echo CrimsonForge si e' chiuso.
pause
