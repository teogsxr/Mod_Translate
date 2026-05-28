"""Unit tests for :mod:`core.file_detector`.

The detector is the single source of truth for whether a given
file can be previewed / edited. Wrong answers here surface as
confusing UI (no preview, no Edit menu item). We test every major
category — images, audio, video, text, fonts, archives, Pearl
Abyss binary formats — plus the magic-byte fallback path.
"""

from __future__ import annotations

import os
import sys
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from core.file_detector import (   # noqa: E402
    EXTENSION_MAP,
    MAGIC_BYTES,
    FileTypeInfo,
    detect_file_type,
    get_syntax_type,
    is_previewable,
    is_text_file,
)


# ═════════════════════════════════════════════════════════════════════
# Categories
# ═════════════════════════════════════════════════════════════════════

class ImageDetection(unittest.TestCase):
    def test_png(self):
        self.assertEqual(detect_file_type("a.png").category, "image")

    def test_jpg(self):
        self.assertEqual(detect_file_type("a.jpg").category, "image")

    def test_jpeg(self):
        self.assertEqual(detect_file_type("a.jpeg").category, "image")

    def test_dds(self):
        self.assertEqual(detect_file_type("a.dds").category, "image")

    def test_bmp(self):
        self.assertEqual(detect_file_type("a.bmp").category, "image")

    def test_tga(self):
        self.assertEqual(detect_file_type("a.tga").category, "image")

    def test_webp(self):
        self.assertEqual(detect_file_type("a.webp").category, "image")

    def test_gif(self):
        self.assertEqual(detect_file_type("a.gif").category, "image")


class AudioDetection(unittest.TestCase):
    def test_wav(self):
        self.assertEqual(detect_file_type("a.wav").category, "audio")

    def test_ogg(self):
        self.assertEqual(detect_file_type("a.ogg").category, "audio")

    def test_mp3(self):
        self.assertEqual(detect_file_type("a.mp3").category, "audio")

    def test_wem(self):
        self.assertEqual(detect_file_type("a.wem").category, "audio")

    def test_bnk(self):
        self.assertEqual(detect_file_type("a.bnk").category, "audio")

    def test_flac(self):
        self.assertEqual(detect_file_type("a.flac").category, "audio")


class VideoDetection(unittest.TestCase):
    def test_mp4(self):
        self.assertEqual(detect_file_type("a.mp4").category, "video")

    def test_webm(self):
        self.assertEqual(detect_file_type("a.webm").category, "video")

    def test_avi(self):
        self.assertEqual(detect_file_type("a.avi").category, "video")

    def test_bk2(self):
        self.assertEqual(detect_file_type("a.bk2").category, "video")

    def test_usm(self):
        self.assertEqual(detect_file_type("a.usm").category, "video")


class TextDetection(unittest.TestCase):
    def test_xml(self):
        self.assertEqual(detect_file_type("a.xml").category, "text")

    def test_html(self):
        self.assertEqual(detect_file_type("a.html").category, "text")

    def test_json(self):
        self.assertEqual(detect_file_type("a.json").category, "text")

    def test_txt(self):
        self.assertEqual(detect_file_type("a.txt").category, "text")

    def test_css(self):
        self.assertEqual(detect_file_type("a.css").category, "text")

    def test_paloc(self):
        self.assertEqual(detect_file_type("a.paloc").category, "text")


class FontDetection(unittest.TestCase):
    def test_ttf(self):
        self.assertEqual(detect_file_type("a.ttf").category, "font")

    def test_otf(self):
        self.assertEqual(detect_file_type("a.otf").category, "font")

    def test_woff(self):
        self.assertEqual(detect_file_type("a.woff").category, "font")

    def test_woff2(self):
        self.assertEqual(detect_file_type("a.woff2").category, "font")


class ArchiveDetection(unittest.TestCase):
    def test_paz(self):
        self.assertEqual(detect_file_type("a.paz").category, "archive")

    def test_pamt(self):
        self.assertEqual(detect_file_type("a.pamt").category, "archive")

    def test_papgt(self):
        self.assertEqual(detect_file_type("a.papgt").category, "archive")


