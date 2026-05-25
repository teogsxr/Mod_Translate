param(
    [string]$GamePath = "C:\Program Files (x86)\Steam\steamapps\common\Crimson Desert",
    [switch]$NoBackup
)

$ErrorActionPreference = "Stop"
$PackageDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$PythonCommand = $null

$BundledPython = Join-Path $PackageDir "installer\python\python.exe"
if (Test-Path $BundledPython) {
    $PythonCommand = @($BundledPython)
} else {
    foreach ($candidate in @("py", "python")) {
        try {
            if ($candidate -eq "py") {
                & py -3 --version *> $null
                if ($LASTEXITCODE -eq 0) { $PythonCommand = @("py", "-3"); break }
            } else {
                & python --version *> $null
                if ($LASTEXITCODE -eq 0) { $PythonCommand = @("python"); break }
            }
        } catch {}
    }
}

if (-not $PythonCommand) {
    throw "Python 3 non trovato e runtime portatile mancante dal pacchetto. Riscarica il pacchetto completo."
}

if (-not (Test-Path (Join-Path $GamePath "meta\0.papgt"))) {
    $manual = Read-Host "Percorso gioco non trovato. Inserisci la cartella Crimson Desert"
    if ($manual) { $GamePath = $manual }
}

$args = @()
if ($PythonCommand.Count -gt 1) { $args += $PythonCommand[1] }
$args += @(
    (Join-Path $PackageDir "installer\apply_patch.py"),
    "--game-path", $GamePath,
    "--package-dir", $PackageDir
)
if ($NoBackup) { $args += "--no-backup" }

& $PythonCommand[0] @args
if ($LASTEXITCODE -ne 0) {
    throw "Installazione fallita."
}
