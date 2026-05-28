"""Input validation utilities.

Validates paths, checksums, file integrity, and other user inputs.
Every validation returns a clear error message on failure.
"""

import os
import struct
from pathlib import Path
from typing import Optional


class ValidationError(Exception):
    """Raised when validation fails, with a user-friendly message."""
    pass


def validate_file_exists(path: str, description: str = "File") -> str:
    """Validate that a file exists and is readable.

    Args:
        path: File path to validate.
        description: Human-readable description for error messages.

    Returns:
        The normalized absolute path.

    Raises:
        ValidationError: If the file doesn't exist or isn't readable.
    """
    p = Path(path).resolve()
    if not p.exists():
        raise ValidationError(
            f"{description} not found: {p}. "
            f"Check that the path is correct and the file has not been moved or deleted."
        )
    if not p.is_file():
        raise ValidationError(
            f"{description} is not a file: {p}. "
            f"Expected a file but found a directory or special file."
        )
    if not os.access(str(p), os.R_OK):
        raise ValidationError(
            f"{description} is not readable: {p}. "
            f"Check file permissions and ensure you have read access."
        )
    return str(p)


def validate_directory_exists(path: str, description: str = "Directory") -> str:
    """Validate that a directory exists and is accessible."""
    p = Path(path).resolve()
    if not p.exists():
        raise ValidationError(
            f"{description} not found: {p}. "
            f"Check that the path is correct."
        )
    if not p.is_dir():
        raise ValidationError(
            f"{description} is not a directory: {p}. "
            f"Expected a directory but found a file."
        )
    return str(p)


def validate_directory_writable(path: str, description: str = "Directory") -> str:
    """Validate that a directory exists and is writable."""
    validated = validate_directory_exists(path, description)
    if not os.access(validated, os.W_OK):
        raise ValidationError(
            f"{description} is not writable: {validated}. "
            f"Check file permissions and ensure you have write access."
        )
    return validated


def validate_pamt_file(path: str) -> str:
    """Validate that a file is a valid PAMT index file.

    Checks: exists, readable, has minimum size (16 bytes for header),
    and starts with a valid structure (paz_count > 0).
    """
    validated = validate_file_exists(path, "PAMT file")
    size = os.path.getsize(validated)
    if size < 16:
        raise ValidationError(
            f"PAMT file is too small ({size} bytes): {validated}. "
            f"A valid PAMT file must be at least 16 bytes. The file may be corrupted."
        )
    with open(validated, "rb") as f:
        header = f.read(16)
    paz_count = struct.unpack_from("<I", header, 4)[0]
    if paz_count == 0 or paz_count > 10000:
        raise ValidationError(
            f"PAMT file has invalid PAZ count ({paz_count}): {validated}. "
            f"Expected between 1 and 10000 PAZ files. The file may be corrupted or not a PAMT file."
        )
    return validated


def validate_paz_file(path: str) -> str:
    """Validate that a PAZ archive file exists and is non-empty."""
    validated = validate_file_exists(path, "PAZ file")
    size = os.path.getsize(validated)
    if size == 0:
        raise ValidationError(
            f"PAZ file is empty (0 bytes): {validated}. "
            f"The archive may be corrupted."
        )
    return validated


def validate_papgt_file(path: str) -> str:
    """Validate that a PAPGT root index file is valid."""
    validated = validate_file_exists(path, "PAPGT file")
    size = os.path.getsize(validated)
    if size < 12:
        raise ValidationError(
            f"PAPGT file is too small ({size} bytes): {validated}. "
            f"A valid PAPGT file must be at least 12 bytes."
        )
    return validated


def validate_checksum_match(expected: int, actual: int, description: str = "Checksum") -> None:
    """Validate that two checksums match."""
    if expected != actual:
        raise ValidationError(
            f"{description} mismatch: expected 0x{expected:08X}, got 0x{actual:08X}. "
            f"The file may have been modified outside of CrimsonForge or may be corrupted."
        )


def validate_positive_int(value, name: str = "Value") -> int:
    """Validate that a value is a positive integer."""
    try:
        i = int(value)
    except (TypeError, ValueError):
        raise ValidationError(
            f"{name} must be a positive integer, got: {value!r}"
        )
    if i <= 0:
        raise ValidationError(
            f"{name} must be positive (greater than 0), got: {i}"
        )
    return i


def validate_api_key(key: str, provider: str) -> str:
    """Validate that an API key is non-empty and has basic format."""
    if not key or not key.strip():
        raise ValidationError(
            f"API key for {provider} is empty. "
            f"Enter a valid API key in Settings → AI Providers → {provider}."
        )
    return key.strip()


def validate_url(url: str, description: str = "URL") -> str:
    """Validate that a URL has basic correct format."""
    if not url or not url.strip():
        raise ValidationError(
            f"{description} is empty. Enter a valid URL."
        )
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        raise ValidationError(
            f"{description} must start with http:// or https://, got: {url}"
        )
    return url
