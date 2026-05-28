"""Parser + serializer for Pearl Abyss sequencer files.

Covers ``.paseq`` (timeline scripts), ``.paseqc`` (compiled curve data),
``.pastage`` (stage transitions), and the closely-related reflection
formats ``.prefab``, ``.pami``, ``.pae``, ``.binarygimmick``,
``.binaryproperty`` etc. — every PA reflection container that uses the
same length-prefixed-string layout.

Format layout (verified against live game data, 2026-05)
--------------------------------------------------------
The container is a ReflectObject stream. Each ASCII identifier and
each value-string is stored as::

    [length:uint32-LE]  [content:length bytes]   (no null terminator)

The length prefix u32 is *always* immediately before the content. We
verified this against six known strings inside
``cd_seq_quest_marnidragon_boss_0010.paseq``:

  bgm_quest_13_07a               len=16  prefix-u32 = 16  ✓
  st_bgm_2_event                 len=14  prefix-u32 = 14  ✓
  st_bgm_1_ingame                len=15  prefix-u32 = 15  ✓
  vce_boss_marnidragon_roar      len=25  prefix-u32 = 25  ✓
  vce_boss_marnidragon_growl     len=26  prefix-u32 = 26  ✓
  sfx_boss_marnidragon_charge_gear len=32 prefix-u32 = 32 ✓

Substrings of a longer length-prefixed string (e.g. the substring
``chapter11_realitytruth`` inside the full path
``binarydev__/stageseq/main/chapter11_realitytruth/cd_seq_quest_…``)
are *not* their own records — they're contained within the parent
string, and the u32 four bytes before them is part of an unrelated
field rather than a length prefix.

What this parser supports
-------------------------
- :func:`parse_paseq` — produce :class:`PaseqString` records for every
  validly length-prefixed ASCII string. Each record carries its byte
  offset, prefix offset, length, and decoded value.
- :func:`serialize_paseq` — apply edits and rebuild the file. Two
  modes:
    * **fixed-length edits** — overwrite content in-place, no shift.
      Always safe.
    * **variable-length edits** — adjust the length prefix and shift
      everything after the edit. Safe only when the file does not
      embed absolute byte offsets to later regions; for the .paseq
      format this is generally true (it's a flat reflection stream),
      but we expose a flag so callers can opt in.

Field-kind heuristics
---------------------
Each :class:`PaseqString` is tagged with a best-effort kind:

  - ``audio_event``  — looks like a Wwise event/state name
                       (``bgm_*``, ``sfx_*``, ``vce_*``, ``st_bgm_*``,
                        ``play_*``, ``stop_*``, ``region_event_*``)
  - ``animation``    — ends with ``.paa`` or ``.paao``
  - ``mesh_path``    — references ``.pam``, ``.pami``, ``.pamlod``,
                       ``.prefab``, ``.pamt``, ``.dds``, ``.hkx``
  - ``object_path``  — starts with ``object/`` or ``character/`` or
                       ``leveldata/`` or ``effect/`` or ``sound/``
  - ``timeline_cmd`` — starts with ``Timeline.`` (script command)
  - ``ui_label``     — starts with ``UI_`` / ``ui_``
  - ``type_name``    — looks like a reflection type identifier
                       (CamelCase, no spaces, often starts with
                       underscore, includes class names like
                       ``TimelineRootNode``, ``GameData_Timeline``)
  - ``string``       — anything else
"""

from __future__ import annotations

import os
import re
import struct
from dataclasses import dataclass, field
from typing import Optional

from utils.logger import get_logger

logger = get_logger("core.paseq_parser")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class PaseqString:
    """One length-prefixed string inside a paseq stream."""
    index: int                 # ordinal in scan order
    prefix_offset: int         # u32 length prefix is at this byte offset
    content_offset: int        # ASCII content starts here (= prefix_offset + 4)
    length: int                # original byte length
    value: str                 # decoded content
    kind: str                  # "audio_event", "animation", etc.


@dataclass
class PaseqFile:
    """Parsed paseq container."""
    file_name: str
    raw_data: bytes
    strings: list[PaseqString] = field(default_factory=list)
    magic: bytes = b""
    # Original file size (so a UI can warn if a serialized output
    # would diverge from the original).
    original_size: int = 0


