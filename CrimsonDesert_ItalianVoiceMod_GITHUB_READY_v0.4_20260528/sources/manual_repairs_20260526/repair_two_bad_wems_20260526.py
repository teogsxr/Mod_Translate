from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path


WORKSPACE = Path(r"C:\Users\matte\Documents\Codex\2026-05-21\ho-provato-a-installare-omnivoice-ma")
FORGE_REPO = Path(r"C:\Users\matte\Downloads\crimsonforge-latest")
GROUP = "0006"
OUT = WORKSPACE / "repair_bad_wems_20260526"

TARGET_AAH = "sound/nhm_adult_citizen_2_aidialogstringinfogroup_criminal_02903.wem"
REF_AAH = "sound/nhm_adult_citizen_2_aidialogstringinfogroup_criminal_00001.wem"
REF_AAH_TEXT = "P-Perch\u00e9 lo stai facendo?!"
TARGET_RESTORE = "sound/nhm_adult_citizen_3_questdialog_hello_00891.wem"
RESTORE_ZIP = (
    Path(r"C:\aaa-crimson-mod\OLD_v0.1_DO_NOT_UPLOAD")
    / "CrimsonDesert_ItalianVoiceMod_READY_v0.1_20260524"
    / "data"
    / "wem_replacements_0006.zip"
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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

    OUT.mkdir(parents=True, exist_ok=True)
    wav_dir = OUT / "wav"
    wem_dir = OUT / "wem"
    before_dir = OUT / "before_wem"
    for folder in (wav_dir, wem_dir, before_dir):
        folder.mkdir(exist_ok=True)

    config, config_data = hv.load_config()
    game_path = hv.read_game_path(config_data)
    papgt = game_path / "meta" / "0.papgt"

    vfs = VfsManager(str(game_path))
    pamt = vfs.load_pamt(GROUP)
    target_aah = find_file_entry(pamt, TARGET_AAH)
    target_restore = find_file_entry(pamt, TARGET_RESTORE)
    ref_aah = find_file_entry(pamt, REF_AAH)
    if target_aah is None or target_restore is None or ref_aah is None:
        raise RuntimeError("Una delle entry richieste non e stata trovata nel PAMT.")

    before = {}
    for rel, entry in [(TARGET_AAH, target_aah), (TARGET_RESTORE, target_restore), (REF_AAH, ref_aah)]:
        data = vfs.read_entry_data(entry)
        before_path = before_dir / Path(rel).name
        before_path.write_bytes(data)
        before[rel] = str(before_path)

    ref_wav = wav_dir / f"reference_{Path(REF_AAH).stem}.wav"
    if not wem_to_wav(before_dir / Path(REF_AAH).name, str(ref_wav)):
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
        "ref_text": REF_AAH_TEXT,
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

    aah_text = "Aah..."
    result = engine.synthesize(aah_text, provider_id, model_id, voice_id, language, speed, options=options)
    if not result.success or not result.audio_data:
        raise RuntimeError(result.error or "TTS Aah audio vuoto")
    aah_wav = wav_dir / "nhm_adult_citizen_2_aidialogstringinfogroup_criminal_02903.wav"
    aah_wav.write_bytes(result.audio_data)
    aah_wem = wem_dir / Path(TARGET_AAH).name
    template_data = vfs.read_entry_data(ref_aah)
    if not wav_to_wem(str(aah_wav), template_data, str(aah_wem), allow_pcm_fallback=False):
        raise RuntimeError("WAV->WEM Aah fallito")
    aah_data = aah_wem.read_bytes()
    if not hv.check_wem_vorbis(aah_data):
        raise RuntimeError("WEM Aah non Vorbis")

    if not RESTORE_ZIP.is_file():
        raise RuntimeError(f"Restore zip non trovato: {RESTORE_ZIP}")
    with zipfile.ZipFile(RESTORE_ZIP, "r") as zf:
        restore_data = zf.read(TARGET_RESTORE)
    if not hv.check_wem_vorbis(restore_data):
        raise RuntimeError("WEM restore 00891 non Vorbis")
    restore_wem = wem_dir / Path(TARGET_RESTORE).name
    restore_wem.write_bytes(restore_data)

    modified = [
        ModifiedFile(data=aah_data, entry=target_aah, pamt_data=pamt, package_group=GROUP),
        ModifiedFile(data=restore_data, entry=target_restore, pamt_data=pamt, package_group=GROUP),
    ]
    repack = RepackEngine(str(game_path))
    result_repack = repack.repack(
        modified,
        papgt_path=str(papgt),
        create_backup=False,
        progress_callback=lambda pct, msg: print(f"REPACK {pct}% {msg}", flush=True) if pct in {0, 100} else None,
    )
    if not result_repack.success:
        raise RuntimeError("Repack fallito: " + "; ".join(result_repack.errors))

    verify_vfs = VfsManager(str(game_path))
    verify_pamt = verify_vfs.load_pamt(GROUP)
    expected = {TARGET_AAH: aah_data, TARGET_RESTORE: restore_data}
    for rel, expected_data in expected.items():
        entry = find_file_entry(verify_pamt, rel)
        if entry is None:
            raise RuntimeError(f"Verify entry missing: {rel}")
        actual = verify_vfs.read_entry_data(entry)
        if sha256_bytes(actual) != sha256_bytes(expected_data):
            raise RuntimeError(f"Verify SHA mismatch: {rel}")

    progress = TTSPatchProgress(str(game_path))
    sig_aah = build_patch_signature(aah_text, provider_id, model_id, voice_id, language, speed, options)
    progress.mark_completed(GROUP, TARGET_AAH, sig_aah, provider_id=provider_id, model_id=model_id, language=language)
    progress.mark_completed(
        GROUP,
        TARGET_RESTORE,
        "restored_valid_v01_vorbis_20260526",
        provider_id=provider_id,
        model_id=model_id,
        language=language,
    )

    overrides_path = Path.home() / ".crimsonforge" / "audio_text_overrides.json"
    overrides = hv.load_json(overrides_path, {"entries": {}, "version": 1})
    entries = overrides.setdefault("entries", {})
    fixes = {
        hv.normalize_key(GROUP, TARGET_AAH): {
            "text": aah_text,
            "source": "manual_bad_wem_repair_20260526",
            "reason": "Rebuilt invalid 78-byte WEM as valid Vorbis.",
        },
        hv.normalize_key(GROUP, TARGET_RESTORE): {
            "text": "Sono andato a pescare nelle Colline orientali e ne ho preso uno grande quanto il mio avambraccio!",
            "source": "manual_bad_wem_restore_20260526",
            "reason": "Restored valid Vorbis WEM from v0.1 package after a bad-format v0.2 restore.",
        },
    }
    for key, meta_payload in fixes.items():
        rec = entries.setdefault(key, {"texts": {}, "metadata": {}})
        rec.setdefault("texts", {})["it"] = meta_payload["text"]
        meta = rec.setdefault("metadata", {})
        meta["source"] = meta_payload["source"]
        meta["reason"] = meta_payload["reason"]
        meta["repaired_at"] = now_iso()
    hv.atomic_json(overrides_path, overrides)

    report = {
        "created_at": now_iso(),
        "patched": [
            {
                "path": TARGET_AAH,
                "method": "tts_rebuild",
                "text": aah_text,
                "wem": str(aah_wem),
                "size": len(aah_data),
                "sha256": sha256_bytes(aah_data),
            },
            {
                "path": TARGET_RESTORE,
                "method": "restore_from_v01",
                "text": fixes[hv.normalize_key(GROUP, TARGET_RESTORE)]["text"],
                "wem": str(restore_wem),
                "size": len(restore_data),
                "sha256": sha256_bytes(restore_data),
            },
        ],
        "before": before,
    }
    (OUT / "repair_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
