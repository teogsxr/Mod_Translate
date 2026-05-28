"""Backup and restore manager for game files.

Creates timestamped backups before any destructive file modification
and supports restoration to any backup point.
"""

import os
import shutil
import json
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass

from utils.logger import get_logger
from utils.platform_utils import safe_copy, ensure_dir

logger = get_logger("core.backup_manager")


@dataclass
class BackupRecord:
    """Record of a single backup operation."""
    timestamp: str
    backup_dir: str
    files: list[dict]
    description: str


class BackupManager:
    """Manages backups of game files before modifications.

    Creates a timestamped backup directory with copies of all files
    that will be modified, plus a manifest JSON for restoration.
    """

    MANIFEST_NAME = "backup_manifest.json"

    def __init__(self, backup_root: str):
        """Initialize backup manager.

        Args:
            backup_root: Root directory for all backups.
        """
        self._backup_root = Path(backup_root)
        ensure_dir(str(self._backup_root))

    def create_backup(
        self,
        files_to_backup: list[str],
        description: str = "",
    ) -> BackupRecord:
        """Create a backup of the specified files.

        Args:
            files_to_backup: List of absolute file paths to back up.
            description: Human-readable description of what's being modified.

        Returns:
            BackupRecord with details of the backup.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = self._backup_root / timestamp
        ensure_dir(str(backup_dir))

        backed_up = []
        for src_path in files_to_backup:
            if not os.path.exists(src_path):
                logger.warning("Skipping backup of non-existent file: %s", src_path)
                continue

            safe_name = os.path.basename(src_path)
            parent_name = os.path.basename(os.path.dirname(src_path))
            dst_name = f"{parent_name}_{safe_name}"
            dst_path = str(backup_dir / dst_name)

            safe_copy(src_path, dst_path)
            backed_up.append({
                "original_path": src_path,
                "backup_path": dst_path,
                "backup_name": dst_name,
                "size": os.path.getsize(src_path),
            })
            logger.info("Backed up: %s -> %s", src_path, dst_path)

        record = BackupRecord(
            timestamp=timestamp,
            backup_dir=str(backup_dir),
            files=backed_up,
            description=description,
        )

        manifest_path = backup_dir / self.MANIFEST_NAME
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump({
                "timestamp": record.timestamp,
                "description": record.description,
                "files": record.files,
            }, f, indent=2)

        logger.info(
            "Backup created: %s (%d files) - %s",
            timestamp, len(backed_up), description
        )
        return record

    def restore_backup(self, backup_dir: str) -> list[str]:
        """Restore files from a backup directory.

        Args:
            backup_dir: Path to the backup directory.

        Returns:
            List of restored file paths.
        """
        manifest_path = Path(backup_dir) / self.MANIFEST_NAME
        if not manifest_path.exists():
            raise FileNotFoundError(
                f"Backup manifest not found in {backup_dir}. "
                f"The backup directory may be corrupted or incomplete."
            )

        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        restored = []
        for file_info in manifest["files"]:
            backup_path = file_info["backup_path"]
            original_path = file_info["original_path"]

            if not os.path.exists(backup_path):
                raise FileNotFoundError(
                    f"Backup file missing: {backup_path}. "
                    f"Cannot restore {original_path}. The backup may be incomplete."
                )

            safe_copy(backup_path, original_path)
            restored.append(original_path)
            logger.info("Restored: %s <- %s", original_path, backup_path)

        logger.info("Restore complete: %d files from %s", len(restored), backup_dir)
        return restored

    def list_backups(self) -> list[dict]:
        """List all available backups, newest first."""
        backups = []
        if not self._backup_root.exists():
            return backups

        for item in sorted(self._backup_root.iterdir(), reverse=True):
            if not item.is_dir():
                continue
            manifest_path = item / self.MANIFEST_NAME
            if not manifest_path.exists():
                continue
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    manifest = json.load(f)
                backups.append({
                    "timestamp": manifest.get("timestamp", item.name),
                    "description": manifest.get("description", ""),
                    "file_count": len(manifest.get("files", [])),
                    "backup_dir": str(item),
                })
            except (json.JSONDecodeError, OSError):
                continue

        return backups

    def delete_backup(self, backup_dir: str) -> None:
        """Delete a backup directory."""
        p = Path(backup_dir)
        if not p.exists():
            return
        if not str(p).startswith(str(self._backup_root)):
            raise ValueError(
                f"Cannot delete {backup_dir}: not inside backup root {self._backup_root}. "
                f"This is a safety check to prevent accidental deletion."
            )
        shutil.rmtree(str(p))
        logger.info("Deleted backup: %s", backup_dir)

    @property
    def backup_root(self) -> str:
        return str(self._backup_root)
