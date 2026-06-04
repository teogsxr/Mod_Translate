from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import patch_intro_citizen8_ponte_voice_20260526 as base


base.OUT = base.WORKSPACE / "intro_citizen8_after_bridge_A_patch_20260526"
base.STATE = base.OUT / "state.json"
base.LOG = base.OUT / "patch.log"
base.REF_WAV = (
    base.WORKSPACE
    / "intro_citizen8_after_bridge_variants_preview"
    / "A_subito_dopo_ponte_010_011"
    / "reference.wav"
)
base.REF_TEXT = (
    "Sarebbe un peccato non fermarsi a godersela. Andiamo con calma. "
    "Ci meritiamo un po' di serenit\u00e0, ogni tanto."
)
base.REFERENCE_PATHS = set()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fix_metadata() -> None:
    sys.path.insert(0, str(base.WORKSPACE))
    sys.path.insert(0, str(base.FORGE_REPO))
    import run_full_dialogue_voice_patch as hv

    report_path = base.OUT / "patch_report.json"
    if not report_path.is_file():
        return
    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    overrides_path = Path.home() / ".crimsonforge" / "audio_text_overrides.json"
    overrides = hv.load_json(overrides_path, {"entries": {}, "version": 1})
    entries = overrides.setdefault("entries", {})
    for item in report.get("items", []):
        key = hv.normalize_key(base.GROUP, item["path"])
        rec = entries.setdefault(key, {"texts": {}, "metadata": {}})
        meta = rec.setdefault("metadata", {})
        meta["source"] = "manual_intro_citizen8_after_bridge_A_voice_patch"
        meta["reason"] = "Regenerated with the approved after-bridge deep-voice reference A."
        meta["repaired_at"] = _now_iso()
    hv.atomic_json(overrides_path, overrides)
    report["metadata_source"] = "manual_intro_citizen8_after_bridge_A_voice_patch"
    report["metadata_reason"] = "Regenerated with the approved after-bridge deep-voice reference A."
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    code = base.main()
    if code == 0:
        _fix_metadata()
    raise SystemExit(code)
