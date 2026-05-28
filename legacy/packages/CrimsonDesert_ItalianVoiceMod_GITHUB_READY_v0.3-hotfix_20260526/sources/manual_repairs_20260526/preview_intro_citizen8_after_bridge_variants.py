from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


WORKSPACE = Path(r"C:\Users\matte\Documents\Codex\2026-05-21\ho-provato-a-installare-omnivoice-ma")
FORGE_REPO = Path(r"C:\Users\matte\Downloads\crimsonforge-latest")
SOURCE = WORKSPACE / "intro_helper_voice_candidates_current" / "wav"
OUT = WORKSPACE / "intro_citizen8_after_bridge_variants_preview"

SAMPLES = [
    ("mantogrigio", "Mantogrigio!"),
    ("fiume", "Ti ho appena tirato fuori dal fiume."),
    ("coraggio", "Coraggio, non è sicuro qua fuori."),
]

VARIANTS = [
    {
        "name": "A_subito_dopo_ponte_010_011",
        "refs": [
            (
                SOURCE / "010_nhm_adult_citizen_8_intro_0450_globalgametrack_00018.wav",
                "Sarebbe un peccato non fermarsi a godersela. Andiamo con calma.",
            ),
            (
                SOURCE / "011_nhm_adult_citizen_8_intro_0450_globalgametrack_00019.wav",
                "Ci meritiamo un po' di serenità, ogni tanto.",
            ),
        ],
    },
    {
        "name": "B_strada_dopo_ponte_023_024",
        "refs": [
            (
                SOURCE / "023_nhm_adult_citizen_8_intro_0450_globalgametrack_00040.wav",
                "A piedi, non ci vorrà molto a raggiungere Hernand.",
            ),
            (
                SOURCE / "024_nhm_adult_citizen_8_intro_0450_globalgametrack_00041.wav",
                "Ma se vuoi raggiungere la città più rapidamente, sarà meglio andare a cavallo.",
            ),
        ],
    },
    {
        "name": "C_post_panoramica_020_022",
        "refs": [
            (
                SOURCE / "020_nhm_adult_citizen_8_intro_0450_globalgametrack_00028.wav",
                "Hernand è molto più grande di quanto pensi.",
            ),
            (
                SOURCE / "022_nhm_adult_citizen_8_intro_0450_globalgametrack_00032.wav",
                "Hai visto qualcosa in particolare?",
            ),
        ],
    },
]


def ffmpeg_path() -> Path:
    return Path(
        r"C:\Users\matte\AppData\Local\Microsoft\WinGet\Packages"
        r"\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
        r"\ffmpeg-8.1.1-full_build\bin\ffmpeg.exe"
    )


def concat_wavs(wavs: list[Path], output: Path) -> None:
    ffmpeg = ffmpeg_path()
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

    OUT.mkdir(parents=True, exist_ok=True)
    config, config_data = hv.load_config()
    engine = TTSEngine()
    engine.initialize_from_config(config_data)
    provider = engine.get_provider("omnivoice_tts")
    status = provider.get_status() if provider else None
    if not status or not status.connected:
        raise RuntimeError(f"OmniVoice non raggiungibile: {status.message if status else 'provider missing'}")

    report = []
    all_montages = []
    original_refs = []
    for variant in VARIANTS:
        variant_dir = OUT / variant["name"]
        variant_dir.mkdir(exist_ok=True)
        ref_paths = [path for path, _ in variant["refs"]]
        for ref_path in ref_paths:
            if not ref_path.is_file():
                raise FileNotFoundError(ref_path)
        ref_wav = variant_dir / "reference.wav"
        concat_wavs(ref_paths, ref_wav)
        original_refs.append(ref_wav)
        ref_text = " ".join(text for _, text in variant["refs"])
        options = {
            "clone_mode": "one_shot",
            "ref_audio_path": str(ref_wav),
            "ref_text": ref_text,
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

        wavs = []
        for idx, (name, text) in enumerate(SAMPLES, start=1):
            print(f"{variant['name']} [{idx}/{len(SAMPLES)}] {text}", flush=True)
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
                raise RuntimeError(f"{variant['name']}: {result.error or 'audio vuoto'}")
            wav_path = variant_dir / f"{idx:02d}_{name}.wav"
            wav_path.write_bytes(result.audio_data)
            wavs.append(wav_path)
        montage = OUT / f"CAMPIONE_{variant['name']}.wav"
        concat_wavs(wavs, montage)
        all_montages.append(montage)
        report.append(
            {
                "name": variant["name"],
                "reference": str(ref_wav),
                "reference_text": ref_text,
                "montage": str(montage),
            }
        )

    concat_wavs(original_refs, OUT / "RIFERIMENTI_ORIGINALI_A_B_C.wav")
    comparison = OUT / "CONFRONTO_A_B_C_DOPO_PONTE.wav"
    concat_wavs(all_montages, comparison)
    (OUT / "preview_report.json").write_text(
        json.dumps({"comparison": str(comparison), "variants": report}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({"comparison": str(comparison), "out": str(OUT)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
