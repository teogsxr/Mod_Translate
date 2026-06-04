"""Strict, deterministic PAC → texture resolution.

This module decodes the `.pac_xml` companion file that ships next
to every character `.pac` and turns it into a per-submesh table of
texture file paths. It does NOT guess — every choice is one attribute
lookup against the parsed XML, against well-defined slot names that
were verified against 14,193 real Materials in 2,000 sample
``character/*.pac_xml`` files.

The full forensic decoding lives in
``test_only/research/2026-05-08_fbx_export_pipeline/14_texture_resolution_chain.md``.
A short summary of the rules this module relies on:

  1. The companion XML lives at ``<pac_path>[:-4] + '.pac_xml'``.
  2. Inside it, ``ModelPropertyList`` may have multiple
     ``<ModelProperty>`` children — the first one (``Index="0"``)
     is the visible main-LOD mesh. We use only that one. Subsequent
     entries are LOD/shadow proxies whose textures we don't need.
  3. Each submesh in the PAC is paired with one
     ``<SkinnedMeshMaterialWrapper>`` element via
     ``_subMeshName == lower(submesh.name)``. This is the strict
     join key.
  4. Inside the wrapper, the ``<Material _materialName="...">``
     element carries one ``<MaterialParameterTexture>`` per slot.
     The slot's ``_name`` attribute names the semantic role.
  5. ``_baseColorTexture`` and ``_overlayColorTexture`` are
     **mutually exclusive** across every observed Material
     (both=0 / 14,193). We use whichever is present as the Base
     Color; if neither is present (≈23% of Materials, the
     procedural mask-driven shaders) we deliberately leave the
     binding empty rather than fake one.
  6. The ``_path`` string inside each MaterialParameterTexture
     uses an "engine-logical" form ``character/texture/foo.dds``
     where the actual VFS entry lives at ``character/foo.dds``.
     The remap rule (verified 191/191 on real samples) is to
     strip the ``texture/`` segment when it is the second path
     component.
  7. ``texture/nonetexture0x*.dds`` are engine sentinels meaning
     "no texture in this slot". We treat them as null bindings.

When any link in this chain is missing, the resolver returns a
record with the corresponding field set to ``None`` and a reason
captured in ``unresolved_reasons`` — never a silent fallback to
some other DDS we found nearby.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional, Protocol

from core.pac_xml_parser import parse_pac_xml


# ── Slot semantics — the strict catalog ────────────────────────────

# Mutually-exclusive primary-color slot names. Verified across
# 14,193 real Materials: every Material has at most one of these.
# Order in this tuple is informational only — there is no priority
# decision because they never co-exist.
COLOR_SLOTS: tuple[str, ...] = (
    "_baseColorTexture",
    "_overlayColorTexture",
)

# Slots whose semantic maps cleanly to a Blender Principled BSDF
# input. These are wired into the FBX material connections.
BSDF_SLOTS: dict[str, str] = {
    "_baseColorTexture":    "base_color",
    "_overlayColorTexture": "base_color",
    "_normalTexture":       "normal_map",
    "_materialTexture":     "material_map",   # game-specific spec/roughness pack
    "_heightTexture":       "height_map",
}

# Engine sentinel paths (nonetexture0x*.dds). These mean "no
# texture in this slot" and must never be exported as a real DDS
# file or wired into a Blender material connection.
_SENTINEL_PREFIX = "texture/nonetexture0x"


# ── Output dataclasses ─────────────────────────────────────────────

@dataclass
class SubmeshTextureRecord:
    """Strict, deterministic texture binding for a single submesh.

    Every field is either populated from a real
    ``<MaterialParameterTexture>`` lookup against real bytes in the
    VFS, or left as ``None`` (with a captured reason) when the
    chain didn't yield an answer. There are no inferred values.
    """
    submesh_index:   int
    submesh_name:    str
    shader_template: str = ""

    # Slot-keyed primary mappings (canonical VFS paths after remap)
    base_color:   Optional[str] = None
    normal_map:   Optional[str] = None
    material_map: Optional[str] = None
    height_map:   Optional[str] = None

    # Every other slot present on this Material, keyed by slot _name.
    # Saved alongside the FBX so users can hand-wire procedural
    # shaders in Blender; not connected automatically because
    # they have no clean Blender BSDF equivalent.
    extra_slots:  dict[str, str] = field(default_factory=dict)

    # Diagnostics — kept around for the .debug.txt sidecar.
    xml_paths_raw:        list[str] = field(default_factory=list)
    sentinel_slots:       list[str] = field(default_factory=list)
    unresolved_reasons:   list[str] = field(default_factory=list)


@dataclass
class PacTextureManifest:
    """Result of resolving every submesh's textures for one PAC."""
    pac_path:        str
    xml_path:        str = ""
    has_xml:         bool = False
    records:         list[SubmeshTextureRecord] = field(default_factory=list)
    # Top-level reason when the manifest came back empty (no XML
    # companion, parse failure, ...). When this is non-empty
    # ``records`` is empty too.
    failure_reason:  str = ""