class MeshDetection(unittest.TestCase):
    def test_pam(self):
        self.assertEqual(detect_file_type("a.pam").category, "mesh")

    def test_pamlod(self):
        self.assertEqual(detect_file_type("a.pamlod").category, "mesh")

    def test_pac(self):
        self.assertEqual(detect_file_type("a.pac").category, "mesh")


# ═════════════════════════════════════════════════════════════════════
# Can-preview / can-edit
# ═════════════════════════════════════════════════════════════════════

class PreviewCapability(unittest.TestCase):
    def test_image_previewable(self):
        self.assertTrue(is_previewable("a.png"))

    def test_audio_previewable(self):
        self.assertTrue(is_previewable("a.wav"))

    def test_video_previewable(self):
        self.assertTrue(is_previewable("a.mp4"))

    def test_text_previewable(self):
        self.assertTrue(is_previewable("a.xml"))

    def test_pac_previewable(self):
        self.assertTrue(is_previewable("a.pac"))

    def test_paz_not_previewable(self):
        self.assertFalse(is_previewable("a.paz"))

    def test_pab_not_previewable(self):
        # Skeleton files don't have a standalone preview.
        self.assertFalse(is_previewable("a.pab"))

    def test_unknown_ext_previewable_as_binary(self):
        info = detect_file_type("a.whatever")
        self.assertTrue(info.can_preview)


class EditCapability(unittest.TestCase):
    def test_png_not_editable(self):
        self.assertFalse(is_text_file("a.png"))

    def test_wav_not_editable(self):
        self.assertFalse(is_text_file("a.wav"))

    def test_xml_editable(self):
        self.assertTrue(is_text_file("a.xml"))

    def test_json_editable(self):
        self.assertTrue(is_text_file("a.json"))

    def test_paloc_editable(self):
        self.assertTrue(is_text_file("a.paloc"))

    def test_prefab_not_editable_as_text(self):
        # Prefabs use a binary editor, not the text editor.
        self.assertFalse(is_text_file("a.prefab"))


# ═════════════════════════════════════════════════════════════════════
# Syntax highlighting
# ═════════════════════════════════════════════════════════════════════

class SyntaxType(unittest.TestCase):
    def test_xml_syntax(self):
        self.assertEqual(get_syntax_type("a.xml"), "xml")

    def test_html_syntax(self):
        self.assertEqual(get_syntax_type("a.html"), "html")

    def test_thtml_syntax(self):
        self.assertEqual(get_syntax_type("a.thtml"), "html")

    def test_json_syntax(self):
        self.assertEqual(get_syntax_type("a.json"), "json")

    def test_css_syntax(self):
        self.assertEqual(get_syntax_type("a.css"), "css")

    def test_paloc_syntax(self):
        self.assertEqual(get_syntax_type("a.paloc"), "paloc")

    def test_txt_syntax(self):
        self.assertEqual(get_syntax_type("a.txt"), "plain")

    def test_unknown_syntax_is_plain(self):
        self.assertEqual(get_syntax_type("a.xyz"), "plain")


# ═════════════════════════════════════════════════════════════════════
# Case-insensitivity
# ═════════════════════════════════════════════════════════════════════

class CaseHandling(unittest.TestCase):
    def test_uppercase_extension(self):
        self.assertEqual(detect_file_type("A.PNG").category, "image")

    def test_mixed_case_extension(self):
        self.assertEqual(detect_file_type("A.Png").category, "image")

    def test_mixed_case_syntax(self):
        self.assertEqual(get_syntax_type("A.XML"), "xml")


# ═════════════════════════════════════════════════════════════════════
# Magic bytes fallback
# ═════════════════════════════════════════════════════════════════════

