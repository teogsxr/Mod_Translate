"""Cross-platform file operation utilities.

Handles platform-specific differences for file timestamps,
path normalization, and system directories.
"""

import os
import sys
import shutil
import platform
import tempfile
from pathlib import Path
from typing import Optional


def get_platform() -> str:
    """Get normalized platform name: 'windows', 'macos', or 'linux'."""
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    if system == "windows":
        return "windows"
    return "linux"


def normalize_path(path: str) -> str:
    """Normalize a file path for the current platform."""
    normalized = os.path.normpath(path)
    if get_platform() == "windows":
        normalized = normalized.replace("/", "\\")
    else:
        normalized = normalized.replace("\\", "/")
    return normalized


def get_file_size(path: str) -> int:
    """Get file size in bytes. Raises FileNotFoundError with clear message."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"File not found: {path}. Check that the path is correct and the file exists."
        )
    return p.stat().st_size


def get_file_timestamps(path: str) -> dict:
    """Get file timestamps as a dict with 'modified', 'accessed', and 'created' keys.

    On macOS/Linux, 'created' is the ctime (metadata change time).
    On Windows, 'created' is the actual creation time.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"File not found: {path}. Cannot read timestamps for a non-existent file."
        )
    stat = p.stat()
    return {
        "modified": stat.st_mtime,
        "accessed": stat.st_atime,
        "created": stat.st_ctime,
    }


def set_file_timestamps(path: str, modified: float, accessed: float) -> None:
    """Set file modified and accessed timestamps."""
    os.utime(path, (accessed, modified))


def preserve_file_timestamps(src_path: str, dst_path: str) -> None:
    """Copy timestamps from source file to destination file."""
    ts = get_file_timestamps(src_path)
    set_file_timestamps(dst_path, ts["modified"], ts["accessed"])


def atomic_write(path: str, data: bytes) -> None:
    """Write data to a file atomically (write to temp, then rename).

    This ensures that if the write is interrupted, the original file
    is not corrupted.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        dir=str(target.parent),
        prefix=f".{target.name}.",
        suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        # os.replace overwrites an existing destination on Windows too. Do not
        # unlink the old index first: a power loss in that gap would leave the
        # game with no valid PAMT/PAPGT file to recover from.
        os.replace(tmp_path, str(target))
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def ensure_dir(path: str) -> str:
    """Create directory and all parents if they don't exist. Returns the path."""
    Path(path).mkdir(parents=True, exist_ok=True)
    return path


def safe_copy(src: str, dst: str) -> str:
    """Copy a file with timestamp preservation. Returns destination path."""
    dst_path = Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return dst


