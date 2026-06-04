"""Export translations to paloc format for repacking."""

import os
from typing import Optional

from core.paloc_parser import PalocEntry, PalocData, build_paloc
from translation.translation_project import TranslationProject
from translation.translation_state import StringStatus
from utils.logger import get_logger

logger = get_logger("translation.export")


class TranslationExporter:
    """Exports translation projects to paloc format."""

    def export_to_paloc(
        self,
        project: TranslationProject,
        output_path: str,
        original_paloc: Optional[PalocData] = None,
        export_only_approved: bool = False,
    ) -> str:
        """Export project translations to a paloc file."""
        entries = []
        exported_count = 0
        skipped_count = 0

        header_entries = []
        if original_paloc and original_paloc.header_entries:
            header_entries = original_paloc.header_entries

        for te in project.entries:
            if export_only_approved and te.status != StringStatus.APPROVED:
                if te.original_text:
                    entries.append(PalocEntry(
                        key=te.key,
                        value=te.original_text,
                        key_offset=0,
                        value_offset=0,
                    ))
                    skipped_count += 1
                continue

            if te.translated_text:
                entries.append(PalocEntry(
                    key=te.key,
                    value=te.translated_text,
                    key_offset=0,
                    value_offset=0,
                ))
                exported_count += 1
            else:
                entries.append(PalocEntry(
                    key=te.key,
                    value=te.original_text,
                    key_offset=0,
                    value_offset=0,
                ))
                skipped_count += 1

        paloc_data = build_paloc(entries, header_entries)

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(paloc_data)

        logger.info(
            "Exported paloc: %d translated, %d kept original, %d bytes -> %s",
            exported_count, skipped_count, len(paloc_data), output_path,
        )
        return output_path

    def get_export_stats(self, project: TranslationProject) -> dict:
        """Get stats about what would be exported."""
        stats = project.get_stats()
        stats["exportable"] = stats.get("translated", 0) + stats.get("reviewed", 0) + stats.get("approved", 0)
        return stats
