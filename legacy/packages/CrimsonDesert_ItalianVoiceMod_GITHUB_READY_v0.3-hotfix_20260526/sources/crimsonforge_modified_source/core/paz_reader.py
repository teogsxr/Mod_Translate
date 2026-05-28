"""PAZ archive reader.

Reads raw data blocks from PAZ archive files at specified offsets.
PAZ files are simple binary containers with no internal structure -
all file metadata lives in the corresponding PAMT index.
"""

import os
from pathlib import Path

from utils.logger import get_logger

logger = get_logger("core.paz_reader")


class PazReader:
    """Reads raw data from PAZ archive files.

    PAZ files are binary blobs - files are stored at byte offsets
    specified by the PAMT index. Data blocks are 16-byte aligned.
    """

    def __init__(self, paz_dir: str):
        """Initialize PAZ reader for a directory containing .paz files.

        Args:
            paz_dir: Path to the directory containing PAZ files (e.g., packages/0012/).
        """
        self._paz_dir = Path(paz_dir)
        if not self._paz_dir.is_dir():
            raise FileNotFoundError(
                f"PAZ directory not found: {paz_dir}. "
                f"Check that the game packages path is correct."
            )
        self._file_handles: dict[str, object] = {}

    def read(self, paz_filename: str, offset: int, size: int) -> bytes:
        """Read raw bytes from a PAZ file at the given offset.

        Args:
            paz_filename: Name or path of the PAZ file (e.g., '2.paz').
            offset: Byte offset within the PAZ file.
            size: Number of bytes to read.

        Returns:
            Raw bytes from the PAZ file.
        """
        paz_path = self._resolve_path(paz_filename)

        with open(paz_path, "rb") as f:
            f.seek(offset)
            data = f.read(size)

        if len(data) != size:
            raise IOError(
                f"Short read from {paz_path}: expected {size} bytes at offset "
                f"0x{offset:08X}, got {len(data)} bytes. "
                f"The PAZ file may be truncated or the offset/size is incorrect."
            )

        return data

    def get_file_size(self, paz_filename: str) -> int:
        """Get the total size of a PAZ file."""
        paz_path = self._resolve_path(paz_filename)
        return os.path.getsize(str(paz_path))

    def paz_exists(self, paz_filename: str) -> bool:
        """Check if a PAZ file exists in the directory."""
        try:
            self._resolve_path(paz_filename)
            return True
        except FileNotFoundError:
            return False

    def list_paz_files(self) -> list[str]:
        """List all .paz files in the directory, sorted numerically."""
        files = [f.name for f in self._paz_dir.iterdir() if f.suffix.lower() == ".paz"]
        files.sort(key=lambda x: int(Path(x).stem) if Path(x).stem.isdigit() else x)
        return files

    def _resolve_path(self, paz_filename: str) -> Path:
        """Resolve a PAZ filename to a full path."""
        if os.path.isabs(paz_filename):
            p = Path(paz_filename)
        else:
            name = os.path.basename(paz_filename)
            p = self._paz_dir / name

        if not p.exists():
            raise FileNotFoundError(
                f"PAZ file not found: {p}. "
                f"Expected to find it in {self._paz_dir}. "
                f"Check that all PAZ files are present."
            )
        return p

    @property
    def directory(self) -> str:
        return str(self._paz_dir)
