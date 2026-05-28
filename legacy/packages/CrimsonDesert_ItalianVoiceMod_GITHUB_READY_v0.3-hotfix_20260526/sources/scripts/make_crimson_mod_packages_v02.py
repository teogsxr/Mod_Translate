from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import shutil
import sys
import textwrap
import zipfile
from datetime import datetime
from pathlib import Path


ROOT = Path(r"C:\aaa-crimson-mod")
GAME = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Crimson Desert")
REPO = Path(r"C:\Users\matte\Downloads\crimsonforge-latest")
WORKSPACE = Path(r"C:\Users\matte\Documents\Codex\2026-05-21\ho-provato-a-installare-omnivoice-ma")
TARGETS = Path.home() / ".crimsonforge" / "italian_audio_targets_0006.json"
PROGRESS = Path.home() / ".crimsonforge" / "tts_patch_progress.json"
TEMPLATE_READY = ROOT / "CrimsonDesert_ItalianVoiceMod_READY_v0.1_20260524"

PACKAGE_DATE = "20260524"
VERSION = "0.2"
STEAM_APPID = "3321460"
STEAM_BUILDID = "23374070"
EXE_VERSION = "1.0.0.1492"
READY_NAME = f"CrimsonDesert_ItalianVoiceMod_READY_v{VERSION}_{PACKAGE_DATE}"
SOURCE_NAME = f"CrimsonDesert_ItalianVoiceMod_SOURCES_v{VERSION}_{PACKAGE_DATE}"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def add_tree_to_zip(zip_path: Path, source_dir: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
        for path in sorted(source_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(source_dir).as_posix())


def unique_path(base: Path) -> Path:
    if not base.exists():
        return base
    idx = 2
    while True:
        candidate = base.with_name(f"{base.name}_{idx}")
        if not candidate.exists():
            return candidate
        idx += 1


def load_installer_module():
    installer_path = TEMPLATE_READY / "installer" / "apply_patch.py"
    spec = importlib.util.spec_from_file_location("crimson_ready_apply_patch", installer_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load installer helper: {installer_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_completed() -> dict:
    state = json.loads(PROGRESS.read_text(encoding="utf-8"))
    for key, game_state in (state.get("games") or {}).items():
        if key.replace("\\", "/").lower().endswith("/crimson desert"):
            return game_state.get("completed") or {}
    return {}


def copy_ready_template(dst: Path) -> None:
    def ignore(dir_name: str, names: list[str]) -> set[str]:
        ignored = {"SHA256SUMS.txt", "__pycache__"}
        if Path(dir_name).name == "data":
            ignored.update({"manifest.json", "wem_replacements_0006.zip"})
        return ignored.intersection(names)

    shutil.copytree(TEMPLATE_READY, dst, ignore=ignore)


def copy_crimsonforge_source(dst: Path) -> None:
    ignore_names = {
        ".git",
        ".venv",
        "__pycache__",
        ".pytest_cache",
        "build",
        "dist",
        "exports",
        "node_modules",
    }

    def ignore(_dir: str, names: list[str]) -> set[str]:
        ignored = set()
        for name in names:
            if name in ignore_names or name.endswith(".pyc") or name.endswith(".log"):
                ignored.add(name)
        return ignored

    shutil.copytree(REPO, dst, ignore=ignore)


def update_prereq_script(ready_dir: Path) -> None:
    path = ready_dir / "installer" / "verifica_prerequisiti.ps1"
    text = path.read_text(encoding="utf-8")
    text = text.replace('$ExpectedBuildId = "23245720"', f'$ExpectedBuildId = "{STEAM_BUILDID}"')
    text = text.replace('$ExpectedExeVersion = "1.0.0.1342"', f'$ExpectedExeVersion = "{EXE_VERSION}"')
    path.write_text(text, encoding="utf-8")


def build_payload(ready_dir: Path, targets: list[dict], completed: dict) -> tuple[dict, list[dict]]:
    installer = load_installer_module()
    pamt = installer.parse_pamt(GAME / "0006" / "0.pamt", GAME / "0006")
    entry_by_path = {e.path.replace("\\", "/").lower(): e for e in pamt.file_entries}

    data_dir = ready_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    payload_zip = data_dir / "wem_replacements_0006.zip"
    manifest_entries: list[dict] = []
    total_audio_bytes = 0

    with zipfile.ZipFile(payload_zip, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
        ordered = sorted(targets, key=lambda t: t["path"].lower())
        for idx, target in enumerate(ordered, start=1):
            rel_path = target["path"].replace("\\", "/")
            entry = entry_by_path.get(rel_path.lower())
            if entry is None:
                raise RuntimeError(f"File not found in current PAMT: {rel_path}")
            with open(entry.paz_file, "rb") as f:
                f.seek(entry.offset)
                data = f.read(entry.comp_size)
            if len(data) != entry.comp_size:
                raise RuntimeError(f"Short read for {rel_path}: {len(data)} / {entry.comp_size}")
            digest = hashlib.sha256(data).hexdigest()
            zf.writestr(rel_path, data)
            total_audio_bytes += len(data)
            progress = completed[target["key"]]
            manifest_entries.append({
                "group": "0006",
                "path": rel_path,
                "category": target.get("category", ""),
                "size": len(data),
                "orig_size": entry.orig_size,
                "sha256": digest,
                "completed_at": progress.get("completed_at", ""),
                "signature": progress.get("signature", ""),
                "manual": bool(progress.get("manual")),
            })
            if idx == 1 or idx % 1000 == 0 or idx == len(ordered):
                print(f"payload {idx}/{len(ordered)}", flush=True)

    manifest = {
        "name": "Crimson Desert Italian Voice Mod",
        "version": VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "package_group": "0006",
        "steam_appid": STEAM_APPID,
        "tested_steam_buildid": STEAM_BUILDID,
        "tested_exe_version": EXE_VERSION,
        "tested_on": "2026-05-24",
        "target_count": len(targets),
        "completed_count": len(targets),
        "excluded_text_dialogue_count": 243,
        "audio_payload": "wem_replacements_0006.zip",
        "audio_payload_sha256": sha256_file(payload_zip),
        "audio_payload_size": payload_zip.stat().st_size,
        "raw_audio_bytes": total_audio_bytes,
        "entries": manifest_entries,
    }
    (data_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest, manifest_entries


def write_ready_docs(ready_dir: Path, manifest: dict) -> None:
    count = manifest["completed_count"]
    payload_size_gb = manifest["audio_payload_size"] / (1024 ** 3)
    write_text(ready_dir / "INSTALLA_MOD_VOCI_ITALIANE.cmd", r'''
        @echo off
        setlocal
        cd /d "%~dp0"
        echo Crimson Desert Italian Voice Mod - installazione voci italiane
        echo.
        powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\install_patch.ps1"
        echo.
        pause
    ''')
    write_text(ready_dir / "CONTROLLA_PRIMA.cmd", r'''
        @echo off
        setlocal
        cd /d "%~dp0"
        echo Crimson Desert Italian Voice Mod - controllo prerequisiti
        echo.
        powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\verifica_prerequisiti.ps1"
        echo.
        pause
    ''')
    write_text(ready_dir / "install.cmd", r'''
        @echo off
        call "%~dp0INSTALLA_MOD_VOCI_ITALIANE.cmd"
    ''')
    write_text(ready_dir / "verifica_prerequisiti.cmd", r'''
        @echo off
        call "%~dp0CONTROLLA_PRIMA.cmd"
    ''')
    write_text(ready_dir / "README_INSTALLAZIONE.md", f'''
        # Crimson Desert Italian Voice Mod v{VERSION}

        Pacchetto pronto per installare solo le voci italiane generate per Crimson Desert.
        Non serve installare Python: il runtime ufficiale portatile e incluso nel pacchetto.

        ## Compatibilita verificata

        - Steam AppID: `{STEAM_APPID}`
        - Steam buildid testato: `{STEAM_BUILDID}`
        - `CrimsonDesert.exe`: `{EXE_VERSION}`
        - Data pacchetto: `2026-05-24`

        Questa e la versione supportata con certezza. Su altre build puo funzionare, ma non e garantito.
        Se il gioco viene aggiornato e aggiunge nuovi audio, quei nuovi audio restano originali/inglesi.
        Se una patch rinomina o rimuove audio gia presenti nel manifest, l'installer si ferma prima di modificare gli archivi.

        ## Contenuto

        - Audio italiani patchabili: {count:,} WEM
        - Package voce modificato: `0006`
        - Payload audio: `data/wem_replacements_0006.zip` ({payload_size_gb:.2f} GB)
        - File `.paz` originali inclusi: nessuno
        - Backup automatico prima della scrittura

        ## Installazione rapida

        1. Chiudi Crimson Desert, Steam Cloud sync in corso e CrimsonForge.
        2. Estrai tutto lo zip in una cartella qualsiasi.
        3. Avvia `CONTROLLA_PRIMA.cmd`.
        4. Avvia `INSTALLA_MOD_VOCI_ITALIANE.cmd`.
        5. Se il gioco non e nel percorso Steam standard, inserisci la cartella di Crimson Desert quando richiesto.
        6. Avvia il gioco e usa la lingua voce inglese/il package voce `0006`.

        L'installer modifica:

        - `0006\\0.pamt`
        - `0006\\0.paz`
        - `0006\\1.paz`
        - `meta\\0.papgt`

        Il backup viene creato in:

        `Crimson Desert\\crimson_desert_it_voice_backup\\DATA_ORA`

        ## Disinstallazione

        Metodo consigliato: da Steam usa "Verifica integrita dei file installati".

        Metodo manuale: copia dal backup i file `meta\\0.papgt`, `0006\\0.pamt`, `0006\\0.paz` e `0006\\1.paz` nella cartella del gioco.

        ## Qualita realistica

        Questa e una beta AI fan-made, non un doppiaggio professionale.
        Le voci sono state generate clonando/condizionando le voci originali: molte battute sono giocabili e comprensibili, ma alcune possono avere accento inglese o straniero, ritmo imperfetto, enfasi strana, pause non ideali o resa emotiva non sempre naturale.

        Per eliminare davvero gli accenti servirebbe un secondo progetto piu lungo con voci italiane dedicate, profili separati per personaggio e revisione manuale.

        ## Nota non commerciale

        Questo pacchetto e un progetto fan gratuito e non a scopo di lucro.
        Gli audio sono generati con AI e derivano/sono condizionati dalle voci originali del gioco: non venderlo, non metterlo dietro paywall e non monetizzarlo.
        Rispetta le regole del gioco, della piattaforma e dei titolari dei diritti. Se un avente diritto chiede la rimozione, il pacchetto va rimosso.
    ''')
    write_text(ready_dir / "DESCRIZIONE_MOD_PORTALE.md", f'''
        # Crimson Desert Italian Voice Mod v{VERSION} Beta

        Doppiaggio italiano AI fan-made per Crimson Desert.

        ## In breve

        Questa mod sostituisce il package voce `0006` con {count:,} file audio italiani generati con AI.
        Il pacchetto non include archivi `.paz` originali del gioco: contiene solo i WEM sostitutivi e un installer che li applica alla tua copia installata.

        ## Compatibilita verificata

        - Steam AppID: `{STEAM_APPID}`
        - Steam buildid testato: `{STEAM_BUILDID}`
        - `CrimsonDesert.exe`: `{EXE_VERSION}`
        - Data pacchetto: `2026-05-24`

        Su build diverse potrebbe funzionare, ma non e garantito. Se la provi su una versione successiva, segnala buildid, versione exe e risultato dell'installer.

        ## Qualita delle voci

        Release beta, molto ampia ma non perfetta.
        Le voci sono state generate clonando/condizionando le voci originali, quindi in alcune frasi si sente accento inglese/straniero o una cadenza non del tutto italiana.
        Possono esserci pronunce non perfette, pause strane, emozioni meno naturali o battute che suonano piu "AI" di altre.

        La mod e pensata per rendere il gioco giocabile in italiano, non per sostituire un doppiaggio professionale.
        Feedback e correzioni sono benvenuti.

        ## Installazione

        1. Estrai lo zip.
        2. Avvia `CONTROLLA_PRIMA.cmd`.
        3. Avvia `INSTALLA_MOD_VOCI_ITALIANE.cmd`.
        4. Se richiesto, indica la cartella di installazione di Crimson Desert.

        L'installer crea un backup automatico degli archivi modificati.

        ## Aggiornamenti del gioco

        Se una patch ufficiale aggiunge nuove quest o nuovi audio, quegli audio resteranno originali, normalmente in inglese.
        Se una patch rinomina o rimuove audio presenti nel manifest della mod, l'installer si ferma prima di patchare e serve una nuova versione.

        ## Uso e distribuzione

        Progetto fan gratuito, non commerciale.
        Gli audio sono generati con AI e derivano/sono condizionati dalle voci originali del gioco; per questo e vietata la vendita, il paywall o qualunque monetizzazione del pacchetto.
        Condividilo solo gratis e rispetta le richieste dei titolari dei diritti.
    ''')
    write_text(ready_dir / "THIRD_PARTY_NOTICES.md", '''
        # Third Party Notices

        - Python embeddable package for Windows x86-64 is included only to run the installer without requiring a system Python install. Python is distributed under the Python Software Foundation License.
        - Generated WEM audio files are included as replacement mod assets. No original `.paz` archive from the game is redistributed.
        - CrimsonForge/OmniVoice were used during creation and modification of the voice package. They are not required by end users of the ready package.
    ''')


def write_source_docs(source_dir: Path, manifest: dict, manifest_entries: list[dict]) -> None:
    count = manifest["completed_count"]
    write_text(source_dir / "README_MODDERS.md", f'''
        # Crimson Desert Italian Voice Mod - sorgenti v{VERSION}

        Questo pacchetto serve a verificare, modificare o continuare la mod.

        ## Cosa contiene

        - `crimsonforge_modified_source/`: sorgente CrimsonForge usato per generare e patchare gli audio.
        - `manifests/`: manifest dei WEM inclusi nel pacchetto ready, piu stato progress/target usato durante la generazione.
        - `scripts/`: installer, script stato e script di packaging.
        - `ready_package_helpers/`: documenti e helper del pacchetto pronto, senza payload audio completo.

        ## Stato release

        - Package group: `0006`
        - Voci italiane incluse: {count:,}
        - Text Dialogue esclusi: {manifest["excluded_text_dialogue_count"]:,}
        - Steam buildid testato: `{STEAM_BUILDID}`
        - `CrimsonDesert.exe`: `{EXE_VERSION}`

        ## Modificare una singola voce

        1. Avvia CrimsonForge patchato.
        2. Apri il game path di Crimson Desert.
        3. Cerca il WEM dal manifest.
        4. Rigenera una riga alla volta, preferibilmente con controllo ascolto.
        5. Usa `Generate + Patch` sulla riga singola.
        6. Ricrea il pacchetto ready con lo script di packaging.

        ## Ricreare il pacchetto ready

        Lo script principale e:

        ```powershell
        python scripts\\make_crimson_mod_packages_v02.py
        ```

        I percorsi sono quelli di questo PC e vanno aggiornati se lavori su un'altra macchina.

        ## Note tecniche importanti

        - Il batch di CrimsonForge patchato salta i record gia completati, cosi non rigenera tutto per errore.
        - Le righe `Text Dialogue` sono state escluse dal pacchetto ready.
        - Il sorgente non include archivi `.paz` originali del gioco.
        - Il ready package include solo WEM sostitutivi e script di patch.

        ## Qualita e diritti

        Questa e una beta AI non commerciale. Le voci sono clonate/condizionate dalle voci originali, quindi non va venduta o monetizzata.
        Per migliorare l'accento servono nuove voci italiane dedicate e revisione manuale.
    ''')
    write_text(source_dir / "SOURCE_CHANGES.txt", '''
        Principali modifiche rilevanti:

        - config.py legge settings.json con utf-8-sig per evitare errori da BOM.
        - ui/tab_audio.py: Generate All + Patch e batch worker saltano i record gia completati.
        - utils/tts_patch_progress.py: aggiunto has_completed_record e gestione dei record force_regenerate_reason.
        - Batch audio limitato al package 0006, file WEM, con Text Dialogue esclusi.
        - Pulizia testo TTS per evitare lettura di tag tipo StaticInfo e markup.
        - Script di stato aggiornati per non contare Text Dialogue come mancanti.
    ''')
    write_text(source_dir / "DESCRIZIONE_MOD_PORTALE.md", (ROOT / READY_NAME / "DESCRIZIONE_MOD_PORTALE.md").read_text(encoding="utf-8"))

    manifests_dir = source_dir / "manifests"
    manifests_dir.mkdir(parents=True, exist_ok=True)
    source_manifest = {
        "name": "Crimson Desert Italian Voice Mod source manifest",
        "version": VERSION,
        "created_at": manifest["created_at"],
        "tested_steam_buildid": STEAM_BUILDID,
        "tested_exe_version": EXE_VERSION,
        "entries": manifest_entries,
    }
    (manifests_dir / "audio_manifest_no_text.json").write_text(json.dumps(source_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    with (manifests_dir / "audio_manifest_no_text.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["group", "path", "category", "size", "orig_size", "sha256", "completed_at", "signature", "manual"])
        writer.writeheader()
        writer.writerows(manifest_entries)
    shutil.copy2(TARGETS, manifests_dir / "italian_audio_targets_0006.json")
    shutil.copy2(PROGRESS, manifests_dir / "tts_patch_progress.json")

    reports_dir = source_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    for pattern in [
        "restore_wrong_regen_*.json",
        "wrongly_regenerated_*.txt",
        "staticinfo_completed_report.json",
    ]:
        for src in WORKSPACE.glob(pattern):
            if src.is_file():
                shutil.copy2(src, reports_dir / src.name)

    scripts_dir = source_dir / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(WORKSPACE / "make_crimson_mod_packages_v02.py", scripts_dir / "make_crimson_mod_packages_v02.py")
    shutil.copy2(TEMPLATE_READY / "installer" / "apply_patch.py", scripts_dir / "apply_patch.py")
    for name in [
        "crimsonforge_status.ps1",
        "crimsonforge_status.cmd",
        "crimsonforge_shutdown_when_done.ps1",
        "crimsonforge_shutdown_when_done.cmd",
        "recover_crimson_batch_after_pamt_loss.py",
    ]:
        src = WORKSPACE / name
        if src.is_file():
            shutil.copy2(src, scripts_dir / name)
    launcher = Path(r"C:\Users\matte\Downloads\AVVIA_CRIMSONFORGE_126_PATCHATO.bat")
    if launcher.is_file():
        shutil.copy2(launcher, scripts_dir / launcher.name)

    helpers = source_dir / "ready_package_helpers"
    helpers.mkdir(parents=True, exist_ok=True)
    for src in [
        ROOT / READY_NAME / "README_INSTALLAZIONE.md",
        ROOT / READY_NAME / "DESCRIZIONE_MOD_PORTALE.md",
        ROOT / READY_NAME / "THIRD_PARTY_NOTICES.md",
        ROOT / READY_NAME / "INSTALLA_MOD_VOCI_ITALIANE.cmd",
        ROOT / READY_NAME / "CONTROLLA_PRIMA.cmd",
    ]:
        if src.is_file():
            shutil.copy2(src, helpers / src.name)


def write_checksums(directory: Path) -> None:
    lines = []
    for path in sorted(directory.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS.txt":
            lines.append(f"{sha256_file(path)}  {path.relative_to(directory).as_posix()}")
    (directory / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    if not TEMPLATE_READY.is_dir():
        raise RuntimeError(f"Template ready package not found: {TEMPLATE_READY}")
    ROOT.mkdir(parents=True, exist_ok=True)

    ready_dir = unique_path(ROOT / READY_NAME)
    source_dir = unique_path(ROOT / SOURCE_NAME)

    targets_payload = json.loads(TARGETS.read_text(encoding="utf-8"))
    all_targets = targets_payload.get("targets") or []
    completed = load_completed()

    non_text_targets = [
        t for t in all_targets
        if t.get("package_group", "0006") == "0006"
        and (t.get("category") or "") != "Text Dialogue"
    ]
    completed_targets = [
        t for t in non_text_targets
        if t.get("key") in completed and not completed[t["key"]].get("force_regenerate_reason")
    ]
    missing = [t for t in non_text_targets if t not in completed_targets]
    if missing:
        examples = ", ".join(t.get("path", "?") for t in missing[:5])
        raise RuntimeError(f"Missing completed non-text targets: {len(missing)}. Examples: {examples}")

    print(f"Creating {ready_dir}", flush=True)
    copy_ready_template(ready_dir)
    update_prereq_script(ready_dir)
    manifest, manifest_entries = build_payload(ready_dir, completed_targets, completed)
    write_ready_docs(ready_dir, manifest)
    write_checksums(ready_dir)

    print(f"Creating {source_dir}", flush=True)
    source_dir.mkdir(parents=True, exist_ok=True)
    copy_crimsonforge_source(source_dir / "crimsonforge_modified_source")
    write_source_docs(source_dir, manifest, manifest_entries)
    write_checksums(source_dir)

    ready_zip = ROOT / f"{ready_dir.name}.zip"
    source_zip = ROOT / f"{source_dir.name}.zip"
    print("Creating ready zip...", flush=True)
    add_tree_to_zip(ready_zip, ready_dir)
    print("Creating source zip...", flush=True)
    add_tree_to_zip(source_zip, source_dir)

    zip_checksums = {
        ready_zip.name: sha256_file(ready_zip),
        source_zip.name: sha256_file(source_zip),
    }
    summary = {
        "ready_dir": str(ready_dir),
        "source_dir": str(source_dir),
        "ready_zip": str(ready_zip),
        "source_zip": str(source_zip),
        "audio_entries": manifest["completed_count"],
        "excluded_text_dialogue_count": manifest["excluded_text_dialogue_count"],
        "payload_size": manifest["audio_payload_size"],
        "ready_zip_size": ready_zip.stat().st_size,
        "source_zip_size": source_zip.stat().st_size,
        "zip_sha256": zip_checksums,
        "tested_game_version": {
            "steam_appid": STEAM_APPID,
            "steam_buildid": STEAM_BUILDID,
            "crimson_desert_exe_version": EXE_VERSION,
            "tested_game_path": str(GAME),
            "tested_on": "2026-05-24",
            "compatibility_note": "Supported with certainty on this build. Other builds are unverified; added WEMs stay original/English, renamed or removed manifest WEMs require a new mod build.",
        },
        "standalone_note": "Ready package uses .cmd launchers plus official bundled Python embeddable runtime instead of an unsigned custom .exe to reduce antivirus false positives.",
    }
    (ROOT / "package_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_text(ROOT / "README_PACCHETTI.txt", f'''
        Pacchetti creati per Crimson Desert Italian Voice Mod v{VERSION}

        Compatibilita verificata:
        - Crimson Desert Steam AppID: {STEAM_APPID}
        - Steam buildid: {STEAM_BUILDID}
        - CrimsonDesert.exe: {EXE_VERSION}
        - Data pacchetto: 2026-05-24

        Su altre build: provare e segnalare esito/errori per aggiornare la compatibilita.

        1. {ready_dir.name}
           Pacchetto per utenti finali. Contiene solo payload voci, installer standalone .cmd e Python portatile ufficiale.
           Avvio consigliato: INSTALLA_MOD_VOCI_ITALIANE.cmd

        2. {source_dir.name}
           Pacchetto sorgenti/modifica. Contiene manifest, script, report e sorgenti CrimsonForge patchati.

        Zip:
        - {ready_zip.name}
          SHA256: {zip_checksums[ready_zip.name]}
        - {source_zip.name}
          SHA256: {zip_checksums[source_zip.name]}

        Nota: progetto fan gratuito/non commerciale. Gli audio sono AI e derivano/sono condizionati dalle voci originali: non vendere e non monetizzare.
    ''')

    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
