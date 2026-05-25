"""Audio format conversion for game modding.

Converts between WEM (Wwise), WAV, and OGG formats.
Uses vgmstream-cli for WEM→WAV and ffmpeg for WAV→OGG.
"""

import os
import subprocess
import tempfile
import shutil
from typing import Optional

from utils.logger import get_logger

logger = get_logger("core.audio_converter")


def _find_tool(name: str) -> str:
    """Find a command-line tool in PATH or managed install."""
    # Check PATH
    path = shutil.which(name)
    if path:
        return path

    # Check managed install location
    managed = os.path.join(
        os.path.expanduser("~"), ".crimsonforge", "tools", name,
        f"{name}.exe" if os.name == "nt" else name
    )
    if os.path.isfile(managed):
        return managed

    return ""


def get_vgmstream_path() -> str:
    """Get path to vgmstream-cli executable. Auto-installs if not found."""
    from utils.vgmstream_installer import get_vgmstream_path as _get, install_vgmstream
    path = _get()
    if path:
        return path
    # Auto-install
    logger.info("vgmstream not found, auto-installing...")
    success, msg = install_vgmstream(lambda m: logger.info("vgmstream: %s", m))
    if success:
        return _get()
    logger.warning("vgmstream auto-install failed: %s", msg)
    return ""


def get_ffmpeg_path() -> str:
    """Get path to ffmpeg executable. Auto-installs if not found."""
    from utils.ffmpeg_installer import get_ffmpeg_path as _get, is_installed, install_ffmpeg
    path = _get()
    if path:
        return path
    # Auto-install
    logger.info("ffmpeg not found, auto-installing...")
    success, msg = install_ffmpeg(lambda m: logger.info("ffmpeg: %s", m))
    if success:
        return _get()
    logger.warning("ffmpeg auto-install failed: %s", msg)
    return ""


