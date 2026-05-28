from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


WORKSPACE = Path(r"C:\Users\matte\Documents\Codex\2026-05-21\ho-provato-a-installare-omnivoice-ma")
FORGE_REPO = Path(r"C:\Users\matte\Downloads\crimsonforge-latest")
TARGETS = Path.home() / ".crimsonforge" / "italian_audio_targets_0006.json"
OVERRIDES = Path.home() / ".crimsonforge" / "audio_text_overrides.json"
STATE = Path.home() / ".crimsonforge" / "headless_voice_recovery_state.json"
LOCK = Path.home() / ".crimsonforge" / "headless_voice_recovery.lock"
LOG = WORKSPACE / "headless_voice_recovery.log"

GAME_FALLBACK = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Crimson Desert")
GROUP = "0006"

MONSTER_VOICE_PREFIXES = (
    "unique_ancientpraevus_",
    "unique_ancientprimus_",
    "unique_ancientpriscus_",
    "unique_antumbraspear_",
    "unique_bastier_",
    "unique_bloodwalkercrussis_",
)

VOICE_GENDER = {
    "nhm": "Human Male",
    "nhw": "Human Female",
    "ndm": "Dwarf Male",
    "ndw": "Dwarf Female",
    "ngm": "Giant Male",
    "ngw": "Giant Female",
}


@dataclass
class PatchJob:
    key: str
    path: str
    category: str
    text: str
    source_text: str
    catalog_key: str
    voice_prefix: str
    npc_class: str
    npc_age: str
    npc_gender: str


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(message: str) -> None:
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {message}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_json(path: Path, default):
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def atomic_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f"{path.stem}_", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def dotted_get(data: dict, key: str, default=None):
    cur = data
    for part in key.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


def normalize_key(group: str, path: str) -> str:
    return f"{group}:{path.replace(chr(92), '/').lower()}"


def clean_tts_text(text: str) -> str:
    text = html.unescape(text or "")

    def repl(match: re.Match) -> str:
        token = match.group(0).strip("{}")
        if "#" not in token:
            return " "
        return token.rsplit("#", 1)[-1].replace("_", " ")

    text = re.sub(r"\{[^{}]*StaticInfo[^{}]*\}", repl, text, flags=re.IGNORECASE)
    parts = re.split(r"\s*<\s*br\s*/?\s*>\s*", text, flags=re.IGNORECASE)
    if len(parts) > 1:
        joined = parts[0].strip()
        for part in (p.strip() for p in parts[1:]):
            if not part:
                continue
            if not joined:
                joined = part
                continue
            if re.search(r"[.!?…,:;]$", joined) or re.match(r"^[,.;:!?]", part):
                separator = " "
            elif re.match(r"^[a-zà-öø-ÿ]", part, flags=re.IGNORECASE) and part[:1].islower():
                separator = " "
            else:
                separator = ". "
            joined += separator + part
        text = joined
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"([,;:])\s*\.", r"\1", text)
    text = re.sub(r"\.\s*([,;:])", r"\1", text)
    text = re.sub(r"([!?])\s*\.", r"\1", text)
    text = re.sub(r"(?<!\.)\.\.(?!\.)", ".", text)
    text = re.sub(r"\.{4,}", "...", text)
    return text.strip()


