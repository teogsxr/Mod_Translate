"""Auto-install ffmpeg binary for audio conversion.

Downloads ffmpeg essentials build from GitHub and extracts to
~/.crimsonforge/tools/ffmpeg/ffmpeg.exe
"""

import os
import sys
import zipfile
import tempfile
import shutil
from typing import Optional, Callable, Tuple

from utils.app_paths import app_root
from utils.logger import get_logger

logger = get_logger("utils.ffmpeg_installer")

FFMPEG_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
INSTALL_DIR = os.path.join(os.path.expanduser("~"), ".crimsonforge", "tools", "ffmpeg")


def get_ffmpeg_path() -> str:
    """Get path to ffmpeg executable."""
    bundled = app_root() / "tools" / "ffmpeg" / "ffmpeg.exe"
    if bundled.is_file():
        return str(bundled)

    managed = os.path.join(INSTALL_DIR, "ffmpeg.exe")
    if os.path.isfile(managed):
        return managed

    path = shutil.which("ffmpeg")
    if path:
        return path

    return ""


def is_installed() -> bool:
    return bool(get_ffmpeg_path())


def install_ffmpeg(progress_callback: Optional[Callable[[str], None]] = None) -> Tuple[bool, str]:
    """Download and install ffmpeg binary.

    Returns:
        (success, message) tuple.
    """
    try:
        import urllib.request
        import ssl

        os.makedirs(INSTALL_DIR, exist_ok=True)

        if progress_callback:
            progress_callback("Downloading ffmpeg (~80MB)...")

        # Download
        zip_path = os.path.join(tempfile.gettempdir(), "ffmpeg_download.zip")

        # Try with SSL first, fall back to no-verify
        try:
            urllib.request.urlretrieve(FFMPEG_URL, zip_path)
        except Exception:
            try:
                import certifi
                ctx = ssl.create_default_context(cafile=certifi.where())
                urllib.request.urlretrieve(FFMPEG_URL, zip_path, context=ctx)
            except Exception:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                opener = urllib.request.build_opener(
                    urllib.request.HTTPSHandler(context=ctx))
                with opener.open(FFMPEG_URL) as resp:
                    with open(zip_path, "wb") as f:
                        f.write(resp.read())

        if not os.path.isfile(zip_path) or os.path.getsize(zip_path) < 1000:
            return False, "Download failed — file too small"

        if progress_callback:
            progress_callback("Extracting ffmpeg...")

        # Extract — find ffmpeg.exe inside the zip
        with zipfile.ZipFile(zip_path, "r") as zf:
            for name in zf.namelist():
                basename = os.path.basename(name)
                if basename in ("ffmpeg.exe", "ffprobe.exe"):
                    # Extract to install dir
                    data = zf.read(name)
                    out_path = os.path.join(INSTALL_DIR, basename)
                    with open(out_path, "wb") as f:
                        f.write(data)
                    logger.info("Extracted %s (%d bytes)", basename, len(data))

        # Cleanup zip
        try:
            os.unlink(zip_path)
        except Exception:
            pass

        # Verify
        ffmpeg_exe = os.path.join(INSTALL_DIR, "ffmpeg.exe")
        if os.path.isfile(ffmpeg_exe):
            logger.info("ffmpeg installed to %s", ffmpeg_exe)
            return True, f"Installed to {INSTALL_DIR}"
        else:
            return False, "Extraction failed — ffmpeg.exe not found in archive"

    except Exception as e:
        logger.error("ffmpeg install error: %s", e)
        return False, str(e)
