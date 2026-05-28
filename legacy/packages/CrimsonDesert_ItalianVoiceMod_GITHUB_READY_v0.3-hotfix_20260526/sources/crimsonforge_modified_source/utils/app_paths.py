"""Helpers for resolving bundled application resources."""

from pathlib import Path
import sys


def app_root() -> Path:
    """Return the runtime application root for source and bundled builds."""
    bundle_root = getattr(sys, "_MEIPASS", "")
    if bundle_root:
        return Path(bundle_root)
    return Path(__file__).resolve().parents[1]


def data_dir() -> Path:
    """Return the packaged data directory."""
    return app_root() / "data"


def data_path(*parts: str) -> Path:
    """Return a path inside the packaged data directory."""
    return data_dir().joinpath(*parts)
