"""Wwise auto-detection and WAV→WEM Vorbis conversion.

Finds WwiseConsole.exe from:
  1. WWISEROOT environment variable
  2. Program Files / Program Files (x86) scan
  3. User-specified path in settings

Uses WwiseConsole.exe convert-external-source to produce proper
Vorbis-encoded WEM files that game engines accept.

If Wwise is not installed, guides the user to install it (free).
"""

import os
import glob
import shutil
import struct
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from typing import Optional, Tuple

from utils.logger import get_logger

logger = get_logger("utils.wwise")


def find_wwise_console() -> str:
    """Find WwiseConsole.exe on this system.

    Search order:
      1. WWISEROOT environment variable
      2. Common install locations (Program Files)
      3. All drives, Wwise folders

    Returns:
        Path to WwiseConsole.exe or empty string if not found.
    """
    # 1. Check WWISEROOT env
    wwiseroot = os.environ.get("WWISEROOT", "")
    if wwiseroot:
        console = os.path.join(wwiseroot, "Authoring", "x64", "Release", "bin",
                               "WwiseConsole.exe")
        if os.path.isfile(console):
            return console
        # Also check directly
        console = os.path.join(wwiseroot, "WwiseConsole.exe")
        if os.path.isfile(console):
            return console

    # 2. Check common locations
    search_roots = []
    for env in ["ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"]:
        p = os.environ.get(env, "")
        if p:
            search_roots.append(p)

    # Also check user AppData (Wwise Launcher installs here)
    appdata = os.environ.get("LOCALAPPDATA", "")
    if appdata:
        search_roots.append(os.path.join(appdata, "Audiokinetic"))

    # Check Audiokinetic root install on all available drive letters (C:\Audiokinetic, D:\Audiokinetic, etc.)
    # Wwise 2025+ installs to C:\Audiokinetic by default, NOT Program Files
    for drive_letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        candidate = f"{drive_letter}:\\Audiokinetic"
        if os.path.isdir(candidate):
            search_roots.append(candidate)

    for root in search_roots:
        if not os.path.isdir(root):
            continue
        # Search for Wwise folders
        for d in os.listdir(root):
            if "wwise" in d.lower() or "audiokinetic" in d.lower():
                wwise_dir = os.path.join(root, d)
                # Search recursively for WwiseConsole.exe
                for dirpath, dirnames, filenames in os.walk(wwise_dir):
                    for fn in filenames:
                        if fn.lower() == "wwiseconsole.exe":
                            return os.path.join(dirpath, fn)
        # Also check if the root itself contains versioned Wwise folders directly
        # e.g. C:\Audiokinetic\Wwise2025.1.6.9117\...
        for dirpath, dirnames, filenames in os.walk(root):
            for fn in filenames:
                if fn.lower() == "wwiseconsole.exe":
                    return os.path.join(dirpath, fn)
            break  # Only first level for root scan to avoid infinite traversal

    # 3. Check PATH
    path = shutil.which("WwiseConsole")
    if path:
        logger.info("Found WwiseConsole in PATH: %s", path)
        return path

    logger.warning("WwiseConsole.exe not found in common locations.")
    return ""


def is_wwise_installed() -> bool:
    """Check if Wwise is installed."""
    return bool(find_wwise_console())


