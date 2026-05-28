from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


WORKSPACE = Path(r"C:\Users\matte\Documents\Codex\2026-05-21\ho-provato-a-installare-omnivoice-ma")
FORGE_REPO = Path(r"C:\Users\matte\Downloads\crimsonforge-latest")
GROUP = "0006"
OUT = WORKSPACE / "intro_citizen8_ponte_voice_patch_20260526"
STATE = OUT / "state.json"
LOG = OUT / "patch.log"
CANDIDATES = WORKSPACE / "intro_helper_voice_candidates_current" / "candidates.json"
REF_WAV = WORKSPACE / "intro_citizen8_voice_variants_preview" / "ponte_due_battute_gravi" / "reference.wav"
REF_TEXT = "Ah... questo qui è uno dei miei posti preferiti. La vista dal ponte è davvero uno spettacolo mozzafiato."

REFERENCE_PATHS = {
    "sound/nhm_adult_citizen_8_intro_0450_globalgametrack_00016.wem",
    "sound/nhm_adult_citizen_8_intro_0450_globalgametrack_00017.wem",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def log(message: str) -> None:
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {message}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def write_state(payload: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    current = {}
    if STATE.is_file():
        try:
            current = json.loads(STATE.read_text(encoding="utf-8-sig"))
        except Exception:
            current = {}
    current.update(payload)
    current["updated_at"] = now_iso()
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE)


def write_montage(wavs: list[Path], output: Path) -> None:
    ffmpeg = Path(
        r"C:\Users\matte\AppData\Local\Microsoft\WinGet\Packages"
        r"\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
        r"\ffmpeg-8.1.1-full_build\bin\ffmpeg.exe"
    )
    if not ffmpeg.is_file() or not wavs:
        return
    concat = output.with_suffix(".concat.txt")
    concat.write_text("\n".join(f"file '{path.as_posix()}'" for path in wavs) + "\n", encoding="utf-8")
    subprocess.run(
        [str(ffmpeg), "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", str(output)],
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )


def load_targets() -> list[dict]:
    rows = json.loads(CANDIDATES.read_text(encoding="utf-8-sig"))
    targets = []
    for row in rows:
        path = (row.get("path") or "").replace("\\", "/")
        text = (row.get("text") or "").strip()
        if not text:
            continue
        if path in REFERENCE_PATHS:
            continue
        if not (
            path.startswith("sound/nhm_adult_citizen_8_intro_0450")
            or path.startswith("sound/nhm_adult_citizen_8_intro_0500")
        ):
            continue
        targets.append({**row, "path": path, "text": text})
    return targets


def main() -> int:
    sys.path.insert(0, str(WORKSPACE))
    sys.path.insert(0, str(FORGE_REPO))

    import run_full_dialogue_voice_patch as hv

    hv.ensure_forge_imports()
    from ai.tts_engine import TTSEngine
    from core.audio_converter import wav_to_wem
    from core.pamt_parser import find_file_entry
    from core.repack_engine import ModifiedFile, RepackEngine
    from core.vfs_manager import VfsManager
    from utils.tts_patch_progress import TTSPatchProgress, build_patch_signature
    from utils.wwise_installer import is_wwise_installed

    if not is_wwise_installed():
        raise RuntimeError("Wwise non risulta installato: non posso creare WEM Vorbis sicuri.")
    if not REF_WAV.is_file():
        raise FileNotFoundError(f"Reference WAV non trovata: {REF_WAV}")

    OUT.mkdir(parents=True, exist_ok=True)
    wav_dir = OUT / "wav"
    wem_dir = OUT / "wem"
    before_dir = OUT / "before_wem"
    for folder in (wav_dir, wem_dir, before_dir):
        folder.mkdir(exist_ok=True)

    targets = load_targets()
    write_state({"status": "starting", "running": True, "total": len(targets), "processed": 0, "patched": 0, "errors": 0})
    log(f"Target da rigenerare con voce ponte: {len(targets)}")

    config, config_data = hv.load_config()
    game_path = hv.read_game_path(config_data)
    papgt = game_path / "meta" / "0.papgt"

    engine = TTSEngine()
    engine.initialize_from_config(config_data)
    provider = engine.get_provider("omnivoice_tts")
    status = provider.get_status() if provider else None
    if not status or not status.connected:
        raise RuntimeError(f"OmniVoice non raggiungibile: {status.message if status else 'provider missing'}")

    options = {
        "clone_mode": "one_shot",
        "ref_audio_path": str(REF_WAV),
        "ref_text": REF_TEXT,
        "language": "Italian",
        "response_format": "wav",
        "stream": False,
        "num_step": 32,
        "guidance_scale": 3.0,
        "denoise": True,
        "duration": 0.0,
        "t_shift": 0.1,
        "position_temperature": 5.0,
        "class_temperature": 0.0,
        "param_9": "Auto",
        "param_10": "Auto",
        "param_11": "Auto",
        "param_12": "Auto",
        "param_13": "Auto",
    }
    provider_id = "omnivoice_tts"
    model_id = "omnivoice"
    voice_id = "auto"
    language = "Italian"
    speed = 1.0

    vfs = VfsManager(str(game_path))
    pamt = vfs.load_pamt(GROUP)
    modified: list[ModifiedFile] = []
    report_items = []
    errors = []
    sample_wavs = []

    for index, target in enumerate(targets, start=1):
        rel = target["path"]
        text = target["text"]
        write_state({"status": "synthesizing", "processed": index - 1, "last_path": rel, "patched": len(modified), "errors": len(errors)})
        log(f"[{index}/{len(targets)}] TTS {rel} | {text}")
        try:
            entry = find_file_entry(pamt, rel)
            if entry is None:
                raise RuntimeError("entry non trovata nel PAMT")
            original_data = vfs.read_entry_data(entry)
            before_path = before_dir / Path(rel).name
            before_path.write_bytes(original_data)

            result = engine.synthesize(
                text,
                provider_id,
                model_id,
                voice_id,
                language,
                speed,
                options=options,
            )
            if not result.success or not result.audio_data:
                raise RuntimeError(result.error or "audio vuoto")

            wav_path = wav_dir / f"{Path(rel).stem}.wav"
            wav_path.write_bytes(result.audio_data)
            wem_path = wem_dir / f"{Path(rel).stem}.wem"
            converted = wav_to_wem(str(wav_path), original_data, str(wem_path), allow_pcm_fallback=False)
            if not converted or not wem_path.is_file():
                raise RuntimeError("WAV->WEM fallito")
            new_data = wem_path.read_bytes()
            if not hv.check_wem_vorbis(new_data):
                raise RuntimeError("WEM generato non Vorbis")

            signature = build_patch_signature(text, provider_id, model_id, voice_id, language, speed, options)
            modified.append(ModifiedFile(data=new_data, entry=entry, pamt_data=pamt, package_group=GROUP))
            report_items.append(
                {
                    "idx": target.get("idx"),
                    "path": rel,
                    "text": text,
                    "before_wem": str(before_path),
                    "wav": str(wav_path),
                    "wem": str(wem_path),
                    "sha256": sha256_bytes(new_data),
                    "size": len(new_data),
                    "signature": signature,
                }
            )
            if len(sample_wavs) < 12:
                sample_wavs.append(wav_path)
        except Exception as exc:
            errors.append({"path": rel, "error": str(exc)})
            log(f"[{index}/{len(targets)}] ERROR {rel}: {exc}")

    if not modified:
        raise RuntimeError(f"Nessun file valido da patchare. Errori: {errors[:5]}")

    write_state({"status": "repacking", "processed": len(targets), "patched": 0, "errors": len(errors)})
    log(f"Repack di {len(modified)} file...")
    repack = RepackEngine(str(game_path))

    def report_repack(pct: int, msg: str) -> None:
        if pct in {0, 25, 50, 75, 100}:
            log(f"REPACK {pct}% {msg}")

    result_repack = repack.repack(
        modified,
        papgt_path=str(papgt),
        create_backup=False,
        progress_callback=report_repack,
    )
    if not result_repack.success:
        raise RuntimeError("Repack fallito: " + "; ".join(result_repack.errors))

    write_state({"status": "verifying", "processed": len(targets), "patched": len(modified), "errors": len(errors)})
    verify_vfs = VfsManager(str(game_path))
    verify_pamt = verify_vfs.load_pamt(GROUP)
    for item in report_items:
        entry = find_file_entry(verify_pamt, item["path"])
        if entry is None:
            errors.append({"path": item["path"], "error": "verify entry missing"})
            continue
        data = verify_vfs.read_entry_data(entry)
        if sha256_bytes(data) != item["sha256"]:
            errors.append({"path": item["path"], "error": "verify sha mismatch"})

    progress = TTSPatchProgress(str(game_path))
    for item in report_items:
        progress.mark_completed(
            GROUP,
            item["path"],
            item["signature"],
            provider_id=provider_id,
            model_id=model_id,
            language=language,
        )

    overrides_path = Path.home() / ".crimsonforge" / "audio_text_overrides.json"
    overrides = hv.load_json(overrides_path, {"entries": {}, "version": 1})
    entries = overrides.setdefault("entries", {})
    for item in report_items:
        key = hv.normalize_key(GROUP, item["path"])
        rec = entries.setdefault(key, {"texts": {}, "metadata": {}})
        rec.setdefault("texts", {})["it"] = item["text"]
        rec["source_transcript"] = item["text"]
        meta = rec.setdefault("metadata", {})
        meta["source"] = "manual_intro_citizen8_ponte_voice_patch"
        meta["reason"] = "Regenerated with the approved bridge deep-voice reference."
        meta["repaired_at"] = now_iso()
    hv.atomic_json(overrides_path, overrides)

    montage = OUT / "CAMPIONE_PRIME_12_DOPO_PATCH.wav"
    write_montage(sample_wavs, montage)
    report = {
        "created_at": now_iso(),
        "game": str(game_path),
        "reference_wav": str(REF_WAV),
        "reference_text": REF_TEXT,
        "skipped_reference_paths": sorted(REFERENCE_PATHS),
        "patched": len(report_items),
        "errors": errors,
        "sample_montage": str(montage) if montage.is_file() else "",
        "items": report_items,
    }
    (OUT / "patch_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_state(
        {
            "status": "completed" if not errors else "completed_with_errors",
            "running": False,
            "processed": len(targets),
            "patched": len(report_items),
            "errors": len(errors),
            "sample_montage": report["sample_montage"],
            "finished_at": now_iso(),
        }
    )
    log(f"COMPLETATO patched={len(report_items)} errors={len(errors)}")
    print(json.dumps({"patched": len(report_items), "errors": len(errors), "out": str(OUT)}, ensure_ascii=False))
    return 0 if not errors else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        OUT.mkdir(parents=True, exist_ok=True)
        log(f"FATAL {exc}")
        write_state({"status": "failed", "running": False, "fatal_error": str(exc), "finished_at": now_iso()})
        raise
