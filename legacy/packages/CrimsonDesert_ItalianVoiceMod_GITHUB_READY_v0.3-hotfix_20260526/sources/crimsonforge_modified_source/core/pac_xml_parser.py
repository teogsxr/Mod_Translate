"""Parser + serializer + field enumerator for ``.pac_xml`` files.

Background
----------
``.pac_xml`` is the post-April-2026 rename of ``.pac.xml`` — a
per-mesh XML sidecar that carries the skinned-mesh material
definitions, submesh grouping, and texture/material references
for a ``.pac`` (skinned character mesh).

File format (observed across 12,692 real samples):

  * UTF-8 BOM  (``EF BB BF``) at offset 0
  * **Multi-root** XML — two top-level elements, not one:
      ``<SkinnedMeshPropertyCommon .../>`` then
      ``<ModelPropertyList>...</ModelPropertyList>``
  * Tab-indented, CRLF line endings
  * No ``<?xml version=...?>`` declaration

The multi-root form means stdlib ``xml.etree.ElementTree`` refuses
to parse the file directly — every parser rejects ``junk after
document element`` because XML is officially required to have one
root. We work around it by **wrapping** the content in a synthetic
root before parsing and unwrapping on serialise. The wrapper never
escapes this module; user-visible paths start at the real top-level
element.

What users edit
---------------
The interesting fields for mod authors are overwhelmingly element
attributes:

  * ``_path`` — texture / material file references
    (``character/texture/foo.dds``)
  * ``_subMeshName`` — submesh grouping names
  * ``_materialName`` — which shader / material template to use
  * ``_jiggleWindWeight`` / ``_useSkinBlendShape`` and friends —
    numeric rig hints
  * Various ``ItemID`` / ``StringItemID`` identifier attributes

Text content nodes exist but are almost always whitespace (layout
only) and are deliberately excluded from the editable field list.

Public API
----------

:func:`parse_pac_xml`       — decode bytes → ``ParsedPacXml``
:func:`apply_edits`         — list of ``(field_index, new_value)``
                              tuples → new ``ParsedPacXml``
:func:`serialize_pac_xml`   — ``ParsedPacXml`` → bytes (with BOM,
                              CRLF, tab indent, multi-root)

All three are pure functions (no disk I/O, no Qt, no VFS). The
dialog layer consumes them.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple


# ── Constants ─────────────────────────────────────────────────────

BOM = b"\xef\xbb\xbf"

# Synthetic wrapper element used to force multi-root content into a
# shape stdlib ElementTree accepts. Chosen to be unmistakably ours
# so future regex-based tooling can identify it reliably.
_WRAPPER = "CrimsonForgePacXmlWrapper_v1"


# ── Data classes ──────────────────────────────────────────────────

@dataclass
class PacXmlField:
    """One editable datum inside a parsed ``.pac_xml`` tree.

    ``element_index`` is the element's document-order index
    starting at 0 (excluding the synthetic wrapper). It survives
    edits unchanged and is the stable cross-reference the editor
    uses to locate the element when applying a value change.

    ``attr`` is empty string for text-content fields; otherwise it
    holds the attribute name to mutate. Callers should key on
    ``kind`` rather than checking the empty string directly.
    """
    index: int                 # position in ParsedPacXml.fields
    path: str                  # display path e.g. "ModelPropertyList/ModelProperty[0]/..."
    attr: str                  # attribute name, or "" for text nodes
    value: str                 # current value as string
    kind: str                  # "attribute" or "text"
    element_index: int         # doc-order element index (stable across edits)
    element_tag: str           # element's tag name, for display


@dataclass
class ParsedPacXml:
    """Fully-parsed ``.pac_xml`` file ready for field-by-field editing."""
    path: str = ""
    raw: bytes = b""
    has_bom: bool = True
    # Parsed tree whose ROOT is the synthetic wrapper. Real top-level
    # game elements are its children. We expose this only so the
    # serializer can walk it; client code addresses fields by index
    # into ``fields`` and never needs to touch the tree directly.
    tree: Optional[ET.Element] = None
    fields: List[PacXmlField] = field(default_factory=list)


# ── Parse ─────────────────────────────────────────────────────────

def _decode_with_bom(data: bytes) -> tuple[str, bool]:
    """Strip the UTF-8 BOM (if present) and decode. Raises ValueError
    when decoding fails — callers typically wrap in a dialog message
    rather than swallowing silently.
    """
    has_bom = data.startswith(BOM)
    payload = data[len(BOM):] if has_bom else data
    try:
        return payload.decode("utf-8"), has_bom
    except UnicodeDecodeError as e:
        raise ValueError(f"PAC XML is not valid UTF-8: {e}") from e


def _compute_paths(root: ET.Element) -> dict[int, str]:
    """Walk ``root`` and return ``{id(element): display_path}``.

    The display path is a slash-joined chain of tag names plus a
    positional index when siblings share a tag (e.g.
    ``ModelPropertyList/ModelProperty[0]/SkinnedMeshProperty``).
    The synthetic wrapper is implicit — paths start at its children.
    """
    paths: dict[int, str] = {}

    def visit(elem: ET.Element, prefix: str) -> None:
        # Group children by tag for sibling indexing.
        by_tag: dict[str, list[ET.Element]] = {}
        for child in elem:
            by_tag.setdefault(child.tag, []).append(child)
        for tag, siblings in by_tag.items():
            for i, sib in enumerate(siblings):
                suffix = f"{tag}" if len(siblings) == 1 else f"{tag}[{i}]"
                this_path = f"{prefix}/{suffix}" if prefix else suffix
                paths[id(sib)] = this_path
                visit(sib, this_path)

    visit(root, "")
    return paths


def parse_pac_xml(data: bytes, path: str = "") -> ParsedPacXml:
    """Parse ``data`` (the plaintext bytes after VFS decrypt+decompress).

    Raises ``ValueError`` when the content isn't valid XML once
    wrapped. Returns a fully-populated :class:`ParsedPacXml` ready
    for editing.
    """
    text, has_bom = _decode_with_bom(data)

    # Wrap in synthetic root so ElementTree accepts the multi-root form.
    # The wrapped content is passed BY STRING so line/column numbers in
    # any future ParseError stay close to user-relevant positions.
    wrapped = f"<{_WRAPPER}>\n{text}\n</{_WRAPPER}>"
    try:
        root = ET.fromstring(wrapped)
    except ET.ParseError as e:
        raise ValueError(f"PAC XML did not parse: {e}") from e

    paths = _compute_paths(root)
    fields: list[PacXmlField] = []
    elem_idx = 0

    # Walk in document order, enumerate every attribute + text node.
    for elem in root.iter():
        if elem.tag == _WRAPPER:
            continue
        this_path = paths.get(id(elem), elem.tag)
        tag_name = elem.tag
        # Attributes (order-preserving; ET.Element keeps insertion order).
        for attr_name, attr_val in elem.attrib.items():
            fields.append(PacXmlField(
                index=len(fields),
                path=this_path,
                attr=attr_name,
                value=attr_val,
                kind="attribute",
                element_index=elem_idx,
                element_tag=tag_name,
            ))
        # Text content, but only when non-trivial. XML whitespace
        # nodes exist just for layout; exposing them as editable
        # fields would clutter the UI with hundreds of blank rows.
        if elem.text and elem.text.strip():
            fields.append(PacXmlField(
                index=len(fields),
                path=this_path,
                attr="",
                value=elem.text,
                kind="text",
                element_index=elem_idx,
                element_tag=tag_name,
            ))
        elem_idx += 1

    return ParsedPacXml(
        path=path,
        raw=data,
        has_bom=has_bom,
        tree=root,
        fields=fields,
    )


# ── Edit ──────────────────────────────────────────────────────────

def apply_edits(
    parsed: ParsedPacXml,
    edits: Sequence[Tuple[int, str]],
) -> ParsedPacXml:
    """Apply ``(field_index, new_value)`` edits and return an updated
    :class:`ParsedPacXml` whose ``tree`` reflects the new values.

    Returns a NEW object — the caller's ``parsed`` argument is left
    untouched. ``raw`` is not modified here; callers that need
    serialised bytes should feed the result to
    :func:`serialize_pac_xml`.

    Raises ``IndexError`` for an unknown field index, ``ValueError``
    for attempts to edit a field whose kind doesn't support the
    requested mutation (currently none — all kinds accept string
    values — but the exception type is reserved).
    """
    if parsed.tree is None:
        raise ValueError("apply_edits called on an empty ParsedPacXml")

    # Walk the tree once to collect elements in document order.
    # Skipping the wrapper matches parse_pac_xml's element_index.
    elements: list[ET.Element] = []
    for elem in parsed.tree.iter():
        if elem.tag == _WRAPPER:
            continue
        elements.append(elem)

    new_fields = [PacXmlField(**f.__dict__) for f in parsed.fields]

    for field_index, new_value in edits:
        if field_index < 0 or field_index >= len(new_fields):
            raise IndexError(
                f"pac_xml field index {field_index} out of range "
                f"(have {len(new_fields)} fields)"
            )
        f = new_fields[field_index]
        if f.element_index >= len(elements):
            raise IndexError(
                f"pac_xml element index {f.element_index} out of range"
            )
        target = elements[f.element_index]
        if f.kind == "attribute":
            target.set(f.attr, new_value)
        elif f.kind == "text":
            target.text = new_value
        else:   # pragma: no cover — defensive
            raise ValueError(f"Unknown pac_xml field kind: {f.kind!r}")
        f.value = new_value

    return ParsedPacXml(
        path=parsed.path,
        raw=parsed.raw,
        has_bom=parsed.has_bom,
        tree=parsed.tree,
        fields=new_fields,
    )


# ── Serialise ─────────────────────────────────────────────────────

def _indent_with_tabs(elem: ET.Element, level: int = 0) -> None:
    """Pretty-print an ElementTree branch with TAB indentation and
    CRLF line separation, mirroring Pearl Abyss's ``.pac_xml`` style.

    Python's built-in ``ET.indent`` uses spaces by default. Tab
    preservation is important — the game engine accepts either but
    mod diff tools (and the original authors) prefer tabs.
    """
    indent = "\n" + "\t" * level
    children = list(elem)
    if children:
        # Open-tag trailing whitespace -> indent children.
        if not elem.text or not elem.text.strip():
            elem.text = indent + "\t"
        for child in children:
            _indent_with_tabs(child, level + 1)
        # Last child's tail drops back to our level (closing tag align).
        if not children[-1].tail or not children[-1].tail.strip():
            children[-1].tail = indent
    # Our own tail separates us from the next sibling at our level.
    # The caller sets level=0 for top-level children; we don't mutate
    # the wrapper's tail.


def serialize_pac_xml(parsed: ParsedPacXml) -> bytes:
    """Serialise ``parsed`` back to bytes.

    Output format mirrors the original files byte-for-byte where
    possible:

      * UTF-8 BOM when the input had one (default).
      * Each top-level element on its own line (no wrapper output).
      * CRLF line endings.
      * Tab indentation.
      * Trailing CRLF.

    Attribute ordering inside each element is preserved by
    ``ElementTree`` which, since Python 3.8, walks attributes in
    insertion order — so unedited attributes stay exactly where the
    game wrote them.
    """
    if parsed.tree is None:
        raise ValueError("serialize_pac_xml called on an empty ParsedPacXml")

    out_parts: list[str] = []
    for top in parsed.tree:
        # Deep-copy-equivalent indent pass on each top-level element.
        _indent_with_tabs(top, level=0)
        # Strip any residual trailing whitespace/newline on the top
        # element's tail so the join below controls the inter-root
        # separation exactly. Without this we'd get an extra blank
        # line between roots because ``_indent_with_tabs`` leaves a
        # "\n" in the last child's tail that propagates out.
        top.tail = None
        fragment = ET.tostring(
            top, encoding="unicode", short_empty_elements=True,
        )
        # ET.tostring always inserts a literal space before the ``/>``
        # in self-closing tags (``<Foo />``). Pearl Abyss's originals
        # use ``<Foo/>`` with no space. Both are valid XML but we
        # match the original style for minimum-diff round-trips so
        # mod-review tooling can show clean diffs.
        fragment = fragment.replace(" />", "/>")
        # Each fragment's own newlines come from the indent pass. Drop
        # any trailing whitespace on the fragment itself so the join
        # below sees a clean line.
        fragment = fragment.rstrip()
        out_parts.append(fragment)

    # Pearl Abyss uses CRLF everywhere + TWO trailing CRLFs at EOF
    # (one closes the final tag's line, one is a blank line after).
    # Observed on every real sample — matching it exactly means
    # unedited .pac_xml files round-trip byte-for-byte, which keeps
    # mod diffs clean and makes git-based mod versioning usable.
    # ET.tostring emits plain LF newlines; normalise in one pass to
    # avoid double-CRLF artefacts on platforms where Python's text
    # layer already translates \n.
    joined = "\r\n".join(out_parts) + "\r\n\r\n"
    joined = joined.replace("\r\n", "\n").replace("\n", "\r\n")

    body = joined.encode("utf-8")
    return (BOM if parsed.has_bom else b"") + body


# ── Helpers for the editor UI ─────────────────────────────────────

# Attribute names that are almost always the "interesting" ones for
# mod authors — texture paths, submesh/material names, and flag
# values. The dialog surfaces these categories in a filter dropdown.
ATTR_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("path",        ("_path",)),
    ("name",        ("_subMeshName", "_materialName", "Name", "_name")),
    ("id",          ("ItemID", "StringItemID", "IdBase", "Index")),
    ("flag",        ("isOverrided", "_useSkinBlendShape",
                     "IncreasePBDSimulationRate", "_jiggleWindWeight")),
    ("version",     ("ReflectObjectXMLDataVersion", "Version")),
)


def categorize_field(f: PacXmlField) -> str:
    """Group an attribute into one of :data:`ATTR_CATEGORIES`.

    Returns the category key (``"path"``, ``"name"``, ``"id"``,
    ``"flag"``, ``"version"``) or ``"other"`` when the attribute
    doesn't fall into any known group. Text fields always route to
    ``"text"`` so the UI can colour them distinctly.
    """
    if f.kind == "text":
        return "text"
    for category, attrs in ATTR_CATEGORIES:
        if f.attr in attrs:
            return category
    return "other"


def summarize(parsed: ParsedPacXml) -> dict[str, int]:
    """Return a ``{category: count}`` map for the dialog's info bar."""
    out: dict[str, int] = {}
    for f in parsed.fields:
        key = categorize_field(f)
        out[key] = out.get(key, 0) + 1
    return out
