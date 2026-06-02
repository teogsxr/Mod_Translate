"""Auto-download and install vgmstream-cli for Wwise audio decoding.

Downloads the latest portable vgmstream-cli from GitHub releases,
extracts it to ~/.crimsonforge/tools/vgmstream/, and makes it
available for subprocess calls.

No admin rights required - everything lives in user-space.
Handles SSL certificate issues silently (common on Windows).
"""

import os
import ssl
import shutil
import zipfile
import tempfile
import platform
import urllib.request
import urllib.error

from utils.app_paths import app_root
from utils.logger import get_logger

logger = get_logger("utils.vgmstream_installer")

VGMSTREAM_RELEASE_URL = (
    "https://github.com/vgmstream/vgmstream-releases/releases/download/nightly/"
    "vgmstream-win64.zip"
)
INSTALL_DIR_NAME = "vgmstream"
TOOLS_DIR = os.path.join(os.path.expanduser("~"), ".crimsonforge", "tools")
VGMSTREAM_DIR = os.path.join(TOOLS_DIR, INSTALL_DIR_NAME)
VGMSTREAM_EXE = os.path.join(VGMSTREAM_DIR, "vgmstream-cli.exe")


def get_vgmstream_path() -> str:
    """Return path to vgmstream-cli if available, or empty string.

    Checks:
    1. Bundled runtime tools
    2. System PATH (user already installed it)
    3. Our managed install at ~/.crimsonforge/tools/vgmstream/
    """
    bundled = app_root() / "tools" / "vgmstream" / "vgmstream-cli.exe"
    if bundled.is_file():
        return str(bundled)

    system_path = shutil.which("vgmstream-cli") or shutil.which("vgmstream123")
    if system_path:
        return system_path

    if os.path.isfile(VGMSTREAM_EXE):
        return VGMSTREAM_EXE

    return ""


def is_installed() -> bool:
    """Check if vgmstream-cli is available."""
    return bool(get_vgmstream_path())


def _download_file(url: str, dest: str) -> None:
    """Download a file, silently handling SSL cert issues on Windows."""
    # Try normal download first
    try:
        urllib.request.urlretrieve(url, dest)
        return
    except urllib.error.URLError as e:
        if "CERTIFICATE_VERIFY_FAILED" not in str(e):
            raise

    # SSL cert issue - try certifi first, then fallback
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx.load_verify_locations(certifi.where())
    except (ImportError, Exception):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ctx)
    )
    with opener.open(url) as resp:
        with open(dest, "wb") as out:
            out.write(resp.read())


def _flatten_zip_subdirs(target_dir: str) -> None:
    """If the zip extracted into a subdirectory, move files up."""
    for root, dirs, files in os.walk(target_dir):
        for f in files:
            if f.lower() == "vgmstream-cli.exe" and root != target_dir:
                for item in os.listdir(root):
                    src = os.path.join(root, item)
                    dst = os.path.join(target_dir, item)
                    if os.path.exists(dst):
                        if os.path.isdir(dst):
                            shutil.rmtree(dst)
                        else:
                            os.remove(dst)
                    shutil.move(src, dst)
                return


def install_vgmstream(progress_callback=None) -> tuple[bool, str]:
    """Download and install vgmstream-cli silently.

    Args:
        progress_callback: Optional callable(message: str) for status updates.

    Returns:
        (success: bool, message: str)
    """
    if platform.system() != "Windows":
        return False, "Auto-install only supported on Windows."

    def report(msg):
        logger.info(msg)
        if progress_callback:
            progress_callback(msg)

    try:
        report("Downloading vgmstream audio decoder...")
        os.makedirs(TOOLS_DIR, exist_ok=True)

        tmp_zip = os.path.join(tempfile.gettempdir(), "vgmstream-win64.zip")
        _download_file(VGMSTREAM_RELEASE_URL, tmp_zip)

        zip_size = os.path.getsize(tmp_zip)
        report(f"Downloaded ({zip_size / 1024 / 1024:.1f} MB). Installing...")

        if os.path.isdir(VGMSTREAM_DIR):
            shutil.rmtree(VGMSTREAM_DIR)

        os.makedirs(VGMSTREAM_DIR, exist_ok=True)
        with zipfile.ZipFile(tmp_zip, "r") as zf:
            zf.extractall(VGMSTREAM_DIR)

        try:
            os.remove(tmp_zip)
        except OSError:
            pass

        # Handle zip with subdirectory
        if not os.path.isfile(VGMSTREAM_EXE):
            _flatten_zip_subdirs(VGMSTREAM_DIR)

        if os.path.isfile(VGMSTREAM_EXE):
            report("vgmstream installed successfully.")
            return True, "Installed successfully"

        return False, "vgmstream-cli.exe not found in archive"

    except Exception as e:
        logger.error("vgmstream install failed: %s", e)
        return False, str(e)


def uninstall_vgmstream() -> tuple[bool, str]:
    """Remove the managed vgmstream installation."""
    if os.path.isdir(VGMSTREAM_DIR):
        shutil.rmtree(VGMSTREAM_DIR)
        return True, "vgmstream-cli uninstalled"
    return True, "vgmstream-cli was not installed"
