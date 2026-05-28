"""Audio import pipeline for game modding.

Imports WAV/OGG audio files back into game archives.
The game uses raw PCM/OGG data in PAZ archives — no WEM encoding needed
for replacement (the game engine handles playback of raw audio data
when the correct size/format headers are maintained).
"""

import os
from typing import Optional

from core.pamt_parser import PamtFileEntry
from utils.logger import get_logger

logger = get_logger("core.audio_importer")


def import_audio(audio_path: str, original_entry: PamtFileEntry,
                 original_data: bytes) -> bytes:
    """Import a WAV/OGG file as replacement for a game audio entry.

    Strategy: Read the new audio file and return its raw bytes.
    The repack engine handles compression and encryption.

    Args:
        audio_path: Path to the replacement audio file (WAV or OGG).
        original_entry: The original PAMT file entry being replaced.
        original_data: The original decompressed/decrypted audio data.

    Returns:
        New audio data bytes ready for the repack engine.
    """
    if not os.path.isfile(audio_path):
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    with open(audio_path, "rb") as f:
        new_data = f.read()

    if not new_data:
        raise ValueError("Audio file is empty")

    ext = os.path.splitext(audio_path)[1].lower()
    orig_ext = os.path.splitext(original_entry.path)[1].lower()

    logger.info(
        "Imported audio %s (%d bytes) to replace %s (%d bytes)",
        os.path.basename(audio_path), len(new_data),
        original_entry.path, len(original_data),
    )

    return new_data
