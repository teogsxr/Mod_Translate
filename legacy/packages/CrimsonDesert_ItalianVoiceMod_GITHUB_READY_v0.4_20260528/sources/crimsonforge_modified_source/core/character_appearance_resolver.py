"""Strict, deterministic resolver for the per-character appearance
manifest (``.app_xml``) → flat list of PAC files.

Format
------
Every shipped character carries a small UTF-8/BOM XML file in
``character/`` named ``cd_<rig>_<char>_<NNNNN>.app_xml`` (e.g.
``cd_phw_damian_00000.app_xml``). It looks like::

    <Appearance>
      <Customization CustomizationFile="..."
                     MeshParamFile="..."
                     DecorationParamFile="..."/>
      <Nude>
        <Prefab Name="cd_phw_00_nude_00_0001_damian"
                CharacterScale="0.94"/>
      </Nude>
      <Head>
        <Prefab Name="cd_phw_00_head_00_0111" HeadScale="1.02"/>
      </Head>
      <Hair>
        <Prefab Name="cd_phw_00_hair_00_0008_01_player"/>
      </Hair>
      <Armor>
        <Prefab Name="cd_phw_00_ub_inner_0003"/>
        ... more ...
      </Armor>
    </Appearance>

Each ``Prefab Name`` resolves to a binary ``.prefab`` file at
``character/<Name>.prefab`` (verified 13/13 on damian). Each
``.prefab`` contains one or more ``SkinnedMeshComponent`` entries
serialised in Pearl Abyss's binary reflection format. We don't
need to decode the full reflection schema — the PAC paths appear
as plain UTF-8 strings ``character/model/1_pc/<rig>/<category>/
<basename>.pac`` next to length prefixes that happen to fall in
ASCII letter range.

Path remap (verified 22/22 on damian)
-------------------------------------
The prefab uses an "engine-canonical" path::

    character/model/1_pc/2_phw/head/head_sub/cd_phw_00_eyeleft_00_0001.pac

The VFS stores the same file at::

    character/cd_phw_00_eyeleft_00_0001.pac

Strict rule: take ``"character/" + os.path.basename(prefab_path)``.
Every prefab PAC ref maps to a basename-unique VFS entry; if the
remap doesn't hit, the resolver records the miss and refuses to
substitute a guess.

Strict refusal
--------------
* Missing ``.app_xml`` → manifest empty, ``failure_reason`` set.
* ``.app_xml`` parses but has zero ``<Prefab>`` children → empty
  parts list, ``failure_reason`` set.
* A ``Prefab Name`` whose ``.prefab`` file isn't in the VFS gets
  recorded with ``prefab_path == ""`` and stays in the parts
  list as evidence — the caller decides whether that's fatal.
* A prefab that yields zero PAC refs (rare; usually means a
  prefab type we haven't surveyed) is recorded with
  ``pac_paths == []`` for the same reason.
* A PAC ref that doesn't remap to an existing VFS entry is
  recorded as ``unresolved_pac_refs`` on its part rather than
  silently dropped.
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional, Protocol


# ── VFS protocol ──────────────────────────────────────────────────

class AppearanceVfs(Protocol):
    """Minimum surface this resolver needs from a VFS-like object.

    ``read_path_bytes`` returns decrypted/decompressed bytes for a
    VFS path, or ``None`` when the path isn't there. ``has_path``
    is a fast existence check used to avoid repeat reads.
    """

    def read_path_bytes(self, path: str) -> Optional[bytes]: ...
    def has_path(self, path: str) -> bool: ...


# ── Output dataclasses ────────────────────────────────────────────

@dataclass
class AppearancePart:
    """One ``<Prefab>`` entry in an ``.app_xml`` and the PACs it
    expands to.

    ``slot`` is the parent element name (``Nude`` / ``Head`` /
    ``Hair`` / ``Armor`` — observed across real ``.app_xml`` files;
    others may exist).

    ``attrs`` carries every attribute the original ``<Prefab>``
    element had so callers can read CharacterScale / HeadScale /
    Preview / etc. without re-parsing.

    ``pac_paths`` lists the canonical VFS paths after remap; an
    empty list with a populated ``unresolved_pac_refs`` means the
    prefab named PACs we couldn't find — strict refusal in action.
    """
    slot: str
    prefab_name: str
    attrs: dict[str, str] = field(default_factory=dict)
    prefab_path: str = ""             # 'character/<name>.prefab' or ''
    pac_paths: list[str] = field(default_factory=list)
    unresolved_pac_refs: list[str] = field(default_factory=list)


@dataclass
class AppearanceManifest:
    """Full result of resolving one ``.app_xml``."""
    app_xml_path: str
    parts: list[AppearancePart] = field(default_factory=list)
    customization_attrs: dict[str, str] = field(default_factory=dict)
    failure_reason: str = ""

    @property
    def has_xml(self) -> bool:
        return not self.failure_reason and bool(self.parts)

    def all_pac_paths(self) -> list[str]:
        """Distinct PAC paths across every part, in document order."""
        seen: set[str] = set()
        out: list[str] = []
        for part in self.parts:
            for p in part.pac_paths:
                if p not in seen:
                    seen.add(p)
                    out.append(p)
        return out


# ── Helpers ───────────────────────────────────────────────────────

# Match a path-like prefix ending in ``.pac`` but NOT ``.pac_xml``
# or ``.pac_other``. The trailing length-prefix byte inside a
# binary prefab is often an ASCII letter (e.g. I=0x49 for
# length=73), so a ``\b`` boundary won't work. The negative
# lookahead ``(?!_)`` filters out ``.pac_xml`` while accepting
# any other byte after ``.pac``.
_PAC_REF_RE = re.compile(rb'[A-Za-z0-9_/\-]+\.pac(?!_)', re.IGNORECASE)


def _xml_companion_path(prefab_name: str) -> str:
    """Map a ``<Prefab Name="X">`` to its on-disk file path."""
    return f"character/{prefab_name}.prefab"


def _remap_prefab_pac_to_vfs(prefab_pac_ref: str) -> str:
    """Strict path remap from prefab's "engine-canonical" path to a
    VFS path: ``"character/" + basename``.

    Verified 22/22 on damian's complete prefab set. Empty inputs
    produce an empty output (the caller checks).
    """
    if not prefab_pac_ref:
        return ""
    base = os.path.basename(prefab_pac_ref.replace("\\", "/"))
    if not base:
        return ""
    return f"character/{base}"


def _parse_app_xml(data: bytes) -> ET.Element:
    """Decode + parse the ``.app_xml``. Strips a UTF-8 BOM if
    present. Raises :class:`ValueError` on malformed input.
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(f".app_xml is not valid UTF-8: {e}") from e
    text = text.replace("﻿", "")
    try:
        return ET.fromstring(text)
    except ET.ParseError as e:
        raise ValueError(f".app_xml did not parse: {e}") from e


