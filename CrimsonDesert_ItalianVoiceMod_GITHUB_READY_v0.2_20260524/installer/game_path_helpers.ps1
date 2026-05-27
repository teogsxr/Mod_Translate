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
    param([string]$GamePath)
    $lower = $GamePath.ToLowerInvariant()
    if ($lower -like "*\steamapps\common\*") { return "Steam" }
    if ($lower -like "*\xboxgames\*" -or $lower -like "*\windowsapps\*") { return "Xbox App / Microsoft Store" }
    if ($lower -like "*\epic games\*" -or $lower -like "*\epicgames\*") { return "Epic Games" }
    if ($lower -like "*\gog galaxy\games\*" -or $lower -like "*\gog games\*") { return "GOG" }
    return "Percorso manuale / store sconosciuto"
}

function Get-CrimsonCandidatePaths {
    $raw = New-Object System.Collections.Generic.List[string]
    $raw.Add("C:\Program Files (x86)\Steam\steamapps\common\Crimson Desert")
    $raw.Add("C:\Program Files\Steam\steamapps\common\Crimson Desert")
    $raw.Add("C:\XboxGames\Crimson Desert")
    $raw.Add("C:\XboxGames\Crimson Desert\Content")
    $raw.Add("C:\XboxGames\CrimsonDesert")
    $raw.Add("C:\XboxGames\CrimsonDesert\Content")
    $raw.Add("C:\Program Files\Epic Games\Crimson Desert")
    $raw.Add("C:\Program Files\Epic Games\CrimsonDesert")
    $raw.Add("C:\GOG Games\Crimson Desert")
    $raw.Add("C:\Program Files (x86)\GOG Galaxy\Games\Crimson Desert")

    $steamLibraryFiles = @(
        "C:\Program Files (x86)\Steam\steamapps\libraryfolders.vdf",
        "C:\Program Files\Steam\steamapps\libraryfolders.vdf"
    )
    foreach ($libraryFile in $steamLibraryFiles) {
        if (Test-Path -LiteralPath $libraryFile) {
            $matches = Select-String -LiteralPath $libraryFile -Pattern '"path"\s+"([^"]+)"'
            foreach ($match in $matches) {
                $library = $match.Matches[0].Groups[1].Value.Replace("\\", "\")
                if ($library) { $raw.Add((Join-Path $library "steamapps\common\Crimson Desert")) }
            }
        }
    }

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
    param(
        [string]$InitialPath,
        [switch]$NoPrompt
    )

    $resolvedInitial = Test-CrimsonGamePath $InitialPath
    if ($resolvedInitial) { return $resolvedInitial }

    $candidates = Get-CrimsonCandidatePaths
    if ($candidates.Count -eq 1) { return $candidates[0] }
    if ($candidates.Count -gt 1 -and -not $NoPrompt) {
        Write-Host "Ho trovato piu installazioni possibili di Crimson Desert:"
        for ($i = 0; $i -lt $candidates.Count; $i++) {
            $store = Get-CrimsonStoreName $candidates[$i]
            Write-Host ("  [{0}] {1} ({2})" -f ($i + 1), $candidates[$i], $store)
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
