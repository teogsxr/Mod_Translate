param(
    [int]$IntervalSeconds = 300,
    [int]$ShutdownDelaySeconds = 60,
    [switch]$NoShutdown,
    [switch]$Once,
    [switch]$RebuildTargets
)

$ErrorActionPreference = "Stop"

$Repo = "C:\Users\matte\Downloads\crimsonforge-latest"
$Python = Join-Path $Repo ".venv\Scripts\python.exe"
$Game = "C:\Program Files (x86)\Steam\steamapps\common\Crimson Desert"
$Progress = Join-Path $env:USERPROFILE ".crimsonforge\tts_patch_progress.json"
$Targets = Join-Path $env:USERPROFILE ".crimsonforge\italian_audio_targets_0006.json"
$LogFile = Join-Path $env:USERPROFILE ".crimsonforge\shutdown_when_done.log"

if (-not (Test-Path $Python)) {
    throw "Python CrimsonForge non trovato: $Python"
}

$env:CF_MONITOR_REPO = $Repo
$env:CF_MONITOR_GAME = $Game
$env:CF_MONITOR_PROGRESS = $Progress
$env:CF_MONITOR_TARGETS = $Targets
$env:CF_MONITOR_REBUILD = if ($RebuildTargets) { "1" } else { "0" }

function Write-MonitorLog {
    param([string]$Message)
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $Message"
    Write-Host $line
    Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8
}

$code = @'
import contextlib
import io
import json
import os
import sys
from datetime import datetime
from pathlib import Path

repo = os.environ["CF_MONITOR_REPO"]
game = os.environ["CF_MONITOR_GAME"]
progress_path = Path(os.environ["CF_MONITOR_PROGRESS"])
targets_path = Path(os.environ["CF_MONITOR_TARGETS"])
rebuild = os.environ.get("CF_MONITOR_REBUILD") == "1"
sys.path.insert(0, repo)

def norm_key(group, path):
    return f"{group}:{path.replace(chr(92), '/').lower()}"

def load_targets():
    if targets_path.is_file() and not rebuild:
        data = json.loads(targets_path.read_text(encoding="utf-8"))
        if data.get("version") == 1 and data.get("game", "").lower() == game.lower():
            return data["targets"], False

    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        from core.vfs_manager import VfsManager
        from core.audio_index import build_paloc_lookup, build_audio_index

        vfs = VfsManager(game)
        groups = vfs.list_package_groups()
        lookup = build_paloc_lookup(vfs, groups)
        entries = build_audio_index(vfs, ["0006"], lookup)

    targets = []
    for e in entries:
        if not (e.text_translations.get("it") or "").strip():
            continue
        targets.append({
            "key": norm_key(e.package_group, e.entry.path),
            "path": e.entry.path,
            "category": e.category or "Other",
        })
    payload = {
        "version": 1,
        "game": game,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "targets": targets,
    }
    targets_path.parent.mkdir(parents=True, exist_ok=True)
    targets_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return targets, True

def load_completed():
    if not progress_path.is_file():
        return {}, ""
    state = json.loads(progress_path.read_text(encoding="utf-8"))
    completed = {}
    for key, game_state in (state.get("games") or {}).items():
        if key.replace("\\", "/").lower().endswith("/crimson desert"):
            completed = game_state.get("completed") or {}
            break
    stamp = datetime.fromtimestamp(progress_path.stat().st_mtime).strftime("%H:%M:%S")
    return completed, stamp

targets, rebuilt = load_targets()
completed, progress_stamp = load_completed()
remaining = [t for t in targets if t["key"] not in completed]
payload = {
    "total": len(targets),
    "completed": len(targets) - len(remaining),
    "remaining": len(remaining),
    "progress_stamp": progress_stamp,
    "rebuilt": rebuilt,
}
print(json.dumps(payload, ensure_ascii=False))
'@

Write-MonitorLog "Monitor avviato. Controllo ogni $IntervalSeconds secondi. Shutdown delay: $ShutdownDelaySeconds secondi. NoShutdown=$NoShutdown"
Write-MonitorLog "Per annullare uno spegnimento gia' programmato: shutdown /a"

while ($true) {
    try {
        $json = $code | & $Python -
        $state = $json | ConvertFrom-Json
        Write-MonitorLog "Completati $($state.completed)/$($state.total), mancanti $($state.remaining), progress $($state.progress_stamp)"

        if ([int]$state.remaining -le 0 -and [int]$state.total -gt 0) {
            if ($NoShutdown) {
                Write-MonitorLog "JOB FINITO. NoShutdown attivo: non spengo il PC."
                break
            }
            Write-MonitorLog "JOB FINITO. Spengo il PC tra $ShutdownDelaySeconds secondi."
            shutdown /s /t $ShutdownDelaySeconds /c "CrimsonForge TTS completato: spegnimento automatico."
            break
        }

        if ($Once) {
            Write-MonitorLog "Controllo singolo completato. Il job non e' ancora finito."
            break
        }
    }
    catch {
        Write-MonitorLog "Errore monitor: $($_.Exception.Message)"
        if ($Once) {
            break
        }
    }

    Start-Sleep -Seconds $IntervalSeconds
}