def _extract_pac_refs(prefab_bytes: bytes) -> list[str]:
    """Pull every ``.pac`` reference from a binary prefab. Returns
    distinct paths in first-seen order so caller iteration is
    deterministic.
    """
    seen: set[str] = set()
    out: list[str] = []
    for m in _PAC_REF_RE.findall(prefab_bytes):
        s = m.decode("utf-8", errors="replace").lower()
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


# ── Public entry points ───────────────────────────────────────────

def resolve_appearance(
    app_xml_path: str,
    vfs: AppearanceVfs,
) -> AppearanceManifest:
    """Resolve ``.app_xml`` → :class:`AppearanceManifest`.

    Parameters
    ----------
    app_xml_path :
        VFS-relative path to a ``.app_xml`` file (e.g.
        ``character/cd_phw_damian_00000.app_xml``).
    vfs :
        Anything satisfying :class:`AppearanceVfs`. The production
        wrapper is :func:`vfs_manager_appearance_view` below.

    Returns
    -------
    :class:`AppearanceManifest` — populated when the chain ran end
    to end, or with ``failure_reason`` set on first failure.

    Never raises — every error path captures into ``failure_reason``
    or per-part diagnostics so the UI can show one consistent
    error surface.
    """
    manifest = AppearanceManifest(app_xml_path=app_xml_path)

    try:
        raw = vfs.read_path_bytes(app_xml_path)
    except Exception as exc:
        manifest.failure_reason = f"VFS read failed: {exc}"
        return manifest
    if raw is None:
        manifest.failure_reason = (
            f"appearance manifest not in VFS: {app_xml_path}"
        )
        return manifest

    try:
        root = _parse_app_xml(raw)
    except ValueError as exc:
        manifest.failure_reason = str(exc)
        return manifest

    # ── Capture <Customization> attributes for the caller ──
    # Not strictly part of the part list but useful — the file
    # names the per-character mesh-param / decoration-param XMLs
    # the engine reads alongside the manifest.
    for child in root:
        if child.tag == "Customization":
            manifest.customization_attrs = dict(child.attrib)
            break

    # ── Walk every <Prefab Name="..."> under any slot child ──
    parts: list[AppearancePart] = []
    for slot in root:
        if slot.tag == "Customization":
            continue
        for prefab_elem in slot:
            if prefab_elem.tag != "Prefab":
                continue
            name = prefab_elem.attrib.get("Name", "").strip()
            if not name:
                continue
            parts.append(AppearancePart(
                slot=slot.tag,
                prefab_name=name,
                attrs=dict(prefab_elem.attrib),
            ))

    if not parts:
        manifest.failure_reason = (
            f"appearance manifest has no <Prefab> children "
            f"({app_xml_path})"
        )
        return manifest

    # ── For each part: locate prefab, extract PAC refs, remap ──
    for part in parts:
        part.prefab_path = _xml_companion_path(part.prefab_name)
        try:
            pf_bytes = vfs.read_path_bytes(part.prefab_path)
        except Exception:
            pf_bytes = None
        if pf_bytes is None:
            # Strict: prefab missing → empty part, no fallback.
            part.prefab_path = ""
            continue

        refs = _extract_pac_refs(pf_bytes)
        for ref in refs:
            remapped = _remap_prefab_pac_to_vfs(ref)
            if remapped and vfs.has_path(remapped):
                part.pac_paths.append(remapped)
            else:
                part.unresolved_pac_refs.append(ref)

    manifest.parts = parts
    return manifest


