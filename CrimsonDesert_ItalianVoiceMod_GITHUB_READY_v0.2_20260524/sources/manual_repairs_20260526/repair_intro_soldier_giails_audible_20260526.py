from __future__ import annotations

import array
import hashlib
import json
import math
import subprocess
import sys
import wave
from datetime import datetime, timezone
from pathlib import Path


WORKSPACE = Path(r"C:\Users\matte\Documents\Codex\2026-05-21\ho-provato-a-installare-omnivoice-ma")
FORGE_REPO = Path(r"C:\Users\matte\Downloads\crimsonforge-latest")
GROUP = "0006"
OUT = WORKSPACE / "intro_soldier_giails_audible_20260526"

TARGET_PATH = "sound/nhm_adult_soldier_8_intro_0310_npc_01_00023.wem"
REFERENCE_PATH = "sound/nhm_adult_soldier_8_intro_0310_npc_01_00020.wem"
REFERENCE_TEXT = "Those fucking Black Bears!"

VARIANTS = [
    ("giails_ellipsis", "Giails..."),
    ("giails_exclaim", "Giails!"),
    ("hey_giails", "Ehi... Giails."),
    ("giails_no", "Giails... no."),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def wav_stats(path: Path) -> dict:
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        sample_rate = wav.getframerate()
        sample_width = wav.getsampwidth()
        frames = wav.readframes(wav.getnframes())
        duration = wav.getnframes() / sample_rate if sample_rate else 0.0
    rms = 0.0
    peak = 0.0
    if sample_width == 2 and frames:
        samples = array.array("h")
        samples.frombytes(frames)
        if channels > 1:
            samples = array.array("h", samples[::channels])
        if samples:
            rms = math.sqrt(sum(float(s) * float(s) for s in samples) / len(samples)) / 32768.0
            peak = max(abs(s) for s in samples) / 32768.0
    return {"duration": duration, "rms": rms, "peak": peak, "sample_rate": sample_rate, "channels": channels}


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
    from core.audio_converter import wav_to_wem, wem_to_wav
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
    before_dir = OUT / "before_wem"
    for folder in (wav_dir, wem_dir, before_dir):
        folder.mkdir(exist_ok=True)

    config, config_data = hv.load_config()
    game_path = hv.read_game_path(config_data)
    papgt = game_path / "meta" / "0.papgt"

    engine = TTSEngine()
    engine.initialize_from_config(config_data)
    provider = engine.get_provider("omnivoice_tts")
    status = provider.get_status() if provider else None
    if not status or not status.connected:
        raise RuntimeError(f"OmniVoice non raggiungibile: {status.message if status else 'provider missing'}")

    vfs = VfsManager(str(game_path))
    pamt = vfs.load_pamt(GROUP)
    target_entry = find_file_entry(pamt, TARGET_PATH)
    ref_entry = find_file_entry(pamt, REFERENCE_PATH)
    if target_entry is None:
        raise RuntimeError(f"Target non trovato: {TARGET_PATH}")
    if ref_entry is None:
        raise RuntimeError(f"Reference non trovata: {REFERENCE_PATH}")

    original_data = vfs.read_entry_data(target_entry)
    before_path = before_dir / Path(TARGET_PATH).name
    before_path.write_bytes(original_data)
    ref_wem = before_dir / Path(REFERENCE_PATH).name
    ref_wem.write_bytes(vfs.read_entry_data(ref_entry))
    ref_wav = wav_dir / f"reference_{Path(REFERENCE_PATH).stem}.wav"
    if not wem_to_wav(str(ref_wem), str(ref_wav)):
        raise RuntimeError("Reference WEM->WAV fallita.")

    base_options = {
        "clone_mode": "one_shot",
        "ref_audio_path": str(ref_wav),
        "ref_text": REFERENCE_TEXT,
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

    variants = []
    for name, text in VARIANTS:
        print(f"TTS {name}: {text}", flush=True)
        result = engine.synthesize(
            text,
            provider_id,
            model_id,
            voice_id,
            language,
            speed,
            options=base_options,
        )
        if not result.success or not result.audio_data:
            variants.append({"name": name, "text": text, "error": result.error or "audio vuoto"})
            continue
        wav_path = wav_dir / f"{name}.wav"
        wav_path.write_bytes(result.audio_data)
        stats = wav_stats(wav_path)
        score = stats["duration"] * 0.55 + stats["rms"] * 20.0 + stats["peak"] * 4.0
        # Penalize extra words, but keep them as fallback if the pure name is silent.
        if name.startswith("giails_"):
            score += 0.5
        variants.append({"name": name, "text": text, "wav": str(wav_path), "stats": stats, "score": score})

    valid = [
        item
        for item in variants
        if item.get("wav") and item["stats"]["duration"] >= 0.45 and item["stats"]["rms"] >= 0.006
    ]
    if not valid:
        valid = [item for item in variants if item.get("wav")]
    if not valid:
        raise RuntimeError(f"Nessuna variante audio valida: {variants}")
    selected = sorted(valid, key=lambda item: item["score"], reverse=True)[0]
    selected_wav = Path(selected["wav"])
    selected_text = selected["text"]

    fixed_wem = wem_dir / f"{Path(TARGET_PATH).stem}.wem"
    converted = wav_to_wem(str(selected_wav), original_data, str(fixed_wem), allow_pcm_fallback=False)
    if not converted or not fixed_wem.is_file():
        raise RuntimeError("WAV->WEM fallito.")
    new_data = fixed_wem.read_bytes()
    if not hv.check_wem_vorbis(new_data):
        raise RuntimeError("WEM generato non Vorbis.")

    signature = build_patch_signature(selected_text, provider_id, model_id, voice_id, language, speed, base_options)
    repack = RepackEngine(str(game_path))

    def report_repack(pct: int, msg: str) -> None:
        if pct in {0, 100}:
            print(f"REPACK {pct}% {msg}", flush=True)

    result_repack = repack.repack(
        [ModifiedFile(data=new_data, entry=target_entry, pamt_data=pamt, package_group=GROUP)],
        papgt_path=str(papgt),
        create_backup=False,
        progress_callback=report_repack,
    )
    if not result_repack.success:
        raise RuntimeError("Repack fallito: " + "; ".join(result_repack.errors))

    verify_vfs = VfsManager(str(game_path))
    verify_pamt = verify_vfs.load_pamt(GROUP)
    verify_entry = find_file_entry(verify_pamt, TARGET_PATH)
    if verify_entry is None:
        raise RuntimeError("Verify entry missing.")
    if sha256_bytes(verify_vfs.read_entry_data(verify_entry)) != sha256_bytes(new_data):
        raise RuntimeError("Verify SHA mismatch.")

    progress = TTSPatchProgress(str(game_path))
    progress.mark_completed(
        GROUP,
        TARGET_PATH,
        signature,
        provider_id=provider_id,
        model_id=model_id,
        language=language,
    )

    overrides_path = Path.home() / ".crimsonforge" / "audio_text_overrides.json"
    overrides = hv.load_json(overrides_path, {"entries": {}, "version": 1})
    key = hv.normalize_key(GROUP, TARGET_PATH)
    rec = overrides.setdefault("entries", {}).setdefault(key, {"texts": {}, "metadata": {}})
    rec.setdefault("texts", {})["it"] = selected_text
    rec["source_transcript"] = "{StaticInfo:Knowledge:Knowledge_GreyFur_Giles#Giles}..."
    meta = rec.setdefault("metadata", {})
    meta["source"] = "manual_intro_soldier_giails_audible_repair"
    meta["reason"] = "Previous short name-only synthesis was near-silent; regenerated audible phonetic line."
    meta["repaired_at"] = now_iso()
    hv.atomic_json(overrides_path, overrides)

    wavs = [Path(item["wav"]) for item in variants if item.get("wav")]
    montage = OUT / "CAMPIONI_VARIANTI_GIAILS.wav"
    write_montage(wavs, montage)
    report = {
        "created_at": now_iso(),
        "target": TARGET_PATH,
        "selected": selected,
        "variants": variants,
        "before_wem": str(before_path),
        "fixed_wem": str(fixed_wem),
        "montage": str(montage) if montage.is_file() else "",
        "sha256": sha256_bytes(new_data),
        "signature": signature,
    }
    (OUT / "repair_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"patched": 1, "selected": selected, "out": str(OUT)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