def _get_batch_script_path() -> str:
    """Return the path to the embedded zSound2wem.cmd batch script."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "zSound2wem.cmd")


def convert_via_batch_script(
    wav_path: str,
    output_path: str = "",
    sample_rate: int = 48000,
    channels: int = 1,
    wwise_console: str = "",
    ffmpeg_path: str = "",
) -> str:
    """Convert WAV to WEM Vorbis using the embedded zSound2wem.cmd batch script.

    This is the primary conversion path — same proven script used for game modding.
    Handles Wwise detection, ffmpeg normalization, project creation, and output retrieval.

    Args:
        wav_path: Absolute path to input WAV file.
        output_path: Desired output WEM path. If empty, auto-generated next to wav.
        sample_rate: Target sample rate (default 48000).
        channels: Target channels (default 1 = mono).

    Returns:
        Path to output .wem file, or empty string on failure.
    """
    script = _get_batch_script_path()
    logger.info("[WWISE] convert_via_batch_script called")
    logger.info("[WWISE]   script path : %s", script)
    logger.info("[WWISE]   script exists: %s", os.path.isfile(script))
    if not os.path.isfile(script):
        logger.error("[WWISE] zSound2wem.cmd NOT FOUND at: %s", script)
        return ""

    wav_path = os.path.abspath(wav_path)
    logger.info("[WWISE]   input WAV   : %s", wav_path)
    logger.info("[WWISE]   WAV exists  : %s", os.path.isfile(wav_path))
    if not os.path.isfile(wav_path):
        logger.error("[WWISE] Input WAV not found: %s", wav_path)
        return ""

    logger.info("[WWISE]   sample_rate : %d  channels: %d", sample_rate, channels)

    import time
    start_time = time.time()

    # Output goes into a dedicated temp folder — batch script puts .wem there
    unique_id = os.urandom(4).hex()
    out_dir = os.path.join(tempfile.gettempdir(), f"cf_wem_out_{unique_id}")
    logger.info("[WWISE]   out_dir     : %s", out_dir)
    if os.path.isdir(out_dir):
        try:
            shutil.rmtree(out_dir)
        except OSError as e:
            logger.warning("[WWISE] Could not clear out_dir %s: %s", out_dir, e)
    os.makedirs(out_dir, exist_ok=True)

    # The script runs from its own directory (it uses cd %~dp0)
    script_dir = os.path.dirname(script)

    cmd = [
        "cmd.exe", "/d", "/c", script,
        f"--samplerate:{sample_rate}",
        f"--channels:{channels}",
        f"--out:{out_dir}",
    ]
    if wwise_console:
        cmd.append(f"--wwise:{wwise_console}")
    if ffmpeg_path:
        cmd.append(f"--ffmpeg:{ffmpeg_path}")
    cmd.append(wav_path)
    logger.info("[WWISE] Running zSound2wem.cmd: %s", subprocess.list2cmdline(cmd))

    try:
        result = subprocess.run(
            cmd,
            shell=False,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=script_dir,
        )
        logger.info("[WWISE] zSound2wem.cmd finished — returncode: %d", result.returncode)

        # Always promote stdout/stderr to INFO so they appear in the log
        if result.stdout.strip():
            logger.info("[WWISE] zSound2wem stdout:\n%s", result.stdout[:3000])
        if result.stderr.strip():
            logger.info("[WWISE] zSound2wem stderr:\n%s", result.stderr[:3000])

        if result.returncode != 0:
            logger.error("[WWISE] zSound2wem.cmd FAILED (rc=%d)", result.returncode)
            return ""
    except subprocess.TimeoutExpired:
        logger.error("[WWISE] zSound2wem.cmd timed out after 5 minutes")
        return ""
    except Exception as e:
        logger.error("[WWISE] zSound2wem.cmd execution error: %s", e)
        return ""

    # Find any .wem output produced in out_dir
    orig_stem = os.path.splitext(os.path.basename(wav_path))[0]
    found_wem = ""
    logger.info("[WWISE] Scanning out_dir for WEM (stem=%s): %s", orig_stem, out_dir)

    # List everything in out_dir for full visibility
    all_found = []
    for dirpath, dirnames, filenames in os.walk(out_dir):
        for fn in filenames:
            all_found.append(os.path.join(dirpath, fn))
    if all_found:
        logger.info("[WWISE] Files in out_dir after script: %s", all_found)
    else:
        logger.warning("[WWISE] out_dir is EMPTY after script ran — no files produced")

    for dirpath, dirnames, filenames in os.walk(out_dir):
        for fn in filenames:
            if fn.lower().endswith(".wem"):
                full = os.path.join(dirpath, fn)

                # Check timestamp to ensure it's NEW
                mtime = os.path.getmtime(full)
                if mtime < start_time - 1.0:  # 1s buffer for clock skew
                    logger.warning("[WWISE] Stale WEM (mtime=%s < start=%s): %s",
                                   mtime, start_time, full)
                    continue

                if os.path.getsize(full) < 100:
                    logger.warning("[WWISE] Empty/invalid WEM file (%d bytes): %s",
                                   os.path.getsize(full), full)
                    continue

                # Strict stem check
                if fn.lower() == f"{orig_stem.lower()}.wem":
                    found_wem = full
                    break
                # Fallback to startswith
                if fn.lower().startswith(orig_stem.lower()):
                    if not found_wem or len(fn) < len(os.path.basename(found_wem)):
                        found_wem = full

        if found_wem and os.path.basename(found_wem).lower() == f"{orig_stem.lower()}.wem":
            break

    if not found_wem:
        logger.error("[WWISE] NO new valid WEM found in %s for stem '%s'", out_dir, orig_stem)
        shutil.rmtree(out_dir, ignore_errors=True)
        return ""

    if not output_path:
        output_path = os.path.join(tempfile.gettempdir(), f"cf_wem_{orig_stem}.wem")

    # If the file exists, move it to output_path
    if os.path.abspath(found_wem) != os.path.abspath(output_path):
        if os.path.exists(output_path):
            try: os.remove(output_path)
            except: pass
        shutil.copy2(found_wem, output_path)
        try:
            os.remove(found_wem)
        except OSError:
            pass

    logger.info("zSound2wem.cmd successfully converted: %s -> %s (%d bytes)",
                wav_path, output_path, os.path.getsize(output_path))
    shutil.rmtree(out_dir, ignore_errors=True)
    return output_path

def convert_wav_to_wem_vorbis(
    wav_path: str,
    output_path: str = "",
    sample_rate: int = 48000,
    channels: int = 1,
    quality: str = "Vorbis Quality High",
    wwise_console: str = "",
) -> str:
    """Convert WAV to Vorbis-encoded WEM using WwiseConsole.exe.

    First attempts to use the robust zSound2wem.cmd batch script.
    If it fails or is missing, falls back to the internal Python implementation.

    Args:
        wav_path: Input WAV file path.
        output_path: Output WEM path. Auto-generated if empty.
        sample_rate: Target sample rate (default 48000).
        channels: Target channels (default 1 = mono).
        quality: Wwise conversion name (default "Vorbis Quality High").
        wwise_console: Path to WwiseConsole.exe. Auto-detected if empty.

    Returns:
        Path to output WEM file, or empty string on failure.
    """
    # 1. Try Batch Script Strategy
    logger.info("[WWISE] convert_wav_to_wem_vorbis — trying batch script first")
    logger.info("[WWISE]   wav_path    : %s", wav_path)
    logger.info("[WWISE]   output_path : %s", output_path or '(auto)')
    try:
        try:
            from core.audio_converter import get_ffmpeg_path
            ffmpeg_path = get_ffmpeg_path()
        except Exception:
            ffmpeg_path = ""
        res = convert_via_batch_script(
            wav_path, output_path, sample_rate, channels,
            wwise_console=wwise_console,
            ffmpeg_path=ffmpeg_path,
        )
        if res and os.path.isfile(res):
            logger.info("[WWISE] Batch script succeeded: %s", res)
            return res
        logger.warning("[WWISE] Batch script returned empty/missing path: %s", res)
    except Exception as e:
        logger.warning("[WWISE] Batch script raised exception: %s — falling back to Python", e)

    # 2. Fallback to Python implementation
    logger.info("Using Python Wwise conversion fallback...")
    if not wwise_console:
        wwise_console = find_wwise_console()
    if not wwise_console:
        logger.error("WwiseConsole.exe not found. Install Wwise from audiokinetic.com")
        return ""


    # Determine output path using the ORIGINAL wav stem (not the normalized copy)
    orig_stem = os.path.splitext(os.path.basename(wav_path))[0]
    if not output_path:
        output_path = os.path.join(tempfile.gettempdir(), f"cf_wem_{orig_stem}.wem")
    output_dir = os.path.dirname(os.path.abspath(output_path)) or tempfile.gettempdir()

    # --- Step 1: Normalize WAV with ffmpeg (matching batch script) ---
    from core.audio_converter import get_ffmpeg_path
    ffmpeg = get_ffmpeg_path()

    # Use a clean stem so Wwise outputs <stem>.wem (not <stem>.wav.norm.wem)
    tmp_dir = tempfile.mkdtemp(prefix="cf_wwise_")
    normalized_wav = os.path.join(tmp_dir, f"{orig_stem}.wav")

    if ffmpeg:
        try:
            result = subprocess.run(
                [ffmpeg, "-hide_banner", "-loglevel", "warning",
                 "-y", "-i", wav_path,
                 "-ar", str(sample_rate), "-ac", str(channels),
                 "-sample_fmt", "s16", normalized_wav],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0 or not os.path.isfile(normalized_wav):
                logger.warning("ffmpeg normalize failed (%s), using original WAV", result.stderr[:200])
                shutil.copy2(wav_path, normalized_wav)
        except Exception as e:
            logger.warning("ffmpeg normalize error: %s, using original WAV", e)
            shutil.copy2(wav_path, normalized_wav)
    else:
        shutil.copy2(wav_path, normalized_wav)

    # --- Step 2: Create persistent Wwise project via WwiseConsole ---
    project_root = os.path.abspath(os.path.join(tempfile.gettempdir(), "cf_wwise_project"))
    project_name = "cf_convert"
    project_dir = os.path.join(project_root, project_name)
    wproj_path = os.path.normpath(os.path.join(project_dir, f"{project_name}.wproj"))

    # If the project exists but was created by our broken minimal XML, it will
    # fail on load. Delete and recreate it via WwiseConsole to ensure validity.
    if os.path.isfile(wproj_path):
        if os.path.getsize(wproj_path) < 2000:
            logger.warning("[WWISE] Existing .wproj looks like broken minimal XML (%d bytes) — deleting",
                           os.path.getsize(wproj_path))
            try:
                shutil.rmtree(os.path.dirname(wproj_path), ignore_errors=True)
            except Exception:
                pass

    if not os.path.isfile(wproj_path):
        logger.info("[WWISE] Creating persistent Wwise project at: %s", wproj_path)
        if os.path.isdir(project_dir):
            shutil.rmtree(project_dir, ignore_errors=True)
        os.makedirs(project_root, exist_ok=True)
        try:
            r = subprocess.run(
                [wwise_console, "create-new-project", wproj_path, "--platform", "Windows", "--quiet"],
                capture_output=True, text=True, timeout=120,
            )
            logger.info("[WWISE] create-new-project rc=%d stdout=%s stderr=%s",
                        r.returncode, r.stdout[:500], r.stderr[:500])
            if r.returncode != 0:
                r = subprocess.run(
                    [wwise_console, "create-new-project", wproj_path, "--platform", "Windows"],
                    capture_output=True, text=True, timeout=120,
                )
                logger.info("[WWISE] create-new-project retry rc=%d stdout=%s stderr=%s",
                            r.returncode, r.stdout[:1000], r.stderr[:1000])
        except Exception as e:
            logger.error("[WWISE] Could not create Wwise project: %s", e)

    if not os.path.isfile(wproj_path):
        logger.error("[WWISE] .wproj still missing after create-new-project — WEM conversion will fail")

    # --- Step 3: Write .wsources XML (batch script format) ---
    wsources_path = os.path.join(tmp_dir, "list.wsources")
    # CRITICAL FIX: Direct WwiseConsole to an ISOLATED directory within tmp_dir
    # previously it aimed at output_dir, causing collisions with other WEMs
    isolated_output_dir = os.path.join(tmp_dir, "wem_out")
    os.makedirs(isolated_output_dir, exist_ok=True)

    _write_wsources(wsources_path, normalized_wav, isolated_output_dir, quality)
    try:
        wsources_content = open(wsources_path, encoding="utf-8").read()
        logger.info("[WWISE] wsources XML:\n%s", wsources_content)
    except OSError:
        pass

    # --- Step 4: Run WwiseConsole (matching batch script command) ---
    cmd = [
        wwise_console,
        "convert-external-source",
        wproj_path,
        "--source-file", wsources_path,
        "--output", isolated_output_dir,
        "--quiet",
    ]
    logger.info("[WWISE] Running WwiseConsole (Python fallback): %s", " ".join(cmd))
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        logger.info("[WWISE] WwiseConsole returncode: %d", result.returncode)
        if result.stdout.strip():
            logger.info("[WWISE] WwiseConsole stdout:\n%s", result.stdout[:3000])
        if result.stderr.strip():
            logger.info("[WWISE] WwiseConsole stderr:\n%s", result.stderr[:3000])
        if result.returncode != 0:
            logger.warning("[WWISE] WwiseConsole rc=%d, retrying without --quiet", result.returncode)
            cmd_nq = [c for c in cmd if c != "--quiet"]
            result = subprocess.run(cmd_nq, capture_output=True, text=True, timeout=180)
            logger.info("[WWISE] WwiseConsole retry returncode: %d", result.returncode)
            if result.stdout.strip():
                logger.info("[WWISE] WwiseConsole retry stdout:\n%s", result.stdout[:3000])
            if result.stderr.strip():
                logger.info("[WWISE] WwiseConsole retry stderr:\n%s", result.stderr[:3000])
    except Exception as e:
        logger.error("[WWISE] WwiseConsole execution error: %s", e)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return ""

    # --- Step 5: Find output WEM (batch script: move Windows\* to output) ---
    stem = os.path.splitext(os.path.basename(normalized_wav))[0]
    found_wem = ""

    # Search isolated_output_dir specifically for our stem
    windows_subdir = os.path.join(isolated_output_dir, "Windows")
    for search_root in [windows_subdir, isolated_output_dir]:
        if not os.path.isdir(search_root):
            continue
        for dirpath, dirnames, filenames in os.walk(search_root):
            for fn in filenames:
                if fn.lower().endswith(".wem") and fn.lower().startswith(stem.lower()):
                    if os.path.getsize(os.path.join(dirpath, fn)) > 100:
                        found_wem = os.path.join(dirpath, fn)
                        break
            if found_wem:
                break
        if found_wem:
            break

    if found_wem:
        if os.path.abspath(found_wem) != os.path.abspath(output_path):
            shutil.copy2(found_wem, output_path)
            # Clean up Windows\ subfolder (match batch: move Windows\* to output then rmdir)
            try:
                if os.path.isdir(windows_subdir):
                    shutil.rmtree(windows_subdir, ignore_errors=True)
            except Exception:
                pass
        logger.info("Wwise converted: %s -> %s (%d bytes)",
                    wav_path, output_path, os.path.getsize(output_path))
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return output_path

    logger.error("WwiseConsole produced no output WEM. stdout=%s stderr=%s",
                 result.stdout[:500], result.stderr[:500])
    shutil.rmtree(tmp_dir, ignore_errors=True)
    return ""


def _write_minimal_wproj(path: str, quality: str = "Vorbis Quality High"):
    """Write a minimal Wwise project file for conversion."""
    content = f"""<?xml version="1.0" encoding="utf-8"?>
<WwiseDocument Type="WorkUnit" SchemaVersion="110">
    <ProjectInfo>
        <Project Name="cf_convert" Version="1">
            <Property Name="DefaultConversion" Type="string" Value="{quality}"/>
        </Project>
    </ProjectInfo>
</WwiseDocument>"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _write_wsources(path: str, wav_path: str, output_dir: str, quality: str = "Vorbis Quality High"):
    """Write a .wsources XML file listing WAV files for conversion.
    
    quality must be the full Wwise conversion name, e.g. 'Vorbis Quality High'.
    """
    abs_wav = os.path.abspath(wav_path)

    content = f"""<?xml version="1.0" encoding="UTF-8"?>
<ExternalSourcesList SchemaVersion="1" Root="{os.path.dirname(abs_wav)}">
    <Source Path="{os.path.basename(abs_wav)}" Conversion="{quality}"/>
</ExternalSourcesList>"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
