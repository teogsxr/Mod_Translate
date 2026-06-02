param(
    [string]$GamePath = "C:\Program Files (x86)\Steam\steamapps\common\Crimson Desert"
)

$ErrorActionPreference = "Stop"
$PackageDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
. (Join-Path $PackageDir "installer\game_path_helpers.ps1")
$Manifest = Join-Path $PackageDir "data\manifest.json"
$PayloadZip = Join-Path $PackageDir "data\wem_replacements_0006.zip"
$PayloadDir = Join-Path $PackageDir "data\wem_replacements_0006"
$ExpectedBuildId = "23374070"
$ExpectedExeVersion = "1.0.0.1492"

function Write-Ok($Message) { Write-Host "[OK] $Message" -ForegroundColor Green }
function Write-Warn($Message) { Write-Host "[ATTENZIONE] $Message" -ForegroundColor Yellow }
function Write-Bad($Message) { Write-Host "[ERRORE] $Message" -ForegroundColor Red }
function Format-GB($Bytes) { "{0:N1} GB" -f ($Bytes / 1GB) }

$Errors = 0
$Warnings = 0
$PythonCommand = $null

$BundledPython = Join-Path $PackageDir "installer\python\python.exe"
if (Test-Path $BundledPython) {
    $PythonCommand = @($BundledPython)
}