# ── VFS protocol ───────────────────────────────────────────────────

class TextureVfs(Protocol):
    """Minimum VFS surface this module needs.

    A single ``read_path_bytes`` method that returns the decrypted
    decompressed bytes for a VFS path, or ``None`` when the path
    isn't in the VFS. Tests use a tiny dict-backed fake; the
    production wiring lives in :func:`vfs_manager_texture_view`
    below which builds the same surface around a real
    :class:`core.vfs_manager.VfsManager`.
    """

    def read_path_bytes(self, path: str) -> Optional[bytes]: ...


class _VfsManagerView:
    """Adapter that exposes a :class:`TextureVfs` surface around a
    real ``VfsManager``.

    Builds (lazily, once) a global ``path -> entry`` index across
    every loaded PAMT group so a single ``read_path_bytes`` call
    is one dict lookup + one decrypt-and-decompress. The path
    index reuses the manager's ``_pamt_cache``; we don't fetch
    anything that isn't already loaded.
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

    def read_path_bytes(self, path: str) -> Optional[bytes]:
        idx = self._ensure_index()
        entry = idx.get(path.replace("\\", "/").lower())
        if entry is None:
            return None
        try:
            return self._vfs.read_entry_data(entry)
        except Exception:
            return None


def vfs_manager_texture_view(vfs) -> TextureVfs:
    """Wrap a ``VfsManager`` into the :class:`TextureVfs` shape.

    Production callers pass the result to :func:`resolve_pac_textures`.
    The wrapper memoises the path index so repeat resolutions over
    the same VfsManager are O(1) per lookup.
    """
    return _VfsManagerView(vfs)


# ── Path-remap rule ────────────────────────────────────────────────

def remap_xml_path_to_vfs(xml_path: str) -> str:
    """Apply the strict XML→VFS path-remap rule.

    Verified on 191/193 real ``_path`` strings: when the second
    path segment is exactly ``"texture"``, drop it. Engine
    sentinel paths (``texture/nonetexture0x*.dds``) and any
    other already-canonical paths are returned unchanged.

    >>> remap_xml_path_to_vfs("character/texture/foo.dds")
    'character/foo.dds'
    >>> remap_xml_path_to_vfs("texture/nonetexture0x00000000.dds")
    'texture/nonetexture0x00000000.dds'
    >>> remap_xml_path_to_vfs("foo.dds")
    'foo.dds'
    """
    if not xml_path:
        return xml_path
    parts = xml_path.replace("\\", "/").split("/")
    if len(parts) >= 3 and parts[1].lower() == "texture":
        del parts[1]
    return "/".join(parts)


def is_sentinel_path(path: str) -> bool:
    """True for engine sentinel "no texture" paths.

    These are ``texture/nonetexture0x<hex>.dds`` strings the engine
    uses to fill texture slots that aren't actually bound. They
    must never be treated as real DDS references.
    """
    if not path:
        return False
    return path.replace("\\", "/").lower().startswith(_SENTINEL_PREFIX)


# ── Core resolver ──────────────────────────────────────────────────

def _xml_companion_path(pac_path: str) -> str:
    """Compute the strict companion ``.pac_xml`` path."""
    p = pac_path.replace("\\", "/")
    if p.lower().endswith(".pac"):
        return p[:-4] + ".pac_xml"
    # Defensive: caller passed something odd. Append the suffix
    # rather than making up an extension swap.
    return p + ".pac_xml"


def _parse_companion_xml(pac_path: str, vfs: TextureVfs):
    """Read the companion .pac_xml and parse it; return None on
    any failure (which is itself a strict signal — there is no
    fallback search for a different XML). The caller wraps with
    a manifest-level reason.
    """
    xml_path = _xml_companion_path(pac_path)
    try:
        raw = vfs.read_path_bytes(xml_path)
    except Exception as exc:
        return None, xml_path, f"failed to read {xml_path}: {exc}"
    if raw is None:
        return None, xml_path, f"companion XML not in VFS: {xml_path}"
    try:
        parsed = parse_pac_xml(raw, path=xml_path)
    except Exception as exc:
        return None, xml_path, f"failed to parse {xml_path}: {exc}"
    return parsed, xml_path, ""


def _first_model_property(parsed_xml):
    """Return the first ``<ModelProperty>`` element under
    ``<ModelPropertyList>``, or None when the document doesn't
    have one.

    "First" = element-order, which on real samples corresponds to
    the visible main-LOD mesh. Subsequent ModelProperty elements
    encode LODs / shadow proxies whose textures we don't need.
    """
    for top in parsed_xml.tree:
        if top.tag != "ModelPropertyList":
            continue
        for child in top:
            if child.tag == "ModelProperty":
                return child
    return None


def _wrappers_under(model_property) -> list:
    """Return every ``<SkinnedMeshMaterialWrapper>`` directly under
    the given ``<ModelProperty>``'s ``<SkinnedMeshProperty>/<Vector>``
    container, in document order.

    The structure is fixed across every observed PAC: each
    wrapper is one direct grandchild of the SkinnedMeshProperty.
    Anything else under there (other element types) is ignored.
    """
    out: list = []
    for child in model_property.iter():
        if child.tag == "SkinnedMeshMaterialWrapper":
            out.append(child)
    return out


def _material_in_wrapper(wrapper):
    """Return the first ``<Material Name="_resourceMaterial">`` inside
    the wrapper. Each wrapper has exactly one in real samples.
    """
    for desc in wrapper.iter():
        if desc.tag == "Material":
            return desc
    return None


def _texture_slots(material) -> list:
    """Yield ``(_name, _path, slot_element)`` for every
    ``<MaterialParameterTexture>`` slot in this material.

    The slot element's ``_name`` attribute is the slot semantic
    (``_baseColorTexture``, ``_normalTexture``, …). The ``_path``
    lives on the inner ``<ResourceReferencePath_ITexture>`` child;
    we walk down to find it. Slots that don't have a ``_path``
    descendant (rare; usually placeholder/unset slots) are
    skipped.
    """
    out = []
    for desc in material.iter():
        if desc.tag != "MaterialParameterTexture":
            continue
        slot_name = desc.attrib.get("_name", "")
        # Walk descendants to find the _path attribute on the
        # ResourceReferencePath_ITexture child. Using descendant
        # iteration so we don't depend on the exact <Vector>
        # nesting depth.
        slot_path = ""
        for sub in desc.iter():
            if "_path" in sub.attrib:
                slot_path = sub.attrib["_path"]
                break
        if slot_name and slot_path:
            out.append((slot_name, slot_path, desc))
    return out


def resolve_pac_textures(
    pac_path: str,
    vfs: TextureVfs,
    submesh_names: list[str],
    *,
    pac_xml_bytes: Optional[bytes] = None,
) -> PacTextureManifest:
    """Resolve every submesh's textures from the .pac_xml companion.

    Parameters
    ----------
    pac_path :
        VFS-relative path of the PAC. The companion XML is found
        by suffix-swap; no other paths are searched.
    vfs :
        Anything satisfying :class:`TextureVfs`. The production
        ``VfsManager`` does.
    submesh_names :
        ``submesh.name`` strings from the PAC, in PAC order. Used
        to build the deterministic submesh→Material join via the
        wrapper's ``_subMeshName`` attribute.
    pac_xml_bytes :
        Optional caller-supplied bytes. When provided, skips the
        VFS read; useful for tests and for callers that already
        loaded the XML for editing.

    Returns
    -------
    :class:`PacTextureManifest`. When the companion XML can't be
    located or parsed, ``records`` is empty and ``failure_reason``
    explains why. When parsing succeeded but a particular slot
    didn't have a real texture, the corresponding field on that
    submesh's record is ``None`` (never a guessed neighbour).
    """
    manifest = PacTextureManifest(pac_path=pac_path)

    # ── 1) Locate + parse the companion .pac_xml ───────────────
    if pac_xml_bytes is None:
        parsed, xml_path, reason = _parse_companion_xml(pac_path, vfs)
        manifest.xml_path = xml_path
        if parsed is None:
            manifest.failure_reason = reason
            return manifest
    else:
        manifest.xml_path = _xml_companion_path(pac_path)
        try:
            parsed = parse_pac_xml(pac_xml_bytes, path=manifest.xml_path)
        except Exception as exc:
            manifest.failure_reason = (
                f"failed to parse provided pac_xml bytes: {exc}"
            )
            return manifest
    manifest.has_xml = True

    # ── 2) Find ModelProperty[Index="0"] (first / main mesh) ───
    mp = _first_model_property(parsed)
    if mp is None:
        manifest.failure_reason = (
            "no <ModelProperty> found under <ModelPropertyList>"
        )
        return manifest

    # ── 3) Index wrappers by lowercase _subMeshName ────────────
    wrappers_by_name: dict[str, object] = {}
    for w in _wrappers_under(mp):
        sub = w.attrib.get("_subMeshName", "").lower().strip()
        if not sub:
            continue
        # First-seen wins: real PACs don't have duplicates inside
        # a single ModelProperty, but defensive in case of mods.
        if sub not in wrappers_by_name:
            wrappers_by_name[sub] = w

    # ── 4) Build one record per PAC submesh ────────────────────
    for i, sm_name in enumerate(submesh_names):
        rec = SubmeshTextureRecord(
            submesh_index=i,
            submesh_name=sm_name,
        )
        key = (sm_name or "").lower().strip()
        wrapper = wrappers_by_name.get(key)
        if wrapper is None:
            rec.unresolved_reasons.append(
                f"no <SkinnedMeshMaterialWrapper> with "
                f"_subMeshName={key!r} in ModelProperty[0]"
            )
            manifest.records.append(rec)
            continue
        material = _material_in_wrapper(wrapper)
        if material is None:
            rec.unresolved_reasons.append(
                "wrapper present but no <Material> child"
            )
            manifest.records.append(rec)
            continue
        rec.shader_template = material.attrib.get("_materialName", "")

        # Walk every slot. Sentinel paths are recorded but not
        # stored as real DDS references.
        for slot_name, slot_path, _slot_elem in _texture_slots(material):
            rec.xml_paths_raw.append(slot_path)
            if is_sentinel_path(slot_path):
                rec.sentinel_slots.append(slot_name)
                continue
            vfs_path = remap_xml_path_to_vfs(slot_path)
            # Map well-known slots to their dedicated record fields;
            # everything else lands in extra_slots verbatim.
            if slot_name == "_baseColorTexture":
                rec.base_color = vfs_path
            elif slot_name == "_overlayColorTexture":
                # Mutually exclusive with base_color in real data.
                # Defensive: only set if base_color is still empty
                # (preserves _baseColor priority if a future mod
                # ever ships both — they shouldn't, but if they do
                # the older slot name wins).
                if rec.base_color is None:
                    rec.base_color = vfs_path
                else:
                    rec.extra_slots[slot_name] = vfs_path
            elif slot_name == "_normalTexture":
                rec.normal_map = vfs_path
            elif slot_name == "_materialTexture":
                rec.material_map = vfs_path
            elif slot_name == "_heightTexture":
                rec.height_map = vfs_path
            else:
                rec.extra_slots[slot_name] = vfs_path

        if rec.base_color is None and rec.normal_map is None \
                and rec.material_map is None and rec.height_map is None:
            rec.unresolved_reasons.append(
                "Material has no _baseColorTexture / _overlayColorTexture / "
                "_normalTexture / _materialTexture / _heightTexture slots — "
                f"shader is procedural ({rec.shader_template!r})"
            )

        manifest.records.append(rec)

    return manifest


def collect_unique_dds_paths(
    manifest: PacTextureManifest,
) -> list[str]:
    """Return every distinct VFS path referenced by the manifest,
    in stable first-seen order. Sentinels are excluded.

    Useful for the FBX exporter when copying DDS files alongside
    the FBX — you want each unique file once, not duplicates per
    submesh.
    """
    seen: set[str] = set()
    out: list[str] = []
    for rec in manifest.records:
        for p in (
            rec.base_color, rec.normal_map,
            rec.material_map, rec.height_map,
        ):
            if p and p not in seen:
                seen.add(p)
                out.append(p)
        for _slot, p in rec.extra_slots.items():
            if p and p not in seen:
                seen.add(p)
                out.append(p)
    return out
