param(
    [string]$GamePath = "C:\Program Files (x86)\Steam\steamapps\common\Crimson Desert",
    [switch]$NoPrompt
)

$ErrorActionPreference = "Stop"
$PackageDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
. (Join-Path $PackageDir "installer\game_path_helpers.ps1")

$ResolvedGamePath = Select-CrimsonGamePath -InitialPath $GamePath -NoPrompt:$NoPrompt
if (-not $ResolvedGamePath) {
    throw "Percorso gioco non trovato. Avvia di nuovo senza -NoPrompt e indica la cartella Crimson Desert."
}

$store = Get-CrimsonStoreName $ResolvedGamePath
$exe = Join-Path $ResolvedGamePath "bin64\CrimsonDesert.exe"
$files = @(
    "meta\0.papgt",
    "0006\0.pamt",
    "0006\0.paz",
    "0006\1.paz"
)

$fileReports = @()
foreach ($rel in $files) {
    $path = Join-Path $ResolvedGamePath $rel
    if (Test-Path -LiteralPath $path) {
        $item = Get-Item -LiteralPath $path
        $hash = Get-FileHash -Algorithm SHA256 -LiteralPath $path
        $fileReports += [pscustomobject]@{
            path = $rel
            exists = $true
            size = $item.Length
            sha256 = $hash.Hash.ToLowerInvariant()
            last_write_time = $item.LastWriteTime.ToString("s")
        }
    } else {
        $fileReports += [pscustomobject]@{
            path = $rel
            exists = $false
            size = $null
            sha256 = $null
            last_write_time = $null
        }
    }
}

$exeInfo = $null
if (Test-Path -LiteralPath $exe) {
    $exeItem = Get-Item -LiteralPath $exe
    $exeHash = Get-FileHash -Algorithm SHA256 -LiteralPath $exe
    $exeInfo = [pscustomobject]@{
        path = "bin64\CrimsonDesert.exe"
        exists = $true
        file_version = $exeItem.VersionInfo.FileVersion
        product_version = $exeItem.VersionInfo.ProductVersion
        size = $exeItem.Length
        sha256 = $exeHash.Hash.ToLowerInvariant()
    }
} else {
    $exeInfo = [pscustomobject]@{ path = "bin64\CrimsonDesert.exe"; exists = $false }
}

$report = [pscustomobject]@{
    created_at = (Get-Date).ToString("s")
    mod_version = "0.3-hotfix-20260526"
    installer_compatibility_update = "2026-05-27"
    game_path = $ResolvedGamePath
    detected_store = $store
    support_note = if ($store -eq "Steam") { "Steam e la piattaforma testata." } elseif ($store -eq "Xbox App / Microsoft Store") { "Xbox App non e supportata in scrittura finche non abbiamo verificato archivi e controlli integrita." } else { "Store non Steam non testato: serve verifica prima della pubblicazione come compatibile." }
    exe = $exeInfo
    files = $fileReports
}

$outDir = Join-Path $PackageDir "compatibility_reports"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$safeStore = ($store -replace '[^A-Za-z0-9]+', '_').Trim('_')
$out = Join-Path $outDir ("crimson_desert_compat_{0}_{1}.json" -f $safeStore, (Get-Date -Format "yyyyMMdd_HHmmss"))
$report | ConvertTo-Json -Depth 5 | Set-Content -Path $out -Encoding UTF8

Write-Host ""
Write-Host "Report compatibilita creato:" -ForegroundColor Green
Write-Host $out
Write-Host ""
if ($store -eq "Xbox App / Microsoft Store") {
    Write-Host "Nota: Xbox App non e attualmente supportata per l'installazione della patch." -ForegroundColor Yellow
    Write-Host "Se il gioco non parte dopo una patch precedente, ripara/verifica il gioco dall'app Xbox e rimuovi eventuali backup modificati."
}