def format_file_size(size_bytes: int) -> str:
    """Format a file size in bytes to a human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def get_default_game_path() -> str:
    """Get a platform-appropriate default search path for game installations."""
    p = get_platform()
    if p == "windows":
        return "C:\\Program Files (x86)\\Steam\\steamapps\\common\\Crimson Desert"
    elif p == "macos":
        return str(Path.home() / "Library" / "Application Support" / "Steam" /
                    "steamapps" / "common" / "Crimson Desert")
    else:
        return str(Path.home() / ".steam" / "steam" / "steamapps" / "common" / "Crimson Desert")


def _parse_steam_library_folders(vdf_path: str) -> list[str]:
    """Parse Steam libraryfolders.vdf to get all Steam library paths.

    The VDF file uses Valve's KeyValues format. We parse it to extract
    the 'path' values from each library entry.
    """
    library_paths = []
    try:
        with open(vdf_path, "r", encoding="utf-8") as f:
            content = f.read()
        import re
        paths = re.findall(r'"path"\s+"([^"]+)"', content)
        for p in paths:
            resolved = p.replace("\\\\", os.sep).replace("\\", os.sep)
            library_paths.append(resolved)
    except (OSError, UnicodeDecodeError):
        pass
    return library_paths


def _get_steam_library_roots() -> list[str]:
    """Get all Steam library root directories on the current platform."""
    p = get_platform()
    roots = []

    if p == "windows":
        default_steam = "C:\\Program Files (x86)\\Steam"
        extra_roots = []
        for env_var in ("ProgramFiles(x86)", "ProgramFiles"):
            base = os.environ.get(env_var, "")
            if base:
                candidate = os.path.join(base, "Steam")
                if candidate != default_steam:
                    extra_roots.append(candidate)
        candidates = [default_steam] + extra_roots
        for candidate in candidates:
            vdf = os.path.join(candidate, "steamapps", "libraryfolders.vdf")
            if os.path.isfile(vdf):
                roots.append(os.path.join(candidate, "steamapps"))
                roots.extend(
                    os.path.join(lp, "steamapps")
                    for lp in _parse_steam_library_folders(vdf)
                )
                break
        if not roots and os.path.isdir(os.path.join(default_steam, "steamapps")):
            roots.append(os.path.join(default_steam, "steamapps"))

    elif p == "macos":
        steam_dir = str(Path.home() / "Library" / "Application Support" / "Steam")
        vdf = os.path.join(steam_dir, "steamapps", "libraryfolders.vdf")
        if os.path.isfile(vdf):
            roots.append(os.path.join(steam_dir, "steamapps"))
            roots.extend(
                os.path.join(lp, "steamapps")
                for lp in _parse_steam_library_folders(vdf)
            )
        elif os.path.isdir(os.path.join(steam_dir, "steamapps")):
            roots.append(os.path.join(steam_dir, "steamapps"))

    else:  # linux
        search_dirs = [
            str(Path.home() / ".steam" / "steam"),
            str(Path.home() / ".local" / "share" / "Steam"),
            str(Path.home() / ".steam" / "debian-installation"),
            "/usr/share/steam",
        ]
        for steam_dir in search_dirs:
            vdf = os.path.join(steam_dir, "steamapps", "libraryfolders.vdf")
            if os.path.isfile(vdf):
                roots.append(os.path.join(steam_dir, "steamapps"))
                roots.extend(
                    os.path.join(lp, "steamapps")
                    for lp in _parse_steam_library_folders(vdf)
                )
                break
        if not roots:
            for steam_dir in search_dirs:
                if os.path.isdir(os.path.join(steam_dir, "steamapps")):
                    roots.append(os.path.join(steam_dir, "steamapps"))
                    break

    return list(dict.fromkeys(roots))


def _find_packages_dir(game_root: str) -> Optional[str]:
    """Given a game root, locate the packages/ directory containing meta/0.papgt.

    On macOS the structure is:
        .../Crimson Desert/CrimsonDesert_Steam.app/Contents/Resources/packages/
    On Windows/Linux:
        .../Crimson Desert/packages/
    """
    # Check game_root directly (Crimson Desert stores packages at root level)
    if os.path.isfile(os.path.join(game_root, "meta", "0.papgt")):
        return game_root

    direct = os.path.join(game_root, "packages")
    if os.path.isfile(os.path.join(direct, "meta", "0.papgt")):
        return direct

    for root_dir, dirs, files in os.walk(game_root):
        # Skip numbered package group dirs (0000-9999) — they may
        # contain their own meta/ folder which is NOT the root papgt
        dirs[:] = [d for d in dirs if not d.isdigit()]
        if "meta" in dirs:
            papgt = os.path.join(root_dir, "meta", "0.papgt")
            if os.path.isfile(papgt):
                return root_dir
        depth = root_dir.replace(game_root, "").count(os.sep)
        if depth > 5:
            dirs.clear()

    return None


def auto_discover_game() -> Optional[str]:
    """Auto-discover the Crimson Desert packages directory.

    Scans all Steam library folders for the Crimson Desert installation
    and locates the packages/ directory containing the PAPGT root index.

    Returns:
        Path to the packages/ directory, or None if not found.
    """
    library_roots = _get_steam_library_roots()

    game_folder_names = ["Crimson Desert", "CrimsonDesert"]
    for steamapps in library_roots:
        common_dir = os.path.join(steamapps, "common")
        if not os.path.isdir(common_dir):
            continue
        for game_name in game_folder_names:
            game_root = os.path.join(common_dir, game_name)
            if os.path.isdir(game_root):
                packages = _find_packages_dir(game_root)
                if packages:
                    return packages

    default_path = get_default_game_path()
    if os.path.isdir(default_path):
        packages = _find_packages_dir(default_path)
        if packages:
            return packages

    return None


def align_to_16(size: int) -> int:
    """Round up a size to the next 16-byte boundary."""
    return (size + 15) & ~15


def pad_to_16(data: bytes) -> bytes:
    """Pad data with zero bytes to reach 16-byte alignment."""
    aligned = align_to_16(len(data))
    if aligned > len(data):
        return data + b"\x00" * (aligned - len(data))
    return data
