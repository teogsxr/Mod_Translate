from __future__ import annotations

import array
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
TARGET_PATH = "sound/nhm_adult_soldier_8_intro_0310_npc_01_00023.wem"
REFERENCE_PATH = "sound/nhm_adult_soldier_8_intro_0310_npc_01_00020.wem"
REFERENCE_TEXT = "Those fucking Black Bears!"
OUT = WORKSPACE / "intro_soldier_giails_pronunciation_variants_20260526"

VARIANTS = [
    ("geils", "Geils..."),
    ("geilz", "Geilz..."),
    ("geils_accent", "Géils..."),
    ("gails", "Gails..."),
    ("djeils", "Djeils..."),
    ("dgeils", "Dgeils..."),
    ("jails", "Jails..."),
    ("jayls", "Jayls..."),
    ("giails_split", "Gia-ils..."),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


def concat_wavs(wavs: list[Path], output: Path) -> None:
    ffmpeg = Path(
        r"C:\Users\matte\AppData\Local\Microsoft\WinGet\Packages"
        r"\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
        r"\ffmpeg-8.1.1-full_build\bin\ffmpeg.exe"
    )
    concat = output.with_suffix(".concat.txt")
    concat.write_text("\n".join(f"file '{path.as_posix()}'" for path in wavs) + "\n", encoding="utf-8")
    subprocess.run(
        [str(ffmpeg), "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", str(output)],
        check=True,
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
    from core.audio_converter import wem_to_wav
    from core.pamt_parser import find_file_entry
    from core.vfs_manager import VfsManager

    OUT.mkdir(parents=True, exist_ok=True)
    wav_dir = OUT / "wav"
    before_dir = OUT / "before_wem"
    wav_dir.mkdir(exist_ok=True)
    before_dir.mkdir(exist_ok=True)

    config, config_data = hv.load_config()
    game_path = hv.read_game_path(config_data)
    vfs = VfsManager(str(game_path))
    pamt = vfs.load_pamt(GROUP)
    ref_entry = find_file_entry(pamt, REFERENCE_PATH)
    if ref_entry is None:
        raise RuntimeError(f"Reference non trovata: {REFERENCE_PATH}")
    ref_wem = before_dir / Path(REFERENCE_PATH).name
    ref_wem.write_bytes(vfs.read_entry_data(ref_entry))
    ref_wav = wav_dir / f"reference_{Path(REFERENCE_PATH).stem}.wav"
    if not wem_to_wav(str(ref_wem), str(ref_wav)):
        raise RuntimeError("Reference WEM->WAV fallita.")

    engine = TTSEngine()
    engine.initialize_from_config(config_data)
    provider = engine.get_provider("omnivoice_tts")
    status = provider.get_status() if provider else None
    if not status or not status.connected:
        raise RuntimeError(f"OmniVoice non raggiungibile: {status.message if status else 'provider missing'}")

    options = {
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

    report = []
    wavs = []
    for idx, (name, text) in enumerate(VARIANTS, start=1):
        print(f"[{idx}/{len(VARIANTS)}] {name}: {text}", flush=True)
        result = engine.synthesize(
            text,
            "omnivoice_tts",
            "omnivoice",
            "auto",
            "Italian",
            1.0,
            options=options,
        )
        if not result.success or not result.audio_data:
            report.append({"name": name, "text": text, "error": result.error or "audio vuoto"})
            continue
        wav_path = wav_dir / f"{idx:02d}_{name}.wav"
        wav_path.write_bytes(result.audio_data)
        wavs.append(wav_path)
        report.append({"name": name, "text": text, "wav": str(wav_path), "stats": wav_stats(wav_path)})

    montage = OUT / "CONFRONTO_PRONUNCIA_GIAILS.wav"
    if wavs:
        concat_wavs(wavs, montage)
    (OUT / "preview_report.json").write_text(
        json.dumps(
            {
                "created_at": now_iso(),
                "target": TARGET_PATH,
                "reference": str(ref_wav),
                "montage": str(montage) if montage.is_file() else "",
                "variants": report,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps({"out": str(OUT), "montage": str(montage), "variants": len(report)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
