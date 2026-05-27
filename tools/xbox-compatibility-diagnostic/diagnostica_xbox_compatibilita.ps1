param(
    [string]$GamePath = "",
    [switch]$NoPrompt
)

$ErrorActionPreference = "Stop"

function Test-CrimsonGamePath {
    param([string]$Path)
    if (-not $Path) { return $null }
    $candidate = $Path.Trim('"')
    if (-not (Test-Path -LiteralPath $candidate)) { return $null }

    $directPapgt = Join-Path $candidate "meta\0.papgt"
    if (Test-Path -LiteralPath $directPapgt) { return (Resolve-Path -LiteralPath $candidate).Path }

    $content = Join-Path $candidate "Content"
    $contentPapgt = Join-Path $content "meta\0.papgt"
    if (Test-Path -LiteralPath $contentPapgt) { return (Resolve-Path -LiteralPath $content).Path }

    return $null
}

function Get-CrimsonStoreName {
    param([string]$Path)
    $lower = $Path.ToLowerInvariant()
    if ($lower -like "*\steamapps\common\*") { return "Steam" }
    if ($lower -like "*\xboxgames\*" -or $lower -like "*\windowsapps\*") { return "Xbox App / Microsoft Store" }
    if ($lower -like "*\epic games\*" -or $lower -like "*\epicgames\*") { return "Epic Games" }
    if ($lower -like "*\gog galaxy\games\*" -or $lower -like "*\gog games\*") { return "GOG" }
    return "Percorso manuale / store sconosciuto"
}

function Get-CrimsonCandidatePaths {
    $raw = New-Object System.Collections.Generic.List[string]
    $raw.Add("C:\XboxGames\Crimson Desert")
    $raw.Add("C:\XboxGames\Crimson Desert\Content")
    $raw.Add("C:\XboxGames\CrimsonDesert")
    $raw.Add("C:\XboxGames\CrimsonDesert\Content")
    $raw.Add("C:\Program Files\WindowsApps\Crimson Desert")
    $raw.Add("C:\Program Files (x86)\Steam\steamapps\common\Crimson Desert")
    $raw.Add("C:\Program Files\Steam\steamapps\common\Crimson Desert")
    $raw.Add("C:\Program Files\Epic Games\Crimson Desert")
    $raw.Add("C:\Program Files\Epic Games\CrimsonDesert")
    $raw.Add("C:\GOG Games\Crimson Desert")

    if (Test-Path -LiteralPath "C:\XboxGames") {
        Get-ChildItem -LiteralPath "C:\XboxGames" -Directory -ErrorAction SilentlyContinue | ForEach-Object {
            $raw.Add($_.FullName)
            $raw.Add((Join-Path $_.FullName "Content"))
        }
    }

    $valid = New-Object System.Collections.Generic.List[string]
    foreach ($item in $raw) {
        $resolved = Test-CrimsonGamePath $item
        if ($resolved -and -not $valid.Contains($resolved)) { $valid.Add($resolved) }
    }
    return @($valid)
}

function Select-CrimsonGamePath {
    param([string]$InitialPath)
    $resolvedInitial = Test-CrimsonGamePath $InitialPath
    if ($resolvedInitial) { return $resolvedInitial }

    $candidates = Get-CrimsonCandidatePaths
    if ($candidates.Count -eq 1) { return $candidates[0] }

    if ($candidates.Count -gt 1 -and -not $NoPrompt) {
        Write-Host "Ho trovato piu installazioni possibili di Crimson Desert:"
        for ($i = 0; $i -lt $candidates.Count; $i++) {
            Write-Host ("  [{0}] {1} ({2})" -f ($i + 1), $candidates[$i], (Get-CrimsonStoreName $candidates[$i]))
        }
        $choice = Read-Host "Scegli numero, oppure premi Invio per inserire un percorso manuale"
        if ($choice -match '^\d+$') {
            $index = [int]$choice - 1
            if ($index -ge 0 -and $index -lt $candidates.Count) { return $candidates[$index] }
        }
    }

    if ($NoPrompt) { return $null }
    $manual = Read-Host "Inserisci la cartella di Crimson Desert"
    return (Test-CrimsonGamePath $manual)
}

