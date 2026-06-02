"""Fill missing Audio-tab text with Whisper + local TranslateGemma."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.audio_converter import wem_to_wav  # noqa: E402
from core.audio_index import build_audio_index_cached  # noqa: E402
from core.pamt_parser import find_file_entry  # noqa: E402
from core.vfs_manager import VfsManager  # noqa: E402
from utils.audio_text_overrides import (  # noqa: E402
    get_audio_text_override,
    upsert_audio_text_override,
)


LANG_NAMES = {
    "ch": "Chinese",
    "de": "German",
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "it": "Italian",
    "ja": "Japanese",
    "ko": "Korean",
    "pl": "Polish",
    "pt-br": "Portuguese",
    "ru": "Russian",
    "tr": "Turkish",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create Audio-tab TTS overrides for dialogue rows missing PALOC text.",
    )
    parser.add_argument("--game-path", required=True)
    parser.add_argument("--group", default="0006")
    parser.add_argument("--target-code", default="it")
    parser.add_argument("--target-language", default="Italian")
    parser.add_argument("--source-code", default="")
    parser.add_argument("--source-language", default="")
    parser.add_argument("--whisper-model", default="small")
    parser.add_argument("--whisper-exe", default="")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434/api/chat")
    parser.add_argument("--translate-model", default="translategemma:12b")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    return parser.parse_args()


def build_translate_prompt(
    text: str,
    source_language: str,
    source_code: str,
    target_language: str,
    target_code: str,
) -> str:
    return (
        f"You are a professional {source_language} ({source_code}) to "
        f"{target_language} ({target_code}) translator. Your goal is to "
        f"accurately convey the meaning and nuances of the original "
        f"{source_language} text while adhering to {target_language} grammar, "
        "vocabulary, and cultural sensitivities.\n"
        f"Produce only the {target_language} translation, without any "
        "additional explanations or commentary. Please translate the following "
        f"{source_language} text into {target_language}:\n\n"
        f"{text}"
    )


def translate_with_ollama(
    text: str,
    *,
    source_language: str,
    source_code: str,
    target_language: str,
    target_code: str,
    model: str,
    url: str,
) -> str:
    payload = {
        "model": model,
        "stream": False,
        "messages": [{
            "role": "user",
            "content": build_translate_prompt(
                text,
                source_language,
                source_code,
                target_language,
                target_code,
            ),
        }],
    }
    req = urlrequest.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlrequest.urlopen(req, timeout=600) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (OSError, urlerror.URLError, json.JSONDecodeError) as e:
        raise RuntimeError(f"Ollama translation failed: {e}") from e
    message = result.get("message", {})
    translated = message.get("content", "") if isinstance(message, dict) else ""
    translated = translated.strip().strip('"')
    if not translated:
        raise RuntimeError(f"Ollama returned no translation for {text!r}")
    return translated


def find_whisper_exe(explicit_path: str) -> str:
    if explicit_path:
        return explicit_path
    found = shutil.which("whisper") or shutil.which("whisper.exe")
    if found:
        return found
    common = Path.home() / "AppData" / "Local" / "Programs" / "Python" / "Python310" / "Scripts" / "whisper.exe"
    if common.is_file():
        return str(common)
    raise RuntimeError("Whisper CLI not found. Install openai-whisper or pass --whisper-exe.")


def transcribe_with_whisper(
    audio_path: Path,
    *,
    whisper_exe: str,
    whisper_model: str,
    source_language: str,
    workdir: Path,
) -> str:
    out_dir = workdir / "whisper"
    out_dir.mkdir(exist_ok=True)
    cmd = [
        whisper_exe,
        str(audio_path),
        "--model",
        whisper_model,
        "--task",
        "transcribe",
        "--output_format",
        "txt",
        "--output_dir",
        str(out_dir),
        "--verbose",
        "False",
    ]
    if source_language:
        cmd.extend(["--language", source_language])
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=600,
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Whisper failed for {audio_path.name}: {result.stderr[-800:]}")
    transcript_path = out_dir / f"{audio_path.stem}.txt"
    if not transcript_path.is_file():
        raise RuntimeError(f"Whisper wrote no transcript for {audio_path.name}")
    transcript = transcript_path.read_text(encoding="utf-8").strip()
    if not transcript:
        raise RuntimeError(f"Whisper transcript was empty for {audio_path.name}")
    return transcript


def main() -> int:
    args = parse_args()
    vfs = VfsManager(args.game_path)
    entries = build_audio_index_cached(vfs, vfs.list_package_groups())
    candidates = [
        ae for ae in entries
        if ae.package_group == args.group
        and ae.category != "Other"
        and not ae.text_original
        and not ae.text_translations.get(args.target_code, "")
    ]
    if args.limit > 0:
        candidates = candidates[:args.limit]

    whisper_exe = find_whisper_exe(args.whisper_exe)
    pamt = vfs.load_pamt(args.group)
    completed = 0
    skipped = 0
    failures = []
    with tempfile.TemporaryDirectory(prefix="cf_missing_audio_text_") as tmp:
        workdir = Path(tmp)
        for ae in candidates:
            existing = get_audio_text_override(
                ae.package_group,
                ae.entry.path,
                args.target_code,
            )
            if existing and not args.overwrite:
                skipped += 1
                continue
            entry = find_file_entry(pamt, ae.entry.path)
            if entry is None:
                failures.append(f"{ae.entry.path}: entry missing from PAMT")
                continue
            try:
                wem_path = workdir / Path(entry.path).name
                wav_path = workdir / f"{wem_path.stem}.wav"
                wem_path.write_bytes(vfs.read_entry_data(entry))
                decoded = wem_to_wav(str(wem_path), str(wav_path))
                if not decoded:
                    raise RuntimeError("WEM decode failed")
                source_code = args.source_code or ae.voice_lang
                source_language = (
                    args.source_language
                    or LANG_NAMES.get(source_code, source_code or "English")
                )
                transcript = transcribe_with_whisper(
                    Path(decoded),
                    whisper_exe=whisper_exe,
                    whisper_model=args.whisper_model,
                    source_language=source_language,
                    workdir=workdir,
                )
                translated = translate_with_ollama(
                    transcript,
                    source_language=source_language,
                    source_code=source_code or "en",
                    target_language=args.target_language,
                    target_code=args.target_code,
                    model=args.translate_model,
                    url=args.ollama_url,
                )
                upsert_audio_text_override(
                    ae.package_group,
                    ae.entry.path,
                    language_code=args.target_code,
                    text=translated,
                    source_language=source_code,
                    source_transcript=transcript,
                    metadata={
                        "translation_model": args.translate_model,
                        "whisper_model": args.whisper_model,
                    },
                )
                completed += 1
                print(f"OK {ae.entry.path}: {translated}")
            except Exception as e:
                failures.append(f"{ae.entry.path}: {e}")
                print(f"ERROR {ae.entry.path}: {e}", file=sys.stderr)

    print(f"completed={completed} skipped={skipped} failed={len(failures)}")
    for failure in failures:
        print(failure, file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