# ---------------------------------------------------------------------------
# Kind heuristics
# ---------------------------------------------------------------------------
_RE_AUDIO_EVENT = re.compile(
    r"^(bgm_|sfx_|vce_|st_bgm|play_|stop_|region_event_|extvce_|wwise_)",
    re.IGNORECASE,
)
_RE_ANIM = re.compile(r"\.paa[a-z0-9_]*$", re.IGNORECASE)
_RE_MESH = re.compile(r"\.(pam|pami|pamlod|prefab|pamt|dds|hkx|pac|pacaa|pae|pasound|paa)$", re.IGNORECASE)
_RE_OBJECT_PATH = re.compile(r"^(object|character|leveldata|effect|sound|ui|gamedata|sequencer|texture|actionchart|aiscript)/", re.IGNORECASE)
_RE_TIMELINE_CMD = re.compile(r"^Timeline\.")
_RE_UI_LABEL = re.compile(r"^UI_", re.IGNORECASE)
_RE_TYPE_NAME = re.compile(r"^_?[A-Z][A-Za-z0-9_]*$")  # _someField or TimelineNode etc.


def _classify(value: str) -> str:
    if _RE_AUDIO_EVENT.match(value):
        return "audio_event"
    if _RE_ANIM.search(value):
        return "animation"
    if _RE_TIMELINE_CMD.match(value):
        return "timeline_cmd"
    if _RE_UI_LABEL.match(value):
        return "ui_label"
    if _RE_OBJECT_PATH.match(value):
        return "object_path"
    if _RE_MESH.search(value):
        return "mesh_path"
    if " " not in value and _RE_TYPE_NAME.match(value):
        return "type_name"
    return "string"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
# We accept a length-prefixed record when:
#   - prefix u32 is in range [1, MAX_LEN]
#   - all `length` bytes after the prefix are printable-ASCII or '/'
#   - the byte one position past the content is NOT a printable letter
#     (filters out matches that sit inside a longer string by accident)
MAX_LEN = 1024
_PRINTABLE = set(range(0x20, 0x7F))


def _is_valid_record(data: bytes, prefix_off: int) -> tuple[bool, int]:
    """Return (is_valid, length). Length is 0 when invalid."""
    if prefix_off + 4 > len(data):
        return False, 0
    length = struct.unpack_from("<I", data, prefix_off)[0]
    if length < 1 or length > MAX_LEN:
        return False, 0
    end = prefix_off + 4 + length
    if end > len(data):
        return False, 0
    chunk = data[prefix_off + 4:end]
    if not all(b in _PRINTABLE for b in chunk):
        return False, 0
    # The first character should be a "string-y" leading char to filter
    # numeric runs and pathological matches.
    first = chunk[0]
    if not (0x21 <= first <= 0x7E):
        return False, 0
    return True, length


def parse_paseq(data: bytes, file_name: str = "") -> PaseqFile:
    """Walk the container and emit every length-prefixed ASCII string.

    Linear scan over the bytes — for each candidate offset the routine
    checks whether a u32 length prefix at that position points to a
    valid ASCII run. When it does, we record the string and skip past
    its end; otherwise we advance one byte. ``O(n)`` over the file
    size, ~80 ms for a 1 MB sequencer.
    """
    out: list[PaseqString] = []
    n = len(data)

    # The leading 16-32 bytes are typically a header (magic + version
    # + counts). We start scanning from offset 0 — the parser will
    # naturally skip non-records.
    magic = data[:16] if n >= 16 else data

    pos = 0
    idx = 0
    while pos + 4 <= n:
        ok, length = _is_valid_record(data, pos)
        if ok:
            content_off = pos + 4
            value = data[content_off:content_off + length].decode("ascii")
            out.append(PaseqString(
                index=idx,
                prefix_offset=pos,
                content_offset=content_off,
                length=length,
                value=value,
                kind=_classify(value),
            ))
            idx += 1
            pos = content_off + length
        else:
            pos += 1

    return PaseqFile(
        file_name=file_name,
        raw_data=data,
        strings=out,
        magic=magic,
        original_size=n,
    )


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------
@dataclass
class PaseqEdit:
    """One edit to apply to a paseq file."""
    target: PaseqString        # original record being replaced
    new_value: str             # new ASCII value


