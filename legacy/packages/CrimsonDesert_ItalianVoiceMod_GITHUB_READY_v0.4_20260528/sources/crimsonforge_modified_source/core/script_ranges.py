"""Unicode script ranges and language-to-script mapping.

Maps each script to its Unicode codepoint ranges, sample text for
preview, and which languages use it. Used by the Font Builder to:
1. Detect which scripts a font already supports
2. Show appropriate preview text based on destination language
3. Know which codepoint ranges to copy from a donor font

Reference: unicode.org/charts/ and unicode.org/reports/tr24/
"""

from dataclasses import dataclass, field


@dataclass
class ScriptInfo:
    """Information about a Unicode script."""
    name: str
    ranges: list[tuple[int, int]]
    sample_text: str
    needs_pua: bool = False
    pua_ranges: list[tuple[int, int, int]] = field(default_factory=list)
    needs_gsub: bool = False
    description: str = ""


SCRIPT_REGISTRY: dict[str, ScriptInfo] = {
    "Latin": ScriptInfo(
        name="Latin",
        ranges=[(0x0000, 0x007F), (0x0080, 0x00FF), (0x0100, 0x024F), (0x1E00, 0x1EFF)],
        sample_text="ABCDEFGHIJKLMNOPQRSTUVWXYZ\nabcdefghijklmnopqrstuvwxyz\n0123456789\nThe quick brown fox jumps over the lazy dog.\n\u00c0\u00c9\u00d1\u00dc\u00e7\u00e8\u00f6\u00fc\u00df \u0141\u0142\u015a\u015b\u017b\u017c",
        description="Basic Latin + Extended Latin (Western European, Polish, Turkish, Vietnamese, etc.)",
    ),
    "Hebrew": ScriptInfo(
        name="Hebrew",
        ranges=[(0x0590, 0x05FF), (0xFB1D, 0xFB4F)],
        sample_text="\u05e2\u05d1\u05e8\u05d9\u05ea \u05e9\u05dc\u05d5\u05dd \u05e2\u05d5\u05dc\u05dd\n\u05d4\u05e9\u05d5\u05e2\u05dc \u05d4\u05d7\u05d5\u05dd \u05d4\u05de\u05d4\u05d9\u05e8 \u05e7\u05e4\u05e5 \u05de\u05e2\u05dc \u05d4\u05db\u05dc\u05d1 \u05d4\u05e2\u05e6\u05dc\u05df",
        description="Hebrew script for Hebrew, Yiddish.",
    ),
    "Cyrillic": ScriptInfo(
        name="Cyrillic",
        ranges=[(0x0400, 0x04FF), (0x0500, 0x052F), (0x2DE0, 0x2DFF), (0xA640, 0xA69F)],
        sample_text="\u0410\u0411\u0412\u0413\u0414\u0415\u0416\u0417\u0418\u041a\u041b\u041c\n\u0430\u0431\u0432\u0433\u0434\u0435\u0436\u0437\u0438\u043a\u043b\u043c\n\u0420\u0443\u0441\u0441\u043a\u0438\u0439 \u0423\u043a\u0440\u0430\u0457\u043d\u0441\u044c\u043a\u0430\n\u0411\u044a\u043b\u0433\u0430\u0440\u0441\u043a\u0438 \u0421\u0440\u043f\u0441\u043a\u0438",
        description="Cyrillic script for Russian, Ukrainian, Bulgarian, Serbian.",
    ),
    "Greek": ScriptInfo(
        name="Greek",
        ranges=[(0x0370, 0x03FF), (0x1F00, 0x1FFF)],
        sample_text="\u0391\u0392\u0393\u0394\u0395\u0396\u0397\u0398\u0399\u039a\u039b\u039c\n\u03b1\u03b2\u03b3\u03b4\u03b5\u03b6\u03b7\u03b8\u03b9\u03ba\u03bb\u03bc\n\u0395\u03bb\u03bb\u03b7\u03bd\u03b9\u03ba\u03ac",
        description="Greek script.",
    ),
    "Han": ScriptInfo(
        name="Han (CJK)",
        ranges=[(0x4E00, 0x9FFF), (0x3400, 0x4DBF), (0x20000, 0x2A6DF), (0x2A700, 0x2B73F)],
        sample_text="\u7b80\u4f53\u4e2d\u6587 \u7e41\u9ad4\u4e2d\u6587\n\u4f60\u597d\u4e16\u754c\n\u524d\u5f80\u5c0f\u5c4b\u4e0e\u540c\u4f34\u4f1a\u5408",
        description="CJK Unified Ideographs for Chinese (Simplified & Traditional).",
    ),
    "Kana": ScriptInfo(
        name="Japanese (Kana + Kanji)",
        ranges=[(0x3040, 0x309F), (0x30A0, 0x30FF), (0x31F0, 0x31FF), (0x4E00, 0x9FFF)],
        sample_text="\u3042\u3044\u3046\u3048\u304a \u304b\u304d\u304f\u3051\u3053\n\u30a2\u30a4\u30a6\u30a8\u30aa \u30ab\u30ad\u30af\u30b1\u30b3\n\u65e5\u672c\u8a9e \u4ef2\u9593\u304c\u96c6\u307e\u308b\u5c0f\u5c4b\u3078\u5411\u304b\u3048",
        description="Hiragana, Katakana, and Kanji for Japanese.",
    ),
    "Hangul": ScriptInfo(
        name="Korean (Hangul)",
        ranges=[(0xAC00, 0xD7AF), (0x1100, 0x11FF), (0x3130, 0x318F)],
        sample_text="\uAC00\uB098\uB2E4\uB77C\uB9C8\uBC14\uC0AC\uC544\n\uD55C\uAD6D\uC5B4 \uC548\uB155\uD558\uC138\uC694\n\uB3D9\uB8CC\uB4E4\uC774 \uBAA8\uC774\uB294 \uC624\uB450\uB9C9\uC73C\uB85C \uD5A5\uD558\uC138\uC694",
        description="Hangul syllables and jamo for Korean.",
    ),
    "Thai": ScriptInfo(
        name="Thai",
        ranges=[(0x0E00, 0x0E7F)],
        sample_text="\u0E01\u0E02\u0E03\u0E04\u0E05\u0E06\u0E07\u0E08\n\u0E20\u0E32\u0E29\u0E32\u0E44\u0E17\u0E22 \u0E2A\u0E27\u0E31\u0E2A\u0E14\u0E35",
        description="Thai script.",
    ),
    "Devanagari": ScriptInfo(
        name="Devanagari",
        ranges=[(0x0900, 0x097F), (0xA8E0, 0xA8FF)],
        sample_text="\u0905\u0906\u0907\u0908\u0909\u090A\n\u0939\u093f\u0928\u094d\u0926\u0940 \u0928\u092e\u0938\u094d\u0924\u0947 \u0926\u0941\u0928\u093f\u092f\u093e",
        needs_gsub=True,
        description="Devanagari for Hindi, Sanskrit, Marathi.",
    ),
    "Bengali": ScriptInfo(
        name="Bengali",
        ranges=[(0x0980, 0x09FF)],
        sample_text="\u0985\u0986\u0987\u0988\u0989\u098A\n\u09AC\u09BE\u0982\u09B2\u09BE \u09A8\u09AE\u09B8\u09CD\u0995\u09BE\u09B0",
        needs_gsub=True,
        description="Bengali script.",
    ),
    "Tamil": ScriptInfo(
        name="Tamil",
        ranges=[(0x0B80, 0x0BFF)],
        sample_text="\u0BA4\u0BAE\u0BBF\u0BB4\u0BCD \u0BB5\u0BA3\u0B95\u0BCD\u0B95\u0BAE\u0BCD",
        needs_gsub=True,
        description="Tamil script.",
    ),
    "Telugu": ScriptInfo(
        name="Telugu",
        ranges=[(0x0C00, 0x0C7F)],
        sample_text="\u0C24\u0C46\u0C32\u0C41\u0C17\u0C41 \u0C28\u0C2E\u0C38\u0C4D\u0C15\u0C3E\u0C30\u0C02",
        needs_gsub=True,
        description="Telugu script.",
    ),
    "Thaana": ScriptInfo(
        name="Thaana",
        ranges=[(0x0780, 0x07BF)],
        sample_text="\u078B\u07A8\u0788\u07AC\u0780\u07A8\u0784\u07A6\u0790\u07B0",
        description="Thaana script for Dhivehi (Maldivian).",
    ),
    "Arabic": ScriptInfo(
        name="Arabic",
        ranges=[(0x0600, 0x06FF), (0x0750, 0x077F), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF)],
        sample_text="\u0627\u0644\u0639\u0631\u0628\u064a\u0629\n\u0645\u0631\u062d\u0628\u0627\u064b \u0628\u0627\u0644\u0639\u0627\u0644\u0645\n\u0627\u0644\u062b\u0639\u0644\u0628 \u0627\u0644\u0628\u0646\u064a \u0627\u0644\u0633\u0631\u064a\u0639 \u064a\u0642\u0641\u0632 \u0641\u0648\u0642 \u0627\u0644\u0643\u0644\u0628 \u0627\u0644\u0643\u0633\u0648\u0644",
        needs_gsub=True,
        description="Arabic script for Arabic and related languages.",
    ),
}