def acquire_lock() -> None:
    try:
        fd = os.open(str(LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        existing = LOCK.read_text(encoding="utf-8", errors="replace") if LOCK.is_file() else ""
        raise RuntimeError(f"Esiste gia' un job headless in corso: {LOCK} {existing}")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(f"pid={os.getpid()} started={now_iso()}\n")


def release_lock() -> None:
    try:
        LOCK.unlink()
    except FileNotFoundError:
        pass


def ensure_forge_imports() -> None:
    sys.path.insert(0, str(FORGE_REPO))


def read_game_path(config_data: dict) -> Path:
    saved = dotted_get(config_data, "general.last_game_path", "")
    candidates = [Path(saved) if saved else None, GAME_FALLBACK]
    for candidate in candidates:
        if candidate and (candidate / "meta" / "0.papgt").is_file():
            return candidate
    raise FileNotFoundError("Non trovo Crimson Desert con meta\\0.papgt")


def load_config():
    ensure_forge_imports()
    from utils.config import ConfigManager

    config = ConfigManager()
    return config, config.data


def load_progress(game_path: Path):
    from utils.tts_patch_progress import TTSPatchProgress

    return TTSPatchProgress(str(game_path))


def parse_voice_info(path: str, catalog_key: str) -> tuple[str, str, str, str]:
    stem = Path(path).stem.lower().strip("_")
    key = (catalog_key or "").lower().strip("_")
    idx = stem.find(key) if key else -1
    prefix = stem[:idx].strip("_") if idx > 0 else stem
    parts = [p for p in prefix.split("_") if p]
    gender = VOICE_GENDER.get(parts[0], "") if parts else ""
    age = parts[1] if len(parts) >= 2 else ""
    npc_class = parts[2] if len(parts) >= 3 else ""
    return prefix, npc_class, age, gender


def sanitize_profile_name(text: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "_", (text or "").strip()).strip("_")
    return value[:80]


def suggest_profile(job: PatchJob) -> str:
    prefix = job.voice_prefix or Path(job.path).stem
    if prefix.startswith("unique_"):
        return sanitize_profile_name(prefix)
    if job.npc_class:
        return sanitize_profile_name(f"{prefix}_{job.npc_class}")
    return sanitize_profile_name(prefix)


def suggest_voice(job: PatchJob) -> str:
    # Saved clone profiles are optional in OmniVoice. A previous pass tried
    # clone:<unique_npc> for unique voices, but missing profiles return HTTP
    # 404 and skip the line. Use auto consistently so every remaining line can
    # synthesize even when no saved profile exists.
    return "auto"


def is_kliff_player_voice(job: PatchJob) -> bool:
    stem = Path(job.path).stem.lower()
    return stem.startswith("unique_kliff_") and "_player_" in stem


def is_monster_voice_risk(job: PatchJob) -> bool:
    stem = Path(job.path).stem.lower()
    return any(stem.startswith(prefix) for prefix in MONSTER_VOICE_PREFIXES)


def find_stable_kliff_ref_audio() -> str:
    candidates: list[Path] = []
    for folder in (
        WORKSPACE / "single_repairs" / "unique_kliff_intro_0310_player_00002",
        WORKSPACE / "single_repairs" / "unique_kliff_intro_0310_player_00000",
    ):
        candidates.extend(folder.glob("ref_original_*.wav"))
    candidates = [path for path in candidates if path.is_file()]
    if not candidates:
        return ""
    return str(sorted(candidates, key=lambda p: p.stat().st_mtime)[-1])


def load_jobs(progress_state, *, category: str = "", limit: int = 0) -> list[PatchJob]:
    targets_payload = load_json(TARGETS, {"targets": []})
    override_payload = load_json(OVERRIDES, {"entries": {}})
    override_entries = override_payload.get("entries", {})

    jobs: list[PatchJob] = []
    seen = set()
    for target in targets_payload.get("targets", []):
        if not isinstance(target, dict):
            continue
        path = (target.get("path") or "").replace("\\", "/")
        cat = target.get("category") or ""
        if target.get("category") == "Text Dialogue":
            continue
        if category and cat != category:
            continue
        if not path.lower().endswith(".wem"):
            continue
        key = normalize_key(GROUP, path)
        if key in seen:
            continue
        seen.add(key)
        if progress_state.has_completed_record(GROUP, path):
            continue
        rec = override_entries.get(key, {})
        texts = rec.get("texts", {}) if isinstance(rec, dict) else {}
        text = clean_tts_text(texts.get("it") or "")
        if not text:
            continue
        metadata = rec.get("metadata", {}) if isinstance(rec, dict) else {}
        catalog_key = metadata.get("catalog_key") or metadata.get("matched_catalog_key") or ""
        prefix, npc_class, age, gender = parse_voice_info(path, catalog_key)
        jobs.append(PatchJob(
            key=key,
            path=path,
            category=cat,
            text=text,
            source_text=rec.get("source_transcript", "") if isinstance(rec, dict) else "",
            catalog_key=catalog_key,
            voice_prefix=prefix,
            npc_class=npc_class,
            npc_age=age,
            npc_gender=gender,
        ))
        if limit and len(jobs) >= limit:
            break
    return jobs


def build_options(config_data: dict, job: PatchJob, ref_audio_path: str = "") -> dict:
    mode = dotted_get(config_data, "tts.omnivoice_clone_mode", "voice") or "voice"
    if is_kliff_player_voice(job):
        mode = "one_shot"
    elif is_monster_voice_risk(job):
        mode = "voice"
    profile_name = sanitize_profile_name(
        dotted_get(config_data, "tts.omnivoice_profile_name", "") or suggest_profile(job)
    )
    use = lambda name: bool(dotted_get(config_data, f"tts.omnivoice_{name}_use", False))
    value = lambda name: dotted_get(config_data, f"tts.omnivoice_{name}", "Auto") or "Auto"
    return {
        "clone_mode": mode,
        "profile_id": profile_name,
        "ref_audio_path": ref_audio_path,
        "ref_text": "Giles... Rest well." if is_kliff_player_voice(job) else (job.source_text if mode == "one_shot" else ""),
        "language": "Italian",
        "refresh_profile": bool(dotted_get(config_data, "tts.omnivoice_refresh_profile", True)),
        "overwrite_profile": True,
        "num_step": int(dotted_get(config_data, "tts.omnivoice_num_step", 32) or 32),
        "guidance_scale": float(dotted_get(config_data, "tts.omnivoice_guidance_scale", 3.0) or 3.0),
        "denoise": bool(dotted_get(config_data, "tts.omnivoice_denoise", True)),
        "duration": float(dotted_get(config_data, "tts.omnivoice_duration_seconds", 0.0) or 0.0),
        "t_shift": float(dotted_get(config_data, "tts.omnivoice_t_shift", 0.1) or 0.1),
        "position_temperature": float(dotted_get(config_data, "tts.omnivoice_position_temperature", 5.0) or 5.0),
        "class_temperature": float(dotted_get(config_data, "tts.omnivoice_class_temperature", 0.0) or 0.0),
        "param_9": value("gender") if use("gender") else "Auto",
        "param_10": value("age") if use("age") else "Auto",
        "param_11": value("pitch") if use("pitch") else "Auto",
        "param_12": value("style") if use("style") else "Auto",
        "param_13": value("accent") if use("accent") else "Auto",
        "response_format": "wav",
        "stream": False,
    }


def write_state(payload: dict) -> None:
    current = load_json(STATE, {})
    current.update(payload)
    current["updated_at"] = now_iso()
    atomic_json(STATE, current)


def write_audio_result(temp_dir: Path, audio_data: bytes, suffix: str = "wav") -> Path:
    stamp = int(time.time() * 1000)
    path = temp_dir / f"tts_{stamp}.{suffix or 'wav'}"
    path.write_bytes(audio_data)
    return path


def ensure_ref_audio(vfs, entry, job: PatchJob, temp_dir: Path) -> str:
    from core.audio_converter import audio_to_wav, wem_to_wav

    raw = temp_dir / f"ref_{Path(job.path).name}"
    raw.write_bytes(vfs.read_entry_data(entry))
    if raw.suffix.lower() in {".wem", ".bnk"}:
        wav = temp_dir / f"ref_{Path(job.path).stem}.wav"
        converted = wem_to_wav(str(raw), str(wav))
        raw.unlink(missing_ok=True)
        if not converted:
            raise RuntimeError(f"Reference decode fallito: {job.path}")
        return converted
    if raw.suffix.lower() != ".wav":
        wav = temp_dir / f"ref_{Path(job.path).stem}.wav"
        converted = audio_to_wav(str(raw), str(wav))
        raw.unlink(missing_ok=True)
        if not converted:
            raise RuntimeError(f"Reference convert fallito: {job.path}")
        return converted
    return str(raw)


def check_wem_vorbis(data: bytes) -> bool:
    if len(data) < 22 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return False
    fmt = int.from_bytes(data[20:22], "little", signed=False)
    return fmt == 0xFFFF


def patch_jobs(args) -> int:
    ensure_forge_imports()
    from ai.tts_engine import TTSEngine
    from core.audio_converter import wav_to_wem
    from core.pamt_parser import find_file_entry
    from core.repack_engine import ModifiedFile, RepackEngine
    from core.vfs_manager import VfsManager
    from utils.tts_patch_progress import build_patch_signature
    from utils.wwise_installer import is_wwise_installed

    if not is_wwise_installed():
        raise RuntimeError("Wwise non risulta installato: non posso creare WEM Vorbis sicuri.")

    config, config_data = load_config()
    game_path = read_game_path(config_data)
    papgt = game_path / "meta" / "0.papgt"
    progress_state = load_progress(game_path)
    jobs = load_jobs(progress_state, category=args.category, limit=args.limit)

    write_state({
        "running": True,
        "started_at": now_iso(),
        "game": str(game_path),
        "total": len(jobs),
        "processed": 0,
        "patched": 0,
        "errors": 0,
        "skipped": 0,
        "last_path": "",
        "status": "starting",
    })

    if args.dry_run:
        log(f"DRY RUN: {len(jobs)} job pronti.")
        for job in jobs[:20]:
            log(f"  {job.category} | {job.path} | {job.text[:80]}")
        write_state({"running": False, "status": "dry_run", "processed": 0})
        return 0

    if not jobs:
        log("Nessun job mancante con testo italiano da generare.")
        write_state({"running": False, "status": "nothing_to_do"})
        return 0

    engine = TTSEngine()
    engine.initialize_from_config(config_data)
    provider = engine.get_provider("omnivoice_tts")
    status = provider.get_status() if provider else None
    if not status or not status.connected:
        raise RuntimeError(f"OmniVoice non raggiungibile: {status.message if status else 'provider missing'}")

    provider_id = "omnivoice_tts"
    model_id = dotted_get(config_data, "tts.omnivoice_tts_default_model", "omnivoice") or "omnivoice"
    configured_voice = dotted_get(config_data, "tts.omnivoice_voice_mode", "auto") or "auto"
    language = "Italian"
    speed = 1.0

    vfs = VfsManager(str(game_path))
    repack = RepackEngine(str(game_path))
    temp_dir = Path(tempfile.mkdtemp(prefix="cf_headless_voice_"))
    started = time.time()
    patched = 0
    errors: list[str] = []
    backed_up_groups = set()

    try:
        total = len(jobs)
        for index, job in enumerate(jobs, start=1):
            elapsed = max(time.time() - started, 0.001)
            rate = patched / (elapsed / 60.0) if patched else 0.0
            eta = ""
            if rate > 0:
                eta_dt = datetime.now() + timedelta(minutes=(total - index + 1) / rate)
                eta = eta_dt.strftime("%H:%M")
            write_state({
                "status": "processing",
                "processed": index - 1,
                "patched": patched,
                "errors": len(errors),
                "last_path": job.path,
                "rate_per_min": rate,
                "eta": eta,
            })

            if progress_state.has_completed_record(GROUP, job.path):
                log(f"[{index}/{total}] SKIP gia' completato: {job.path}")
                continue

            vfs.invalidate_pamt_cache(GROUP)
            pamt = vfs.load_pamt(GROUP)
            entry = find_file_entry(pamt, job.path)
            if entry is None:
                message = f"{job.path}: entry non trovata nel PAMT corrente"
                errors.append(message)
                log(f"[{index}/{total}] ERROR {message}")
                continue

            ref_audio = ""
            base_options = build_options(config_data, job)
            if base_options.get("clone_mode") == "one_shot":
                ref_audio = find_stable_kliff_ref_audio() if is_kliff_player_voice(job) else ""
                if not ref_audio:
                    ref_audio = ensure_ref_audio(vfs, entry, job, temp_dir)
                base_options["ref_audio_path"] = ref_audio
            voice_id = configured_voice
            if voice_id in {"", "auto", "design:"}:
                voice_id = suggest_voice(job)

            signature = build_patch_signature(
                job.text,
                provider_id,
                model_id,
                voice_id,
                language,
                speed,
                base_options,
            )

            log(f"[{index}/{total}] TTS {job.path} | {job.text[:90]}")
            result = engine.synthesize(
                job.text,
                provider_id,
                model_id,
                voice_id,
                language,
                speed,
                options=base_options,
            )
            if not result.success or not result.audio_data:
                message = f"{job.path}: sintesi fallita: {result.error or 'audio vuoto'}"
                errors.append(message)
                log(f"[{index}/{total}] ERROR {message}")
                continue

            wav_path = write_audio_result(temp_dir, result.audio_data, result.audio_format or "wav")
            if wav_path.suffix.lower() != ".wav":
                from core.audio_converter import audio_to_wav
                converted = audio_to_wav(str(wav_path), str(wav_path.with_suffix(".wav")))
                if not converted:
                    message = f"{job.path}: conversione TTS in WAV fallita"
                    errors.append(message)
                    log(f"[{index}/{total}] ERROR {message}")
                    continue
                wav_path = Path(converted)

            original_data = vfs.read_entry_data(entry)
            wem_path = wav_to_wem(str(wav_path), original_data, allow_pcm_fallback=False)
            if not wem_path or not os.path.isfile(wem_path):
                message = f"{job.path}: WAV->WEM fallito"
                errors.append(message)
                log(f"[{index}/{total}] ERROR {message}")
                continue
            new_data = Path(wem_path).read_bytes()
            if not check_wem_vorbis(new_data):
                message = f"{job.path}: WEM generato non e' Vorbis"
                errors.append(message)
                log(f"[{index}/{total}] ERROR {message}")
                continue

            modified = ModifiedFile(
                data=new_data,
                entry=entry,
                pamt_data=pamt,
                package_group=GROUP,
            )
            create_backup = (not args.no_backup) and GROUP not in backed_up_groups

            def report_repack(pct: int, msg: str) -> None:
                if pct in {0, 100}:
                    log(f"[{index}/{total}] REPACK {pct}% {msg}")

            result_repack = repack.repack(
                [modified],
                papgt_path=str(papgt),
                create_backup=create_backup,
                progress_callback=report_repack,
            )
            if create_backup:
                backed_up_groups.add(GROUP)
            if not result_repack.success:
                message = f"{job.path}: repack fallito: {'; '.join(result_repack.errors)}"
                errors.append(message)
                log(f"[{index}/{total}] ERROR {message}")
                if any(token.lower() in message.lower() for token in ("access", "permission", "winerror 5")):
                    raise RuntimeError(message)
                continue

            progress_state.mark_completed(
                GROUP,
                job.path,
                signature,
                provider_id=provider_id,
                model_id=model_id,
                language=language,
            )
            patched += 1
            log(f"[{index}/{total}] OK patched={patched} errors={len(errors)}")

        write_state({
            "running": False,
            "status": "completed",
            "processed": len(jobs),
            "patched": patched,
            "errors": len(errors),
            "error_samples": errors[-20:],
            "finished_at": now_iso(),
        })
        log(f"COMPLETATO: patched={patched}, errors={len(errors)}, total={len(jobs)}")
        return 0 if not errors else 2
    except Exception as exc:
        errors.append(str(exc))
        write_state({
            "running": False,
            "status": "failed",
            "processed": patched + len(errors),
            "patched": patched,
            "errors": len(errors),
            "error_samples": errors[-20:],
            "fatal_error": str(exc),
            "traceback": traceback.format_exc(),
            "finished_at": now_iso(),
        })
        log(f"FATAL: {exc}")
        log(traceback.format_exc())
        return 1
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="Processa solo N job mancanti.")
    parser.add_argument("--category", default="", help="Filtra una categoria esatta.")
    parser.add_argument("--dry-run", action="store_true", help="Mostra i job senza patchare.")
    parser.add_argument("--no-backup", action="store_true", help="Non crea un nuovo backup PAZ/PAMT/PAPGT.")
    args = parser.parse_args()

    acquire_lock()
    try:
        return patch_jobs(args)
    finally:
        release_lock()


if __name__ == "__main__":
    raise SystemExit(main())
