"""Strict export of a complete character from a ``.app_xml`` manifest.

Reads the per-character appearance manifest, resolves every PAC
it names, parses each, resolves a shared skeleton + per-PAC
textures, and writes one merged FBX with all parts skinned to
the same rig.

Strict 1+1 design
-----------------
* The ``.app_xml`` is the sole source of truth for which PACs
  make up the character. No PAC is added through name guessing
  or directory scanning.
* The skeleton is palette-matched against the **Nude** PAC (the
  first ``<Prefab>`` in document order, by spec). Every other
  PAC must skin against this same rig — that's how the engine
  loads them at runtime.
* Each PAC's bone-palette is resolved per-PAC via
  :func:`derive_skin_slot_to_pab_geometric` BEFORE the merge so
  the merged mesh's ``bone_indices`` live in a single unified
  PAB-index space.
* Failure to load any individual PAC is recorded but never
  fatal — the rest of the export proceeds with an explicit
  ``pacs_skipped`` audit trail.
* Failure to resolve the shared skeleton IS fatal — exporting
  geometry without the right rig produces visibly broken FBX
  that Blender renders as a frozen T-pose at world origin.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CompleteCharacterResult:
    """Result of one complete-character export attempt.

    All fields are populated regardless of whether the export
    succeeded so the caller can show a detailed report. The
    ``failure_reason`` field is the single signal of "did the
    FBX get written at all" — non-empty means no FBX written.
    """
    fbx_path: str = ""
    app_xml_path: str = ""
    skeleton_pab_path: str = ""
    skeleton_bone_count: int = 0
    pacs_requested: list[str] = field(default_factory=list)
    pacs_loaded: list[str] = field(default_factory=list)
    pacs_skipped: list[tuple[str, str]] = field(
        default_factory=list,
    )   # (path, reason)
    total_submeshes: int = 0
    total_vertices: int = 0
    total_faces: int = 0
    unique_textures: int = 0
    base_color_count: int = 0    # how many submeshes got a diffuse
    failure_reason: str = ""


# ── Mesh + manifest mergers ───────────────────────────────────────

def merge_meshes(meshes: list, pac_paths: list[str]):
    """Combine N parsed meshes into one ParsedMesh with all
    submeshes concatenated.

    Each input mesh **must already have** its ``bone_indices``
    resolved to PAB indices (run
    :func:`core.mesh_parser.derive_skin_slot_to_pab_geometric`
    on each PAC before merging). The merger does not perform
    palette resolution; it only concatenates.

    Submesh names are prefixed with the source PAC stem so
    duplicates from different PACs stay distinguishable in
    Blender's outliner. Bbox is the union of inputs.
    """
    from core.mesh_parser import ParsedMesh, SubMesh   # late import — avoid Qt-import side effects in unit tests

    merged = ParsedMesh(path="merged_character", format="merged_pac")
    bbox_mins: list = []
    bbox_maxs: list = []

    for m, pac_path in zip(meshes, pac_paths):
        pac_stem = os.path.splitext(
            os.path.basename(pac_path),
        )[0]
        for sm in m.submeshes:
            new_sm = SubMesh(
                # Prefix the submesh name with the PAC stem so the
                # exported FBX shows e.g.
                # "cd_phw_00_head_00_0111__cd_phw_00_head_0001_01"
                # rather than 22 submeshes whose names collide.
                name=f"{pac_stem}__{sm.name}",
                material=sm.material,
                texture=sm.texture,
                vertices=list(sm.vertices),
                uvs=list(sm.uvs),
                normals=list(sm.normals),
                faces=list(sm.faces),
                bone_indices=list(sm.bone_indices),
                bone_weights=list(sm.bone_weights),
                vertex_count=sm.vertex_count,
                face_count=sm.face_count,
                source_vertex_offsets=list(sm.source_vertex_offsets),
                source_index_offset=sm.source_index_offset,
                source_index_count=sm.source_index_count,
                source_vertex_stride=sm.source_vertex_stride,
                source_descriptor_offset=sm.source_descriptor_offset,
                source_bbox_min=sm.source_bbox_min,
                source_bbox_extent=sm.source_bbox_extent,
                source_lod_count=sm.source_lod_count,
                source_vertex_map=list(sm.source_vertex_map),
            )
            merged.submeshes.append(new_sm)
        if m.bbox_min and m.bbox_max:
            bbox_mins.append(m.bbox_min)
            bbox_maxs.append(m.bbox_max)

    if bbox_mins:
        merged.bbox_min = (
            min(b[0] for b in bbox_mins),
            min(b[1] for b in bbox_mins),
            min(b[2] for b in bbox_mins),
        )
        merged.bbox_max = (
            max(b[0] for b in bbox_maxs),
            max(b[1] for b in bbox_maxs),
            max(b[2] for b in bbox_maxs),
        )

    merged.total_vertices = sum(
        sm.vertex_count for sm in merged.submeshes
    )
    merged.total_faces = sum(
        sm.face_count for sm in merged.submeshes
    )
    merged.has_uvs = any(sm.uvs for sm in merged.submeshes)
    merged.has_normals = any(sm.normals for sm in merged.submeshes)

    # Tell the FBX exporter the palette is already in PAB space.
    # Without this, ``derive_skin_slot_to_pab_geometric`` would run
    # on the merged mesh and re-interpret already-resolved indices
    # against the merged centroid — wrong answer guaranteed.
    merged._palette_resolved = True

    return merged


def merge_texture_manifests(manifests: list):
    """Concatenate per-PAC :class:`PacTextureManifest`s. The
    merged manifest carries one record per merged-mesh submesh
    in the same order as :func:`merge_meshes`. Submesh indices
    are renumbered so each record's ``submesh_index`` matches its
    position in the merged mesh's submesh list.
    """
    from core.pac_xml_texture_resolver import (   # late import
        PacTextureManifest, SubmeshTextureRecord,
    )

    out = PacTextureManifest(pac_path="merged_character")
    out.has_xml = True
    for tm in manifests:
        for rec in tm.records:
            out.records.append(SubmeshTextureRecord(
                submesh_index=len(out.records),
                submesh_name=rec.submesh_name,
                shader_template=rec.shader_template,
                base_color=rec.base_color,
                normal_map=rec.normal_map,
                material_map=rec.material_map,
                height_map=rec.height_map,
                extra_slots=dict(rec.extra_slots),
                xml_paths_raw=list(rec.xml_paths_raw),
                sentinel_slots=list(rec.sentinel_slots),
                unresolved_reasons=list(rec.unresolved_reasons),
            ))
    return out


# ── Top-level entry point ─────────────────────────────────────────

def export_complete_character(
    app_xml_path: str,
    output_dir: str,
    name: str,
    vfs,
) -> CompleteCharacterResult:
    """Export every PAC named by ``app_xml_path`` as a single
    merged FBX (mesh + skeleton + textures).

    Steps (each one strict; failures captured into the result):
      1. Resolve the appearance manifest.
      2. Parse every PAC the manifest names.
      3. Resolve the shared skeleton via the Nude PAC's palette
         match.
      4. Per-PAC: convert palette-slot bone_indices to direct
         PAB indices (so the merged mesh has a single bone-index
         space).
      5. Per-PAC: resolve textures from its ``.pac_xml``.
      6. Merge meshes + texture manifests.
      7. Call ``export_fbx_with_skeleton`` with the merged data.
    """
    # Late imports — keep this module importable in unit tests
    # that don't have access to the full PAC parser stack.
    from core.character_appearance_resolver import (
        resolve_appearance, vfs_manager_appearance_view,
    )
    from core.mesh_parser import (
        parse_mesh, derive_skin_slot_to_pab_geometric,
    )
    from core.skeleton_resolver import (
        resolve_skeleton, VfsManagerAdapter,
    )
    from core.pac_xml_texture_resolver import (
        resolve_pac_textures, vfs_manager_texture_view,
        collect_unique_dds_paths,
    )
    from core.mesh_exporter import export_fbx_with_skeleton

    result = CompleteCharacterResult(app_xml_path=app_xml_path)

    # ── Step 1: Resolve appearance manifest ──
    appearance_view = vfs_manager_appearance_view(vfs)
    manifest = resolve_appearance(app_xml_path, appearance_view)
    if not manifest.has_xml:
        result.failure_reason = (
            f"appearance resolution failed: {manifest.failure_reason}"
        )
        return result
    pac_paths = manifest.all_pac_paths()
    result.pacs_requested = list(pac_paths)
    if not pac_paths:
        result.failure_reason = (
            f"appearance manifest yielded zero PAC files"
        )
        return result

    # ── Step 2: Read + parse every PAC ──
    pamt_cache = getattr(vfs, "_pamt_cache", None) or {}
    vfs_idx: dict = {}
    for _gid, pamt in pamt_cache.items():
        for entry in getattr(pamt, "file_entries", []):
            p = getattr(entry, "path", "")
            if p:
                vfs_idx[p.replace("\\", "/").lower()] = entry

    pac_meshes: list = []
    pac_data_list: list[bytes] = []
    pac_path_list: list[str] = []
    for pac_path in pac_paths:
        entry = vfs_idx.get(pac_path.lower())
        if entry is None:
            result.pacs_skipped.append((pac_path, "not in VFS"))
            continue
        try:
            data = vfs.read_entry_data(entry)
        except Exception as exc:
            result.pacs_skipped.append(
                (pac_path, f"read failed: {exc}"),
            )
            continue
        try:
            mesh = parse_mesh(data, pac_path)
        except Exception as exc:
            result.pacs_skipped.append(
                (pac_path, f"parse failed: {exc}"),
            )
            continue
        if not mesh.submeshes:
            result.pacs_skipped.append(
                (pac_path, "no submeshes"),
            )
            continue
        # Attach raw PAC bytes for the per-PAC palette scan in
        # step 4. derive_skin_slot_to_pab_geometric reads
        # mesh._pac_bytes to find the section-0 hash palette.
        mesh._pac_bytes = data
        pac_meshes.append(mesh)
        pac_data_list.append(data)
        pac_path_list.append(pac_path)
        result.pacs_loaded.append(pac_path)

    if not pac_meshes:
        result.failure_reason = (
            f"no PACs loaded successfully "
            f"({len(result.pacs_skipped)} skipped)"
        )
        return result

    # ── Step 3: Resolve shared skeleton via the body PAC ──
    # The Nude prefab is always first in document order in a
    # well-formed .app_xml. Its PAC IS the body — its palette
    # match picks the right rig (verified: phw_01.pab for
    # damian, 448 bones).
    body_idx = 0
    sk_adapter = VfsManagerAdapter(vfs)
    sk_resolution = resolve_skeleton(
        pac_path_list[body_idx], sk_adapter,
        pac_bytes=pac_data_list[body_idx],
    )
    skeleton = sk_resolution.skeleton
    if skeleton is None or not skeleton.bones:
        result.failure_reason = (
            f"skeleton resolution failed for body PAC "
            f"{pac_path_list[body_idx]!r}: "
            f"{sk_resolution.reason or 'unknown'}"
        )
        return result
    result.skeleton_pab_path = sk_resolution.pab_path
    result.skeleton_bone_count = len(skeleton.bones)

    # ── Step 4: Per-PAC palette resolution ──
    # Each PAC has its own slot palette. Resolve each independently
    # against the shared skeleton so all submeshes' bone_indices
    # share a single PAB-index space after the merge. Failures
    # here are non-fatal — rigid props with no skinning palette
    # still produce a valid mesh-only export.
    for mesh in pac_meshes:
        try:
            derive_skin_slot_to_pab_geometric(mesh, skeleton)
        except Exception:
            # Per-PAC palette failures are silent — the resolver
            # already returns 0 successful pairs in that case,
            # which exports the mesh as static (no Skin deformer).
            pass

    # ── Step 5: Per-PAC texture resolution ──
    tex_view = vfs_manager_texture_view(vfs)
    pac_tex_manifests: list = []
    for mesh, pac_path in zip(pac_meshes, pac_path_list):
        tm = resolve_pac_textures(
            pac_path, tex_view,
            [sm.name for sm in mesh.submeshes],
        )
        pac_tex_manifests.append(tm)

    # ── Step 6: Merge meshes + manifests ──
    merged_mesh = merge_meshes(pac_meshes, pac_path_list)
    merged_tex_manifest = merge_texture_manifests(pac_tex_manifests)

    result.total_submeshes = len(merged_mesh.submeshes)
    result.total_vertices = merged_mesh.total_vertices
    result.total_faces = merged_mesh.total_faces
    result.unique_textures = len(
        collect_unique_dds_paths(merged_tex_manifest),
    )
    result.base_color_count = sum(
        1 for r in merged_tex_manifest.records if r.base_color
    )

    # ── Step 7: Export the merged FBX ──
    fbx_path = export_fbx_with_skeleton(
        merged_mesh, skeleton, output_dir, name,
        textures=merged_tex_manifest, texture_vfs=tex_view,
    )
    result.fbx_path = fbx_path
    return result