if (-not $PythonCommand) {
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

if ($PythonCommand) {
    $args = @()
    if ($PythonCommand.Count -gt 1) { $args += $PythonCommand[1] }
    $args += "--version"
    $Version = & $PythonCommand[0] @args 2>&1
    Write-Ok "Python disponibile: $Version"
} else {
    Write-Bad "Python 3 non trovato e runtime portatile mancante dal pacchetto. Riscarica il pacchetto completo."
    $Errors++
}

if (Test-Path $Manifest) { Write-Ok "Manifest trovato" } else { Write-Bad "Manifest mancante"; $Errors++ }
if (Test-Path $PayloadDir) {
    $PayloadBytes = (Get-ChildItem -LiteralPath $PayloadDir -Recurse -File | Measure-Object -Property Length -Sum).Sum
    Write-Ok "Payload audio in cartella trovato: $(Format-GB $PayloadBytes)"
} elseif (Test-Path $PayloadZip) {
    Write-Ok "Payload audio zip trovato: $(Format-GB ((Get-Item $PayloadZip).Length))"
} else {
    Write-Bad "Payload audio mancante"
    $Errors++
}

$ResolvedGamePath = Select-CrimsonGamePath -InitialPath $GamePath
if ($ResolvedGamePath) {
    $GamePath = $ResolvedGamePath
} else {
    Write-Warn "Percorso gioco non trovato. Puoi installare solo indicando una cartella valida di Crimson Desert."
}

if (Test-Path (Join-Path $GamePath "meta\0.papgt")) {
    Write-Ok "Percorso gioco valido: $GamePath"
    $StoreName = Get-CrimsonStoreName $GamePath
    if ($StoreName -eq "Steam") {
        Write-Ok "Store rilevato: Steam"
    } elseif ($StoreName -eq "Xbox App / Microsoft Store") {
        Write-Bad "Store rilevato: Xbox App / Microsoft Store. Questa versione e bloccata per sicurezza: e stato segnalato errore all'avvio dopo patch."
        Write-Warn "Esegui DIAGNOSTICA_COMPATIBILITA.cmd e inviaci il report. Per ripristinare, usa Ripara/Verifica dall'app Xbox."
        $Errors++
    } else {
        Write-Warn "Store rilevato: $StoreName. Non testato ufficialmente; installazione possibile solo con conferma esplicita."
        $Warnings++
    }
    $Exe = Join-Path $GamePath "bin64\CrimsonDesert.exe"
    if (Test-Path $Exe) {
        $ExeVersion = (Get-Item $Exe).VersionInfo.FileVersion
        if ($ExeVersion -eq $ExpectedExeVersion) { Write-Ok "Versione eseguibile testata: $ExeVersion" }
        else { Write-Warn "Versione eseguibile diversa: $ExeVersion (testata: $ExpectedExeVersion)"; $Warnings++ }
    } else { Write-Warn "CrimsonDesert.exe non trovato in bin64"; $Warnings++ }

    if ($StoreName -eq "Steam") {
        $SteamApps = Split-Path -Parent (Split-Path -Parent $GamePath)
        $ManifestPath = Join-Path $SteamApps "appmanifest_3321460.acf"
        if (Test-Path $ManifestPath) {
            $BuildLine = Select-String -Path $ManifestPath -Pattern '"buildid"\s+"([^"]+)"' | Select-Object -First 1
            if ($BuildLine -and $BuildLine.Matches[0].Groups[1].Value -eq $ExpectedBuildId) { Write-Ok "Steam buildid testato: $ExpectedBuildId" }
            elseif ($BuildLine) { $Found = $BuildLine.Matches[0].Groups[1].Value; Write-Warn "Steam buildid diverso: $Found (testata: $ExpectedBuildId)"; $Warnings++ }
            else { Write-Warn "Buildid Steam non letto dal manifest"; $Warnings++ }
        } else { Write-Warn "Manifest Steam non trovato: $ManifestPath"; $Warnings++ }
    }

    $NeedBackup = @(
        Join-Path $GamePath "meta\0.papgt"
        Join-Path $GamePath "0006\0.pamt"
        Join-Path $GamePath "0006\0.paz"
        Join-Path $GamePath "0006\1.paz"
    )
    $BackupBytes = 0
    foreach ($Path in $NeedBackup) {
        if (Test-Path $Path) { $BackupBytes += (Get-Item $Path).Length }
        else { Write-Bad "File richiesto mancante: $Path"; $Errors++ }
    }
    $Drive = Get-PSDrive -Name ([System.IO.Path]::GetPathRoot($GamePath).Substring(0,1))
    $RecommendedFree = $BackupBytes + 2GB
    if ($Drive.Free -ge $RecommendedFree) { Write-Ok "Spazio libero sufficiente: $(Format-GB $Drive.Free) (backup stimato: $(Format-GB $BackupBytes))" }
    else { Write-Warn "Spazio libero basso: $(Format-GB $Drive.Free). Consigliati almeno $(Format-GB $RecommendedFree)."; $Warnings++ }

    if ($PythonCommand -and $StoreName -ne "Xbox App / Microsoft Store") {
        Write-Host ""
        Write-Host "Verifico compatibilita base con dry-run..."
        $DryArgs = @()
        if ($PythonCommand.Count -gt 1) { $DryArgs += $PythonCommand[1] }
        $DryArgs += @(
            (Join-Path $PackageDir "installer\apply_patch.py"),
            "--game-path", $GamePath,
            "--package-dir", $PackageDir,
            "--dry-run"
        )
        if ($StoreName -ne "Steam") { $DryArgs += "--allow-untested-store" }
        & $PythonCommand[0] @DryArgs
        if ($LASTEXITCODE -eq 0) { Write-Ok "Dry-run compatibilita completato" }
        else { Write-Bad "Dry-run compatibilita fallito"; $Errors++ }
    }
} else {
    Write-Warn "Percorso gioco non verificato. L'installer lo richiedera al momento dell'installazione."
    $Warnings++
}

if ($PythonCommand -and (Test-Path $Manifest) -and ((Test-Path $PayloadDir) -or (Test-Path $PayloadZip))) {
    Write-Host ""
    Write-Host "Verifico integrita payload audio, puo richiedere qualche minuto..."
    $CheckCode = @'
import hashlib, json, sys, zipfile
from pathlib import Path
package = Path(sys.argv[1])
manifest = json.loads((package / "data" / "manifest.json").read_text(encoding="utf-8"))
payload_dir = package / "data" / "wem_replacements_0006"
payload_zip = package / "data" / "wem_replacements_0006.zip"
if payload_dir.is_dir():
    for item in manifest["entries"]:
        path = payload_dir / item["path"]
        if not path.is_file():
            raise SystemExit(f"Payload mancante: {item['path']}")
        h = hashlib.sha256(path.read_bytes()).hexdigest()
        if h != item["sha256"]:
            raise SystemExit(f"SHA256 diverso: {item['path']}")
    print(f"Payload OK: {len(manifest['entries'])} WEM")
elif payload_zip.is_file():
    with zipfile.ZipFile(payload_zip) as zf:
        bad = zf.testzip()
        if bad:
            raise SystemExit(f"Zip payload corrotto: {bad}")
        names = set(zf.namelist())
        for item in manifest["entries"]:
            if item["path"] not in names:
                raise SystemExit(f"Payload mancante: {item['path']}")
        print(f"Payload OK: {len(names)} WEM")
else:
    raise SystemExit("Payload audio mancante")
'@
    $TempCheck = Join-Path $env:TEMP "crimson_mod_payload_check.py"
    Set-Content -Path $TempCheck -Value $CheckCode -Encoding UTF8
    $args = @()
    if ($PythonCommand.Count -gt 1) { $args += $PythonCommand[1] }
    $args += @($TempCheck, $PackageDir)
    & $PythonCommand[0] @args
    Remove-Item -Path $TempCheck -Force -ErrorAction SilentlyContinue
    if ($LASTEXITCODE -eq 0) { Write-Ok "Integrita payload confermata" }
    else { Write-Bad "Integrita payload fallita"; $Errors++ }
}

Write-Host ""
if ($Errors -gt 0) { Write-Bad "Verifica conclusa con $Errors errore/i e $Warnings avviso/i. Non installare finche gli errori non sono risolti."; exit 1 }
if ($Warnings -gt 0) { Write-Warn "Verifica conclusa con $Warnings avviso/i. Puoi installare, ma leggi gli avvisi sopra."; exit 0 }
Write-Ok "Tutto pronto per installare."