function Get-FileReport {
    param(
        [string]$BasePath,
        [string]$RelativePath
    )
    $path = Join-Path $BasePath $RelativePath
    if (Test-Path -LiteralPath $path) {
        $item = Get-Item -LiteralPath $path
        $hash = Get-FileHash -Algorithm SHA256 -LiteralPath $path
        return [pscustomobject]@{
            path = $RelativePath
            exists = $true
            size = $item.Length
            sha256 = $hash.Hash.ToLowerInvariant()
            last_write_time = $item.LastWriteTime.ToString("s")
        }
    }
    return [pscustomobject]@{
        path = $RelativePath
        exists = $false
        size = $null
        sha256 = $null
        last_write_time = $null
    }
}

$ResolvedGamePath = Select-CrimsonGamePath -InitialPath $GamePath
if (-not $ResolvedGamePath) {
    throw "Percorso gioco non trovato. Indica la cartella che contiene meta\0.papgt e 0006\0.pamt."
}

$store = Get-CrimsonStoreName $ResolvedGamePath
$files = @(
    "meta\0.papgt",
    "0006\0.pamt",
    "0006\0.paz",
    "0006\1.paz"
)

$exeReport = Get-FileReport -BasePath $ResolvedGamePath -RelativePath "bin64\CrimsonDesert.exe"
if ($exeReport.exists) {
    $exeItem = Get-Item -LiteralPath (Join-Path $ResolvedGamePath "bin64\CrimsonDesert.exe")
    $exeReport | Add-Member -NotePropertyName file_version -NotePropertyValue $exeItem.VersionInfo.FileVersion
    $exeReport | Add-Member -NotePropertyName product_version -NotePropertyValue $exeItem.VersionInfo.ProductVersion
}

$fileReports = foreach ($rel in $files) { Get-FileReport -BasePath $ResolvedGamePath -RelativePath $rel }

$packageFolders = @()
Get-ChildItem -LiteralPath $ResolvedGamePath -Directory -ErrorAction SilentlyContinue | ForEach-Object {
    if (Test-Path -LiteralPath (Join-Path $_.FullName "0.pamt")) {
        $packageFolders += $_.Name
    }
}

$report = [pscustomobject]@{
    created_at = (Get-Date).ToString("s")
    tool = "Crimson Desert Italian Voice Mod Xbox compatibility diagnostic"
    tool_version = "2026-05-27"
    note = "Questo report non modifica il gioco. Serve a capire se Xbox App/Microsoft Store usa archivi compatibili con la patch Steam."
    detected_store = $store
    game_path = $ResolvedGamePath
    package_folders_with_pamt = $packageFolders
    exe = $exeReport
    files = $fileReports
}

$outDir = Join-Path $PSScriptRoot "reports"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$jsonPath = Join-Path $outDir "crimson_xbox_compat_report_$stamp.json"
$txtPath = Join-Path $outDir "crimson_xbox_compat_report_$stamp.txt"

$report | ConvertTo-Json -Depth 6 | Set-Content -Path $jsonPath -Encoding UTF8

$lines = @()
$lines += "Crimson Desert Italian Voice Mod - Xbox compatibility diagnostic"
$lines += "Created: $($report.created_at)"
$lines += "Detected store: $store"
$lines += "Game path: $ResolvedGamePath"
$lines += ""
$lines += "Executable:"
$lines += ($exeReport | ConvertTo-Json -Depth 4)
$lines += ""
$lines += "Required archives:"
$lines += ($fileReports | ConvertTo-Json -Depth 4)
$lines += ""
$lines += "Package folders with 0.pamt:"
$lines += ($packageFolders -join ", ")
$lines += ""
$lines += "Attach this TXT or JSON file to a GitHub Issue or Nexus comment."
$lines += "This diagnostic does not patch or modify the game."
$lines | Set-Content -Path $txtPath -Encoding UTF8

Write-Host ""
Write-Host "Report creati:" -ForegroundColor Green
Write-Host $jsonPath
Write-Host $txtPath
Write-Host ""
Write-Host "Carica il file .txt o .json nella Issue GitHub/Nexus. Il tool non ha modificato il gioco." -ForegroundColor Yellow