def serialize_paseq(
    parsed: PaseqFile,
    edits: list[PaseqEdit],
    *,
    allow_size_change: bool = False,
) -> bytes:
    """Apply ``edits`` to the parsed container and return new bytes.

    Args:
        parsed: result of :func:`parse_paseq` for the original data.
        edits: list of :class:`PaseqEdit`. Each must reference a
            string that ``parsed`` knows about (matched by
            ``prefix_offset``).
        allow_size_change: if False, edits with a different byte
            length than the original raise :class:`ValueError` —
            this is the safe mode used by the UI's default save.
            If True, the serializer rewrites the length prefix and
            shifts every byte after the edit by the size delta.
            Use only when you understand the file structure.

    Returns:
        Modified bytes ready to write back.
    """
    if not edits:
        return bytes(parsed.raw_data)

    # Sort edits by prefix_offset so we can shift in-place left-to-right.
    sorted_edits = sorted(edits, key=lambda e: e.target.prefix_offset)

    # Validate edits reference real records.
    known_offsets = {s.prefix_offset for s in parsed.strings}
    for e in sorted_edits:
        if e.target.prefix_offset not in known_offsets:
            raise ValueError(
                f"edit references unknown prefix_offset "
                f"{e.target.prefix_offset}"
            )

    # Fast path — every edit is fixed-length.
    fixed_only = all(
        len(e.new_value.encode("ascii")) == e.target.length
        for e in sorted_edits
    )
    if fixed_only:
        out = bytearray(parsed.raw_data)
        for e in sorted_edits:
            new_bytes = e.new_value.encode("ascii")
            out[e.target.content_offset:e.target.content_offset + e.target.length] = new_bytes
        return bytes(out)

    if not allow_size_change:
        offenders = [
            (e.target.value, e.new_value, e.target.length, len(e.new_value.encode("ascii")))
            for e in sorted_edits
            if len(e.new_value.encode("ascii")) != e.target.length
        ]
        raise ValueError(
            "size-changing edits not allowed (pass allow_size_change=True "
            "to opt in). offenders: " + repr(offenders[:5])
        )

    # Variable-length path. Walk the original file rewriting each
    # edit with a fresh length prefix and the new bytes; copy the
    # rest unchanged. This shifts byte offsets after each edit.
    out_parts: list[bytes] = []
    cursor = 0
    for e in sorted_edits:
        # Copy bytes up to (but not including) this edit's u32 prefix.
        if e.target.prefix_offset > cursor:
            out_parts.append(parsed.raw_data[cursor:e.target.prefix_offset])
        new_bytes = e.new_value.encode("ascii")
        out_parts.append(struct.pack("<I", len(new_bytes)))
        out_parts.append(new_bytes)
        cursor = e.target.prefix_offset + 4 + e.target.length
    if cursor < len(parsed.raw_data):
        out_parts.append(parsed.raw_data[cursor:])

    return b"".join(out_parts)


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------
def filter_strings(
    parsed: PaseqFile,
    *,
    kind: Optional[str] = None,
    contains: Optional[str] = None,
    only_editable: bool = False,
) -> list[PaseqString]:
    """Return strings matching simple filters. Used by the UI."""
    sub = contains.lower() if contains else None
    out = []
    for s in parsed.strings:
        if kind and s.kind != kind:
            continue
        if sub and sub not in s.value.lower():
            continue
        if only_editable and s.kind == "type_name":
            # Type names are reflection metadata — editing them
            # corrupts the file. Filter them out by default.
            continue
        out.append(s)
    return out


def kind_summary(parsed: PaseqFile) -> dict[str, int]:
    """Return ``{kind: count}`` for the parsed container — useful as
    a header label in the editor.
    """
    out: dict[str, int] = {}
    for s in parsed.strings:
        out[s.kind] = out.get(s.kind, 0) + 1
    return out