class MagicBytesFallback(unittest.TestCase):
    def test_png_magic_detected_without_extension(self):
        magic_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        # File path with no known extension — magic bytes drive
        # detection when data is supplied.
        info = detect_file_type("unknown", data=magic_png)
        self.assertEqual(info.category, "image")

    def test_riff_magic_maps_to_wav(self):
        data = b"RIFF" + b"\x00" * 100
        info = detect_file_type("unknown", data=data)
        self.assertEqual(info.category, "audio")

    def test_jpg_magic_short_header(self):
        data = b"\xff\xd8\xff" + b"\x00" * 100
        info = detect_file_type("unknown", data=data)
        self.assertEqual(info.category, "image")

    def test_dds_magic(self):
        data = b"DDS " + b"\x00" * 100
        info = detect_file_type("unknown", data=data)
        self.assertEqual(info.category, "image")

    def test_xml_magic(self):
        data = b"<?xml version='1.0'?>\n<root/>" + b"\x00" * 100
        info = detect_file_type("unknown", data=data)
        self.assertEqual(info.category, "text")

    def test_unknown_magic_returns_binary_fallback(self):
        data = b"\x00\x01\x02\x03" + b"\x00" * 100
        info = detect_file_type("unknown", data=data)
        self.assertEqual(info.category, "binary")

    def test_short_data_skips_magic(self):
        # Data shorter than 8 bytes is too short for reliable magic.
        info = detect_file_type("unknown", data=b"\x89PN")
        self.assertEqual(info.category, "binary")


# ═════════════════════════════════════════════════════════════════════
# Unknown extensions fallback
# ═════════════════════════════════════════════════════════════════════

class UnknownExtensions(unittest.TestCase):
    def test_unknown_ext_returns_binary_category(self):
        self.assertEqual(detect_file_type("a.unknown").category, "binary")

    def test_unknown_ext_preserves_extension(self):
        info = detect_file_type("a.zzz")
        self.assertEqual(info.extension, ".zzz")

    def test_empty_extension_defaults(self):
        info = detect_file_type("noext")
        self.assertEqual(info.extension, ".bin")

    def test_can_preview_binary_unknown(self):
        self.assertTrue(detect_file_type("a.zzz").can_preview)

    def test_cannot_edit_binary_unknown(self):
        self.assertFalse(detect_file_type("a.zzz").can_edit)


# ═════════════════════════════════════════════════════════════════════
# Dataclass contract
# ═════════════════════════════════════════════════════════════════════

class FileTypeInfoContract(unittest.TestCase):
    def test_fields_present(self):
        info = detect_file_type("a.png")
        for field in ("category", "mime_type", "description",
                      "extension", "can_preview", "can_edit"):
            self.assertTrue(hasattr(info, field))

    def test_category_is_string(self):
        self.assertIsInstance(detect_file_type("a.png").category, str)

    def test_can_preview_is_bool(self):
        self.assertIsInstance(detect_file_type("a.png").can_preview, bool)

    def test_can_edit_is_bool(self):
        self.assertIsInstance(detect_file_type("a.png").can_edit, bool)


class ExtensionMapCompleteness(unittest.TestCase):
    """Regression guards — key formats must remain registered."""

    def test_has_png(self):
        self.assertIn(".png", EXTENSION_MAP)

    def test_has_wav(self):
        self.assertIn(".wav", EXTENSION_MAP)

    def test_has_wem(self):
        self.assertIn(".wem", EXTENSION_MAP)

    def test_has_pac(self):
        self.assertIn(".pac", EXTENSION_MAP)

    def test_has_pam(self):
        self.assertIn(".pam", EXTENSION_MAP)

    def test_has_pab(self):
        self.assertIn(".pab", EXTENSION_MAP)

    def test_has_paa(self):
        self.assertIn(".paa", EXTENSION_MAP)

    def test_has_prefab(self):
        self.assertIn(".prefab", EXTENSION_MAP)

    def test_has_pabgb(self):
        self.assertIn(".pabgb", EXTENSION_MAP)

    def test_has_paloc(self):
        self.assertIn(".paloc", EXTENSION_MAP)

    def test_all_extensions_lowercase_with_dot(self):
        for ext in EXTENSION_MAP:
            self.assertTrue(ext.startswith("."))
            self.assertEqual(ext, ext.lower())


if __name__ == "__main__":
    unittest.main()
