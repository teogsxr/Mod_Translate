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
OUT = WORKSPACE / "intro_pronunciation_repair_20260526"

TARGETS = [
    {
        "path": "sound/unique_kliff_intro_0310_player_00000.wem",
        "text": "La spada di Giails...",
        "source_text": "Giles's sword...",
        "reason": "Use phonetic Italian TTS spelling for Giles.",
    },
    {
        "path": "sound/unique_kliff_intro_0310_player_00002.wem",
        "text": "Giails... riposa in pace.",
        "source_text": "Giles... Rest well.",
        "reason": "Use phonetic Italian TTS spelling for Giles.",
    },
    {
        "path": "sound/unique_kliff_splithorn_boss_0200_player_00004.wem",
        "text": "Dai, adesso c\u00e0lmati.",
        "source_text": "Hey, hey. Calm down.",
        "reason": "Force correct Italian stress on calmati.",
    },
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def find_kliff_reference() -> Path:
    candidates: list[Path] = []
    for folder in (
        WORKSPACE / "single_repairs" / "unique_kliff_intro_0310_player_00002",
        WORKSPACE / "single_repairs" / "unique_kliff_intro_0310_player_00000",
    ):
        candidates.extend(folder.glob("ref_original_*.wav"))
    candidates = [path for path in candidates if path.is_file()]
    if not candidates:
        raise FileNotFoundError("Reference WAV di Kliff non trovata.")
    return sorted(candidates, key=lambda path: path.stat().st_mtime)[-1]


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
        raise RuntimeError("Wwise non risulta installato: non posso creare WEM Vorbis sicuri.")

    OUT.mkdir(parents=True, exist_ok=True)
    wav_dir = OUT / "wav"
    wem_dir = OUT / "wem"
    before_dir = OUT / "before_wem"
    wav_dir.mkdir(exist_ok=True)
    wem_dir.mkdir(exist_ok=True)
    before_dir.mkdir(exist_ok=True)

    config, config_data = hv.load_config()
    game_path = hv.read_game_path(config_data)
    papgt = game_path / "meta" / "0.papgt"

    engine = TTSEngine()
    engine.initialize_from_config(config_data)
    provider = engine.get_provider("omnivoice_tts")
    status = provider.get_status() if provider else None
    if not status or not status.connected:
        raise RuntimeError(f"OmniVoice non raggiungibile: {status.message if status else 'provider missing'}")

    ref_wav = find_kliff_reference()
    options = {
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
    provider_id = "omnivoice_tts"
    model_id = "omnivoice"
    voice_id = "auto"
    language = "Italian"
    speed = 1.0

    vfs = VfsManager(str(game_path))
    pamt = vfs.load_pamt(GROUP)
    repack = RepackEngine(str(game_path))
    progress = TTSPatchProgress(str(game_path))

    modified: list[ModifiedFile] = []
    report_items = []
    wavs: list[Path] = []

    for idx, target in enumerate(TARGETS, start=1):
        rel = target["path"]
        print(f"[{idx}/{len(TARGETS)}] TTS {rel} | {target['text']}", flush=True)
        entry = find_file_entry(pamt, rel)
        if entry is None:
            raise RuntimeError(f"Entry non trovata nel PAMT: {rel}")

        original_data = vfs.read_entry_data(entry)
        stem = Path(rel).stem
        before_path = before_dir / f"{stem}.wem"
        before_path.write_bytes(original_data)

        result = engine.synthesize(
            target["text"],
            provider_id,
            model_id,
            voice_id,
            language,
            speed,
            options=options,
        )
        if not result.success or not result.audio_data:
            raise RuntimeError(f"Sintesi fallita per {rel}: {result.error or 'audio vuoto'}")

        wav_path = wav_dir / f"{stem}.wav"
        wav_path.write_bytes(result.audio_data)
        wavs.append(wav_path)

        wem_path = wem_dir / f"{stem}.wem"
        converted = wav_to_wem(str(wav_path), original_data, str(wem_path), allow_pcm_fallback=False)
        if not converted or not wem_path.is_file():
            raise RuntimeError(f"WAV->WEM fallito per {rel}")

        new_data = wem_path.read_bytes()
        if not hv.check_wem_vorbis(new_data):
            raise RuntimeError(f"WEM generato non Vorbis: {rel}")

        signature = build_patch_signature(
            target["text"],
            provider_id,
            model_id,
            voice_id,
            language,
            speed,
            options,
        )
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
                **target,
                "key": hv.normalize_key(GROUP, rel),
                "before_wem": str(before_path),
                "wav": str(wav_path),
                "wem": str(wem_path),
                "sha256": sha256_bytes(new_data),
                "size": len(new_data),
                "signature": signature,
            }
        )

    def repack_progress(pct: int, msg: str) -> None:
        if pct in {0, 100}:
            print(f"REPACK {pct}% {msg}", flush=True)

    result_repack = repack.repack(
        modified,
        papgt_path=str(papgt),
        create_backup=False,
        progress_callback=repack_progress,
    )
    if not result_repack.success:
        raise RuntimeError("Repack fallito: " + "; ".join(result_repack.errors))

    verify_vfs = VfsManager(str(game_path))
    verify_pamt = verify_vfs.load_pamt(GROUP)
    for item in report_items:
        entry = find_file_entry(verify_pamt, item["path"])
        if entry is None:
            raise RuntimeError(f"Verifica fallita, entry mancante: {item['path']}")
        current = verify_vfs.read_entry_data(entry)
        if sha256_bytes(current) != item["sha256"]:
            raise RuntimeError(f"Verifica fallita, SHA diverso: {item['path']}")

    overrides_path = Path.home() / ".crimsonforge" / "audio_text_overrides.json"
    overrides = hv.load_json(overrides_path, {"entries": {}, "version": 1})
    entries = overrides.setdefault("entries", {})
    for item in report_items:
        rec = entries.setdefault(item["key"], {"texts": {}, "metadata": {}})
        rec.setdefault("texts", {})["it"] = item["text"]
        rec["source_transcript"] = item["source_text"]
        meta = rec.setdefault("metadata", {})
        meta["source"] = "manual_intro_pronunciation_repair"
        meta["reason"] = item["reason"]
        meta["repaired_at"] = now_iso()
    hv.atomic_json(overrides_path, overrides)

    montage = OUT / "CAMPIONE_INTRO_PRONUNCIA_FIX.wav"
    write_montage(wavs, montage)

    report = {
        "created_at": now_iso(),
        "game": str(game_path),
        "kliff_reference": str(ref_wav),
        "montage": str(montage) if montage.is_file() else "",
        "items": report_items,
    }
    (OUT / "repair_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"patched": len(report_items), "montage": report["montage"], "out": str(OUT)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
