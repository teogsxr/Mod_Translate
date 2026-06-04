param(
    [switch]$RebuildTargets,
    [int]$RecentMinutes = 10
)

$ErrorActionPreference = "Stop"

$Repo = "C:\Users\matte\Downloads\crimsonforge-latest"
$Python = Join-Path $Repo ".venv\Scripts\python.exe"
$Game = "C:\Program Files (x86)\Steam\steamapps\common\Crimson Desert"
$Progress = Join-Path $env:USERPROFILE ".crimsonforge\tts_patch_progress.json"
$Targets = Join-Path $env:USERPROFILE ".crimsonforge\italian_audio_targets_0006.json"
$Log = "C:\Users\matte\Downloads\crimsonforge.log"

if (-not (Test-Path $Python)) {
    throw "Python CrimsonForge non trovato: $Python"
}

$env:CF_STATUS_REPO = $Repo
$env:CF_STATUS_GAME = $Game
$env:CF_STATUS_PROGRESS = $Progress
$env:CF_STATUS_TARGETS = $Targets
$env:CF_STATUS_LOG = $Log
$env:CF_STATUS_REBUILD = if ($RebuildTargets) { "1" } else { "0" }
$env:CF_STATUS_RECENT_MINUTES = [string]$RecentMinutes

$code = @'
import json
import os
import re
import sys
import contextlib
import io
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

repo = os.environ["CF_STATUS_REPO"]
game = os.environ["CF_STATUS_GAME"]
progress_path = Path(os.environ["CF_STATUS_PROGRESS"])
targets_path = Path(os.environ["CF_STATUS_TARGETS"])
log_path = Path(os.environ["CF_STATUS_LOG"])
rebuild = os.environ.get("CF_STATUS_REBUILD") == "1"
recent_minutes = int(os.environ.get("CF_STATUS_RECENT_MINUTES", "10") or "10")

sys.path.insert(0, repo)

def norm_key(group, path):
    return f"{group}:{path.replace(chr(92), '/').lower()}"

def read_text_gently(path, encoding="utf-8", errors="strict", attempts=6, delay=0.15):
    last_error = None
    for attempt in range(attempts):
        try:
            return path.read_text(encoding=encoding, errors=errors)
        except (PermissionError, OSError) as exc:
            last_error = exc
            if attempt == attempts - 1:
                break
            time.sleep(delay * (attempt + 1))
    raise RuntimeError(f"File occupato, riprova tra qualche secondo: {path} ({last_error})")

def load_targets():
    if targets_path.is_file() and not rebuild:
        data = json.loads(read_text_gently(targets_path, encoding="utf-8"))
        if data.get("version") == 1 and data.get("game", "").lower() == game.lower():
            return data["targets"], False

    # CrimsonForge logs verbosely while importing/scanning. Hide that noise so
    # this status script stays readable.
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        from core.vfs_manager import VfsManager
        from core.audio_index import build_paloc_lookup, build_audio_index

        vfs = VfsManager(game)
        groups = vfs.list_package_groups()
        lookup = build_paloc_lookup(vfs, groups)
        entries = build_audio_index(vfs, ["0006"], lookup)
    targets = []
    for e in entries:
        text = (e.text_translations.get("it") or "").strip()
        if not text:
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
        return {}, None
    state = json.loads(read_text_gently(progress_path, encoding="utf-8"))
    completed = {}
    for key, game_state in (state.get("games") or {}).items():
        if key.replace("\\", "/").lower().endswith("/crimson desert"):
            completed = game_state.get("completed") or {}
            break
    return completed, datetime.fromtimestamp(progress_path.stat().st_mtime)

def parse_recent_rate(completed, target_keys):
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=recent_minutes)
    count = 0
    for key in target_keys:
        rec = completed.get(key)
        if not isinstance(rec, dict):
            continue
        ts = rec.get("completed_at")
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt >= cutoff:
            count += 1
    rate = count / max(recent_minutes, 1)
    return count, rate

