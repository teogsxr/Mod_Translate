from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


WORKSPACE = Path(r"C:\Users\matte\Documents\Codex\2026-05-21\ho-provato-a-installare-omnivoice-ma")
FORGE_REPO = Path(r"C:\Users\matte\Downloads\crimsonforge-latest")
GAME = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Crimson Desert")
GROUP = "0006"
LOG = WORKSPACE / "headless_voice_recovery.log"
OUT = WORKSPACE / "headless_731_voice_repair"

MONSTER_PREFIXES = (
    "unique_ancientpraevus_",
    "unique_ancientprimus_",
    "unique_ancientpriscus_",
    "unique_antumbraspear_",
    "unique_bastier_",
    "unique_bloodwalkercrussis_",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_5760_completed(limit: int = 731) -> list[dict]:
    rx = re.compile(r"\[(\d+)/5760\] TTS (.*?) \| (.*)$")
    by_idx: dict[int, dict] = {}
    for line in LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        match = rx.search(line)
        if not match:
            continue
        idx = int(match.group(1))
        if idx <= limit:
            by_idx[idx] = {
                "idx": idx,
                "path": match.group(2),
                "text": match.group(3),
            }
    return [by_idx[idx] for idx in sorted(by_idx)]


def classify(item: dict) -> str:
    name = Path(item["path"]).name.lower()
    if name.startswith("unique_kliff_") and "_player_" in name:
        return "kliff_stable_clone"
    if any(name.startswith(prefix) for prefix in MONSTER_PREFIXES):
        return "monster_voice_auto"
    return ""


def find_kliff_reference() -> Path:
    candidates: list[Path] = []
    for folder in (
        WORKSPACE / "single_repairs" / "unique_kliff_intro_0310_player_00002",
        WORKSPACE / "single_repairs" / "unique_kliff_intro_0310_player_00000",
    ):
        candidates.extend(folder.glob("ref_original_*.wav"))
    candidates = [path for path in candidates if path.is_file()]
    if not candidates:
        raise FileNotFoundError("Reference WAV lunga di Kliff non trovata.")
    return sorted(candidates, key=lambda p: p.stat().st_mtime)[-1]


def existing_preview(item: dict, strategy: str) -> Path | None:
    stem = Path(item["path"]).stem
    if strategy == "kliff_stable_clone":
        path = WORKSPACE / "kliff_rps_repair" / "preview_wav"
        matches = sorted(path.glob(f"*_{stem}_kliff_ref.wav"))
        return matches[-1] if matches else None
    if strategy == "monster_voice_auto":
        path = WORKSPACE / "monster_voice_regen_preview" / "wav"
        matches = sorted(path.glob(f"*_{stem}_voice_auto.wav"))
        return matches[-1] if matches else None
    return None


def safe_name(item: dict) -> str:
    stem = Path(item["path"]).stem
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{item['idx']:03d}_{stem}")


def voice_options(strategy: str, ref_wav: Path | None = None) -> dict:
    if strategy == "kliff_stable_clone":
        if not ref_wav:
            raise ValueError("Kliff stable clone requires a reference WAV")
        return {
            "clone_mode": "one_shot",
            "ref_audio_path": str(ref_wav),
            "ref_text": "Giles... Rest well.",
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
    return {
        "clone_mode": "voice",
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


def write_montage(wavs: list[Path], output: Path) -> None:
    ffmpeg = Path(
        r"C:\Users\matte\AppData\Local\Microsoft\WinGet\Packages"
        r"\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
        r"\ffmpeg-8.1.1-full_build\bin\ffmpeg.exe"
    )
    if not ffmpeg.is_file() or not wavs:
        return
    concat = output.with_suffix(".concat.txt")
    lines = [f"file '{path.as_posix()}'" for path in wavs]
    concat.write_text("\n".join(lines) + "\n", encoding="utf-8")
    subprocess.run(
        [str(ffmpeg), "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", str(output)],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )


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
        raise RuntimeError("Wwise non risulta installato.")

    OUT.mkdir(parents=True, exist_ok=True)
    wav_dir = OUT / "wav"
    wem_dir = OUT / "wem"
    wav_dir.mkdir(exist_ok=True)
    wem_dir.mkdir(exist_ok=True)

    items = load_5760_completed()
    audit = []
    targets = []
    for item in items:
        strategy = classify(item)
        audited = dict(item)
        audited["strategy"] = strategy or "keep"
        audit.append(audited)
        if strategy:
            targets.append({**item, "strategy": strategy})

    (OUT / "headless_731_voice_audit.json").write_text(
        json.dumps(
            {
                "created_at": now_iso(),
                "total_reviewed": len(items),
                "repair_count": len(targets),
                "kept_count": len(items) - len(targets),
                "monster_prefixes": MONSTER_PREFIXES,
                "items": audit,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    config, config_data = hv.load_config()
    engine = TTSEngine()
    engine.initialize_from_config(config_data)
    provider = engine.get_provider("omnivoice_tts")
    status = provider.get_status() if provider else None
    if not status or not status.connected:
        raise RuntimeError(f"OmniVoice non raggiungibile: {status.message if status else 'provider missing'}")

    ref_wav = find_kliff_reference()
    vfs = VfsManager(str(GAME))
    pamt = vfs.load_pamt(GROUP)
    repack = RepackEngine(str(GAME))
    progress = TTSPatchProgress(str(GAME))
    provider_id = "omnivoice_tts"
    model_id = "omnivoice"
    language = "Italian"
    speed = 1.0
    modified: list[ModifiedFile] = []
    report_items = []
    errors = []
    montage_wavs = []

    for pos, item in enumerate(targets, start=1):
        strategy = item["strategy"]
        rel = item["path"]
        entry = find_file_entry(pamt, rel)
        if entry is None:
            errors.append({"path": rel, "error": "entry not found"})
            continue
        original_data = vfs.read_entry_data(entry)
        options = voice_options(strategy, ref_wav if strategy == "kliff_stable_clone" else None)
        signature = build_patch_signature(item["text"], provider_id, model_id, "auto", language, speed, options)

        wav_path = existing_preview(item, strategy)
        if wav_path is None:
            wav_path = wav_dir / f"{safe_name(item)}_{strategy}.wav"
            print(f"[{pos}/{len(targets)}] TTS {strategy}: {rel} | {item['text']}", flush=True)
            result = engine.synthesize(
                item["text"],
                provider_id,
                model_id,
                "auto",
                language,
                speed,
                options=options,
            )
            if not result.success or not result.audio_data:
                errors.append({"path": rel, "error": result.error or "audio vuoto"})
                print(f"[{pos}/{len(targets)}] ERROR {rel}: {result.error}", flush=True)
                continue
            wav_path.write_bytes(result.audio_data)
        else:
            print(f"[{pos}/{len(targets)}] PREVIEW {strategy}: {rel}", flush=True)

        fixed_wem = wem_dir / f"{safe_name(item)}_{strategy}.wem"
        wem = wav_to_wem(str(wav_path), original_data, str(fixed_wem), allow_pcm_fallback=False)
        if not wem or not fixed_wem.is_file():
            errors.append({"path": rel, "error": "wav_to_wem failed"})
            continue
        new_data = fixed_wem.read_bytes()
        if not hv.check_wem_vorbis(new_data):
            errors.append({"path": rel, "error": "generated WEM is not Vorbis"})
            continue
        modified.append(ModifiedFile(data=new_data, entry=entry, pamt_data=pamt, package_group=GROUP))
        progress.mark_completed(
            GROUP,
            rel,
            signature,
            provider_id=provider_id,
            model_id=model_id,
            language=language,
        )
        report_items.append(
            {
                **item,
                "wav": str(wav_path),
                "wem": str(fixed_wem),
                "sha256": sha256_bytes(new_data),
                "size": len(new_data),
                "signature": signature,
            }
        )
        if len(montage_wavs) < 20:
            montage_wavs.append(wav_path)

    if not modified:
        raise RuntimeError(f"Nessun WEM valido da patchare. Errori: {errors[:5]}")

    def repack_progress(pct: int, msg: str) -> None:
        if pct in {0, 100} or pct % 20 == 0:
            print(f"REPACK {pct}% {msg}", flush=True)

    result = repack.repack(
        modified,
        papgt_path=str(GAME / "meta" / "0.papgt"),
        create_backup=False,
        progress_callback=repack_progress,
    )
    if not result.success:
        raise RuntimeError("Repack fallito: " + "; ".join(result.errors))

    verify_vfs = VfsManager(str(GAME))
    verify_pamt = verify_vfs.load_pamt(GROUP)
    for item in report_items:
        entry = find_file_entry(verify_pamt, item["path"])
        if entry is None:
            errors.append({"path": item["path"], "error": "verify entry missing"})
            continue
        data = verify_vfs.read_entry_data(entry)
        if sha256_bytes(data) != item["sha256"]:
            errors.append({"path": item["path"], "error": "verify sha mismatch"})

    overrides_path = Path.home() / ".crimsonforge" / "audio_text_overrides.json"
    overrides = hv.load_json(overrides_path, {"entries": {}})
    entries = overrides.setdefault("entries", {})
    for item in report_items:
        key = hv.normalize_key(GROUP, item["path"])
        rec = entries.setdefault(key, {"texts": {"it": item["text"]}, "metadata": {}})
        rec.setdefault("texts", {})["it"] = item["text"]
        meta = rec.setdefault("metadata", {})
        meta["voice_repair_source"] = "headless_731_voice_review"
        meta["voice_repair_strategy"] = item["strategy"]
        meta["voice_repaired_at"] = now_iso()
    hv.atomic_json(overrides_path, overrides)

    write_montage(montage_wavs, OUT / "CAMPIONE_POST_RIPARAZIONE_731.wav")
    (OUT / "repair_report.json").write_text(
        json.dumps(
            {
                "created_at": now_iso(),
                "reviewed": len(items),
                "patched": len(report_items),
                "errors": errors,
                "kliff_reference": str(ref_wav),
                "items": report_items,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps({"patched": len(report_items), "errors": len(errors), "out": str(OUT)}, ensure_ascii=False))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
