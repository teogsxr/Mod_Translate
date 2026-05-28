@echo off
setlocal

set "APPDIR=%~dp0crimsonforge-latest"
set "PYTHON=%APPDIR%\.venv\Scripts\python.exe"
set "MAIN=%APPDIR%\main.py"

if not exist "%APPDIR%\" (
    echo ERRORE: cartella CrimsonForge non trovata:
    echo %APPDIR%
    echo.
    pause
    exit /b 1
)

if not exist "%PYTHON%" (
    echo ERRORE: Python virtualenv non trovato:
    echo %PYTHON%
    echo.
    pause
    exit /b 1
)

if not exist "%MAIN%" (
    echo ERRORE: main.py non trovato:
    echo %MAIN%
    echo.
    pause
    exit /b 1
)

cd /d "%APPDIR%"

echo Avvio CrimsonForge patchato...
echo Cartella: %APPDIR%
echo.

"%PYTHON%" "%MAIN%"
set "EXITCODE=%ERRORLEVEL%"

echo.
if not "%EXITCODE%"=="0" (
    echo CrimsonForge si e' chiuso con errore: %EXITCODE%
    echo Log: C:\Users\matte\Downloads\crimsonforge.log
) else (
    echo CrimsonForge si e' chiuso normalmente.
)
echo.
pause
exit /b %EXITCODE%