def log_summary():
    if not log_path.is_file():
        return "Log non trovato.", [], ""
    text = read_text_gently(log_path, encoding="utf-8", errors="replace", attempts=3, delay=0.05)
    lines = text.splitlines()
    last_processing = ""
    for line in reversed(lines):
        if "Processing " in line and ".wem" in line:
            m = re.search(r"Processing (.+?\.wem)", line)
            last_processing = m.group(1) if m else line
            break
    problem_patterns = (
        "[ERROR   ]",
        "Traceback",
        "WAV to WEM conversion failed",
        "Batch TTS completed with",
    )
    problems = [line for line in lines if any(p in line for p in problem_patterns)]
    return f"{log_path.stat().st_mtime_ns}", problems[-8:], last_processing

def intro_block(target):
    path = (target.get("path") or "").lower()
    match = re.search(r"intro_(\d+)", path)
    return match.group(1) if match else ""

def completion_label(done_count, total_count, noun):
    if total_count <= 0:
        return f"{noun}: non trovato"
    if done_count >= total_count:
        return f"{noun}: DOPPIATO ({done_count}/{total_count})"
    if done_count <= 0:
        return f"{noun}: non ancora doppiato (0/{total_count})"
    return f"{noun}: in corso ({done_count}/{total_count}, mancano {total_count - done_count})"

targets, rebuilt = load_targets()
completed, progress_mtime = load_completed()
target_keys = {t["key"] for t in targets}
active_targets = [t for t in targets if t.get("category") != "Text Dialogue"]
excluded_targets = [t for t in targets if t.get("category") == "Text Dialogue"]

def is_completed_key(key):
    rec = completed.get(key)
    return isinstance(rec, dict) and not rec.get("force_regenerate_reason")

target_keys = {t["key"] for t in active_targets}
done = [t for t in active_targets if is_completed_key(t["key"])]
remaining = [t for t in active_targets if not is_completed_key(t["key"])]
intro_targets = [t for t in targets if intro_block(t)]
intro_done = [t for t in intro_targets if is_completed_key(t["key"])]
intro_remaining = [t for t in intro_targets if not is_completed_key(t["key"])]
prologue_blocks = {"0450", "0500"}
prologue_targets = [t for t in intro_targets if intro_block(t) in prologue_blocks]
prologue_done = [t for t in prologue_targets if is_completed_key(t["key"])]
recent_count, rate = parse_recent_rate(completed, target_keys)
eta = ""
if rate > 0 and remaining:
    minutes_left = len(remaining) / rate
    eta_time = datetime.now() + timedelta(minutes=minutes_left)
    eta = f"ETA circa: {eta_time.strftime('%H:%M')} ({minutes_left:.0f} min)"
else:
    eta = "ETA: non stimabile ora"

_, recent_problems, last_processing = log_summary()

print("")
print("=== CrimsonForge TTS status ===")
print("Ora:", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
print("Target cache:", "ricostruita ora" if rebuilt else "ok")
if progress_mtime:
    print("Progress aggiornato:", progress_mtime.strftime("%H:%M:%S"))
print("")
print(f"Totale audio italiani attivi: {len(active_targets):,}".replace(",", "."))
if excluded_targets:
    print(f"Esclusi Text Dialogue: {len(excluded_targets):,}".replace(",", "."))
print(f"Completati:            {len(done):,}".replace(",", "."))
print(f"Mancanti:              {len(remaining):,}".replace(",", "."))
print(f"Fatti ultimi {recent_minutes} min: {recent_count} ({rate:.1f}/min)")
print(eta)
if last_processing:
    print("Ultimo file visto:", last_processing)
print("")
print("Intro / prologo:")
print("  " + completion_label(len(prologue_done), len(prologue_targets), "Prologo iniziale 0450+0500"))
print("  " + completion_label(len(intro_done), len(intro_targets), "Intro totale"))
if intro_targets:
    print("  Blocchi intro:")
    block_counts = Counter(intro_block(t) for t in intro_targets)
    block_done = Counter(intro_block(t) for t in intro_done)
    for block in sorted(block_counts):
        total = block_counts[block]
        done_count = block_done.get(block, 0)
        status = "OK" if done_count >= total else "..."
        print(f"    intro_{block}: {done_count}/{total} {status}")
print("")
print("Mancanti per categoria:")
for category, count in Counter(t["category"] for t in remaining).most_common(20):
    print(f"  {category}: {count}")
if recent_problems:
    print("")
    print("Problemi recenti nel log:")
    for line in recent_problems:
        print("  " + line[-220:])
else:
    print("")
    print("Problemi recenti nel log: nessuno")
print("")
'@

$code | & $Python -