def wem_to_wav(wem_path: str, output_path: str = "") -> str:
    """Convert WEM/BNK to WAV using vgmstream-cli.

    Args:
        wem_path: Path to input WEM/BNK file.
        output_path: Optional output WAV path. Auto-generated if empty.

    Returns:
        Path to output WAV file, or empty string on failure.
    """
    vgmstream = get_vgmstream_path()
    if not vgmstream:
        raise RuntimeError(
            "vgmstream-cli not found. It will be auto-installed on first audio preview."
        )

    if not output_path:
        basename = os.path.splitext(os.path.basename(wem_path))[0]
        output_path = os.path.join(tempfile.gettempdir(), f"cf_export_{basename}.wav")

    try:
        result = subprocess.run(
            [vgmstream, "-o", output_path, wem_path],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0 and os.path.isfile(output_path):
            logger.info("Converted WEM to WAV: %s -> %s", wem_path, output_path)
            return output_path

        # ── Better diagnostics for BNK previews (2026-05-08) ──
        # BNK = Wwise SoundBank, a CONTAINER of streams. vgmstream
        # opens it but can fail when the bank uses Crimson Desert's
        # encryption variant or when the requested sub-stream index
        # is out of range. The previous error log was a single line
        # of stderr, which buried the actual cause. Now we surface
        # the file path + size + sub-stream count hint so the next
        # preview attempt can be targeted, and we log at WARNING
        # rather than ERROR (the app keeps working — only this one
        # preview failed; ERROR level scares users into thinking the
        # whole audio pipeline is broken).
        ext = os.path.splitext(wem_path.lower())[1]
        if ext == ".bnk":
            try:
                size = os.path.getsize(wem_path)
            except OSError:
                size = -1
            logger.warning(
                "BNK preview unavailable for %s (%d bytes). "
                "vgmstream stderr: %s. "
                "Crimson Desert ships some BNKs in Wwise's encrypted "
                "form which this vgmstream build can't decode. "
                "Single-WEM previews continue to work — only the "
                "in-bank streams of this specific BNK are affected.",
                os.path.basename(wem_path), size,
                result.stderr.strip().splitlines()[0]
                if result.stderr.strip() else "(empty)",
            )
        else:
            logger.error("vgmstream failed: %s", result.stderr)
        return ""
    except Exception as e:
        logger.error("WEM to WAV conversion error: %s", e)
        return ""


def wav_to_ogg(wav_path: str, output_path: str = "", quality: int = 5) -> str:
    """Convert WAV to OGG Vorbis using ffmpeg.

    Args:
        wav_path: Path to input WAV file.
        output_path: Optional output OGG path.
        quality: OGG quality (0-10, default 5).

    Returns:
        Path to output OGG file, or empty string on failure.
    """
    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found. Install ffmpeg and add to PATH.")

    if not output_path:
        basename = os.path.splitext(os.path.basename(wav_path))[0]
        output_path = os.path.join(tempfile.gettempdir(), f"cf_export_{basename}.ogg")

    try:
        result = subprocess.run(
            [ffmpeg, "-y", "-i", wav_path, "-c:a", "libvorbis",
             "-q:a", str(quality), output_path],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0 and os.path.isfile(output_path):
            logger.info("Converted WAV to OGG: %s -> %s", wav_path, output_path)
            return output_path
        else:
            logger.error("ffmpeg WAV->OGG failed: %s", result.stderr)
            return ""
    except Exception as e:
        logger.error("WAV to OGG conversion error: %s", e)
        return ""


def audio_to_wav(audio_path: str, output_path: str = "",
                 sample_rate: int = 24000, channels: int = 1) -> str:
    """Convert any ffmpeg-supported audio file to WAV PCM."""
    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found. Install ffmpeg and add to PATH.")

    if not output_path:
        basename = os.path.splitext(os.path.basename(audio_path))[0]
        output_path = os.path.join(tempfile.gettempdir(), f"cf_audio_{basename}.wav")

    try:
        result = subprocess.run(
            [
                ffmpeg, "-y", "-i", audio_path,
                "-ar", str(sample_rate),
                "-ac", str(channels),
                "-sample_fmt", "s16",
                output_path,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0 and os.path.isfile(output_path):
            logger.info("Converted audio to WAV: %s -> %s", audio_path, output_path)
            return output_path
        logger.error("ffmpeg audio->WAV failed: %s", result.stderr)
        return ""
    except Exception as e:
        logger.error("Audio to WAV conversion error: %s", e)
        return ""


def wav_to_wem(wav_path: str, original_wem_data: bytes,
               output_path: str = "", allow_pcm_fallback: bool = True) -> str:
    """Convert WAV to WEM format for game patching.

    Strategy: The game's WEM files use Vorbis encoding (format 0xFFFF).
    We normalize the WAV to match the original (48kHz mono), convert to
    OGG Vorbis via ffmpeg, then wrap in the original WEM's RIFF header
    structure so the game engine recognizes it.

    If ffmpeg is not available, falls back to writing raw PCM WAV
    with a RIFF/WAVE header matching the original WEM parameters.

    Args:
        wav_path: Path to input WAV file (e.g. from TTS).
        original_wem_data: Original WEM file bytes (for header reference).
        output_path: Optional output path.
        allow_pcm_fallback: Build a PCM WEM if Wwise conversion fails. Set
            this to False when patching game audio that requires Vorbis WEM.

    Returns:
        Path to output WEM file, or empty string on failure.
    """
    import struct

    logger.info("[CONVERTER] wav_to_wem called")
    logger.info("[CONVERTER]   wav_path   : %s", wav_path)
    logger.info("[CONVERTER]   wav exists : %s", os.path.isfile(wav_path))
    logger.info("[CONVERTER]   orig_wem   : %d bytes", len(original_wem_data))

    if not output_path:
        basename = os.path.splitext(os.path.basename(wav_path))[0]
        output_path = os.path.join(tempfile.gettempdir(), f"cf_wem_{basename}.wem")
    logger.info("[CONVERTER]   output_path: %s", output_path)

    # Read original WEM to get sample rate and channels
    orig_sample_rate = 48000
    orig_channels = 1
    if len(original_wem_data) >= 28 and original_wem_data[:4] == b"RIFF":
        orig_channels = struct.unpack_from("<H", original_wem_data, 22)[0]
        orig_sample_rate = struct.unpack_from("<I", original_wem_data, 24)[0]
        logger.info("[CONVERTER]   orig WEM header: %d Hz, %d ch", orig_sample_rate, orig_channels)
    else:
        logger.warning("[CONVERTER]   original WEM is not RIFF/WEM — using defaults 48000 Hz mono")

    # Step 1: Normalize WAV to match original (sample rate, channels)
    ffmpeg = get_ffmpeg_path()
    logger.info("[CONVERTER]   ffmpeg path: %s", ffmpeg or '(NOT FOUND)')
    normalized_wav = wav_path + ".norm.wav"

    if ffmpeg:
        try:
            result = subprocess.run(
                [ffmpeg, "-y", "-i", wav_path,
                 "-ar", str(orig_sample_rate),
                 "-ac", str(orig_channels),
                 "-sample_fmt", "s16",
                 normalized_wav],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode == 0 and os.path.isfile(normalized_wav):
                logger.info("[CONVERTER]   ffmpeg normalize OK -> %s", normalized_wav)
                wav_path = normalized_wav
            else:
                logger.warning("[CONVERTER]   ffmpeg normalize FAILED (rc=%d): %s",
                               result.returncode, result.stderr[:500])
        except Exception as exc:
            logger.warning("[CONVERTER]   ffmpeg normalize exception: %s", exc)
    else:
        logger.warning("[CONVERTER]   ffmpeg not available — skipping WAV normalization")

    # Step 2: Try to create OGG Vorbis (matches the game's WEM Vorbis format)
    ogg_path = wav_path + ".ogg"
    has_ogg = False

    if ffmpeg:
        try:
            result = subprocess.run(
                [ffmpeg, "-y", "-i", wav_path,
                 "-c:a", "libvorbis",
                 "-ar", str(orig_sample_rate),
                 "-ac", str(orig_channels),
                 "-q:a", "5",
                 ogg_path],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode == 0 and os.path.isfile(ogg_path):
                has_ogg = True
                logger.info("[CONVERTER]   ffmpeg OGG created: %s", ogg_path)
            else:
                logger.warning("[CONVERTER]   ffmpeg OGG FAILED (rc=%d): %s",
                               result.returncode, result.stderr[:300])
        except Exception as exc:
            logger.warning("[CONVERTER]   ffmpeg OGG exception: %s", exc)

    # Try Wwise for proper Vorbis encoding (game requires Vorbis WEM)
    logger.info("[CONVERTER]   Calling Wwise for Vorbis WEM encoding...")
    from utils.wwise_installer import find_wwise_console, convert_wav_to_wem_vorbis
    wwise = find_wwise_console()
    logger.info("[CONVERTER]   WwiseConsole path: %s", wwise or '(NOT FOUND)')
    if wwise:
        result = convert_wav_to_wem_vorbis(
            wav_path, output_path,
            sample_rate=orig_sample_rate,
            channels=orig_channels,
            wwise_console=wwise,
        )
        if result:
            logger.info("[CONVERTER]   Wwise conversion OK: %s (%d bytes)",
                        result, os.path.getsize(result))
            # Cleanup temp files
            for tmp_file in [normalized_wav, ogg_path]:
                if os.path.isfile(tmp_file) and tmp_file != wav_path:
                    try:
                        os.unlink(tmp_file)
                    except Exception:
                        pass
            return result
        else:
            logger.error("[CONVERTER]   Wwise conversion returned empty path")

    if not allow_pcm_fallback:
        logger.error("[CONVERTER]   PCM fallback disabled; refusing non-Vorbis WEM")
        return ""

    # Fallback: build PCM WEM (may not play in all games)
    logger.warning("Wwise not found — building PCM WEM (may be silent in-game). "
                    "Install Wwise from audiokinetic.com for proper Vorbis encoding.")
    with open(wav_path, "rb") as f:
        wav_data = f.read()

    wem = _build_pcm_wem(wav_data, orig_sample_rate, orig_channels)

    with open(output_path, "wb") as f:
        f.write(wem)

    # Cleanup temp files
    for tmp_file in [normalized_wav, ogg_path]:
        if os.path.isfile(tmp_file) and tmp_file != wav_path:
            try:
                os.unlink(tmp_file)
            except Exception:
                pass

    logger.info("Converted WAV to WEM (PCM fallback): %s -> %s (%d bytes)",
                wav_path, output_path, len(wem))
    return output_path


def _build_pcm_wem(wav_data: bytes, sample_rate: int, channels: int) -> bytes:
    """Build a PCM WEM file (RIFF/WAVE format=1) from WAV data.

    This creates a clean RIFF/WAVE with PCM audio data matching the original
    file's sample rate and channels. Game patch callers should keep
    ``allow_pcm_fallback=False`` so they do not inject PCM where the voice
    banks expect Vorbis WEMs.
    """
    import struct

    # Extract raw PCM from input WAV
    pcm_data = b""
    if wav_data[:4] == b"RIFF" and wav_data[8:12] == b"WAVE":
        # Find 'data' chunk
        i = 12
        while i < len(wav_data) - 8:
            cid = wav_data[i:i + 4]
            csz = struct.unpack_from("<I", wav_data, i + 4)[0]
            if cid == b"data":
                pcm_data = wav_data[i + 8:i + 8 + csz]
                break
            i += 8 + csz
    else:
        # Raw PCM data
        pcm_data = wav_data

    if not pcm_data:
        return wav_data  # fallback: return as-is

    # Build clean RIFF/WAVE with PCM format
    bits_per_sample = 16
    byte_rate = sample_rate * channels * (bits_per_sample // 8)
    block_align = channels * (bits_per_sample // 8)

    fmt_chunk = struct.pack("<4sIHHIIHH",
                            b"fmt ", 16,      # chunk size
                            1,                  # format = PCM
                            channels,
                            sample_rate,
                            byte_rate,
                            block_align,
                            bits_per_sample)

    data_chunk = b"data" + struct.pack("<I", len(pcm_data)) + pcm_data

    riff_size = 4 + len(fmt_chunk) + len(data_chunk)  # 4 for "WAVE"
    riff_header = b"RIFF" + struct.pack("<I", riff_size) + b"WAVE"

    return riff_header + fmt_chunk + data_chunk


def normalize_wav(wav_path: str, sample_rate: int = 48000,
                  channels: int = 1, bit_depth: int = 16) -> str:
    """Normalize a WAV file to specific parameters using ffmpeg.

    Ensures the WAV matches the game's expected format before import.
    """
    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        return wav_path  # return as-is if no ffmpeg

    output = wav_path + ".normalized.wav"
    try:
        result = subprocess.run(
            [ffmpeg, "-y", "-i", wav_path,
             "-ar", str(sample_rate), "-ac", str(channels),
             "-sample_fmt", f"s{bit_depth}", output],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0 and os.path.isfile(output):
            os.replace(output, wav_path)
            return wav_path
    except Exception:
        pass
    return wav_path


def get_audio_info(path: str) -> dict:
    """Get audio file info (duration, sample rate, channels, format)."""
    info = {
        "path": path,
        "size": os.path.getsize(path) if os.path.isfile(path) else 0,
        "duration_ms": 0,
        "sample_rate": 0,
        "channels": 0,
        "format": os.path.splitext(path)[1].lower(),
    }

    # Try reading WAV header
    if info["format"] == ".wav" and info["size"] > 44:
        try:
            import struct
            with open(path, "rb") as f:
                riff = f.read(4)
                if riff == b"RIFF":
                    f.read(4)  # file size
                    f.read(4)  # WAVE
                    f.read(4)  # fmt chunk id
                    f.read(4)  # chunk size
                    audio_fmt = struct.unpack("<H", f.read(2))[0]
                    channels = struct.unpack("<H", f.read(2))[0]
                    sample_rate = struct.unpack("<I", f.read(4))[0]
                    byte_rate = struct.unpack("<I", f.read(4))[0]
                    f.read(2)  # block align
                    bits_per_sample = struct.unpack("<H", f.read(2))[0]

                    info["sample_rate"] = sample_rate
                    info["channels"] = channels
                    if byte_rate > 0:
                        data_size = info["size"] - 44
                        info["duration_ms"] = int((data_size / byte_rate) * 1000)
        except Exception:
            pass

    return info