# ── VFS adapter for the production VfsManager ────────────────────

class _VfsManagerAppearanceView:
    """Wrap a real :class:`core.vfs_manager.VfsManager` into the
    :class:`AppearanceVfs` shape.

    Builds (lazily) a global ``path -> entry`` index so
    ``has_path`` and ``read_path_bytes`` are both O(1) per call,
    and so :func:`_build_body_pac_index` can enumerate every VFS
    path through :meth:`iter_paths` without the caller having to
    reach into ``_pamt_cache`` directly.
    """

    def __init__(self, vfs):
        self._vfs = vfs
        self._index: Optional[dict] = None

    def _ensure_index(self) -> dict:
        if self._index is not None:
            return self._index
        idx: dict = {}
        cache = getattr(self._vfs, "_pamt_cache", None) or {}
        for _gid, pamt in cache.items():
            for entry in getattr(pamt, "file_entries", []):
                p = getattr(entry, "path", "")
                if p:
                    idx[p.replace("\\", "/").lower()] = entry
        self._index = idx
        return idx

    def has_path(self, path: str) -> bool:
        return path.replace("\\", "/").lower() in self._ensure_index()

    def read_path_bytes(self, path: str) -> Optional[bytes]:
        idx = self._ensure_index()
        entry = idx.get(path.replace("\\", "/").lower())
        if entry is None:
            return None
        try:
            return self._vfs.read_entry_data(entry)
        except Exception:
            return None

    def iter_paths(self):
        """Yield every VFS path the wrapped manager knows about."""
        return iter(self._ensure_index().keys())


