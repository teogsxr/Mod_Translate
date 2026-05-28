from __future__ import annotations

import collections
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path


WORKSPACE = Path(r"C:\Users\matte\Documents\Codex\2026-05-21\ho-provato-a-installare-omnivoice-ma")
GIT_PACKAGE = Path(
    r"C:\Users\matte\Documents\Codex\github-Mod_Translate"
    r"\CrimsonDesert_ItalianVoiceMod_GITHUB_READY_v0.2_20260524"
)
MANIFEST = GIT_PACKAGE / "data" / "manifest.json"
PAYLOAD = GIT_PACKAGE / "data" / "wem_replacements_0006"
TARGETS = Path.home() / ".crimsonforge" / "italian_audio_targets_0006.json"
PROGRESS = Path.home() / ".crimsonforge" / "tts_patch_progress.json"
OVERRIDES = Path.home() / ".crimsonforge" / "audio_text_overrides.json"
OUT = WORKSPACE / "voice_audit_20260526"

EVENT_MARKERS = [
    "intro",
    "questdialog",
    "quest",
    "aidialogstringinfogroup",
    "aidialogstringinfo",
    "aidialog",
    "boss",
    "globalgametrack",
    "textdialog",
    "closedialog",
    "dialog",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def check_wem_vorbis(data: bytes) -> bool:
    if len(data) < 22 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return False
    fmt = int.from_bytes(data[20:22], "little", signed=False)
    return fmt == 0xFFFF


def normalize_key(path: str) -> str:
    return "0006:" + path.replace("\\", "/").lower()


def infer_speaker_and_event(rel_path: str) -> tuple[str, str, str]:
    stem = Path(rel_path).stem.lower()
    parts = stem.split("_")
    marker_idx = None
    marker = ""
    for idx, part in enumerate(parts):
        if part in EVENT_MARKERS or any(part.startswith(m) for m in EVENT_MARKERS):
            marker_idx = idx
            marker = part
            break
    if marker_idx is None:
        return stem, "unknown", "unknown"
    speaker = "_".join(parts[:marker_idx]) or "unknown"
    tail = parts[marker_idx:]
    if tail and re.fullmatch(r"\d{5}", tail[-1]):
        tail = tail[:-1]
    role = "unknown"
    if "player" in tail:
        role = "player"
    elif "npc" in tail:
        role = "npc"
    elif "globalgametrack" in tail:
        role = "globalgametrack"
    return speaker, "_".join(tail) or marker, role


def load_json(path: Path, default):
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = load_json(MANIFEST, {})
    entries = manifest.get("entries") or []
    targets = load_json(TARGETS, {}).get("targets") or []
    targets_by_key = {item.get("key", "").lower(): item for item in targets}
    progress_state = load_json(PROGRESS, {})
    completed = {}
    for key, game_state in (progress_state.get("games") or {}).items():
        if key.replace("\\", "/").lower().endswith("/crimson desert"):
            completed = game_state.get("completed") or {}
            break
    overrides = (load_json(OVERRIDES, {"entries": {}}).get("entries") or {})

    categories = collections.Counter()
    speakers = collections.Counter()
    events = collections.Counter()
    roles = collections.Counter()
    speaker_categories: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    speaker_sources: dict[str, set[str]] = collections.defaultdict(set)
    speaker_events: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)

    missing_files = []
    hash_mismatches = []
    bad_wem = []
    tiny = []
    very_small = []
    placeholder_text = []
    completed_missing = []
    force_regenerate = []
    review_candidates = []

    for idx, item in enumerate(entries, start=1):
        rel = item["path"].replace("\\", "/")
        key = normalize_key(rel)
        category = item.get("category") or "Unknown"
        speaker, event, role = infer_speaker_and_event(rel)
        categories[category] += 1
        speakers[speaker] += 1
        events[event] += 1
        roles[role] += 1
        speaker_categories[speaker][category] += 1
        speaker_events[speaker][event] += 1

        progress = completed.get(key)
        if not progress:
            completed_missing.append({"path": rel, "category": category})
        elif progress.get("force_regenerate_reason"):
            force_regenerate.append({"path": rel, "reason": progress.get("force_regenerate_reason")})

        override = overrides.get(key) or {}
        text_it = ((override.get("texts") or {}).get("it") or "").strip()
        source = (override.get("metadata") or {}).get("source") or ""
        if source:
            speaker_sources[speaker].add(source)
        if text_it and re.search(r"\{[^}]*StaticInfo|StaticInfo|<[^>]+>|#.+\}", text_it, re.IGNORECASE):
            placeholder_text.append({"path": rel, "text": text_it[:240]})

        path = PAYLOAD / Path(rel)
        if not path.is_file():
            missing_files.append(rel)
            continue
        size = path.stat().st_size
        if size < 2500:
            very_small.append({"path": rel, "size": size, "category": category, "speaker": speaker})
        elif size < 5000:
            tiny.append({"path": rel, "size": size, "category": category, "speaker": speaker})

        data = path.read_bytes()
        if len(data) != item.get("size"):
            hash_mismatches.append({"path": rel, "reason": f"size {len(data)} != manifest {item.get('size')}"})
        elif hashlib.sha256(data).hexdigest() != item.get("sha256"):
            hash_mismatches.append({"path": rel, "reason": "sha256 mismatch"})
        if not check_wem_vorbis(data):
            bad_wem.append({"path": rel, "size": size})

        risk = []
        if size < 5000:
            risk.append("audio molto corto/piccolo")
        if text_it and len(text_it) <= 4:
            risk.append("testo molto corto")
        if source and "manual" in source:
            risk.append(f"riparazione manuale: {source}")
        if risk:
            review_candidates.append({"path": rel, "category": category, "speaker": speaker, "size": size, "risk": risk})

        if idx == 1 or idx % 5000 == 0 or idx == len(entries):
            print(f"audit {idx}/{len(entries)}", flush=True)

    multi_source_speakers = [
        {"speaker": speaker, "count": speakers[speaker], "sources": sorted(srcs)}
        for speaker, srcs in speaker_sources.items()
        if len(srcs) > 1
    ]
    mixed_category_speakers = [
        {"speaker": speaker, "count": speakers[speaker], "categories": cats.most_common(8)}
        for speaker, cats in speaker_categories.items()
        if speakers[speaker] >= 20 and len(cats) >= 4
    ]

    report = {
        "created_at": now_iso(),
        "package": str(GIT_PACKAGE),
        "manifest_version": manifest.get("version"),
        "manifest_entries": len(entries),
        "payload_wem_count": sum(1 for _ in PAYLOAD.rglob("*.wem")),
        "categories": categories.most_common(),
        "roles": roles.most_common(),
        "top_speakers": speakers.most_common(80),
        "top_events": events.most_common(80),
        "speaker_event_map_top": [
            {"speaker": speaker, "count": count, "events": speaker_events[speaker].most_common(12)}
            for speaker, count in speakers.most_common(60)
        ],
        "problems": {
            "missing_files": missing_files,
            "hash_mismatches": hash_mismatches,
            "bad_wem_format": bad_wem,
            "completed_missing": completed_missing,
            "force_regenerate": force_regenerate,
            "placeholder_text": placeholder_text,
        },
        "review_queues": {
            "very_small_wem_lt_2500_bytes": very_small[:300],
            "small_wem_2500_5000_bytes": tiny[:300],
            "manual_or_short_candidates": review_candidates[:500],
            "speakers_with_multiple_manual_sources": multi_source_speakers[:100],
            "speakers_spanning_many_categories": mixed_category_speakers[:100],
        },
        "summary": {
            "hard_errors": len(missing_files) + len(hash_mismatches) + len(bad_wem) + len(completed_missing) + len(force_regenerate),
            "placeholder_text_count": len(placeholder_text),
            "very_small_count": len(very_small),
            "small_count": len(tiny),
            "multi_source_speaker_count": len(multi_source_speakers),
            "mixed_category_speaker_count": len(mixed_category_speakers),
        },
    }
    (OUT / "voice_audit_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# Crimson Desert Voice Audit 2026-05-26",
        "",
        f"- Manifest entries: {len(entries):,}",
        f"- Payload WEM files: {report['payload_wem_count']:,}",
        f"- Hard errors: {report['summary']['hard_errors']}",
        f"- Placeholder/static text still in IT overrides: {len(placeholder_text)}",
        f"- Very small WEM < 2500 bytes: {len(very_small)}",
        f"- Small WEM 2500-5000 bytes: {len(tiny)}",
        "",
        "## Top Categories",
    ]
    for category, count in categories.most_common(25):
        lines.append(f"- {category}: {count:,}")
    lines.extend(["", "## Top Speakers / Groups"])
    for speaker, count in speakers.most_common(40):
        lines.append(f"- {speaker}: {count:,}")
    lines.extend(["", "## Review Notes"])
    if report["summary"]["hard_errors"] == 0:
        lines.append("- No missing files, hash mismatches, invalid WEM headers, missing completed records, or force-regenerate records were found.")
    else:
        lines.append("- Hard errors exist; inspect `voice_audit_report.json` before publishing.")
    lines.append("- Small files are not automatically wrong, but they are the first queue to sample for silent/near-silent lines.")
    lines.append("- Speakers spanning many categories are map/context groups, not necessarily bugs; they help locate game areas.")
    lines.append("- This audit is structural/heuristic. It cannot understand acting quality or accent without listening/ASR pass.")
    (OUT / "voice_audit_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps(report["summary"], ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
