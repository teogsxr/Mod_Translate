@echo off
setlocal enabledelayedexpansion
chcp 65001>nul
set "script_dir=%~dp0"
if "!script_dir:~-1,1!"=="\" set "script_dir=!script_dir:~0,-1!"
cd /d "!script_dir!"

:: [Script Author:   "Leo Pasanen"] [Version: 6] [License: MPL 2.0]
:: Embedded in CrimsonForge for WAV->WEM Vorbis conversion.
:: Called by Python: zSound2wem.cmd --samplerate:48000 --channels:1 --out:<dir> <wav_file>

set "wwiseBASE=%WWISEROOT%"
set "wwisePATH=%wwiseBASE%\Authoring\x64\Release\bin\WwiseConsole.exe"
set "ffmpeg="
set samplerate=
set channels=
set volume=
set extra=
set "conversion=Vorbis Quality High"
set "project_dir=%TEMP%\cf_wwise_project"
set "project_name=cf_wwise_project"
set "out="
set "audioformats=.wav .mp3 .ogg .flac"
set CloseOnExit=true

:: Parse command line arguments
for %%a in (%*) do (
    set "validarg=%%~a"
    if "!validarg:~0,2!"=="--" (
        for /f "tokens=1* delims=:" %%b in ("!validarg:~2!") do (
            set "argument=%%b"
            set "value=%%c"
        )
        if "!argument!"=="ffmpeg"      set "ffmpeg=!value!"
        if "!argument!"=="wwise"       set "wwisePATH=!value!"
        if "!argument!"=="samplerate"  set "samplerate=!value!"
        if "!argument!"=="channels"    set "channels=!value!"
        if "!argument!"=="volume"      set "volume=!value!"
        if "!argument!"=="extra"       set "extra=!value!"
        if "!argument!"=="conversion"  set "conversion=!value!"
        if "!argument!"=="out"         set "out=!value!"
        if "!argument!"=="project"     set "project_dir=!value!"
        set CloseOnExit=true
    )
)
for %%p in ("!project_dir!") do set "project_name=%%~nxp"

:: Find Wwise
if exist "!wwisePATH!" goto foundwwise
if defined WWISEROOT (
    set "wwiseBASE=%WWISEROOT%"
    set "wwisePATH=%WWISEROOT%\Authoring\x64\Release\bin\WwiseConsole.exe"
    if exist "!wwisePATH!" goto foundwwise
)

:: Try all drives: Program Files (x86), Audiokinetic root
for /f "tokens=1*" %%a in ('fsutil fsinfo drives 2^>nul')do set "Drives=%%b"
if "!Drives!"=="" set "Drives=C D E F G H"

for %%a in (!Drives!) do (
    if exist "%%a\Audiokinetic\" (
        for /f "delims=" %%b in ('dir "%%a\Audiokinetic" /b /a:d /o:-d /t:c 2^>nul ^| findstr /i "Wwise"') do (
            set "cand=%%a\Audiokinetic\%%b\Authoring\x64\Release\bin\WwiseConsole.exe"
            if exist "!cand!" set "wwisePATH=!cand!" & goto foundwwise
        )
    )
    if exist "%%a\Program Files (x86)\" (
        for /f "delims=" %%b in ('dir "%%a\Program Files (x86)" /b /a:d /o:-d /t:c 2^>nul ^| findstr /i "Wwise"') do (
            set "cand=%%a\Program Files (x86)\%%b\Authoring\x64\Release\bin\WwiseConsole.exe"
            if exist "!cand!" set "wwisePATH=!cand!" & goto foundwwise
        )
    )
)
echo [ERROR] WwiseConsole.exe not found. 1>&2
exit /b 1

:foundwwise
:: Find ffmpeg
if defined ffmpeg if exist "!ffmpeg!" goto foundffmpeg
where /q ffmpeg 2>nul && (set ffmpeg=ffmpeg & goto foundffmpeg)
for /f "tokens=*" %%a in ('dir /b /a:d /o:-d /t:c 2^>nul ^| findstr /i "ffmpeg"') do (
    if exist "%%a\bin\ffmpeg.exe" set "ffmpeg=%%a\bin\ffmpeg.exe" & goto foundffmpeg
)
echo [ERROR] ffmpeg not found. 1>&2
exit /b 1

:foundffmpeg
if "%~1"=="" echo [ERROR] No input file. 1>&2 & exit /b 1

:: Create persistent Wwise project (only once)
if not exist "!project_dir!\!project_name!.wproj" (
    if exist "!project_dir!\" rmdir /s /q "!project_dir!" >nul 2>&1
    "!wwisePATH!" create-new-project "!project_dir!\!project_name!.wproj" --platform Windows --quiet
    if errorlevel 1 (
        echo [ERROR] Wwise project creation failed: !project_dir!\!project_name!.wproj 1>&2
        exit /b 1
    )
)
if not exist "!project_dir!\!project_name!.wproj" (
    echo [ERROR] Wwise project was not created: !project_dir!\!project_name!.wproj 1>&2
    exit /b 1
)

:: Normalize audio with ffmpeg into a clean temp folder.
:: A failed previous conversion must not leak stale WAVs into this run.
if exist audiotemp rmdir /s /q audiotemp >nul 2>&1
md audiotemp >nul 2>&1
set "samplerate_flag="
set "channels_flag="
set "volume_flag="
set "extra_flag="
if not "!samplerate!"=="" set "samplerate_flag=-ar !samplerate! "
if not "!channels!"==""   set "channels_flag=-ac !channels! "
if not "!volume!"==""     set "volume_flag=-filter:a volume=!volume! "
if not "!extra!"==""      set "extra_flag=!extra! "

set /a uid=0
for %%a in (%*) do (
    set "validarg=%%~a"
    if not "!validarg:~0,2!"=="--" (
        set "name_modifier="
        if exist "audiotemp\%%~na.wav" set "name_modifier=!uid!" & set /a uid+=1
        "!ffmpeg!" -hide_banner -loglevel warning -y -i "%%~a" !samplerate_flag!!channels_flag!!volume_flag!!extra_flag!"audiotemp\%%~na!name_modifier!.wav"
    )
)

:: Build .wsources
if exist list.wsources del /f /q list.wsources
(
echo ^<?xml version="1.0" encoding="UTF-8"?^>
echo ^<ExternalSourcesList SchemaVersion="1" Root="!script_dir!\audiotemp"^>
for /f "tokens=* delims=" %%a in ('dir audiotemp /b 2^>nul') do echo     ^<Source Path="%%a" Conversion="!conversion!"/^>
echo ^</ExternalSourcesList^>
) > list.wsources

:: Run Wwise conversion
if "!out!"=="" set "out=!script_dir!"
if not exist "!out!" md "!out!" >nul 2>&1
"!wwisePATH!" convert-external-source "!project_dir!\!project_name!.wproj" --source-file "!script_dir!\list.wsources" --output "!out!" --quiet
if errorlevel 1 (
    echo [ERROR] Wwise convert-external-source failed. 1>&2
    rmdir /s /q audiotemp >nul 2>&1
    del /f /q list.wsources >nul 2>&1
    exit /b 1
)

:: Move output from Windows\ subfolder (Wwise quirk)
if exist "!out!\Windows\*" (
    move /y "!out!\Windows\*" "!out!" >nul 2>&1
    rmdir /s /q "!out!\Windows" >nul 2>&1
)

:: Cleanup
rmdir /s /q audiotemp >nul 2>&1
del /f /q list.wsources >nul 2>&1
exit /b 0