def vfs_manager_appearance_view(vfs) -> AppearanceVfs:
    """Wrap a ``VfsManager`` so it satisfies :class:`AppearanceVfs`.

    Production callers pass the result to
    :func:`resolve_appearance`. The wrapper memoises the path
    index so repeat resolutions over the same VfsManager are O(1)
    per lookup.
    """
    return _VfsManagerAppearanceView(vfs)


# ── Reverse lookup: PAC → .app_xml ────────────────────────────────

# Module-level cache keyed by id(vfs) so right-click handlers don't
# re-scan all 5,000+ ``character/*.app_xml`` files on every click.
# The cache is invalidated when the VFS instance changes (e.g. user
# reloads the game), because the id() of a new VfsManager won't
# match a previous one.
_BODY_PAC_TO_APP_XML_CACHE: dict[int, dict[str, list[str]]] = {}


def _iter_vfs_paths(vfs) -> "list[str] | object":
    """Yield every VFS path visible through ``vfs``.

    Three accepted surfaces, in priority order:
      1. ``vfs.iter_paths()`` (preferred — explicit protocol method).
      2. ``vfs._pamt_cache`` (real ``VfsManager`` shape).
      3. Test-fake shape: a ``paths`` mapping.

    Returns a list (already materialised) so the caller can iterate
    multiple times if needed without exhausting a generator.
    """
    iter_method = getattr(vfs, "iter_paths", None)
    if callable(iter_method):
        return [str(p) for p in iter_method()]
    cache = getattr(vfs, "_pamt_cache", None)
    if cache:
        out: list[str] = []
        for _gid, pamt in cache.items():
            for entry in getattr(pamt, "file_entries", []):
                p = getattr(entry, "path", "")
                if p:
                    out.append(str(p))
        return out
    paths = getattr(vfs, "paths", None)
    if isinstance(paths, dict):
        return list(paths.keys())
    return []


def _build_body_pac_index(vfs: AppearanceVfs) -> dict[str, list[str]]:
    """Walk every ``character/*.app_xml`` and build a map from
    body-PAC basename stem → list of .app_xml paths that include it.

    The strict 1+1 chain is:

        .app_xml  →  <Nude><Prefab Name="X">
                  →  character/X.prefab
                  →  binary _skinnedMeshFile bytes
                  →  PAC reference(s) (after path remap)

    For **heroes** the prefab's name happens to equal the body
    PAC's basename stem (e.g. ``cd_phw_00_nude_00_0001_damian``
    is both the prefab name AND the PAC stem). A name-only
    match works there.

    For **monsters / animals** the prefab and PAC have different
    names by design. Damian's hero prefab is named after the PAC
    it wraps; ogre's monster prefab is
    ``cd_m0001_00_ogre_nude_0001.prefab`` while the body PAC it
    wraps is ``cd_m0001_00_ogre_0001.pac``. A name-only match
    misses every monster.

    The ONLY strict link that works for both families is the
    PAC reference inside the prefab's binary payload, which we
    extract via :func:`_extract_pac_refs` (the same regex the
    main resolver uses). The reference is then path-remapped
    via :func:`_remap_prefab_pac_to_vfs` to the canonical VFS
    location, and we index the basename stem so the right-click
    handler can match by either PAC path or just basename.

    The map is keyed by lowercase basename stem and the values
    are lists so a body PAC re-used across multiple appearance
    variants surfaces all of them.
    """
    out: dict[str, list[str]] = {}
    for path in _iter_vfs_paths(vfs):
        if not path:
            continue
        pl = path.replace("\\", "/").lower()
        if not pl.endswith(".app_xml"):
            continue
        if not pl.startswith("character/"):
            continue
        try:
            raw = vfs.read_path_bytes(path)
        except Exception:
            raw = None
        if raw is None:
            continue
        try:
            root = _parse_app_xml(raw)
        except ValueError:
            continue
        for slot in root:
            if slot.tag != "Nude":
                continue
            for prefab in slot:
                if prefab.tag != "Prefab":
                    continue
                nm = prefab.attrib.get("Name", "").strip()
                if not nm:
                    continue
                lower_path = path.replace("\\", "/")

                # Always index the prefab name itself — for hero
                # appearances the prefab name == PAC stem, so a
                # quick lookup hits without needing to read the
                # prefab bytes.
                out.setdefault(nm.lower(), []).append(lower_path)

                # Then ALSO read the prefab's binary payload and
                # extract its PAC references. Monsters need this
                # because their prefab name differs from the PAC
                # name. This step is the strict 1+1 link the
                # engine itself follows (prefab._skinnedMeshFile
                # → PAC).
                prefab_vfs_path = _xml_companion_path(nm)
                try:
                    pf_bytes = vfs.read_path_bytes(prefab_vfs_path)
                except Exception:
                    pf_bytes = None
                if pf_bytes is None:
                    continue
                for raw_ref in _extract_pac_refs(pf_bytes):
                    remapped = _remap_prefab_pac_to_vfs(raw_ref)
                    if not remapped:
                        continue
                    # Index by basename stem (case-insensitive)
                    # so the right-click handler's path-stem
                    # lookup hits regardless of which directory
                    # the PAC actually lives in.
                    base = os.path.basename(remapped)
                    stem = os.path.splitext(base)[0].lower()
                    if not stem:
                        continue
                    out.setdefault(stem, []).append(lower_path)
    return out


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    """Drop duplicates from ``items`` preserving first-seen order.

    The body-pac index records the same .app_xml under multiple
    keys (the prefab name AND every PAC ref inside the prefab),
    which can surface the same path twice when a PAC stem matches
    both the prefab name and a content ref. The picker UI shouldn't
    show the same option twice.
    """
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        if it not in seen:
            seen.add(it)
            out.append(it)
    return out