LANG_TO_SCRIPT: dict[str, str] = {
    "en": "Latin", "de": "Latin", "fr": "Latin", "es": "Latin", "es-MX": "Latin",
    "it": "Latin", "pt": "Latin", "pt-BR": "Latin", "pl": "Latin", "nl": "Latin",
    "sv": "Latin", "da": "Latin", "no": "Latin", "fi": "Latin", "cs": "Latin",
    "sk": "Latin", "hu": "Latin", "ro": "Latin", "hr": "Latin", "tr": "Latin",
    "vi": "Latin", "id": "Latin", "ms": "Latin", "sw": "Latin", "af": "Latin",
    "ar": "Arabic",
    "yi": "Hebrew",
    "ru": "Cyrillic", "uk": "Cyrillic", "bg": "Cyrillic", "sr": "Cyrillic",
    "el": "Greek",
    "zh": "Han", "zh-TW": "Han",
    "ja": "Kana", "ko": "Hangul",
    "th": "Thai",
    "hi": "Devanagari",
    "bn": "Bengali", "ta": "Tamil", "te": "Telugu",
    "dv": "Thaana",
}


def get_script_for_lang(lang_code: str) -> ScriptInfo:
    """Get the Unicode script info for a language code."""
    script_name = LANG_TO_SCRIPT.get(lang_code, "Latin")
    return SCRIPT_REGISTRY.get(script_name, SCRIPT_REGISTRY["Latin"])


def detect_font_scripts(cmap: dict[int, str]) -> dict[str, int]:
    """Detect which scripts a font supports based on its cmap.

    Returns dict of script_name -> number of codepoints covered.
    """
    result = {}
    for script_name, info in SCRIPT_REGISTRY.items():
        count = 0
        for start, end in info.ranges:
            for cp in range(start, min(end + 1, start + 500)):
                if cp in cmap:
                    count += 1
            if end - start > 500:
                sample_points = list(range(start, end + 1, max(1, (end - start) // 100)))
                for cp in sample_points:
                    if cp in cmap:
                        count += 1
        if count > 0:
            result[script_name] = count
    return result


def get_missing_codepoints(cmap: dict[int, str], script_name: str) -> list[int]:
    """Get codepoints needed for a script that are missing from the font."""
    info = SCRIPT_REGISTRY.get(script_name)
    if not info:
        return []
    missing = []
    for start, end in info.ranges:
        for cp in range(start, end + 1):
            if cp not in cmap:
                missing.append(cp)
    return missing
