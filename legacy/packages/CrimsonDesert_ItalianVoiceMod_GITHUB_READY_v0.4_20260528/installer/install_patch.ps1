param(
    [string]$GamePath = "C:\Program Files (x86)\Steam\steamapps\common\Crimson Desert",
    [switch]$NoBackup,
    [switch]$AllowUntestedStore
)

$ErrorActionPreference = "Stop"
$PackageDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
. (Join-Path $PackageDir "installer\game_path_helpers.ps1")
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

$ResolvedGamePath = Select-CrimsonGamePath -InitialPath $GamePath
if (-not $ResolvedGamePath) {
    throw "Percorso gioco non valido o non trovato. Deve contenere meta\0.papgt e 0006\0.pamt."
}
$GamePath = $ResolvedGamePath
$StoreName = Get-CrimsonStoreName $GamePath
Write-Host "Percorso gioco: $GamePath"
Write-Host "Store rilevato: $StoreName"

if ($StoreName -eq "Xbox App / Microsoft Store") {
    throw "Installazione bloccata: la versione Xbox App/Microsoft Store non e supportata in scrittura. Un utente ha segnalato errore all'avvio dopo patch. Usa DIAGNOSTICA_COMPATIBILITA.cmd e invia il report per aggiungere supporto in sicurezza."
}

if ($StoreName -ne "Steam" -and -not $AllowUntestedStore) {
    Write-Host ""
    Write-Host "ATTENZIONE: questo store non e stato testato ufficialmente." -ForegroundColor Yellow
    Write-Host "La patch puo funzionare solo se gli archivi sono compatibili con la build Steam testata."
    Write-Host "Verranno fatti controlli e backup, ma procedi solo se sai ripristinare/verificare il gioco."
    $confirm = Read-Host "Scrivi PROSEGUI per installare su store non testato"
    if ($confirm -ne "PROSEGUI") {
        throw "Installazione annullata."
    }
    $AllowUntestedStore = $true
}

$args = @()
if ($PythonCommand.Count -gt 1) { $args += $PythonCommand[1] }
$args += @(
    (Join-Path $PackageDir "installer\apply_patch.py"),
    "--game-path", $GamePath,
    "--package-dir", $PackageDir
)
if ($NoBackup) { $args += "--no-backup" }
if ($AllowUntestedStore) { $args += "--allow-untested-store" }

& $PythonCommand[0] @args
if ($LASTEXITCODE -ne 0) {
    throw "Installazione fallita."
}