def find_app_xmls_for_body_pac(
    pac_path: str,
    vfs,
) -> list[str]:
    """Return every appearance manifest whose ``<Nude>`` Prefab is
    the body PAC at ``pac_path``.

    The match is by basename stem (case-insensitive), e.g.
    ``character/cd_phw_00_nude_00_0001_damian.pac`` finds every
    ``.app_xml`` whose ``<Nude><Prefab Name="...">`` is
    ``cd_phw_00_nude_00_0001_damian``.

    Strict 1+1: there is no name-pattern fallback. The match is
    against the prefab name the engine actually consumes — if the
    XML doesn't list the prefab, this returns an empty list.

    Accepts either an :class:`AppearanceVfs` (test fakes) or a
    real ``VfsManager`` (production). When given a VfsManager the
    helper wraps it on demand so the caller doesn't have to
    remember which surface to construct.

    Result is cached per VFS instance — the first call scans every
    ``character/*.app_xml`` once; subsequent calls are O(1).
    """
    if not pac_path:
        return []
    cache_key = id(vfs)
    index = _BODY_PAC_TO_APP_XML_CACHE.get(cache_key)
    if index is None:
        # Auto-wrap a real VfsManager into the AppearanceVfs surface
        # so callers can pass either. We detect "needs wrapping" by
        # the absence of the read_path_bytes method.
        view = (
            vfs
            if hasattr(vfs, "read_path_bytes")
            else vfs_manager_appearance_view(vfs)
        )
        index = _build_body_pac_index(view)
        _BODY_PAC_TO_APP_XML_CACHE[cache_key] = index

    base = os.path.basename(pac_path.replace("\\", "/"))
    stem = os.path.splitext(base)[0].lower()
    # The index records each .app_xml under multiple keys (the
    # prefab name AND every PAC ref inside the prefab) so a hero
    # whose prefab name equals its PAC stem ends up twice. Dedupe
    # before returning so the picker UI sees each variant exactly
    # once.
    return _dedupe_preserve_order(index.get(stem, []))


def invalidate_body_pac_index_cache() -> None:
    """Drop every cached body-pac → app_xml index. Called from the
    VFS reload path so a new game install / patch picks up changes
    without restarting the app.
    """
    _BODY_PAC_TO_APP_XML_CACHE.clear()
