"""OBJ / FBX importer and PAC/PAM binary builder for round-trip mesh modding.

Pipeline: Export .pac → edit in Blender → save .obj/.fbx → import_obj/import_fbx()
          → build_pac() → repack

The OBJ/FBX must have been exported by CrimsonForge so the .cfmeta.json sidecar
is present alongside it. The original PAC/PAM binary is needed to preserve
metadata (names, materials, bones, flags) that OBJ/FBX cannot store.
"""

from __future__ import annotations

import copy
import os
import struct
import math
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from core.mesh_parser import (
    ParsedMesh,
    SubMesh,
    parse_pac,
    parse_pam,
    parse_pamlod,
    _find_pac_descriptors,
    _parse_par_sections,
    _compute_smooth_normals,
    _find_local_stride,
    STRIDE_CANDIDATES,
)
from utils.logger import get_logger

logger = get_logger("core.mesh_importer")


def _resolve_obj_index(raw_index: str, item_count: int) -> int:
    """Resolve a Wavefront OBJ index token to a zero-based Python index."""
    value = int(raw_index)
    if value > 0:
        return value - 1
    if value < 0:
        return item_count + value
    raise ValueError("OBJ indices are 1-based and cannot be zero")


# ═══════════════════════════════════════════════════════════════════════
#  OBJ IMPORTER
# ═══════════════════════════════════════════════════════════════════════

def _load_cfmeta_sidecar(obj_path: str) -> dict | None:
    """Read the ``<obj>.cfmeta.json`` sidecar if present.

    The sidecar lives next to the OBJ/FBX and records skin weights +
    per-vertex source-PAC mapping. Returns None when the sidecar is
    missing or malformed — callers must tolerate that case, because
    Blender users may edit the OBJ in a path that loses the sidecar.

    Supported schema versions:
      v1 — original. Per-submesh: name, vertex_count, bone_indices,
           bone_weights. No source_vertex_map (identity assumed by
           import path), no filtered_vertices.
      v2 — adds source_vertex_map (each FBX vertex's original PAC
           slot) and filtered_vertices (spike donor records preserved
           verbatim across the round-trip). v2 is what FBX exports
           with skin write — see ``_write_cfmeta_sidecar_v2`` in
           mesh_exporter for schema details.

    Both versions are returned as-is; callers branch on
    ``data.get('schema_version')`` to consume new fields.
    """
    import json
    sidecar_path = obj_path + ".cfmeta.json"
    if not os.path.isfile(sidecar_path):
        return None
    try:
        with open(sidecar_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Failed to read cfmeta sidecar %s: %s", sidecar_path, e)
        return None
    if not isinstance(data, dict):
        logger.warning("cfmeta sidecar %s has invalid root type", sidecar_path)
        return None
    schema = data.get("schema_version")
    if schema not in (1, 2):
        logger.warning(
            "cfmeta sidecar %s has unsupported schema_version %s",
            sidecar_path, schema,
        )
        return None
    return data


def import_obj(obj_path: str) -> ParsedMesh:
    """Import an OBJ file back into a ParsedMesh.

    Reads CrimsonForge metadata comments (source_path, source_format)
    to identify the original game file. Also loads the optional
    ``<obj>.cfmeta.json`` sidecar to recover skin weights + vertex
    identity across the Blender round-trip. When the sidecar is
    present:

      * ``SubMesh.source_vertex_map`` is populated so the PAC
        rebuilder picks the correct donor record for each vertex
        (survives user edits that move vertices far from any
        original position).
      * ``SubMesh.bone_indices`` / ``bone_weights`` are propagated
        from the original mesh, and correctly duplicated when a
        vertex gets split by the UV-seam handling below.

    When the sidecar is absent, the importer falls back to the
    pre-v1.22.3 behaviour (empty skin data, positional donor
    matching in ``build_pac``).

    Returns:
        ParsedMesh with vertices, UVs, normals, faces per submesh.
    """
    sidecar = _load_cfmeta_sidecar(obj_path)
    source_path = ""
    source_format = ""
    submeshes: list[SubMesh] = []

    # Current submesh being built
    current_name = ""
    verts: list[tuple[float, float, float]] = []
    uvs: list[tuple[float, float]] = []
    normals: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []

    # Global vertex/uv/normal arrays (OBJ uses global indices)
    all_verts: list[tuple[float, float, float]] = []
    all_uvs: list[tuple[float, float]] = []
    all_normals: list[tuple[float, float, float]] = []

    # Per-submesh: track which global indices belong to each submesh
    submesh_list: list[dict] = []
    current_faces_global: list[tuple] = []
    current_material = ""

    with open(obj_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()

            # Parse metadata comments
            if line.startswith("# source_path:"):
                source_path = line.split(":", 1)[1].strip()
                continue
            if line.startswith("# source_format:"):
                source_format = line.split(":", 1)[1].strip()
                continue
            if line.startswith("#") or not line:
                continue

            parts = line.split()
            if not parts:
                continue

            if parts[0] == "v" and len(parts) >= 4:
                all_verts.append((float(parts[1]), float(parts[2]), float(parts[3])))

            elif parts[0] == "vt" and len(parts) >= 3:
                u = float(parts[1])
                v = 1.0 - float(parts[2])  # flip V back (OBJ export flipped it)
                all_uvs.append((u, v))

            elif parts[0] == "vn" and len(parts) >= 4:
                all_normals.append((float(parts[1]), float(parts[2]), float(parts[3])))

            elif parts[0] == "o":
                # New object/submesh — save previous
                if current_name and current_faces_global:
                    submesh_list.append({
                        "name": current_name,
                        "material": current_material,
                        "faces_global": current_faces_global,
                    })
                current_name = parts[1] if len(parts) > 1 else f"submesh_{len(submesh_list)}"
                current_faces_global = []
                current_material = ""

            elif parts[0] == "usemtl":
                current_material = parts[1] if len(parts) > 1 else ""

            elif parts[0] == "f" and len(parts) >= 4:
                # Parse face indices (supports v, v/vt, v/vt/vn, v//vn) and
                # triangulate polygons by fan because Blender commonly exports quads.
                face_verts = []
                for fp in parts[1:]:
                    indices = fp.split("/")
                    vi = _resolve_obj_index(indices[0], len(all_verts))
                    ti = _resolve_obj_index(indices[1], len(all_uvs)) if len(indices) > 1 and indices[1] else -1
                    ni = _resolve_obj_index(indices[2], len(all_normals)) if len(indices) > 2 and indices[2] else -1
                    face_verts.append((vi, ti, ni))
                if len(face_verts) < 3:
                    continue
                for tri_idx in range(1, len(face_verts) - 1):
                    current_faces_global.append(
                        (face_verts[0], face_verts[tri_idx], face_verts[tri_idx + 1])
                    )

    # Save last submesh
    if current_name and current_faces_global:
        submesh_list.append({
            "name": current_name,
            "material": current_material,
            "faces_global": current_faces_global,
        })

    # Convert global indices to per-submesh local indices.
    # Key: keep ALL vertices in each submesh's range (not just face-referenced ones).
    # Some meshes have unused vertices that must be preserved for correct rebuild.

    # First, determine vertex ownership: each submesh "owns" a contiguous range
    # based on the order vertices appear in the OBJ (submesh 0 first, etc.)
    vert_offset = 0
    for sm_data in submesh_list:
        # Count vertices that belong to this submesh in the OBJ
        # (vertices appear between 'o' markers, counted during parse above)
        # We stored them in all_verts in order — need to find this submesh's range
        pass

    # Build vertex ranges from the OBJ structure:
    # Vertices between successive 'o' markers belong to that submesh
    # Re-parse to find vertex counts per submesh
    sm_vert_counts = []
    sm_uv_counts = []
    sm_normal_counts = []
    current_v = current_vt = current_vn = 0

    with open(obj_path, "r", encoding="utf-8") as f:
        in_submesh = False
        for line in f:
            line = line.strip()
            if line.startswith("o "):
                if in_submesh:
                    sm_vert_counts.append(current_v)
                    sm_uv_counts.append(current_vt)
                    sm_normal_counts.append(current_vn)
                current_v = current_vt = current_vn = 0
                in_submesh = True
            elif line.startswith("v ") and not line.startswith("vt") and not line.startswith("vn"):
                current_v += 1
            elif line.startswith("vt "):
                current_vt += 1
            elif line.startswith("vn "):
                current_vn += 1
        if in_submesh:
            sm_vert_counts.append(current_v)
            sm_uv_counts.append(current_vt)
            sm_normal_counts.append(current_vn)

    # Now build each submesh using the FULL vertex range (not just face-referenced).
    # Blender may remap/deduplicate vt/vn indices independently from position indices,
    # so we must honor the face-level vi/ti/ni tuples instead of assuming vi==ti==ni.
    v_offset = 0
    vt_offset = 0
    vn_offset = 0

    # Build a lookup keyed on submesh name so we can find the sidecar
    # record for each imported submesh. Names must match; Blender
    # preserves `o` lines verbatim on export, so this is reliable.
    sidecar_by_name: dict[str, dict] = {}
    if sidecar is not None:
        for sm_json in sidecar.get("submeshes", []) or []:
            name = sm_json.get("name", "") or ""
            if name:
                sidecar_by_name[name] = sm_json

    for si, sm_data in enumerate(submesh_list):
        nv = sm_vert_counts[si] if si < len(sm_vert_counts) else 0
        nvt = sm_uv_counts[si] if si < len(sm_uv_counts) else 0
        nvn = sm_normal_counts[si] if si < len(sm_normal_counts) else 0

        # Preserve the original exported vertex slots, including any unused vertices,
        # then split only when the same position is referenced with multiple UV/normal
        # pairs after Blender re-export.
        base_verts = [
            all_verts[v_offset + i] if (v_offset + i) < len(all_verts) else (0.0, 0.0, 0.0)
            for i in range(nv)
        ]
        base_uvs = [
            all_uvs[vt_offset + i] if i < nvt and (vt_offset + i) < len(all_uvs) else (0.0, 0.0)
            for i in range(nv)
        ]
        base_normals = [
            all_normals[vn_offset + i] if i < nvn and (vn_offset + i) < len(all_normals) else (0.0, 1.0, 0.0)
            for i in range(nv)
        ]

        local_verts = list(base_verts)
        local_uvs = list(base_uvs)
        local_normals = list(base_normals)

        # Sidecar-driven skin data. When present, bone_indices/weights
        # start out indexed by the ORIGINAL vertex slot (identity with
        # base_verts). We maintain the same indexing for local_verts
        # and clone alongside whenever _resolve_corner_index clones.
        sidecar_record = sidecar_by_name.get(sm_data["name"]) if sidecar_by_name else None
        sidecar_bone_indices: list[tuple[int, ...]] = []
        sidecar_bone_weights: list[tuple[float, ...]] = []
        if sidecar_record is not None:
            # Tolerate a vertex-count mismatch (e.g. user added geometry
            # in Blender) by clamping / padding. The extras end up with
            # empty skin data — caller's build path handles that via
            # the positional fallback for truly new vertices.
            raw_bi = sidecar_record.get("bone_indices", []) or []
            raw_bw = sidecar_record.get("bone_weights", []) or []
            for i in range(nv):
                if i < len(raw_bi):
                    try:
                        sidecar_bone_indices.append(tuple(int(x) for x in raw_bi[i]))
                    except (TypeError, ValueError):
                        sidecar_bone_indices.append(())
                else:
                    sidecar_bone_indices.append(())
                if i < len(raw_bw):
                    try:
                        sidecar_bone_weights.append(tuple(float(x) for x in raw_bw[i]))
                    except (TypeError, ValueError):
                        sidecar_bone_weights.append(())
                else:
                    sidecar_bone_weights.append(())

        # Per local-slot back-pointer to the vertex slot in the ORIGINAL
        # submesh this one came from. Starts as identity; clones inherit
        # from the slot they were cloned from.
        source_vertex_map: list[int] = list(range(nv))

        assigned_uvs: list[tuple[float, float] | None] = [None] * nv
        assigned_normals: list[tuple[float, float, float] | None] = [None] * nv
        split_vertex_map: dict[tuple[int, int, int], int] = {}

        def _resolve_corner_index(vi: int, ti: int, ni: int) -> int:
            local_vi = vi - v_offset
            if not (0 <= local_vi < nv):
                return 0

            local_ti = ti - vt_offset if ti >= 0 else -1
            local_ni = ni - vn_offset if ni >= 0 else -1
            key = (local_vi, local_ti, local_ni)
            existing_idx = split_vertex_map.get(key)
            if existing_idx is not None:
                return existing_idx

            uv_value = (
                all_uvs[ti]
                if 0 <= ti < len(all_uvs)
                else (base_uvs[local_vi] if local_vi < len(base_uvs) else (0.0, 0.0))
            )
            normal_value = (
                all_normals[ni]
                if 0 <= ni < len(all_normals)
                else (base_normals[local_vi] if local_vi < len(base_normals) else (0.0, 1.0, 0.0))
            )

            current_uv = assigned_uvs[local_vi]
            current_normal = assigned_normals[local_vi]
            if current_uv is None and current_normal is None:
                assigned_uvs[local_vi] = uv_value
                assigned_normals[local_vi] = normal_value
                local_uvs[local_vi] = uv_value
                local_normals[local_vi] = normal_value
                split_vertex_map[key] = local_vi
                return local_vi

            if current_uv == uv_value and current_normal == normal_value:
                split_vertex_map[key] = local_vi
                return local_vi

            # Clone the vertex slot. Critically: propagate the bone
            # data + source index so the PAC rebuilder can route the
            # clone to the correct donor. Before v1.22.3 only the
            # position/uv/normal were cloned, which silently dropped
            # skin weights on UV-seam vertices and made the mesh
            # "explode" in-game after repack.
            clone_idx = len(local_verts)
            local_verts.append(base_verts[local_vi])
            local_uvs.append(uv_value)
            local_normals.append(normal_value)
            source_vertex_map.append(source_vertex_map[local_vi])
            if sidecar_record is not None:
                sidecar_bone_indices.append(sidecar_bone_indices[local_vi])
                sidecar_bone_weights.append(sidecar_bone_weights[local_vi])
            split_vertex_map[key] = clone_idx
            return clone_idx

        local_faces = []
        for face in sm_data["faces_global"]:
            local_face = []
            for vi, ti, ni in face:
                local_face.append(_resolve_corner_index(vi, ti, ni))
            if len(local_face) == 3:
                local_faces.append(tuple(local_face))

        sm = SubMesh(
            name=sm_data["name"],
            material=sm_data["material"],
            vertices=local_verts,
            uvs=local_uvs if len(local_uvs) == len(local_verts) else [],
            normals=local_normals if len(local_normals) == len(local_verts) else [],
            faces=local_faces,
            bone_indices=sidecar_bone_indices if sidecar_record is not None else [],
            bone_weights=sidecar_bone_weights if sidecar_record is not None else [],
            vertex_count=len(local_verts),
            face_count=len(local_faces),
            source_vertex_map=source_vertex_map,
        )
        submeshes.append(sm)

        v_offset += nv
        vt_offset += nvt
        vn_offset += nvn

    result = ParsedMesh(
        path=source_path,
        format=source_format,
        submeshes=submeshes,
        total_vertices=sum(len(s.vertices) for s in submeshes),
        total_faces=sum(len(s.faces) for s in submeshes),
        has_uvs=any(s.uvs for s in submeshes),
    )
    # Stash the sidecar so the PAC rebuilder can read its
    # ``pab_to_slot`` per-submesh map for strict skin write-back.
    if sidecar is not None:
        result._cfmeta_sidecar = sidecar

    if result.submeshes:
        all_v = [v for s in submeshes for v in s.vertices]
        if all_v:
            xs, ys, zs = zip(*all_v)
            result.bbox_min = (min(xs), min(ys), min(zs))
            result.bbox_max = (max(xs), max(ys), max(zs))

    logger.info("Imported OBJ %s: %d submeshes, %d verts, %d faces, source=%s (%s)",
                obj_path, len(submeshes), result.total_vertices,
                result.total_faces, source_path, source_format)
    return result


# ═══════════════════════════════════════════════════════════════════════
#  FBX BINARY PARSER
# ═══════════════════════════════════════════════════════════════════════

def _fbx_read_prop(data: bytes, pos: int) -> tuple:
    """Parse one FBX property, return (value, new_pos)."""
    t = chr(data[pos])
    pos += 1
    if t == 'Y':
        v, = struct.unpack_from('<h', data, pos)
        return v, pos + 2
    if t in ('C', 'B'):
        return bool(data[pos]), pos + 1
    if t == 'I':
        v, = struct.unpack_from('<i', data, pos)
        return v, pos + 4
    if t == 'F':
        v, = struct.unpack_from('<f', data, pos)
        return v, pos + 4
    if t == 'D':
        v, = struct.unpack_from('<d', data, pos)
        return v, pos + 8
    if t == 'L':
        v, = struct.unpack_from('<q', data, pos)
        return v, pos + 8
    if t in ('f', 'd', 'l', 'i', 'b'):
        count, enc, clen = struct.unpack_from('<III', data, pos)
        raw = data[pos + 12: pos + 12 + clen]
        if enc:
            raw = zlib.decompress(raw)
        fmt = {'f': 'f', 'd': 'd', 'l': 'q', 'i': 'i', 'b': 'B'}[t]
        return list(struct.unpack_from(f'<{count}{fmt}', raw)), pos + 12 + clen
    if t == 'S':
        slen, = struct.unpack_from('<I', data, pos)
        return data[pos + 4: pos + 4 + slen].decode('utf-8', errors='replace'), pos + 4 + slen
    if t == 'R':
        rlen, = struct.unpack_from('<I', data, pos)
        return data[pos + 4: pos + 4 + rlen], pos + 4 + rlen
    raise ValueError(f"Unknown FBX property type {t!r} at {pos}")


def _fbx_parse_nodes(data: bytes, pos: int, end: int, is_v75: bool) -> list[dict]:
    """Recursively parse FBX binary nodes from pos to end."""
    nodes: list[dict] = []
    null_size = 25 if is_v75 else 13
    off_fmt = '<Q' if is_v75 else '<I'
    off_size = 8 if is_v75 else 4
    hdr_size = 25 if is_v75 else 13  # 3×off + 1 name_len byte

    while pos < end:
        if pos + hdr_size > len(data):
            break
        node_end, = struct.unpack_from(off_fmt, data, pos)
        if node_end == 0:
            break  # null sentinel
        if is_v75:
            num_props, = struct.unpack_from('<Q', data, pos + 8)
            prop_len, = struct.unpack_from('<Q', data, pos + 16)
            name_len = data[pos + 24]
        else:
            num_props, = struct.unpack_from('<I', data, pos + 4)
            prop_len, = struct.unpack_from('<I', data, pos + 8)
            name_len = data[pos + 12]

        name = data[pos + hdr_size: pos + hdr_size + name_len].decode('ascii', errors='replace')
        prop_start = pos + hdr_size + name_len

        props = []
        p = prop_start
        try:
            for _ in range(num_props):
                val, p = _fbx_read_prop(data, p)
                props.append(val)
        except Exception:
            pass

        children_start = prop_start + prop_len
        children = _fbx_parse_nodes(data, children_start, node_end, is_v75)

        nodes.append({'name': name, 'props': props, 'children': children})
        pos = node_end

    return nodes


def _fbx_find(nodes: list[dict], name: str) -> dict | None:
    for n in nodes:
        if n['name'] == name:
            return n
    return None


def _fbx_find_all(nodes: list[dict], name: str) -> list[dict]:
    return [n for n in nodes if n['name'] == name]


def _fbx_child_val(node: dict, child_name: str, default=None):
    c = _fbx_find(node['children'], child_name)
    return c['props'][0] if (c and c['props']) else default


# ═══════════════════════════════════════════════════════════════════════
#  FBX IMPORTER
# ═══════════════════════════════════════════════════════════════════════

def _apply_model_transform(
    model_node: dict | None,
    verts: list[tuple[float, float, float]],
) -> list[tuple[float, float, float]]:
    """Apply a Model node's Lcl Translation/Rotation/Scaling to vertex positions.

    FBX vertices are stored in the mesh object's local space. When a user
    moves or rotates a mesh object in Blender (rather than editing vertices in
    Edit Mode), Blender stores the delta in Lcl Translation/Rotation and keeps
    the Geometry vertices at their original local coordinates.

    Blender bakes the original FBX UnitScaleFactor (e.g. 100 for cm) into the
    object's Lcl Scaling on re-export. The vertices in the Geometry node are
    already in game-space units (metres). The correct world-space formula that
    preserves the original unit scale is:

        V_world = R(V_local) + T / S

    where S is the (assumed uniform) Lcl Scaling factor. Dividing T by S
    converts the translation from Blender's scaled unit back to game-space
    metres before adding it.

    For meshes exported from CrimsonForge, Lcl Translation and Rotation are
    zero, so this is always a no-op for the standard round-trip.
    """
    import math as _math

    if model_node is None:
        return verts

    p70 = _fbx_find(model_node['children'], 'Properties70')
    if p70 is None:
        return verts

    tx = ty = tz = 0.0
    rx = ry = rz = 0.0
    sx = sy = sz = 1.0

    for p in _fbx_find_all(p70['children'], 'P'):
        if not p['props']:
            continue
        name = p['props'][0]
        if name == 'Lcl Translation' and len(p['props']) >= 7:
            tx, ty, tz = float(p['props'][4]), float(p['props'][5]), float(p['props'][6])
        elif name == 'Lcl Rotation' and len(p['props']) >= 7:
            rx, ry, rz = float(p['props'][4]), float(p['props'][5]), float(p['props'][6])
        elif name == 'Lcl Scaling' and len(p['props']) >= 7:
            sx, sy, sz = float(p['props'][4]), float(p['props'][5]), float(p['props'][6])

    no_translation = abs(tx) < 1e-8 and abs(ty) < 1e-8 and abs(tz) < 1e-8
    no_rotation    = abs(rx) < 1e-8 and abs(ry) < 1e-8 and abs(rz) < 1e-8
    if no_translation and no_rotation:
        return verts

    # Build rotation matrix from Euler XYZ angles (degrees).
    rx_r = _math.radians(rx)
    ry_r = _math.radians(ry)
    rz_r = _math.radians(rz)

    cx, sx_ = _math.cos(rx_r), _math.sin(rx_r)
    cy, sy_ = _math.cos(ry_r), _math.sin(ry_r)
    cz, sz_ = _math.cos(rz_r), _math.sin(rz_r)

    # R = Rz * Ry * Rx (FBX Euler XYZ order)
    r00 =  cy * cz;  r01 = cz * sx_ * sy_ - cx * sz_;  r02 = cx * cz * sy_ + sx_ * sz_
    r10 =  cy * sz_;  r11 = cx * cz + sx_ * sy_ * sz_;  r12 = -cz * sx_ + cx * sy_ * sz_
    r20 = -sy_;       r21 = cy * sx_;                    r22 = cy * cx

    # Blender bakes UnitScaleFactor into Lcl Scaling. Dividing T by S converts
    # the translation back to game-space units (same as the Geometry vertices).
    # Lcl Scaling is assumed uniform; use sx as the representative factor.
    s = sx if abs(sx) > 1e-8 else 1.0
    ttx, tty, ttz = tx / s, ty / s, tz / s

    out: list[tuple[float, float, float]] = []
    for x, y, z in verts:
        xr = r00 * x + r01 * y + r02 * z + ttx
        yr = r10 * x + r11 * y + r12 * z + tty
        zr = r20 * x + r21 * y + r22 * z + ttz
        out.append((xr, yr, zr))
    return out


def import_fbx(fbx_path: str) -> ParsedMesh:
    """Import a Blender-exported binary FBX into a ParsedMesh.

    Supports FBX binary 7.4 (32-bit offsets) and 7.5 (64-bit offsets).
    Loads the .cfmeta.json sidecar for bone data and source_vertex_map,
    identical to import_obj. UV-seam splitting uses the same vertex
    cloning logic so build_pac receives a correctly mapped mesh.

    Handles:
    - LayerElementNormal: ByPolygonVertex/Direct and ByVertice/Direct
    - LayerElementUV: ByPolygonVertex/IndexToDirect and ByPolygonVertex/Direct
    - Polygon triangulation by fan (handles quads and ngons from Blender)
    """
    data = Path(fbx_path).read_bytes()

    if data[:21] != b"Kaydara FBX Binary  \x00":
        raise ValueError(f"Not a binary FBX file: {fbx_path}")

    version = struct.unpack_from('<I', data, 23)[0]
    is_v75 = version >= 7500

    sidecar = _load_cfmeta_sidecar(fbx_path)

    nodes = _fbx_parse_nodes(data, 27, len(data), is_v75)

    objects = _fbx_find(nodes, 'Objects')
    conns_node = _fbx_find(nodes, 'Connections')
    if objects is None:
        raise ValueError(f"FBX has no Objects section: {fbx_path}")

    # geometry_id → node
    geo_nodes: dict[int, dict] = {}
    for n in _fbx_find_all(objects['children'], 'Geometry'):
        if n['props'] and isinstance(n['props'][0], int):
            geo_nodes[n['props'][0]] = n

    # model_id → display name (strip FBX \x00\x01Type suffix)
    model_names: dict[int, str] = {}
    model_nodes: dict[int, dict] = {}
    model_order: list[int] = []
    for n in _fbx_find_all(objects['children'], 'Model'):
        if n['props'] and isinstance(n['props'][0], int):
            mid = n['props'][0]
            raw = n['props'][1] if len(n['props']) > 1 and isinstance(n['props'][1], str) else ''
            model_names[mid] = raw.split('\x00')[0]
            model_nodes[mid] = n
            model_order.append(mid)

    # Skin / Cluster deformer nodes
    skin_nodes: dict[int, dict] = {}
    cluster_nodes: dict[int, dict] = {}
    for n in _fbx_find_all(objects['children'], 'Deformer'):
        if not (n['props'] and isinstance(n['props'][0], int)):
            continue
        did = n['props'][0]
        dtype = n['props'][2] if len(n['props']) > 2 and isinstance(n['props'][2], str) else ''
        if dtype == 'Skin':
            skin_nodes[did] = n
        elif dtype == 'Cluster':
            cluster_nodes[did] = n

    # Parse all OO connections in one pass
    geo_to_model: dict[int, int] = {}
    geo_to_skin: dict[int, int] = {}       # geo_id  → skin_id
    cluster_to_skin: dict[int, int] = {}   # cluster_id → skin_id
    cluster_to_bone: dict[int, int] = {}   # cluster_id → bone_model_id

    if conns_node:
        for c in _fbx_find_all(conns_node['children'], 'C'):
            if len(c['props']) < 3 or c['props'][0] != 'OO':
                continue
            src, dst = c['props'][1], c['props'][2]
            if not (isinstance(src, int) and isinstance(dst, int)):
                continue
            if src in geo_nodes and dst in model_names:
                geo_to_model[src] = dst
            elif src in skin_nodes and dst in geo_nodes:
                geo_to_skin[dst] = src
            elif src in cluster_nodes and dst in skin_nodes:
                cluster_to_skin[src] = dst
            elif src in model_names and dst in cluster_nodes:
                cluster_to_bone[dst] = src

    model_to_geo = {mid: gid for gid, mid in geo_to_model.items()}

    # skin_id → [cluster_ids]
    skin_to_clusters: dict[int, list[int]] = {}
    for cid, sid in cluster_to_skin.items():
        skin_to_clusters.setdefault(sid, []).append(cid)

    # Resolve cluster bone NAMES to PAB indices via the sidecar's
    # skeleton_bones table when present. The sidecar stores the
    # PAB-index-ordered bone-name list at export time, so a
    # cluster whose target Model is named "Bip01 Pelvis" maps
    # deterministically to PAB index N where
    # ``sidecar.skeleton_bones[N] == "Bip01 Pelvis"``.
    #
    # When the sidecar is absent or carries no skeleton_bones list
    # (older exports), we fall through to the legacy synthetic-index
    # assignment, but the rebuild path (_build_pac_*) will then
    # refuse to write skin into the PAC because there is no strict
    # name-to-PAB mapping available — exactly the no-fallback rule.
    sidecar_skeleton_bones: list[str] = []
    if isinstance(sidecar, dict):
        sb = sidecar.get("skeleton_bones") or []
        if isinstance(sb, list):
            sidecar_skeleton_bones = [str(x) for x in sb]
    name_to_pab: dict[str, int] = {
        nm: i for i, nm in enumerate(sidecar_skeleton_bones) if nm
    }

    bone_name_to_idx: dict[str, int] = {}

    def _bone_idx_for_name(bone_name: str) -> int:
        """Resolve an FBX cluster's bone Model name to a stable
        per-mesh integer index. When the sidecar carries
        ``skeleton_bones`` we use the PAB index from that list
        (so SubMesh.bone_indices ends up in PAB-index space —
        the exact form the rebuilder needs to write skin back).
        Otherwise we fall back to a per-import synthetic index
        keyed on first-seen order; the rebuilder treats those
        as untrusted and refuses to write skin from them.
        """
        pab_idx = name_to_pab.get(bone_name)
        if pab_idx is not None:
            return pab_idx
        # No mapping — synthetic per-import index. We still need
        # SOMETHING for SubMesh.bone_indices to keep the import
        # path useful for callers that don't go through
        # _build_pac_* (e.g. third-party diff tooling).
        if bone_name not in bone_name_to_idx:
            bone_name_to_idx[bone_name] = len(bone_name_to_idx)
        return bone_name_to_idx[bone_name]

    def _geo_skin_weights(
        geo_id: int,
    ) -> tuple[dict[int, list[tuple[int, float]]],
                dict[int, list[tuple[str, float]]]]:
        """Return ``(by_idx, by_name)`` for the skin bound to ``geo_id``.

        ``by_idx`` maps ``vertex_index → [(int_idx, weight)]`` where
        ``int_idx`` is the PAB index when the sidecar carries
        ``skeleton_bones``, else a synthetic per-import id.

        ``by_name`` maps ``vertex_index → [(bone_name, weight)]`` for
        every cluster contribution. The rebuilder uses this verbatim
        when it needs to map names directly to slots without trusting
        the synthetic-index path.
        """
        sid = geo_to_skin.get(geo_id)
        if sid is None:
            return {}, {}
        by_idx: dict[int, list[tuple[int, float]]] = {}
        by_name: dict[int, list[tuple[str, float]]] = {}
        for cid in skin_to_clusters.get(sid, []):
            cn = cluster_nodes.get(cid)
            if cn is None:
                continue
            bone_mid = cluster_to_bone.get(cid)
            bone_name = (
                model_names.get(bone_mid, f'_bone_{cid}')
                if bone_mid else f'_bone_{cid}'
            )
            bidx = _bone_idx_for_name(bone_name)
            idx_n = _fbx_find(cn['children'], 'Indexes')
            wt_n = _fbx_find(cn['children'], 'Weights')
            if not (idx_n and wt_n and idx_n['props'] and wt_n['props']):
                continue
            for vi, w in zip(idx_n['props'][0], wt_n['props'][0]):
                if isinstance(vi, int) and w > 0.0:
                    by_idx.setdefault(int(vi), []).append((bidx, float(w)))
                    by_name.setdefault(int(vi), []).append(
                        (bone_name, float(w)),
                    )
        return by_idx, by_name

    # source_path comes from the optional sidecar (FBX itself doesn't carry
    # the game PAC path; build_pac only uses it for logging).
    source_path = sidecar.get('source_path', '') if sidecar else ''
    source_format = sidecar.get('source_format', '') if sidecar else ''

    # Build a lookup of per-submesh sidecar records keyed by name. We'll
    # use this during the import loop both for v1 (bone_indices/weights)
    # and v2 (source_vertex_map + filtered_vertices/faces) consumption.
    sidecar_by_name: dict[str, dict] = {}
    if sidecar is not None:
        for sm_json in sidecar.get("submeshes", []) or []:
            name = sm_json.get("name", "") or ""
            if name:
                sidecar_by_name[name] = sm_json

    # Determine axis convention of the FBX so we can convert vertices /
    # normals back to Pearl Abyss native Y-up if the file is Z-up.
    # CrimsonForge's exporter writes UpAxis=2 (Z-up) after pre-converting
    # all geometry to Z-up via the +90° X rotation. To round-trip
    # correctly we apply the INVERSE rotation here.
    #   Z-up (x, y, z) → Y-up (x, z, -y)    (rotate -90° around X)
    fbx_up_axis = 1   # default: Y-up
    fbx_global = _fbx_find(nodes, 'GlobalSettings')
    if fbx_global is not None:
        for p70 in _fbx_find_all(fbx_global['children'], 'Properties70'):
            for p in _fbx_find_all(p70['children'], 'P'):
                if (p['props'] and len(p['props']) >= 5
                        and p['props'][0] == 'UpAxis'
                        and isinstance(p['props'][4], int)):
                    fbx_up_axis = p['props'][4]
                    break
            if fbx_up_axis != 1:
                break

    needs_zup_to_yup = (fbx_up_axis == 2)
    if needs_zup_to_yup:
        logger.info(
            "FBX %s declares Z-up (UpAxis=2); converting vertices/normals "
            "back to Y-up for PAC native convention", fbx_path,
        )

    def _conv_vec(v):
        # Z-up (x, y, z) → Y-up (x, z, -y).  No-op if FBX is Y-up.
        if needs_zup_to_yup:
            return (v[0], v[2], -v[1])
        return tuple(v)

    submeshes: list[SubMesh] = []

    for mod_id in model_order:
        geo_id = model_to_geo.get(mod_id)
        if geo_id is None:
            continue
        geo = geo_nodes[geo_id]
        sm_name = model_names.get(mod_id, f'submesh_{len(submeshes)}')

        # Vertices
        vn = _fbx_find(geo['children'], 'Vertices')
        if not vn or not vn['props'] or not isinstance(vn['props'][0], list):
            continue
        vf = vn['props'][0]
        base_verts: list[tuple[float, float, float]] = [
            _conv_vec((vf[i], vf[i + 1], vf[i + 2]))
            for i in range(0, len(vf) - 2, 3)
        ]

        # Apply the Model's local TRS so that object-level transforms made in
        # Blender (e.g. moving the mesh object rather than editing vertices) are
        # correctly reflected in the imported vertex positions.
        base_verts = _apply_model_transform(model_nodes.get(mod_id), base_verts)

        # PolygonVertexIndex → list of polygons (each a list of vertex indices)
        pn = _fbx_find(geo['children'], 'PolygonVertexIndex')
        if not pn or not pn['props'] or not isinstance(pn['props'][0], list):
            continue
        pvi = pn['props'][0]
        polygons: list[list[int]] = []
        cur: list[int] = []
        for idx in pvi:
            if idx < 0:
                cur.append(~idx)   # ~idx == -(idx+1), recovers the real vertex index
                polygons.append(cur)
                cur = []
            else:
                cur.append(idx)

        # LayerElementNormal
        normals_flat: list[float] | None = None
        normal_index: list[int] | None = None
        normal_by_poly_vert = True
        normal_indexed = False
        for le in _fbx_find_all(geo['children'], 'LayerElementNormal'):
            mapping = _fbx_child_val(le, 'MappingInformationType', 'ByPolygonVertex')
            ref     = _fbx_child_val(le, 'ReferenceInformationType', 'Direct')
            nn  = _fbx_find(le['children'], 'Normals')
            ni_n = _fbx_find(le['children'], 'NormalsIndex')
            if nn and nn['props'] and isinstance(nn['props'][0], list):
                normals_flat = nn['props'][0]
                normal_by_poly_vert = (mapping != 'ByVertice')
                normal_indexed = (ref == 'IndexToDirect')
                if ni_n and ni_n['props'] and isinstance(ni_n['props'][0], list):
                    normal_index = ni_n['props'][0]
                break

        # LayerElementUV
        uv_flat: list[float] | None = None
        uv_index: list[int] | None = None
        uv_by_poly_vert = True
        uv_indexed = True
        for le in _fbx_find_all(geo['children'], 'LayerElementUV'):
            mapping = _fbx_child_val(le, 'MappingInformationType', 'ByPolygonVertex')
            ref = _fbx_child_val(le, 'ReferenceInformationType', 'IndexToDirect')
            un = _fbx_find(le['children'], 'UV')
            ui_n = _fbx_find(le['children'], 'UVIndex')
            if un and un['props'] and isinstance(un['props'][0], list):
                uv_flat = un['props'][0]
                uv_by_poly_vert = (mapping != 'ByVertice')
                uv_indexed = (ref == 'IndexToDirect')
                if ui_n and ui_n['props'] and isinstance(ui_n['props'][0], list):
                    uv_index = ui_n['props'][0]
                break

        # Per-corner UV and normal lookup (closed over per-geometry arrays)
        def _get_uv(poly_vi: int, vi: int) -> tuple[float, float]:
            if uv_flat is None:
                return (0.0, 0.0)
            if uv_by_poly_vert:
                slot = uv_index[poly_vi] if (uv_indexed and uv_index is not None) else poly_vi
            else:
                slot = vi
            u = uv_flat[slot * 2]
            v = 1.0 - uv_flat[slot * 2 + 1]   # flip V (game convention)
            return (u, v)

        def _get_normal(poly_vi: int, vi: int) -> tuple[float, float, float]:
            if normals_flat is None:
                return (0.0, 1.0, 0.0)
            if normal_by_poly_vert:
                slot = normal_index[poly_vi] if (normal_indexed and normal_index is not None) else poly_vi
            else:
                slot = vi
            base = slot * 3
            n = (normals_flat[base], normals_flat[base + 1], normals_flat[base + 2])
            return _conv_vec(n)

        # Bone weights from FBX Cluster nodes.
        skin_weights, skin_names = _geo_skin_weights(geo_id)
        has_skin = bool(skin_weights)
        n_orig = len(base_verts)
        sb_indices: list[tuple[int, ...]] = []
        sb_weights: list[tuple[float, ...]] = []
        sb_names: list[tuple[str, ...]] = []
        for i in range(n_orig):
            # Sort by weight descending so the dominant bone leads.
            # We sort by IDX-keyed pairs (definitive for sb_indices /
            # sb_weights) and apply the same permutation to names so
            # all three lists stay aligned.
            idx_pairs = sorted(
                skin_weights.get(i, []), key=lambda x: -x[1],
            )
            name_pairs = sorted(
                skin_names.get(i, []), key=lambda x: -x[1],
            )
            sb_indices.append(tuple(b for b, _ in idx_pairs))
            sb_weights.append(tuple(w for _, w in idx_pairs))
            sb_names.append(tuple(n for n, _ in name_pairs))

        # Expand vertices: UV-seam splitting (same logic as import_obj)
        local_verts: list[tuple[float, float, float]] = list(base_verts)
        local_uvs: list[tuple[float, float]] = [(0.0, 0.0)] * len(base_verts)
        local_norms: list[tuple[float, float, float]] = [(0.0, 1.0, 0.0)] * len(base_verts)
        assigned: list[bool] = [False] * len(base_verts)
        src_map: list[int] = list(range(len(base_verts)))
        bone_indices_out: list[tuple[int, ...]] = list(sb_indices)
        bone_weights_out: list[tuple[float, ...]] = list(sb_weights)
        bone_names_out: list[tuple[str, ...]] = list(sb_names)
        corner_cache: dict[tuple, int] = {}

        def _resolve(vi: int, uv: tuple, norm: tuple) -> int:
            key = (vi, uv, norm)
            hit = corner_cache.get(key)
            if hit is not None:
                return hit
            if 0 <= vi < len(assigned) and not assigned[vi]:
                local_uvs[vi] = uv
                local_norms[vi] = norm
                assigned[vi] = True
                corner_cache[key] = vi
                return vi
            if 0 <= vi < len(local_uvs) and local_uvs[vi] == uv and local_norms[vi] == norm:
                corner_cache[key] = vi
                return vi
            clone = len(local_verts)
            local_verts.append(base_verts[vi] if vi < len(base_verts) else (0.0, 0.0, 0.0))
            local_uvs.append(uv)
            local_norms.append(norm)
            src_map.append(src_map[vi] if vi < len(src_map) else vi)
            bone_indices_out.append(bone_indices_out[vi] if vi < len(bone_indices_out) else ())
            bone_weights_out.append(bone_weights_out[vi] if vi < len(bone_weights_out) else ())
            bone_names_out.append(
                bone_names_out[vi] if vi < len(bone_names_out) else ()
            )
            corner_cache[key] = clone
            return clone

        local_faces: list[tuple[int, int, int]] = []
        poly_vi = 0
        for poly in polygons:
            corners = []
            for vi in poly:
                uv = _get_uv(poly_vi, vi)
                norm = _get_normal(poly_vi, vi)
                corners.append(_resolve(vi, uv, norm))
                poly_vi += 1
            for i in range(1, len(corners) - 1):
                local_faces.append((corners[0], corners[i], corners[i + 1]))

        # ── v2 SIDECAR EXPANSION ──
        # If the sidecar is schema v2, it carries:
        #   - source_vertex_map[i] = the ORIGINAL PAC vertex slot for FBX
        #     vertex i (the slot before spike-filtering at export).
        #     UV-seam clones inherit this via src_map[parent].
        #   - filtered_vertices = donor records for spike verts that
        #     were removed from the FBX. Each has source_index +
        #     position [+ uv + normal + bone_indices/weights].
        #   - filtered_faces = original-index triangles that were
        #     dropped because they touched a spike vertex.
        #
        # We expand the visible mesh by:
        #   1. Remapping src_map values from FBX-index → original-PAC-slot
        #      (this makes build_pac's donor lookup hit the right slot)
        #   2. Appending each filtered_vertex as a new vertex with no
        #      face references (or with restored face references)
        #   3. Adding filtered_faces back, remapped to the new index
        #      space (visible verts + appended filtered verts)
        # The result: an imported mesh whose vertex count matches the
        # ORIGINAL PAC, even though the FBX user never saw the spike
        # geometry.
        sm_sidecar = sidecar_by_name.get(sm_name) if sidecar_by_name else None
        if sm_sidecar and isinstance(sm_sidecar, dict):
            v2_map = sm_sidecar.get("source_vertex_map")
            v2_filtered_verts = sm_sidecar.get("filtered_vertices") or []
            v2_filtered_faces = sm_sidecar.get("filtered_faces") or []

            if v2_map and isinstance(v2_map, list):
                # Remap FBX-index → original-PAC-slot for the verts that
                # CAME FROM the FBX. UV-seam clones already share src_map
                # with their parent (via _resolve), so re-applying the
                # mapping by current src_map value is correct.
                fbx_to_orig = list(v2_map)
                # src_map currently holds FBX-vertex indices. Replace
                # each with the corresponding original-PAC slot.
                for vi in range(len(src_map)):
                    fbx_idx = src_map[vi]
                    if 0 <= fbx_idx < len(fbx_to_orig):
                        src_map[vi] = int(fbx_to_orig[fbx_idx])

            if v2_filtered_verts:
                # orig_to_new map: original PAC slot → index in expanded mesh.
                # First populate with visible verts (their src_map values
                # are now PAC slots after the remap above).
                orig_to_new: dict[int, int] = {}
                for new_idx, pac_slot in enumerate(src_map):
                    # First-write wins (UV-seam clones share PAC slot
                    # with parent — keep parent's new_idx for face
                    # remapping continuity).
                    orig_to_new.setdefault(int(pac_slot), new_idx)

                # Append filtered verts at end of mesh.
                appended = 0
                for entry in v2_filtered_verts:
                    if not isinstance(entry, dict):
                        continue
                    src_idx = entry.get("source_index")
                    pos = entry.get("position")
                    if src_idx is None or not isinstance(pos, (list, tuple)) or len(pos) != 3:
                        continue
                    new_idx = len(local_verts)
                    local_verts.append(tuple(float(v) for v in pos))
                    uv = entry.get("uv") or [0.0, 0.0]
                    local_uvs.append(tuple(float(v) for v in uv))
                    norm = entry.get("normal") or [0.0, 1.0, 0.0]
                    local_norms.append(tuple(float(v) for v in norm))
                    bi = entry.get("bone_indices") or []
                    bw = entry.get("bone_weights") or []
                    bone_indices_out.append(tuple(int(b) for b in bi))
                    bone_weights_out.append(tuple(float(w) for w in bw))
                    # Filtered verts come from the sidecar where we
                    # don't store names — leave names empty. The
                    # rebuild path treats empty names as "no name
                    # info, fall back to int indices for this vert."
                    bone_names_out.append(())
                    src_map.append(int(src_idx))
                    orig_to_new[int(src_idx)] = new_idx
                    appended += 1

                # Re-add filtered faces, remapping ORIGINAL indices to
                # the new expanded-mesh indices.
                restored_faces = 0
                for face in v2_filtered_faces:
                    if not isinstance(face, (list, tuple)) or len(face) != 3:
                        continue
                    try:
                        a = orig_to_new[int(face[0])]
                        b = orig_to_new[int(face[1])]
                        c = orig_to_new[int(face[2])]
                    except (KeyError, ValueError, TypeError):
                        continue
                    local_faces.append((a, b, c))
                    restored_faces += 1

                logger.info(
                    "FBX %s submesh %s: expanded with %d filtered verts "
                    "+ %d filtered faces from sidecar v2",
                    fbx_path, sm_name, appended, restored_faces,
                )

        sm = SubMesh(
            name=sm_name,
            material=sm_name,
            vertices=local_verts,
            uvs=local_uvs if len(local_uvs) == len(local_verts) else [],
            normals=local_norms if len(local_norms) == len(local_verts) else [],
            faces=local_faces,
            bone_indices=bone_indices_out if has_skin else [],
            bone_weights=bone_weights_out if has_skin else [],
            bone_names=bone_names_out if has_skin else [],
            vertex_count=len(local_verts),
            face_count=len(local_faces),
            source_vertex_map=src_map,
        )
        submeshes.append(sm)

    result = ParsedMesh(
        path=source_path,
        format=source_format,
        submeshes=submeshes,
        total_vertices=sum(len(s.vertices) for s in submeshes),
        total_faces=sum(len(s.faces) for s in submeshes),
        has_uvs=any(s.uvs for s in submeshes),
        has_bones=any(s.bone_indices for s in submeshes),
    )
    # Stash the full sidecar dict on the imported mesh. The PAC
    # rebuilder reads ``pab_to_slot`` from each submesh entry to
    # write the user's edited skin weights back into the PAC vertex
    # byte slots — without this the rebuilder has no strict mapping
    # and refuses to overwrite donor skin bytes.
    if sidecar is not None:
        result._cfmeta_sidecar = sidecar

    if submeshes:
        all_v = [v for s in submeshes for v in s.vertices]
        if all_v:
            xs, ys, zs = zip(*all_v)
            result.bbox_min = (min(xs), min(ys), min(zs))
            result.bbox_max = (max(xs), max(ys), max(zs))

    logger.info("Imported FBX %s: %d submeshes, %d verts, %d faces, source=%s (%s)",
                fbx_path, len(submeshes), result.total_vertices,
                result.total_faces, source_path, source_format)

    # ── Skin-weight normalisation diagnostic ──
    # Pearl Abyss's PAC vertex shader expects per-vertex bone weights
    # to sum to 1.0 (255 in u8). When the user paints in Blender with
    # 'Auto Normalize Weights' OFF, multiple vertex groups can hold
    # arbitrary values (e.g. three groups all at 1.0 → sum 3.0).
    # Forge then renormalises during build_pac so the engine math
    # works, but the round-trip back to FBX shows weight 0.333 per
    # bone instead of the painted 1.0 — the source of just4u's
    # "weight 1.000 -> 0.337" report.
    #
    # Surface this loudly the moment we see un-normalised input so
    # the user knows what's about to happen and can toggle Auto
    # Normalize before re-painting.
    n_unnorm = 0
    n_total = 0
    for sm in submeshes:
        for weights in sm.bone_weights:
            wsum = sum(float(w) for w in weights if w > 0.0)
            if wsum > 0.0:
                n_total += 1
                if abs(wsum - 1.0) > 0.01:
                    n_unnorm += 1
    if n_total and n_unnorm > 0:
        pct = 100.0 * n_unnorm / n_total
        logger.warning(
            "FBX %s: %d / %d skinned vertices (%.1f%%) carry "
            "un-normalised weights (Σ != 1.0). Forge will normalise "
            "to engine convention on rebuild — this is what makes "
            "painted weights round-trip as fractions (e.g. three "
            "groups at 1.0 each → 0.333 per group). To preserve the "
            "painted value, enable Blender's 'Weight Paint > Tool > "
            "Options > Auto Normalize' BEFORE painting.",
            fbx_path, n_unnorm, n_total, pct,
        )
    return result


# ═══════════════════════════════════════════════════════════════════════
#  QUANTIZATION UTILITIES
# ═══════════════════════════════════════════════════════════════════════

def _quantize_u16(value: float, vmin: float, vmax: float) -> int:
    """Float → uint16 quantized: inverse of dequantize."""
    if abs(vmax - vmin) < 1e-10:
        return 32768
    t = (value - vmin) / (vmax - vmin)
    t = max(0.0, min(1.0, t))
    return min(65535, max(0, round(t * 65535)))


def _compute_bbox(vertices: list[tuple[float, float, float]]):
    """Compute tight bounding box from vertex list."""
    if not vertices:
        return (0, 0, 0), (1, 1, 1)
    xs, ys, zs = zip(*vertices)
    # Add tiny epsilon to avoid zero-size bbox
    eps = 1e-6
    bmin = (min(xs) - eps, min(ys) - eps, min(zs) - eps)
    bmax = (max(xs) + eps, max(ys) + eps, max(zs) + eps)
    return bmin, bmax


def _reorder_submeshes_to_match_original(original_mesh: ParsedMesh, imported_mesh: ParsedMesh) -> None:
    """Restore original submesh and vertex slot order for PAM/PAMLOD rebuilds."""
    if len(original_mesh.submeshes) != len(imported_mesh.submeshes):
        raise ValueError(
            "PAM/PAMLOD import requires the same submesh count as the original mesh."
        )

    orig_names = [sm.name for sm in original_mesh.submeshes]
    imp_names = [sm.name for sm in imported_mesh.submeshes]
    if orig_names != imp_names:
        name_to_submesh = {}
        for sm in imported_mesh.submeshes:
            if not sm.name or sm.name in name_to_submesh:
                break
            name_to_submesh[sm.name] = sm
        if len(name_to_submesh) == len(imported_mesh.submeshes) and set(name_to_submesh) == set(orig_names):
            imported_mesh.submeshes = [name_to_submesh[name] for name in orig_names]

    for sm_idx, (orig_sm, imp_sm) in enumerate(zip(original_mesh.submeshes, imported_mesh.submeshes)):
        if len(orig_sm.vertices) != len(imp_sm.vertices):
            raise ValueError(
                f"Submesh {sm_idx} changed vertex count "
                f"({len(orig_sm.vertices)} -> {len(imp_sm.vertices)}). "
                "PAM/PAMLOD import currently requires keeping the same topology."
            )
        if len(orig_sm.faces) != len(imp_sm.faces):
            raise ValueError(
                f"Submesh {sm_idx} changed face count "
                f"({len(orig_sm.faces)} -> {len(imp_sm.faces)}). "
                "PAM/PAMLOD import currently requires keeping the same topology."
            )

        if imp_sm.faces == orig_sm.faces:
            continue

        mapping: dict[int, int] = {}
        reverse: dict[int, int] = {}
        mapping_ok = True

        for orig_face, imp_face in zip(orig_sm.faces, imp_sm.faces):
            if len(orig_face) != len(imp_face):
                mapping_ok = False
                break
            for orig_idx, imp_idx in zip(orig_face, imp_face):
                prev_orig = mapping.get(imp_idx)
                prev_imp = reverse.get(orig_idx)
                if (prev_orig is not None and prev_orig != orig_idx) or (
                    prev_imp is not None and prev_imp != imp_idx
                ):
                    mapping_ok = False
                    break
                mapping[imp_idx] = orig_idx
                reverse[orig_idx] = imp_idx
            if not mapping_ok:
                break

        if (not mapping_ok or
                len(mapping) != len(orig_sm.vertices) or
                len(reverse) != len(orig_sm.vertices)):
            raise ValueError(
                f"Submesh {sm_idx} no longer matches the original triangle order. "
                "PAM/PAMLOD import can handle vertex renumbering, but it still "
                "requires preserving the original triangle list."
            )

        reordered_vertices = [None] * len(orig_sm.vertices)
        reordered_uvs = [None] * len(orig_sm.vertices) if len(imp_sm.uvs) == len(imp_sm.vertices) else None
        reordered_normals = [None] * len(orig_sm.vertices) if len(imp_sm.normals) == len(imp_sm.vertices) else None

        for imp_idx, orig_idx in mapping.items():
            reordered_vertices[orig_idx] = imp_sm.vertices[imp_idx]
            if reordered_uvs is not None:
                reordered_uvs[orig_idx] = imp_sm.uvs[imp_idx]
            if reordered_normals is not None:
                reordered_normals[orig_idx] = imp_sm.normals[imp_idx]

        imp_sm.vertices = reordered_vertices
        imp_sm.uvs = reordered_uvs if reordered_uvs is not None else imp_sm.uvs
        imp_sm.normals = reordered_normals if reordered_normals is not None else imp_sm.normals
        imp_sm.faces = list(orig_sm.faces)
        imp_sm.vertex_count = len(imp_sm.vertices)
        imp_sm.face_count = len(imp_sm.faces)


def _resolve_pam_alias_vertex(
    byte_off: int,
    refs: list[tuple[tuple[float, float, float], tuple[float, float, float], int, int]],
    eps: float = 1e-6,
    allow_average_conflicts: bool = False,
) -> tuple[float, float, float]:
    """Choose one final position for a shared vertex byte offset."""
    changed: list[tuple[tuple[float, float, float], int, int]] = []
    for orig_v, new_v, sm_idx, vert_idx in refs:
        if math.dist(orig_v, new_v) > eps:
            changed.append((new_v, sm_idx, vert_idx))

    if not changed:
        return refs[0][1]

    chosen = changed[0][0]
    for new_v, sm_idx, vert_idx in changed[1:]:
        if math.dist(new_v, chosen) > eps:
            if allow_average_conflicts:
                xs = [pos[0][0] for pos in changed]
                ys = [pos[0][1] for pos in changed]
                zs = [pos[0][2] for pos in changed]
                return (
                    sum(xs) / len(xs),
                    sum(ys) / len(ys),
                    sum(zs) / len(zs),
                )
            raise ValueError(
                "Mesh import detected linked vertices that share the same source bytes, "
                f"but they were edited differently (offset 0x{byte_off:X}, "
                f"submesh {sm_idx} vertex {vert_idx}). "
                "Edit all linked copies to the same position, or keep the topology "
                "and overlapping pieces unchanged."
            )
    return chosen


def _make_temp_mesh(path: str, fmt: str, submeshes: list[SubMesh]) -> ParsedMesh:
    """Build a lightweight ParsedMesh wrapper for helper operations."""
    return ParsedMesh(
        path=path,
        format=fmt,
        submeshes=submeshes,
        total_vertices=sum(len(sm.vertices) for sm in submeshes),
        total_faces=sum(len(sm.faces) for sm in submeshes),
        has_uvs=any(sm.uvs for sm in submeshes),
    )


def _expand_bbox_to_vertices(
    orig_bmin: tuple[float, float, float],
    orig_bmax: tuple[float, float, float],
    vertices: list[tuple[float, float, float]],
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Expand an existing bbox to include all provided vertices."""
    if not vertices:
        return orig_bmin, orig_bmax
    xs, ys, zs = zip(*vertices)
    bmin = (
        min(orig_bmin[0], min(xs)),
        min(orig_bmin[1], min(ys)),
        min(orig_bmin[2], min(zs)),
    )
    bmax = (
        max(orig_bmax[0], max(xs)),
        max(orig_bmax[1], max(ys)),
        max(orig_bmax[2], max(zs)),
    )
    return bmin, bmax


def _collect_vertex_offset_refs(
    original_data: bytes,
    original_mesh: ParsedMesh,
    new_mesh: ParsedMesh,
    orig_bmin: tuple[float, float, float],
    orig_bmax: tuple[float, float, float],
    search_start: int = 0,
) -> dict[int, list[tuple[tuple[float, float, float], tuple[float, float, float], int, int]]]:
    """Map source byte offsets to original/new vertex pairs."""
    _reorder_submeshes_to_match_original(original_mesh, new_mesh)

    offset_refs: dict[int, list[tuple[tuple[float, float, float], tuple[float, float, float], int, int]]] = {}
    search_cursor = search_start

    for sm_idx, (orig_sm, new_sm) in enumerate(zip(original_mesh.submeshes, new_mesh.submeshes)):
        n = min(len(orig_sm.vertices), len(new_sm.vertices))
        sm_offsets = list(orig_sm.source_vertex_offsets) if (
            len(orig_sm.source_vertex_offsets) == len(orig_sm.vertices)
        ) else []

        if not sm_offsets:
            for vi in range(len(orig_sm.vertices)):
                vx, vy, vz = orig_sm.vertices[vi]
                xu = _quantize_u16(vx, orig_bmin[0], orig_bmax[0])
                yu = _quantize_u16(vy, orig_bmin[1], orig_bmax[1])
                zu = _quantize_u16(vz, orig_bmin[2], orig_bmax[2])
                target = struct.pack("<HHH", xu, yu, zu)

                found = -1
                for scan in range(search_cursor, len(original_data) - 6):
                    if original_data[scan:scan + 6] == target:
                        found = scan
                        search_cursor = scan + 6
                        break

                sm_offsets.append(found)

        for vi in range(n):
            if vi >= len(sm_offsets) or sm_offsets[vi] < 0:
                continue
            byte_off = sm_offsets[vi]
            offset_refs.setdefault(byte_off, []).append(
                (orig_sm.vertices[vi], new_sm.vertices[vi], sm_idx, vi)
            )

    return offset_refs


def _apply_quantized_vertex_patches(
    result: bytearray,
    offset_refs: dict[int, list[tuple[tuple[float, float, float], tuple[float, float, float], int, int]]],
    bmin: tuple[float, float, float],
    bmax: tuple[float, float, float],
    allow_average_conflicts: bool = False,
) -> int:
    """Patch quantized XYZ values at the collected byte offsets."""
    patched_offsets = 0
    for byte_off, refs in offset_refs.items():
        if byte_off + 6 > len(result):
            continue

        vx, vy, vz = _resolve_pam_alias_vertex(
            byte_off, refs, allow_average_conflicts=allow_average_conflicts
        )
        xu = _quantize_u16(vx, bmin[0], bmax[0])
        yu = _quantize_u16(vy, bmin[1], bmax[1])
        zu = _quantize_u16(vz, bmin[2], bmax[2])
        struct.pack_into("<HHH", result, byte_off, xu, yu, zu)
        patched_offsets += 1

    return patched_offsets


def _align_submesh_order_like_original(original_mesh: ParsedMesh, new_mesh: ParsedMesh) -> None:
    """Align submesh order by name when possible without enforcing topology."""
    if len(original_mesh.submeshes) != len(new_mesh.submeshes):
        return

    orig_names = [sm.name for sm in original_mesh.submeshes]
    if [sm.name for sm in new_mesh.submeshes] == orig_names:
        return

    name_to_submesh: dict[str, SubMesh] = {}
    for sm in new_mesh.submeshes:
        if not sm.name or sm.name in name_to_submesh:
            return
        name_to_submesh[sm.name] = sm

    if set(name_to_submesh) == set(orig_names):
        new_mesh.submeshes = [name_to_submesh[name] for name in orig_names]


def _submesh_uvs_match(orig_sm: SubMesh, new_sm: SubMesh, eps: float = 1e-6) -> bool:
    """Check whether two submeshes have equivalent UV payloads."""
    orig_has_uv = len(orig_sm.uvs) == len(orig_sm.vertices)
    new_has_uv = len(new_sm.uvs) == len(new_sm.vertices)
    if orig_has_uv != new_has_uv:
        return False
    if not orig_has_uv:
        return True
    return all(
        abs(ou - nu) <= eps and abs(ov - nv) <= eps
        for (ou, ov), (nu, nv) in zip(orig_sm.uvs, new_sm.uvs)
    )


def _pam_needs_full_rebuild(original_mesh: ParsedMesh, new_mesh: ParsedMesh) -> bool:
    """Return True when edits go beyond in-place XYZ patching."""
    if len(original_mesh.submeshes) != len(new_mesh.submeshes):
        return True

    for orig_sm, new_sm in zip(original_mesh.submeshes, new_mesh.submeshes):
        if len(orig_sm.vertices) != len(new_sm.vertices):
            return True
        if len(orig_sm.faces) != len(new_sm.faces):
            return True
        if orig_sm.faces != new_sm.faces:
            return True
        if not _submesh_uvs_match(orig_sm, new_sm):
            return True

    return False


def _inspect_pam_layout(original_data: bytes) -> dict:
    """Inspect whether the PAM uses a standard layout we can serialize."""
    hdr_geom_off = 0x3C
    hdr_mesh_count = 0x10
    submesh_table = 0x410
    submesh_stride = 0x218
    pam_idx_off = 0x19840

    if not original_data or original_data[:4] != b"PAR ":
        return {"kind": "unsupported", "reason": "missing PAM header"}

    geom_off = struct.unpack_from("<I", original_data, hdr_geom_off)[0]
    mesh_count = struct.unpack_from("<I", original_data, hdr_mesh_count)[0]
    if mesh_count <= 0:
        return {"kind": "unsupported", "reason": "mesh table is empty"}

    entries = []
    for i in range(mesh_count):
        desc_off = submesh_table + i * submesh_stride
        if desc_off + submesh_stride > len(original_data):
            return {"kind": "unsupported", "reason": "submesh table is truncated"}
        nv = struct.unpack_from("<I", original_data, desc_off)[0]
        ni = struct.unpack_from("<I", original_data, desc_off + 4)[0]
        ve = struct.unpack_from("<I", original_data, desc_off + 8)[0]
        ie = struct.unpack_from("<I", original_data, desc_off + 12)[0]
        entries.append({
            "desc_off": desc_off,
            "nv": nv,
            "ni": ni,
            "ve": ve,
            "ie": ie,
        })

    is_combined = mesh_count > 1
    if is_combined:
        ve_acc = ie_acc = 0
        for entry in entries:
            if entry["ve"] != ve_acc or entry["ie"] != ie_acc:
                is_combined = False
                break
            ve_acc += entry["nv"]
            ie_acc += entry["ni"]

    total_nv = sum(entry["nv"] for entry in entries)
    total_ni = sum(entry["ni"] for entry in entries)

    def detect_forward_scan_layout() -> Optional[dict]:
        if total_nv <= 0 or total_ni <= 0:
            return None

        search_limit = min(len(original_data) - 100, geom_off + min(len(original_data) // 2, 2_000_000))
        step = 2 if (search_limit - geom_off) < 500_000 else 4
        scan_candidates = [6, 8, 10, 12, 14, 16, 20, 24, 28, 32]

        for scan_start in range(geom_off, search_limit, step):
            if scan_start + 60 > len(original_data):
                break
            vals = [struct.unpack_from("<H", original_data, scan_start + j * 2)[0] for j in range(30)]
            if max(vals) - min(vals) < 5000:
                continue

            for stride in scan_candidates:
                idx_base = scan_start + total_nv * stride
                if idx_base + total_ni * 2 > len(original_data):
                    continue

                valid = True
                for j in range(min(50, total_ni)):
                    val = struct.unpack_from("<H", original_data, idx_base + j * 2)[0]
                    if val >= total_nv:
                        valid = False
                        break
                if not valid:
                    continue

                valid = all(
                    struct.unpack_from("<H", original_data, idx_base + j * 2)[0] < total_nv
                    for j in range(min(total_ni, 500))
                )
                if not valid:
                    continue

                return {
                    "kind": "scan_combined",
                    "geom_off": geom_off,
                    "scan_start": scan_start,
                    "entries": entries,
                    "stride": stride,
                    "old_geom_end": idx_base + total_ni * 2,
                }
        return None

    def detect_backward_scan_layout() -> Optional[dict]:
        if total_nv <= 0 or total_ni <= 0:
            return None

        scan_candidates = [6, 8, 10, 12, 14, 16, 20, 24, 28, 32]
        for scan_end_off in range(len(original_data) - 2, geom_off + total_nv * 6, -2):
            idx_base = scan_end_off - total_ni * 2 + 2
            if idx_base < geom_off:
                break

            first_val = struct.unpack_from("<H", original_data, idx_base)[0]
            if first_val >= total_nv:
                continue

            valid = True
            for j in range(min(30, total_ni)):
                val = struct.unpack_from("<H", original_data, idx_base + j * 2)[0]
                if val >= total_nv:
                    valid = False
                    break
            if not valid:
                continue

            valid = all(
                struct.unpack_from("<H", original_data, idx_base + j * 2)[0] < total_nv
                for j in range(min(total_ni, 300))
            )
            if not valid:
                continue

            valid = all(
                struct.unpack_from("<H", original_data, idx_base + j * 2)[0] < total_nv
                for j in range(total_ni)
            )
            if not valid:
                continue

            vert_region = idx_base - geom_off
            stride = None
            for try_stride in scan_candidates:
                expected_end = geom_off + total_nv * try_stride
                if expected_end <= idx_base and (idx_base - expected_end) < 16384:
                    stride = try_stride
                    break
            if stride is None:
                stride = max(6, vert_region // max(total_nv, 1))

            vertex_end = geom_off + total_nv * stride
            if vertex_end > idx_base or vertex_end > len(original_data):
                continue

            return {
                "kind": "backward_scan_combined",
                "geom_off": geom_off,
                "entries": entries,
                "stride": stride,
                "idx_base": idx_base,
                "vertex_end": vertex_end,
                "old_geom_end": idx_base + total_ni * 2,
            }
        return None

    if is_combined:
        if total_nv <= 0:
            return {"kind": "unsupported", "reason": "combined PAM has no vertices"}
        avail = len(original_data) - geom_off
        target_stride = (avail - total_ni * 2) / total_nv
        stride = min(STRIDE_CANDIDATES, key=lambda s: abs(s - target_stride))
        idx_base = geom_off + total_nv * stride
        if idx_base + total_ni * 2 <= len(original_data):
            return {
                "kind": "combined",
                "geom_off": geom_off,
                "entries": entries,
                "stride": stride,
                "old_geom_end": idx_base + total_ni * 2,
            }

        scan_layout = detect_forward_scan_layout()
        if scan_layout is not None:
            return scan_layout

        backward_layout = detect_backward_scan_layout()
        if backward_layout is not None:
            return backward_layout

        return {"kind": "unsupported", "reason": "combined PAM geometry block is truncated"}

    idx_avail = max(0, (len(original_data) - pam_idx_off) // 2)
    local_entries = []
    uses_global = False
    old_geom_end = geom_off
    for entry in entries:
        stride, idx_off = _find_local_stride(
            original_data, geom_off, entry["ve"], entry["nv"], entry["ni"]
        )
        if stride is not None:
            entry = dict(entry)
            entry["stride"] = stride
            entry["idx_off"] = idx_off
            local_entries.append(entry)
            old_geom_end = max(old_geom_end, idx_off + entry["ni"] * 2)
            continue

        if entry["ie"] + entry["ni"] <= idx_avail:
            uses_global = True
        else:
            scan_layout = detect_forward_scan_layout()
            if scan_layout is not None:
                return scan_layout

            backward_layout = detect_backward_scan_layout()
            if backward_layout is not None:
                return backward_layout

            return {"kind": "unsupported", "reason": "PAM uses scan-fallback geometry layout"}

    if uses_global:
        backward_layout = detect_backward_scan_layout()
        if backward_layout is not None:
            return backward_layout

        return {"kind": "unsupported", "reason": "global-buffer PAM rebuild is not implemented yet"}

    return {
        "kind": "local",
        "geom_off": geom_off,
        "entries": local_entries,
        "old_geom_end": old_geom_end,
    }


def _make_vertex_template_record(
    original_data: bytes,
    base_off: int,
    stride: int,
    index: int,
    fallback_count: int,
) -> bytearray:
    """Copy a template vertex record from the original file when possible."""
    if fallback_count > 0:
        src_idx = min(index, fallback_count - 1)
        rec_off = base_off + src_idx * stride
        if rec_off + stride <= len(original_data):
            return bytearray(original_data[rec_off:rec_off + stride])
    return bytearray(stride)


def _pack_static_vertex_record(
    rec: bytearray,
    stride: int,
    vertex: tuple[float, float, float],
    uv: Optional[tuple[float, float]],
    bmin: tuple[float, float, float],
    bmax: tuple[float, float, float],
) -> bytearray:
    """Write XYZ and optional UVs into a static-mesh vertex record."""
    if len(rec) < stride:
        rec.extend(b"\x00" * (stride - len(rec)))

    xu = _quantize_u16(vertex[0], bmin[0], bmax[0])
    yu = _quantize_u16(vertex[1], bmin[1], bmax[1])
    zu = _quantize_u16(vertex[2], bmin[2], bmax[2])
    struct.pack_into("<HHH", rec, 0, xu, yu, zu)

    if stride >= 12 and uv is not None:
        try:
            struct.pack_into("<e", rec, 8, uv[0])
            struct.pack_into("<e", rec, 10, uv[1])
        except (OverflowError, ValueError):
            struct.pack_into("<e", rec, 8, 0.0)
            struct.pack_into("<e", rec, 10, 0.0)

    return rec


def _static_alignment_match_cost(
    orig_vertex: tuple[float, float, float],
    new_vertex: tuple[float, float, float],
    orig_idx: int,
    new_idx: int,
    diag: float,
    max_count: int,
) -> float:
    """Score how likely an imported static vertex maps to an original slot."""
    dist = math.dist(orig_vertex, new_vertex)
    if orig_idx == new_idx:
        dist *= 0.75
    elif abs(orig_idx - new_idx) <= 2:
        dist *= 0.85

    order_penalty = (
        abs(orig_idx - new_idx) / max(max_count, 1)
    ) * max(diag * 0.05, 0.01)
    return dist + order_penalty


def _align_static_vertex_sequences(
    orig_vertices: list[tuple[float, float, float]],
    new_vertices: list[tuple[float, float, float]],
) -> list[int]:
    """Align original/new static vertex order while allowing inserted vertices."""
    orig_count = len(orig_vertices)
    new_count = len(new_vertices)
    aligned = [-1] * new_count
    if orig_count == 0 or new_count == 0:
        return aligned

    bbox_min, bbox_max = _compute_bbox(orig_vertices)
    diag = math.dist(bbox_min, bbox_max)
    gap_penalty = max(diag * 0.02, 0.01)
    band = max(128, abs(orig_count - new_count) + 128)
    max_states = (orig_count + 1) * min(new_count + 1, band * 2 + 1)
    if max_states > 3_000_000:
        raise ValueError(
            f"Static vertex alignment too large ({orig_count}x{new_count}, band={band})"
        )

    prev_row = {j: j * gap_penalty for j in range(0, min(new_count, band) + 1)}
    backtrack: dict[tuple[int, int], str] = {}
    for j in range(1, min(new_count, band) + 1):
        backtrack[(0, j)] = "left"

    max_count = max(orig_count, new_count)
    for i in range(1, orig_count + 1):
        j_start = max(0, i - band)
        j_end = min(new_count, i + band)
        curr_row: dict[int, float] = {}
        if j_start == 0:
            curr_row[0] = i * gap_penalty
            backtrack[(i, 0)] = "up"

        for j in range(max(1, j_start), j_end + 1):
            best_cost = float("inf")
            best_move = ""

            diag_prev = prev_row.get(j - 1)
            if diag_prev is not None:
                cost = diag_prev + _static_alignment_match_cost(
                    orig_vertices[i - 1],
                    new_vertices[j - 1],
                    i - 1,
                    j - 1,
                    diag,
                    max_count,
                )
                if cost < best_cost:
                    best_cost = cost
                    best_move = "diag"

            up_prev = prev_row.get(j)
            if up_prev is not None:
                cost = up_prev + gap_penalty
                if cost < best_cost:
                    best_cost = cost
                    best_move = "up"

            left_prev = curr_row.get(j - 1)
            if left_prev is not None:
                cost = left_prev + gap_penalty
                if cost < best_cost:
                    best_cost = cost
                    best_move = "left"

            if best_move:
                curr_row[j] = best_cost
                backtrack[(i, j)] = best_move

        prev_row = curr_row

    if new_count not in prev_row:
        raise ValueError("Static vertex alignment band did not reach the final state")

    i = orig_count
    j = new_count
    while i > 0 or j > 0:
        move = backtrack.get((i, j))
        if move == "diag":
            aligned[j - 1] = i - 1
            i -= 1
            j -= 1
        elif move == "left":
            j -= 1
        elif move == "up":
            i -= 1
        else:
            # Recover gracefully if a rare boundary state is missing.
            if j > 0 and i > 0:
                aligned[j - 1] = i - 1
                i -= 1
                j -= 1
            elif j > 0:
                j -= 1
            else:
                i -= 1

    return aligned


def _choose_static_donor_indices(orig_sm: SubMesh, new_sm: SubMesh) -> list[int]:
    """Choose donor records for a topology-changing static mesh rebuild."""
    orig_vertices = list(orig_sm.vertices)
    new_vertices = list(new_sm.vertices)
    if not new_vertices:
        return []
    if not orig_vertices:
        return [0] * len(new_vertices)

    try:
        donor_indices = _align_static_vertex_sequences(orig_vertices, new_vertices)
    except Exception as exc:
        logger.debug(
            "Static donor alignment fallback for %s: %s",
            getattr(new_sm, "name", "") or getattr(orig_sm, "name", "") or "<submesh>",
            exc,
        )
        donor_indices = [-1] * len(new_vertices)

    rounded_map: dict[tuple[int, int, int], list[int]] = {}
    for orig_idx, vertex in enumerate(orig_vertices):
        key = (
            round(vertex[0] * 100000),
            round(vertex[1] * 100000),
            round(vertex[2] * 100000),
        )
        rounded_map.setdefault(key, []).append(orig_idx)

    cell_size, grid = _build_spatial_hash(orig_vertices)
    for new_idx, vertex in enumerate(new_vertices):
        if 0 <= donor_indices[new_idx] < len(orig_vertices):
            continue

        key = (
            round(vertex[0] * 100000),
            round(vertex[1] * 100000),
            round(vertex[2] * 100000),
        )
        exact_hits = rounded_map.get(key)
        if exact_hits:
            donor_indices[new_idx] = min(
                exact_hits,
                key=lambda orig_idx: abs(orig_idx - new_idx),
            )
            continue

        donor_indices[new_idx] = _nearest_point_index(vertex, orig_vertices, cell_size, grid)

    return donor_indices


def _replace_all_in_region(
    data: bytearray,
    start: int,
    end: int,
    old: bytes,
    new: bytes,
) -> int:
    """Replace all occurrences of a fixed-size pattern inside a bounded region."""
    if not old or old == new or start >= end:
        return 0

    hits = 0
    cursor = start
    while True:
        pos = data.find(old, cursor, end)
        if pos < 0:
            break
        data[pos:pos + len(old)] = new
        hits += 1
        cursor = pos + len(new)
    return hits


def _sync_pam_header_mirrors(
    result: bytearray,
    original_mesh: ParsedMesh,
    new_mesh: ParsedMesh,
    geom_off: int,
) -> int:
    """Update mirrored PAM metadata between the main table and geometry block."""
    def _bbox_close(candidate: tuple[float, float, float, float, float, float], reference: tuple[float, float, float, float, float, float], tol: float = 1e-3) -> bool:
        return all(math.isfinite(value) and abs(value - target) <= tol for value, target in zip(candidate, reference))

    mesh_count = min(len(original_mesh.submeshes), len(new_mesh.submeshes))
    region_start = 0x410 + mesh_count * 0x218
    region_end = min(max(geom_off, region_start), len(result))
    if region_start >= region_end:
        return 0

    patched = 0

    for orig_sm, new_sm in zip(original_mesh.submeshes, new_mesh.submeshes):
        orig_nv = len(orig_sm.vertices)
        orig_ni = len(orig_sm.faces) * 3
        new_nv = len(new_sm.vertices)
        new_ni = len(new_sm.faces) * 3

        if orig_sm.vertices:
            oxs, oys, ozs = zip(*orig_sm.vertices)
            old_bbox = (
                min(oxs), min(oys), min(ozs),
                max(oxs), max(oys), max(ozs),
            )
        else:
            old_bbox = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

        if new_sm.vertices:
            nxs, nys, nzs = zip(*new_sm.vertices)
            new_bbox = (
                min(nxs), min(nys), min(nzs),
                max(nxs), max(nys), max(nzs),
            )
        else:
            new_bbox = old_bbox

        old_bbox_bytes = struct.pack("<6f", *old_bbox)
        new_bbox_bytes = struct.pack("<6f", *new_bbox)

        patched += _replace_all_in_region(
            result,
            region_start,
            region_end,
            struct.pack("<I", orig_ni) + old_bbox_bytes,
            struct.pack("<I", new_ni) + new_bbox_bytes,
        )
        patched += _replace_all_in_region(
            result,
            region_start,
            region_end,
            old_bbox_bytes,
            new_bbox_bytes,
        )

        for off in range(region_start, max(region_start, region_end - 28) + 1, 4):
            count_and_bbox = result[off:off + 28]
            if len(count_and_bbox) < 28:
                break
            count = struct.unpack_from("<I", count_and_bbox, 0)[0]
            bbox = struct.unpack_from("<6f", count_and_bbox, 4)
            if count == orig_ni and _bbox_close(bbox, old_bbox):
                struct.pack_into("<I", result, off, new_ni)
                struct.pack_into("<6f", result, off + 4, *new_bbox)
                patched += 1

        for off in range(region_start, max(region_start, region_end - 24) + 1, 4):
            bbox_bytes = result[off:off + 24]
            if len(bbox_bytes) < 24:
                break
            bbox = struct.unpack_from("<6f", bbox_bytes, 0)
            if _bbox_close(bbox, old_bbox):
                struct.pack_into("<6f", result, off, *new_bbox)
                patched += 1

        old_pair = struct.pack("<II", orig_nv, orig_ni)
        new_pair = struct.pack("<II", new_nv, new_ni)
        if old_pair == new_pair:
            continue

        anchor_names = []
        if orig_sm.texture:
            anchor_names.append(orig_sm.texture.encode("ascii", "ignore"))
        if orig_sm.material:
            anchor_names.append(orig_sm.material.encode("ascii", "ignore"))

        for anchor in anchor_names:
            if not anchor:
                continue
            cursor = region_start
            while True:
                pos = result.find(anchor, cursor, region_end)
                if pos < 0:
                    break
                pair_off = pos - 8
                if pair_off >= region_start and bytes(result[pair_off:pair_off + 8]) == old_pair:
                    result[pair_off:pair_off + 8] = new_pair
                    patched += 1
                cursor = pos + len(anchor)

    return patched


def _sync_pam_geom_size_header(
    result: bytearray,
    original_data: bytes,
    geom_off: int,
    old_geom_end: int,
    new_geom_end: int,
) -> bool:
    """Refresh PAM header geometry-size field when it mirrors the geometry block length."""
    header_geom_size_off = 0x40
    if (
        len(result) < header_geom_size_off + 4
        or len(original_data) < header_geom_size_off + 4
        or geom_off <= 0
        or old_geom_end < geom_off
        or new_geom_end < geom_off
    ):
        return False

    original_geom_len = old_geom_end - geom_off
    original_header_geom_len = struct.unpack_from("<I", original_data, header_geom_size_off)[0]
    if original_header_geom_len != original_geom_len:
        return False

    struct.pack_into("<I", result, header_geom_size_off, new_geom_end - geom_off)
    return True


def _serialize_pam_combined_layout(
    mesh: ParsedMesh,
    original_mesh: ParsedMesh,
    original_data: bytes,
    layout: dict,
    bmin: tuple[float, float, float],
    bmax: tuple[float, float, float],
) -> bytes:
    """Rebuild a standard combined-buffer PAM from scratch."""
    hdr_bbox_min = 0x14
    hdr_bbox_max = 0x20

    geom_off = layout["geom_off"]
    stride = layout["stride"]
    entries = layout["entries"]
    old_geom_end = layout["old_geom_end"]
    result = bytearray(original_data[:geom_off])

    struct.pack_into("<fff", result, hdr_bbox_min, *bmin)
    struct.pack_into("<fff", result, hdr_bbox_max, *bmax)

    geom_data = bytearray()
    index_data = bytearray()
    vert_cursor = 0
    idx_cursor = 0

    for sm_idx, (sm, orig_sm, entry) in enumerate(zip(mesh.submeshes, original_mesh.submeshes, entries)):
        struct.pack_into("<I", result, entry["desc_off"], len(sm.vertices))
        struct.pack_into("<I", result, entry["desc_off"] + 4, len(sm.faces) * 3)
        struct.pack_into("<I", result, entry["desc_off"] + 8, vert_cursor)
        struct.pack_into("<I", result, entry["desc_off"] + 12, idx_cursor)

        orig_vert_base = geom_off + entry["ve"] * stride
        orig_nv = entry["nv"]
        uv_data = sm.uvs if len(sm.uvs) == len(sm.vertices) else []
        donor_indices = _choose_static_donor_indices(orig_sm, sm)

        for vi, vertex in enumerate(sm.vertices):
            donor_idx = donor_indices[vi] if vi < len(donor_indices) else vi
            rec = _make_vertex_template_record(original_data, orig_vert_base, stride, donor_idx, orig_nv)
            uv = uv_data[vi] if uv_data else None
            geom_data.extend(_pack_static_vertex_record(rec, stride, vertex, uv, bmin, bmax))

        for a, b, c in sm.faces:
            index_data.extend(struct.pack("<HHH", a + vert_cursor, b + vert_cursor, c + vert_cursor))

        vert_cursor += len(sm.vertices)
        idx_cursor += len(sm.faces) * 3

    result.extend(geom_data)
    result.extend(index_data)
    new_geom_end = geom_off + len(geom_data) + len(index_data)
    _sync_pam_geom_size_header(result, original_data, geom_off, old_geom_end, new_geom_end)
    result.extend(original_data[old_geom_end:])
    mirror_patches = _sync_pam_header_mirrors(result, original_mesh, mesh, geom_off)
    logger.info(
        "Built PAM %s with full combined rebuild: %d submeshes, %d verts, %d faces (%d mirrored header patches)",
        mesh.path, len(mesh.submeshes), sum(len(sm.vertices) for sm in mesh.submeshes),
        sum(len(sm.faces) for sm in mesh.submeshes), mirror_patches,
    )
    return bytes(result)


def _serialize_pam_scan_combined_layout(
    mesh: ParsedMesh,
    original_mesh: ParsedMesh,
    original_data: bytes,
    layout: dict,
    bmin: tuple[float, float, float],
    bmax: tuple[float, float, float],
) -> bytes:
    """Rebuild a scan-fallback PAM whose real geometry starts after geom_off."""
    hdr_bbox_min = 0x14
    hdr_bbox_max = 0x20

    scan_start = layout["scan_start"]
    stride = layout["stride"]
    entries = layout["entries"]
    old_geom_end = layout["old_geom_end"]
    result = bytearray(original_data[:scan_start])

    struct.pack_into("<fff", result, hdr_bbox_min, *bmin)
    struct.pack_into("<fff", result, hdr_bbox_max, *bmax)

    geom_data = bytearray()
    index_data = bytearray()
    vert_cursor = 0
    idx_cursor = 0

    for sm, orig_sm, entry in zip(mesh.submeshes, original_mesh.submeshes, entries):
        struct.pack_into("<I", result, entry["desc_off"], len(sm.vertices))
        struct.pack_into("<I", result, entry["desc_off"] + 4, len(sm.faces) * 3)
        struct.pack_into("<I", result, entry["desc_off"] + 8, vert_cursor)
        struct.pack_into("<I", result, entry["desc_off"] + 12, idx_cursor)

        orig_vert_base = scan_start + entry["ve"] * stride
        orig_nv = entry["nv"]
        uv_data = sm.uvs if len(sm.uvs) == len(sm.vertices) else []
        donor_indices = _choose_static_donor_indices(orig_sm, sm)

        for vi, vertex in enumerate(sm.vertices):
            donor_idx = donor_indices[vi] if vi < len(donor_indices) else vi
            rec = _make_vertex_template_record(original_data, orig_vert_base, stride, donor_idx, orig_nv)
            uv = uv_data[vi] if uv_data else None
            geom_data.extend(_pack_static_vertex_record(rec, stride, vertex, uv, bmin, bmax))

        for a, b, c in sm.faces:
            index_data.extend(struct.pack("<HHH", a + vert_cursor, b + vert_cursor, c + vert_cursor))

        vert_cursor += len(sm.vertices)
        idx_cursor += len(sm.faces) * 3

    result.extend(geom_data)
    result.extend(index_data)
    new_geom_end = layout["geom_off"] + len(geom_data) + len(index_data)
    _sync_pam_geom_size_header(result, original_data, layout["geom_off"], old_geom_end, new_geom_end)
    result.extend(original_data[old_geom_end:])
    mirror_patches = _sync_pam_header_mirrors(result, original_mesh, mesh, layout["geom_off"])
    logger.info(
        "Built PAM %s with full scan-combined rebuild: %d submeshes, %d verts, %d faces (%d mirrored header patches)",
        mesh.path, len(mesh.submeshes), sum(len(sm.vertices) for sm in mesh.submeshes),
        sum(len(sm.faces) for sm in mesh.submeshes), mirror_patches,
    )
    return bytes(result)


def _serialize_pam_backward_scan_combined_layout(
    mesh: ParsedMesh,
    original_mesh: ParsedMesh,
    original_data: bytes,
    layout: dict,
    bmin: tuple[float, float, float],
    bmax: tuple[float, float, float],
) -> bytes:
    """Rebuild a backward-scan PAM with padding between vertices and indices."""
    hdr_bbox_min = 0x14
    hdr_bbox_max = 0x20

    geom_off = layout["geom_off"]
    stride = layout["stride"]
    idx_base = layout["idx_base"]
    vertex_end = layout["vertex_end"]
    entries = layout["entries"]
    old_geom_end = layout["old_geom_end"]
    result = bytearray(original_data[:geom_off])

    struct.pack_into("<fff", result, hdr_bbox_min, *bmin)
    struct.pack_into("<fff", result, hdr_bbox_max, *bmax)

    geom_data = bytearray()
    index_data = bytearray()
    vert_cursor = 0
    idx_cursor = 0

    for sm, orig_sm, entry in zip(mesh.submeshes, original_mesh.submeshes, entries):
        struct.pack_into("<I", result, entry["desc_off"], len(sm.vertices))
        struct.pack_into("<I", result, entry["desc_off"] + 4, len(sm.faces) * 3)
        struct.pack_into("<I", result, entry["desc_off"] + 8, vert_cursor)
        struct.pack_into("<I", result, entry["desc_off"] + 12, idx_cursor)

        orig_vert_base = geom_off + entry["ve"] * stride
        orig_nv = entry["nv"]
        uv_data = sm.uvs if len(sm.uvs) == len(sm.vertices) else []
        donor_indices = _choose_static_donor_indices(orig_sm, sm)

        for vi, vertex in enumerate(sm.vertices):
            donor_idx = donor_indices[vi] if vi < len(donor_indices) else vi
            rec = _make_vertex_template_record(original_data, orig_vert_base, stride, donor_idx, orig_nv)
            uv = uv_data[vi] if uv_data else None
            geom_data.extend(_pack_static_vertex_record(rec, stride, vertex, uv, bmin, bmax))

        for a, b, c in sm.faces:
            index_data.extend(struct.pack("<HHH", a + vert_cursor, b + vert_cursor, c + vert_cursor))

        vert_cursor += len(sm.vertices)
        idx_cursor += len(sm.faces) * 3

    result.extend(geom_data)
    result.extend(original_data[vertex_end:idx_base])
    result.extend(index_data)
    new_geom_end = geom_off + len(geom_data) + (idx_base - vertex_end) + len(index_data)
    _sync_pam_geom_size_header(result, original_data, geom_off, old_geom_end, new_geom_end)
    result.extend(original_data[old_geom_end:])
    mirror_patches = _sync_pam_header_mirrors(result, original_mesh, mesh, geom_off)
    logger.info(
        "Built PAM %s with full backward-scan rebuild: %d submeshes, %d verts, %d faces (%d mirrored header patches)",
        mesh.path, len(mesh.submeshes), sum(len(sm.vertices) for sm in mesh.submeshes),
        sum(len(sm.faces) for sm in mesh.submeshes), mirror_patches,
    )
    return bytes(result)


def _serialize_pam_local_layout(
    mesh: ParsedMesh,
    original_mesh: ParsedMesh,
    original_data: bytes,
    layout: dict,
    bmin: tuple[float, float, float],
    bmax: tuple[float, float, float],
) -> bytes:
    """Rebuild a single-submesh local-layout PAM from scratch."""
    hdr_bbox_min = 0x14
    hdr_bbox_max = 0x20

    geom_off = layout["geom_off"]
    entries = layout["entries"]
    old_geom_end = layout["old_geom_end"]
    result = bytearray(original_data[:geom_off])

    struct.pack_into("<fff", result, hdr_bbox_min, *bmin)
    struct.pack_into("<fff", result, hdr_bbox_max, *bmax)

    geom_data = bytearray()
    current_voff = 0

    for sm, orig_sm, entry in zip(mesh.submeshes, original_mesh.submeshes, entries):
        stride = entry["stride"]
        struct.pack_into("<I", result, entry["desc_off"], len(sm.vertices))
        struct.pack_into("<I", result, entry["desc_off"] + 4, len(sm.faces) * 3)
        struct.pack_into("<I", result, entry["desc_off"] + 8, current_voff)
        struct.pack_into("<I", result, entry["desc_off"] + 12, 0)

        orig_vert_base = geom_off + entry["ve"]
        orig_nv = entry["nv"]
        uv_data = sm.uvs if len(sm.uvs) == len(sm.vertices) else []
        donor_indices = _choose_static_donor_indices(orig_sm, sm)

        for vi, vertex in enumerate(sm.vertices):
            donor_idx = donor_indices[vi] if vi < len(donor_indices) else vi
            rec = _make_vertex_template_record(original_data, orig_vert_base, stride, donor_idx, orig_nv)
            uv = uv_data[vi] if uv_data else None
            geom_data.extend(_pack_static_vertex_record(rec, stride, vertex, uv, bmin, bmax))

        for a, b, c in sm.faces:
            geom_data.extend(struct.pack("<HHH", a, b, c))

        current_voff += len(sm.vertices) * stride + len(sm.faces) * 6

    result.extend(geom_data)
    new_geom_end = geom_off + len(geom_data)
    _sync_pam_geom_size_header(result, original_data, geom_off, old_geom_end, new_geom_end)
    result.extend(original_data[old_geom_end:])
    mirror_patches = _sync_pam_header_mirrors(result, original_mesh, mesh, geom_off)
    logger.info(
        "Built PAM %s with full local rebuild: %d submeshes, %d verts, %d faces (%d mirrored header patches)",
        mesh.path, len(mesh.submeshes), sum(len(sm.vertices) for sm in mesh.submeshes),
        sum(len(sm.faces) for sm in mesh.submeshes), mirror_patches,
    )
    return bytes(result)


def _spatial_cell_key(point: tuple[float, float, float], cell_size: float) -> tuple[int, int, int]:
    return (
        int(math.floor(point[0] / cell_size)),
        int(math.floor(point[1] / cell_size)),
        int(math.floor(point[2] / cell_size)),
    )


def _build_spatial_hash(points: list[tuple[float, float, float]]) -> tuple[float, dict[tuple[int, int, int], list[int]]]:
    """Create a simple spatial hash for nearest-vertex transfer."""
    if not points:
        return 1.0, {}

    xs, ys, zs = zip(*points)
    extent = max(
        max(xs) - min(xs),
        max(ys) - min(ys),
        max(zs) - min(zs),
        1e-5,
    )
    cell_size = max(extent / max(round(len(points) ** (1.0 / 3.0)), 1), 1e-5)

    grid: dict[tuple[int, int, int], list[int]] = {}
    for idx, point in enumerate(points):
        grid.setdefault(_spatial_cell_key(point, cell_size), []).append(idx)
    return cell_size, grid


def _nearest_point_index(
    point: tuple[float, float, float],
    source_points: list[tuple[float, float, float]],
    cell_size: float,
    grid: dict[tuple[int, int, int], list[int]],
) -> int:
    """Find the nearest source point using the spatial hash."""
    if not source_points:
        raise ValueError("Cannot transfer displacement from an empty source mesh.")

    base = _spatial_cell_key(point, cell_size)
    best_idx = -1
    best_d2 = float("inf")

    for radius in range(0, 8):
        found_any = False
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                for dz in range(-radius, radius + 1):
                    cell = (base[0] + dx, base[1] + dy, base[2] + dz)
                    for idx in grid.get(cell, ()):
                        found_any = True
                        sx, sy, sz = source_points[idx]
                        d2 = ((sx - point[0]) ** 2 +
                              (sy - point[1]) ** 2 +
                              (sz - point[2]) ** 2)
                        if d2 < best_d2:
                            best_d2 = d2
                            best_idx = idx
        if found_any and best_idx >= 0:
            return best_idx

    for idx, src in enumerate(source_points):
        d2 = ((src[0] - point[0]) ** 2 +
              (src[1] - point[1]) ** 2 +
              (src[2] - point[2]) ** 2)
        if d2 < best_d2:
            best_d2 = d2
            best_idx = idx

    return best_idx


def _nearby_point_indices(
    point: tuple[float, float, float],
    source_points: list[tuple[float, float, float]],
    cell_size: float,
    grid: dict[tuple[int, int, int], list[int]],
    radius: float,
) -> list[int]:
    """Return source points within the given radius."""
    if not source_points:
        return []

    base = _spatial_cell_key(point, cell_size)
    cell_radius = max(1, int(math.ceil(radius / max(cell_size, 1e-6))))
    radius_sq = radius * radius
    candidates: list[int] = []

    for dx in range(-cell_radius, cell_radius + 1):
        for dy in range(-cell_radius, cell_radius + 1):
            for dz in range(-cell_radius, cell_radius + 1):
                cell = (base[0] + dx, base[1] + dy, base[2] + dz)
                for idx in grid.get(cell, ()):
                    sx, sy, sz = source_points[idx]
                    d2 = ((sx - point[0]) ** 2 +
                          (sy - point[1]) ** 2 +
                          (sz - point[2]) ** 2)
                    if d2 <= radius_sq:
                        candidates.append(idx)

    return candidates


def _percentile(values: list[float], pct: float) -> float:
    """Return a simple percentile from a non-empty list."""
    if not values:
        return 0.0
    clamped = max(0.0, min(1.0, pct))
    ordered = sorted(values)
    idx = int(round((len(ordered) - 1) * clamped))
    return ordered[idx]


def transfer_pam_edit_to_pamlod_mesh(
    edited_pam_mesh: ParsedMesh,
    original_pam_data: bytes,
    original_pamlod_data: bytes,
    pamlod_path: str,
) -> ParsedMesh:
    """Project a PAM edit onto the paired PAMLOD levels via nearest displacement."""
    original_pam_mesh = parse_pam(original_pam_data, edited_pam_mesh.path)
    editable_pam_mesh = copy.deepcopy(edited_pam_mesh)
    _align_submesh_order_like_original(original_pam_mesh, editable_pam_mesh)

    source_orig = [v for sm in original_pam_mesh.submeshes for v in sm.vertices]
    source_new = [v for sm in editable_pam_mesh.submeshes for v in sm.vertices]
    if not source_orig or not source_new:
        raise ValueError("PAM to PAMLOD transfer requires non-empty source geometry.")

    if len(source_orig) == len(source_new):
        paired_points = zip(source_orig, source_new)
    else:
        # Topology edits cannot be transferred one-to-one, so approximate the
        # deformation field by matching each original PAM vertex to its nearest
        # edited-space vertex. This keeps paired PAMLOD patching alive for
        # sculpt/retopo-style edits instead of failing outright.
        edit_cell_size, edit_grid = _build_spatial_hash(source_new)
        nearest_points = [
            source_new[_nearest_point_index(orig_v, source_new, edit_cell_size, edit_grid)]
            for orig_v in source_orig
        ]
        paired_points = zip(source_orig, nearest_points)

    changed_points: list[tuple[float, float, float]] = []
    changed_displacements: list[tuple[float, float, float]] = []
    for orig_v, new_v in paired_points:
        disp = (new_v[0] - orig_v[0], new_v[1] - orig_v[1], new_v[2] - orig_v[2])
        if math.sqrt(disp[0] ** 2 + disp[1] ** 2 + disp[2] ** 2) > 1e-6:
            changed_points.append(orig_v)
            changed_displacements.append(disp)

    pamlod_mesh = parse_pamlod(original_pamlod_data, pamlod_path)
    if not changed_points:
        return pamlod_mesh

    cell_size, grid = _build_spatial_hash(changed_points)
    transferred = copy.deepcopy(pamlod_mesh)

    for lod_level in transferred.lod_levels:
        lod_vertices = [vertex for sm in lod_level for vertex in sm.vertices]
        if not lod_vertices:
            continue

        target_cell_size, target_grid = _build_spatial_hash(lod_vertices)
        sample_step = max(1, len(changed_points) // 512)
        target_distances = []
        for idx in range(0, len(changed_points), sample_step):
            source_vertex = changed_points[idx]
            nearest_idx = _nearest_point_index(
                source_vertex, lod_vertices, target_cell_size, target_grid
            )
            target_distances.append(math.dist(source_vertex, lod_vertices[nearest_idx]))

        influence_radius = max(
            _percentile(target_distances, 0.75) * 1.25,
            1e-4,
        )

        for sm in lod_level:
            new_vertices = []
            for vertex in sm.vertices:
                nearby = _nearby_point_indices(
                    vertex, changed_points, cell_size, grid, influence_radius
                )
                if not nearby:
                    new_vertices.append(vertex)
                    continue

                exact_disp = None
                acc_x = acc_y = acc_z = 0.0
                weight_sum = 0.0
                for idx in nearby:
                    src = changed_points[idx]
                    disp = changed_displacements[idx]
                    dist = math.dist(vertex, src)
                    if dist <= 1e-8:
                        exact_disp = disp
                        break
                    weight = (1.0 - min(dist / influence_radius, 1.0)) ** 2
                    if weight <= 0.0:
                        continue
                    acc_x += disp[0] * weight
                    acc_y += disp[1] * weight
                    acc_z += disp[2] * weight
                    weight_sum += weight

                if exact_disp is not None:
                    dx, dy, dz = exact_disp
                elif weight_sum > 0.0:
                    dx = acc_x / weight_sum
                    dy = acc_y / weight_sum
                    dz = acc_z / weight_sum
                else:
                    dx = dy = dz = 0.0

                new_vertices.append((vertex[0] + dx, vertex[1] + dy, vertex[2] + dz))
            sm.vertices = new_vertices
            sm.vertex_count = len(new_vertices)
            sm.normals = _compute_smooth_normals(sm.vertices, sm.faces)

    if transferred.lod_levels:
        for lod_level in transferred.lod_levels:
            if lod_level:
                transferred.submeshes = lod_level
                break

    transferred.total_vertices = sum(len(sm.vertices) for sm in transferred.submeshes)
    transferred.total_faces = sum(len(sm.faces) for sm in transferred.submeshes)
    transferred.has_uvs = any(sm.uvs for sm in transferred.submeshes)
    return transferred


# ═══════════════════════════════════════════════════════════════════════
#  PAM BUILDER
# ═══════════════════════════════════════════════════════════════════════

def build_pam(mesh: ParsedMesh, original_data: bytes) -> bytes:
    """Rebuild a PAM binary from a modified mesh.

    Standard combined/local PAM layouts can be fully reserialized so UV
    edits and same-submesh topology edits survive round-trip. More exotic
    scan-fallback/global layouts still fall back to the older position-only
    patch path.
    """
    if not original_data or original_data[:4] != b"PAR ":
        raise ValueError("Original PAM data required for rebuild")

    HDR_BBOX_MIN = 0x14
    HDR_BBOX_MAX = 0x20
    HDR_GEOM_OFF = 0x3C

    result = bytearray(original_data)

    # Read original bbox — use for quantization, expand only if needed
    orig_bmin = struct.unpack_from("<fff", original_data, HDR_BBOX_MIN)
    orig_bmax = struct.unpack_from("<fff", original_data, HDR_BBOX_MAX)
    original_mesh = parse_pam(original_data, mesh.path)
    working_mesh = copy.deepcopy(mesh)
    _align_submesh_order_like_original(original_mesh, working_mesh)

    all_v = [v for s in working_mesh.submeshes for v in s.vertices]
    if all_v:
        bmin, bmax = _compute_bbox(all_v)
    else:
        bmin, bmax = orig_bmin, orig_bmax

    if _pam_needs_full_rebuild(original_mesh, working_mesh):
        if len(original_mesh.submeshes) != len(working_mesh.submeshes):
            raise ValueError(
                "PAM import currently requires keeping the same submesh count as the original mesh."
            )

        layout = _inspect_pam_layout(original_data)
        if layout["kind"] == "combined":
            return _serialize_pam_combined_layout(
                working_mesh, original_mesh, original_data, layout, bmin, bmax
            )
        if layout["kind"] == "scan_combined":
            return _serialize_pam_scan_combined_layout(
                working_mesh, original_mesh, original_data, layout, bmin, bmax
            )
        if layout["kind"] == "backward_scan_combined":
            return _serialize_pam_backward_scan_combined_layout(
                working_mesh, original_mesh, original_data, layout, bmin, bmax
            )
        if layout["kind"] == "local":
            return _serialize_pam_local_layout(
                working_mesh, original_mesh, original_data, layout, bmin, bmax
            )
        raise ValueError(
            "This PAM layout currently supports position-only patching. "
            f"Topology/UV edits are not supported for it yet ({layout.get('reason', 'unknown layout')})."
        )

    if not original_mesh.submeshes:
        return bytes(result)

    bmin, bmax = _expand_bbox_to_vertices(orig_bmin, orig_bmax, all_v)
    struct.pack_into("<fff", result, HDR_BBOX_MIN, *bmin)
    struct.pack_into("<fff", result, HDR_BBOX_MAX, *bmax)

    geom_off = struct.unpack_from("<I", original_data, HDR_GEOM_OFF)[0]
    offset_refs = _collect_vertex_offset_refs(
        original_data, original_mesh, working_mesh, orig_bmin, orig_bmax, search_start=geom_off
    )
    patched_offsets = _apply_quantized_vertex_patches(result, offset_refs, bmin, bmax)

    total_patched = patched_offsets
    logger.info("Built PAM %s: %d bytes (patched %d verts in-place)",
                mesh.path, len(result), total_patched)
    return bytes(result)


def build_pamlod(mesh: ParsedMesh, original_data: bytes) -> bytes:
    """Rebuild a PAMLOD binary by patching vertex positions in-place."""
    if not original_data or len(original_data) < 0x20:
        raise ValueError("Original PAMLOD data required for rebuild")

    HDR_BBOX_MIN = 0x10
    HDR_BBOX_MAX = 0x1C

    result = bytearray(original_data)
    orig_bmin = struct.unpack_from("<fff", original_data, HDR_BBOX_MIN)
    orig_bmax = struct.unpack_from("<fff", original_data, HDR_BBOX_MAX)

    orig_mesh = parse_pamlod(original_data, mesh.path)
    if not orig_mesh.lod_levels:
        return bytes(result)

    target_lod_levels = copy.deepcopy(orig_mesh.lod_levels)
    if mesh.lod_levels:
        for lod_idx, lod_level in enumerate(mesh.lod_levels):
            if lod_idx < len(target_lod_levels) and lod_level:
                target_lod_levels[lod_idx] = copy.deepcopy(lod_level)
    elif mesh.submeshes:
        replace_idx = next((i for i, lod in enumerate(target_lod_levels) if lod), 0)
        target_lod_levels[replace_idx] = copy.deepcopy(mesh.submeshes)

    all_vertices = [
        v
        for lod_level in target_lod_levels
        for sm in lod_level
        for v in sm.vertices
    ]
    bmin, bmax = _expand_bbox_to_vertices(orig_bmin, orig_bmax, all_vertices)
    struct.pack_into("<fff", result, HDR_BBOX_MIN, *bmin)
    struct.pack_into("<fff", result, HDR_BBOX_MAX, *bmax)

    offset_refs: dict[int, list[tuple[tuple[float, float, float], tuple[float, float, float], int, int]]] = {}
    for lod_idx, orig_level in enumerate(orig_mesh.lod_levels):
        if lod_idx >= len(target_lod_levels):
            break
        new_level = target_lod_levels[lod_idx]
        if not orig_level or not new_level:
            continue

        level_orig_mesh = _make_temp_mesh(orig_mesh.path, "pamlod", orig_level)
        level_new_mesh = _make_temp_mesh(mesh.path or orig_mesh.path, "pamlod", new_level)
        level_refs = _collect_vertex_offset_refs(
            original_data, level_orig_mesh, level_new_mesh, orig_bmin, orig_bmax, search_start=0
        )
        for byte_off, refs in level_refs.items():
            offset_refs.setdefault(byte_off, []).extend(refs)

    patched_offsets = _apply_quantized_vertex_patches(
        result, offset_refs, bmin, bmax, allow_average_conflicts=True
    )
    logger.info("Built PAMLOD %s: %d bytes (patched %d verts in-place)",
                mesh.path, len(result), patched_offsets)
    return bytes(result)


# ═══════════════════════════════════════════════════════════════════════
#  AUTO-DETECT AND BUILD
# ═══════════════════════════════════════════════════════════════════════

def _quantize_pac_u16(value: float, bbox_min: float, bbox_extent: float) -> int:
    """Float -> PAC uint16 quantized using bbox min/extent encoding."""
    if abs(bbox_extent) < 1e-10:
        return 0
    t = (value - bbox_min) / bbox_extent
    t = max(0.0, min(1.0, t))
    return min(32767, max(0, round(t * 32767.0)))


def _patch_pac_descriptor_bounds(
    data: bytearray,
    descriptor_offset: int,
    bbox_min: tuple[float, float, float],
    bbox_extent: tuple[float, float, float],
) -> None:
    """Update a PAC descriptor's bbox min/extent floats in section 0."""
    if descriptor_offset < 0 or descriptor_offset + 35 > len(data):
        return

    floats_off = descriptor_offset + 3
    struct.pack_into("<f", data, floats_off + 2 * 4, bbox_min[0])
    struct.pack_into("<f", data, floats_off + 3 * 4, bbox_min[1])
    struct.pack_into("<f", data, floats_off + 4 * 4, bbox_min[2])
    struct.pack_into("<f", data, floats_off + 5 * 4, bbox_extent[0])
    struct.pack_into("<f", data, floats_off + 6 * 4, bbox_extent[1])
    struct.pack_into("<f", data, floats_off + 7 * 4, bbox_extent[2])


def _pac_submesh_match_score(imported_sm: SubMesh, original_sm: SubMesh) -> float:
    """Score how likely an imported PAC object maps back to an original slot."""
    imp_center = tuple((mn + mx) * 0.5 for mn, mx in zip(*_compute_bbox(imported_sm.vertices)))
    orig_center = tuple((mn + mx) * 0.5 for mn, mx in zip(*_compute_bbox(original_sm.vertices)))
    center_dist = math.dist(imp_center, orig_center)

    vert_ratio = abs(math.log((len(imported_sm.vertices) + 1) / (len(original_sm.vertices) + 1)))
    face_ratio = abs(math.log((len(imported_sm.faces) + 1) / (len(original_sm.faces) + 1)))
    return center_dist + vert_ratio * 0.75 + face_ratio * 0.75


def _merge_partial_pac_import(
    original_mesh: ParsedMesh,
    imported_mesh: ParsedMesh,
) -> ParsedMesh:
    """Merge a partial PAC OBJ import onto the original submesh set by name.

    Two distinct user intents share this code path:

      A. **User deleted submeshes from the OBJ.** The OBJ is the
         authoritative source — submeshes the user removed should NOT
         come back from the original PAC. (User reported 2026-05-08:
         "I import a 2-submesh OBJ over a 7-submesh helmet PAC and
         the resulting PAC still shows all 7 in-game and in preview".)

      B. **Blender exporter omitted hidden / unselected objects.** The
         user wants the visible / selected submeshes patched and the
         hidden ones preserved verbatim.

    The pre-2026-05-08 behaviour treated every missing submesh as case
    B and silently restored it from the original PAC. That broke case
    A — users had no way to delete submeshes via OBJ import.

    Fix: detect intent from the OBJ. When AT LEAST ONE imported
    submesh carries an explicit name that matches an original
    submesh, the OBJ is treated as authoritative — every original
    submesh whose name is missing from the OBJ is DROPPED. Users who
    really want case B can either (a) include all submesh names by
    selecting all objects in Blender and re-exporting, or (b) explicitly
    re-add the helper submesh as an empty group in their OBJ.

    Fallback (no named matches at all → all-unnamed OBJ): preserve
    the original behaviour (positional consumption + deepcopy of any
    original past the imported count) so legacy partial-export
    workflows still produce a result.
    """
    if len(imported_mesh.submeshes) >= len(original_mesh.submeshes):
        return imported_mesh

    original_names = [sm.name for sm in original_mesh.submeshes]
    imported_by_name: dict[str, SubMesh] = {}
    unknown_named: list[SubMesh] = []
    unnamed: list[SubMesh] = []

    for sm in imported_mesh.submeshes:
        if sm.name:
            if sm.name in original_names:
                if sm.name in imported_by_name:
                    raise ValueError(
                        f"PAC import contains duplicate submesh name '{sm.name}'. "
                        "Keep unique object names when exporting OBJ from Blender."
                    )
                imported_by_name[sm.name] = copy.deepcopy(sm)
            else:
                unknown_named.append(copy.deepcopy(sm))
        else:
            unnamed.append(copy.deepcopy(sm))

    heuristic_by_name: dict[str, SubMesh] = {}
    unmatched_originals = [
        copy.deepcopy(sm)
        for sm in original_mesh.submeshes
        if sm.name not in imported_by_name
    ]
    for imported_unknown in sorted(unknown_named, key=lambda sm: len(sm.vertices), reverse=True):
        if not unmatched_originals:
            raise ValueError(
                "PAC import contains more renamed submeshes than the original mesh can match."
            )
        best_original = min(
            unmatched_originals,
            key=lambda original_sm: _pac_submesh_match_score(imported_unknown, original_sm),
        )
        imported_unknown.name = best_original.name
        if not imported_unknown.material:
            imported_unknown.material = best_original.material
        heuristic_by_name[best_original.name] = imported_unknown
        unmatched_originals = [sm for sm in unmatched_originals if sm.name != best_original.name]

    # ── Intent detection ──
    # If the OBJ carries any explicit submesh name that matches the
    # original PAC, the user is in "named replace" mode (case A) —
    # drop originals that the OBJ doesn't mention. Otherwise the OBJ
    # is positional/anonymous (case B) — preserve count.
    obj_is_authoritative = bool(imported_by_name)
    dropped_names: list[str] = []

    merged_submeshes: list[SubMesh] = []
    unnamed_iter = iter(unnamed)
    used_named = 0
    for original_sm in original_mesh.submeshes:
        replacement = imported_by_name.get(original_sm.name)
        if replacement is None:
            replacement = heuristic_by_name.get(original_sm.name)
        if replacement is not None:
            merged_submeshes.append(replacement)
            used_named += 1
            continue

        if obj_is_authoritative:
            # User's OBJ is authoritative and didn't mention this
            # submesh by name → emit an EMPTY PLACEHOLDER (same
            # name, same material, same descriptor metadata, but
            # zero vertices / zero faces).
            #
            # Why a placeholder rather than actually dropping the
            # submesh? Because the downstream rebuilder
            # (``_build_pac_full_rebuild``) can patch the per-LOD
            # vertex / index counts inside the existing descriptor
            # records, but it CAN'T shrink section 0's descriptor
            # table without reflowing every later section header
            # and the LOD section-offset table — much higher risk.
            # An empty placeholder produces the same visual result
            # (game's renderer skips submeshes with 0 indices)
            # while keeping the descriptor count == original count,
            # so the existing rebuild path Just Works.
            placeholder = copy.deepcopy(original_sm)
            placeholder.vertices = []
            placeholder.uvs = []
            placeholder.normals = []
            placeholder.faces = []
            placeholder.bone_indices = []
            placeholder.bone_weights = []
            placeholder.source_vertex_offsets = []
            placeholder.vertex_count = 0
            placeholder.face_count = 0
            placeholder.source_vertex_map = []
            merged_submeshes.append(placeholder)
            dropped_names.append(original_sm.name)
            continue

        # Anonymous-OBJ legacy path: consume an unnamed submesh in
        # order, or fall back to keeping the original.
        try:
            merged_submeshes.append(next(unnamed_iter))
        except StopIteration:
            merged_submeshes.append(copy.deepcopy(original_sm))

    try:
        extra_unnamed = next(unnamed_iter)
    except StopIteration:
        extra_unnamed = None
    if extra_unnamed is not None:
        raise ValueError(
            "PAC import contains extra unnamed submeshes that could not be matched to the original mesh."
        )

    if not obj_is_authoritative and used_named == 0 and imported_mesh.submeshes and len(imported_mesh.submeshes) != len(original_mesh.submeshes):
        raise ValueError(
            "PAC import only contained a partial mesh without recognizable original submesh names."
        )

    if dropped_names:
        logger.info(
            "PAC OBJ import is authoritative — emitting %d empty "
            "placeholder(s) for original submesh(es) absent from "
            "the OBJ (game and preview render these as nothing): %s",
            len(dropped_names), ", ".join(dropped_names),
        )

    merged = copy.deepcopy(imported_mesh)
    merged.submeshes = merged_submeshes
    merged.total_vertices = sum(len(sm.vertices) for sm in merged_submeshes)
    merged.total_faces = sum(len(sm.faces) for sm in merged_submeshes)
    merged.has_uvs = any(sm.uvs for sm in merged_submeshes)
    merged.has_bones = any(sm.bone_indices for sm in merged_submeshes)
    return merged


def _decode_donor_skin(rec: bytes) -> tuple[list[int], list[int]]:
    """Decode a donor PAC vertex record into 8 raw bone slots + 8 raw u8 weights.

    Layout (verified from shader DXIL — see
    ``test_only/research/PAC_VERTEX_RECORD_DECODED.md``):

        bytes 12-13 : bone slot 6 as f16 (decoded ``int(f16 + 0.5)``)
        bytes 14-15 : bone slot 7 as f16
        bytes 20-23 : u32 packing slots 0/1/2 as 3 × 10-bit
        bytes 24-27 : u32 packing slots 3/4/5 as 3 × 10-bit
        bytes 28-35 : 8 × u8 weights (each / 255 → unit weight)

    The returned slots are RAW 10-bit values (0..1023). The returned
    weights are RAW u8 values (0..255). Both arrays always have
    length 8 even for vertices using fewer than 8 bones — the
    rebuilder masks out zero-weight slots itself.

    Raises ``ValueError`` if the record is shorter than 36 bytes
    (which means the source PAC isn't using the verified 8-bone
    skinning layout — strict refusal so caller knows nothing is
    salvageable here).
    """
    if len(rec) < 36:
        raise ValueError(
            f"PAC vertex record too short for skin decode "
            f"({len(rec)} bytes, need >= 36)"
        )
    b20_lo, b20_hi = struct.unpack_from("<II", rec, 20)
    slot6_h, slot7_h = struct.unpack_from("<ee", rec, 12)
    slots = [
         b20_lo        & 0x3FF,
        (b20_lo >> 10) & 0x3FF,
        (b20_lo >> 20) & 0x3FF,
         b20_hi        & 0x3FF,
        (b20_hi >> 10) & 0x3FF,
        (b20_hi >> 20) & 0x3FF,
        int(slot6_h + 0.5) if not math.isnan(slot6_h) else 0,
        int(slot7_h + 0.5) if not math.isnan(slot7_h) else 0,
    ]
    raw_weights = list(struct.unpack_from("<BBBBBBBB", rec, 28))
    return slots, raw_weights


def _pack_pac_skin_into_record(
    rec: bytearray,
    slots: list[int],
    weights_u8: list[int],
) -> None:
    """Write 8 bone slots + 8 u8 weights into ``rec`` at the verified
    PAC vertex-record offsets.

    ``slots`` and ``weights_u8`` MUST each be length 8. Slots beyond
    the 10-bit range raise ``ValueError`` (slots are physical bone
    palette indices and the engine reads them as 10-bit; passing
    1024+ would silently lose the high bits, which we refuse to do).
    Weights outside 0..255 are clamped — but only after a debug
    log because that case shouldn't happen if the caller normalised
    correctly.

    The byte writes mirror exactly the read layout in
    :func:`_decode_donor_skin`. Bytes 12-15 (slot 6/7 as f16),
    bytes 20-27 (slots 0-5 packed), bytes 28-35 (8 × weight u8).
    Other bytes in ``rec`` are left untouched — the caller has
    already overwritten position/UV/normal/tangent.
    """
    if len(slots) != 8 or len(weights_u8) != 8:
        raise ValueError(
            f"_pack_pac_skin_into_record needs exactly 8 slots + "
            f"8 weights, got {len(slots)} / {len(weights_u8)}"
        )
    if len(rec) < 36:
        raise ValueError(
            f"PAC vertex record too short for skin write "
            f"({len(rec)} bytes, need >= 36)"
        )

    # Slots 0-5: packed 3 × 10-bit per u32 at bytes 20-27.
    for i, s in enumerate(slots):
        if not (0 <= s <= 0x3FF):
            raise ValueError(
                f"bone slot {i} = {s} is outside the 10-bit range "
                "the engine reads (0..1023)"
            )
    b20_lo = (
        (slots[0] & 0x3FF)
        | ((slots[1] & 0x3FF) << 10)
        | ((slots[2] & 0x3FF) << 20)
    )
    b20_hi = (
        (slots[3] & 0x3FF)
        | ((slots[4] & 0x3FF) << 10)
        | ((slots[5] & 0x3FF) << 20)
    )
    struct.pack_into("<II", rec, 20, b20_lo, b20_hi)

    # Slots 6-7: f16 such that ``int(f16 + 0.5)`` recovers the slot.
    # ``float(int_value)`` is exactly representable in f16 for
    # 0..1023 (f16 has 11-bit mantissa), so the round-trip is exact.
    struct.pack_into(
        "<ee", rec, 12, float(slots[6]), float(slots[7]),
    )

    # 8 weights at bytes 28-35.
    clamped = bytes(max(0, min(255, int(w))) for w in weights_u8)
    rec[28:36] = clamped


def _quantize_weights_to_u8(weights: list[float]) -> list[int]:
    """Quantize a list of float weights (each in [0, 1]) into 8 u8
    values that sum to 255 (the engine's normalisation invariant).

    Strict procedure:
      1. Pad / truncate to length 8.
      2. Sum-normalise to 1.0 (refuse if input sum <= 0).
      3. Multiply by 255 and round to nearest int.
      4. Distribute residual ±1 to the largest-weight slot so the
         u8 sum equals exactly 255 even after rounding (engine's
         streamout shader divides each by 255 — sums that differ
         from 1.0 cause subtle bone-influence drift).
    """
    if len(weights) > 8:
        weights = weights[:8]
    while len(weights) < 8:
        weights.append(0.0)

    total = sum(max(0.0, w) for w in weights)
    if total <= 0.0:
        # No weights at all — caller should have skipped this vertex.
        # Returning all-zeros lets the rec end up with bone byte 0
        # weighted at 0/255 = 0 (vertex contributes nothing through
        # this slot), which is the valid "unweighted" pattern the
        # engine accepts.
        return [0] * 8
    norm = [max(0.0, w) / total for w in weights]
    quant = [int(round(w * 255.0)) for w in norm]
    quant = [max(0, min(255, q)) for q in quant]
    diff = 255 - sum(quant)
    if diff != 0:
        # Walk slots in weight-descending order and adjust until
        # the sum matches. Strict 1+1: each ±1 adjustment touches
        # the largest-weight slot first so the perceptible weight
        # ratio matches the input as closely as possible.
        order = sorted(range(8), key=lambda i: -norm[i])
        idx = 0
        step = 1 if diff > 0 else -1
        while diff != 0:
            slot = order[idx % 8]
            new_val = quant[slot] + step
            if 0 <= new_val <= 255:
                quant[slot] = new_val
                diff -= step
            idx += 1
            if idx > 64:
                # Defensive: we've cycled 8x and still can't
                # balance — caller's weights were degenerate.
                break
    return quant


def _build_pab_to_slot_for_submesh(
    sm,
    pac_bytes: bytes,
) -> dict[int, int]:
    """Compute the per-submesh PAB-index → raw-slot inverse map.

    This is the strict join between the FBX-side (which uses PAB
    indices, named via ``skeleton.bones[i].name``) and the PAC-side
    (which writes raw 10-bit slots into the vertex bytes). For each
    vertex of ``sm`` we:

      1. Decode the donor PAC record's 8 raw slots + 8 raw u8
         weights via :func:`_decode_donor_skin`.
      2. Filter to the non-zero-weight (slot, weight) pairs — the
         parser drops zero-weight bones, so this matches the
         length of ``sm.bone_indices[vi]`` exactly.
      3. Pair each non-zero (slot, weight) with the corresponding
         PAB index from ``sm.bone_indices[vi]`` (must be in PAB
         space — caller is responsible for running
         :func:`derive_skin_slot_to_pab_geometric` first).
      4. Record the mapping with first-seen-wins semantics so the
         result is deterministic across vertex order.

    Returns ``{pab_index: raw_slot}``. PAB indices NOT seen in any
    donor record are absent from the map; the rebuilder treats
    that as strict refusal — there is no slot to write for those
    PABs without inventing one (which would corrupt the engine's
    skinning indirection).

    ``sm.bone_indices`` MUST be populated and ``sm.source_vertex_offsets``
    MUST point at valid donor records inside ``pac_bytes``. Any
    vertex whose offset is out of range or whose record is too
    short to decode skin from is silently skipped — those vertices
    don't contribute to the inverse, but other vertices in the
    same submesh still do.
    """
    inverse: dict[int, int] = {}
    stride = getattr(sm, "source_vertex_stride", 0) or 0
    if stride < 36:
        # Submesh's PAC records are too short to carry the verified
        # 8-bone skinning layout. Caller treats empty inverse as
        # "no skin write-back possible for this submesh".
        return inverse
    offsets = getattr(sm, "source_vertex_offsets", []) or []
    bone_indices = getattr(sm, "bone_indices", []) or []
    for vi, rec_off in enumerate(offsets):
        if vi >= len(bone_indices):
            break
        if rec_off < 0 or rec_off + stride > len(pac_bytes):
            continue
        rec = pac_bytes[rec_off:rec_off + stride]
        try:
            slots, weights = _decode_donor_skin(rec)
        except ValueError:
            continue
        non_zero = [
            (s, w) for s, w in zip(slots, weights) if w > 0
        ]
        pab_indices = bone_indices[vi]
        for (slot, _w), pab_idx in zip(non_zero, pab_indices):
            inverse.setdefault(int(pab_idx), int(slot))
    return inverse


def _pack_pac_normal(normal: tuple[float, float, float], existing_packed: int = 0) -> int:
    """Pack a float normal into the PAC vertex-record's u32 at byte +16.

    Layout — verified May 2026 by disassembling the shader cache shader
    ``shader/skinnedmeshstreamout.hlsl`` entry ``CSMainSkinnedMeshStreamOutVertexData``
    via ``dxc -dumpbin`` on the extracted DXBC payload from
    ``shadercache__/*_3964b6b0_*.padxil``. The vertex shader reads:

        bits 10-19  — nx, decoded as ``(value / 511.5) - 1.0``
        bits 20-29  — ny, decoded the same way
        bit  30     — tested by ``packed & 0x40000000``; if non-zero, nz
                      is negated. nz magnitude is reconstructed via
                      ``sqrt(max(0, 1 - nx² - ny²))``.

    The shader does NOT read bits 0-9 or bit 31 of this u32 in the
    streamout pass. They may carry data consumed by other shaders
    (parser comments hint at "normal/tangent auxiliary"); we preserve
    them verbatim from the donor record.

    The DXIL evidence (line numbers from the disassembled .ll IR):
        %124 = lshr i32 %79, 10                  ; >> 10
        %125 = and  i32 %124, 1023               ; mask 0x3FF
        %126 = uitofp i32 %125 to float
        %127 = fmul  fast float %126, 0x3F60040100000000  ; * (1/511.5)
        %128 = fadd  fast float %127, -1.000000e+00       ; - 1.0     → nx
        ; (same pattern for ny via shifts of 20)
        %134 = fmul  fast float %128, %128                ; nx²
        %135 = fsub  fast float 1.000000e+00, %134
        %136 = fmul  fast float %133, %133                ; ny²
        %137 = fsub  fast float %135, %136                ; 1 - nx² - ny²
        %138 = call  float @dx.op.binary.f32(i32 35, 0.0, %137)  ; max(0, _)
        %139 = call  float @dx.op.unary.f32 (i32 24, %138)       ; sqrt(_)
        %140 = and   i32 %79, 1073741824                  ; bit 30
        %141 = icmp  ne i32 %140, 0
        %142 = select i1 %141, float -1.000000e+00, float 1.000000e+00
        %143 = fmul  fast float %139, %142                ; nz

    History
    -------
    Pre-May-2026 implementations encoded ``_enc(nz)`` into bits 0-9 and
    preserved bits 30-31 from the donor. The encoder/decoder pair in
    :func:`core.mesh_parser._decode_pac_normal` mirrored that legacy
    layout (it still does — that decoder needs the matching fix). The
    ENGINE never read bits 0-9 or bit 31 for normal reconstruction, so
    those legacy writes had no in-game effect; the donor's bit 30 (the
    only bit the engine reads for nz sign) was carried verbatim into
    every new vertex via spatial-hash donor cloning, flipping ~half the
    surface's normals on topology-changing rebuilds (the "rainbow on
    forehead" / "white-with-sparks" lighting artefact).
    """

    def _enc(value: float) -> int:
        value = max(-1.0, min(1.0, value))
        return max(0, min(1023, round((value + 1.0) * 511.5)))

    nx, ny, nz = normal
    # Encode nx into bits 10-19, ny into bits 20-29.
    packed = (_enc(nx) << 10) | (_enc(ny) << 20)
    # Compute bit 30 from the NEW geometry's nz sign.
    if nz < 0.0:
        packed |= 0x40000000
    # Preserve donor's bits 0-9 and bit 31 (engine doesn't read them
    # for normals; semantic still TBD).
    return packed | (existing_packed & 0x800003FF)


def _compute_mesh_tangents(
    vertices: list[tuple[float, float, float]],
    uvs: list[tuple[float, float]],
    normals: list[tuple[float, float, float]],
    faces: list[tuple[int, int, int]],
) -> tuple[
    list[tuple[float, float, float]],
    list[float],
    list[bool],
]:
    """Compute per-vertex unit tangents + bitangent-handedness signs + validity flags.

    Returns ``(tangents, bsigns, valid)`` where ``valid[i]`` is True when
    the tangent at vertex ``i`` was derived from non-degenerate UV
    gradients, and False when it came from the N-orthogonal fallback
    used for vertices whose only contributing faces had degenerate or
    cancelling UVs. Callers should NOT overwrite donor tangent bytes
    when ``valid[i]`` is False — the donor record holds the engine's
    baked tangent for that vertex, which is more trustworthy than any
    arbitrary N-perpendicular axis we could pick at import time.

    Standard MikkTSpace-style algorithm (Blender / Unity / Unreal / Substance):

      For each triangle, derive (T, B) from edge vectors and UV gradients:
          T = (e1 * dv2 - e2 * dv1) / det
          B = (e2 * du1 - e1 * du2) / det
      Accumulate per vertex (uniform).
      Per vertex: orthogonalize T against N, normalize, derive handedness
      from sign(dot(cross(N, T), B)).

    Strict mode — NO FALLBACK. Any of the following raises ``ValueError``:
      * UV count != vertex count
      * normal count != vertex count
      * face references out-of-range vertex
      * UV triangle degenerate (det ≈ 0)
      * orthogonalized tangent is zero-length

    Returns (tangents_list, bsigns_list) where each list has one entry per
    vertex; tangents are unit vectors; bsigns are +1.0 or -1.0.
    """
    n = len(vertices)
    if len(uvs) != n:
        raise ValueError(
            f"_compute_mesh_tangents: UV count mismatch — got {len(uvs)} UVs "
            f"but {n} vertices. The OBJ/FBX import must carry per-vertex UVs "
            "for tangent computation. Re-export from Blender with 'Include "
            "UVs' checked."
        )
    if len(normals) != n:
        raise ValueError(
            f"_compute_mesh_tangents: normal count mismatch — got {len(normals)} "
            f"normals but {n} vertices."
        )

    tan_accum = [[0.0, 0.0, 0.0] for _ in range(n)]
    bitan_accum = [[0.0, 0.0, 0.0] for _ in range(n)]
    contrib = [0] * n   # how many non-degenerate faces touched each vertex

    n_degenerate = 0
    for fi, (i0, i1, i2) in enumerate(faces):
        if min(i0, i1, i2) < 0 or max(i0, i1, i2) >= n:
            raise ValueError(
                f"_compute_mesh_tangents: face {fi} indices ({i0}, {i1}, {i2}) "
                f"out of range [0, {n})."
            )

        p0 = vertices[i0]; p1 = vertices[i1]; p2 = vertices[i2]
        uv0 = uvs[i0];     uv1 = uvs[i1];     uv2 = uvs[i2]

        e1 = (p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2])
        e2 = (p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2])
        du1 = uv1[0] - uv0[0]; dv1 = uv1[1] - uv0[1]
        du2 = uv2[0] - uv0[0]; dv2 = uv2[1] - uv0[1]

        det = du1 * dv2 - du2 * dv1
        if abs(det) < 1e-10:
            # Standard MikkTSpace behavior: a triangle whose UVs are
            # colinear contributes no information about tangent space, so
            # skip it during accumulation. This is NOT a silent fallback —
            # it's the published reference algorithm. Game source meshes
            # routinely contain UV-collapsed faces (shadow planes, hidden
            # stubs); they get their tangents from the OTHER faces sharing
            # those vertices. We fail loudly at the per-vertex level below
            # if a vertex ends up with zero contribution.
            n_degenerate += 1
            continue
        inv = 1.0 / det

        tx = (e1[0] * dv2 - e2[0] * dv1) * inv
        ty = (e1[1] * dv2 - e2[1] * dv1) * inv
        tz = (e1[2] * dv2 - e2[2] * dv1) * inv
        bx = (e2[0] * du1 - e1[0] * du2) * inv
        by = (e2[1] * du1 - e1[1] * du2) * inv
        bz = (e2[2] * du1 - e1[2] * du2) * inv

        for vi in (i0, i1, i2):
            tan_accum[vi][0] += tx
            tan_accum[vi][1] += ty
            tan_accum[vi][2] += tz
            bitan_accum[vi][0] += bx
            bitan_accum[vi][1] += by
            bitan_accum[vi][2] += bz
            contrib[vi] += 1

    def _n_perp(N: tuple[float, float, float]) -> tuple[float, float, float]:
        """Pick a unit vector orthogonal to N — used when the UV-derived
        tangent is unrecoverable. This is the same construction
        Blender's mesh_calc_tangents and the MikkTSpace reference impl
        use in the degenerate corner case.
        """
        ax = abs(N[0]); ay = abs(N[1]); az = abs(N[2])
        if ax <= ay and ax <= az:
            ref = (1.0, 0.0, 0.0)
        elif ay <= az:
            ref = (0.0, 1.0, 0.0)
        else:
            ref = (0.0, 0.0, 1.0)
        dot_RN = ref[0] * N[0] + ref[1] * N[1] + ref[2] * N[2]
        ox = ref[0] - dot_RN * N[0]
        oy = ref[1] - dot_RN * N[1]
        oz = ref[2] - dot_RN * N[2]
        L = math.sqrt(ox * ox + oy * oy + oz * oz)
        if L < 1e-10:
            raise ValueError(
                "_compute_mesh_tangents: degenerate normal "
                f"({N[0]:.3e}, {N[1]:.3e}, {N[2]:.3e}); cannot derive tangent."
            )
        return (ox / L, oy / L, oz / L)

    tangents: list[tuple[float, float, float]] = []
    bsigns:   list[float] = []
    valid:    list[bool] = []
    n_isolated = 0   # vertices with NO non-degenerate face
    n_cancelled = 0  # vertices where contributing faces cancelled
    for vi in range(n):
        N = normals[vi]
        T = tan_accum[vi]
        B = bitan_accum[vi]

        if contrib[vi] == 0:
            # Every face touching this vertex had degenerate UVs. No UV
            # gradient exists; mark INVALID so caller preserves donor
            # bytes. We still return a sane unit vector via N-perp so
            # that callers without a donor (e.g. tests) get something
            # usable.
            n_isolated += 1
            tangents.append(_n_perp(N))
            bsigns.append(1.0)
            valid.append(False)
            continue

        # Orthogonalize T against N: T = T - (T·N) * N
        dot_TN = T[0] * N[0] + T[1] * N[1] + T[2] * N[2]
        ox = T[0] - dot_TN * N[0]
        oy = T[1] - dot_TN * N[1]
        oz = T[2] - dot_TN * N[2]

        L = math.sqrt(ox * ox + oy * oy + oz * oz)
        if L < 1e-10:
            # Faces contributed but their tangent directions cancelled
            # (typical UV-mirror seam). Mark INVALID so caller preserves
            # donor bytes. Bitangent sign still derivable from the
            # accumulated B vs N-perp tangent.
            n_cancelled += 1
            Tx, Ty, Tz = _n_perp(N)
            cx = N[1] * Tz - N[2] * Ty
            cy = N[2] * Tx - N[0] * Tz
            cz = N[0] * Ty - N[1] * Tx
            d  = cx * B[0] + cy * B[1] + cz * B[2]
            tangents.append((Tx, Ty, Tz))
            bsigns.append(-1.0 if d < 0.0 else 1.0)
            valid.append(False)
            continue

        Tx = ox / L; Ty = oy / L; Tz = oz / L

        # Handedness from sign(dot(cross(N, T), B))
        cx = N[1] * Tz - N[2] * Ty
        cy = N[2] * Tx - N[0] * Tz
        cz = N[0] * Ty - N[1] * Tx
        d  = cx * B[0] + cy * B[1] + cz * B[2]
        bsign = -1.0 if d < 0.0 else 1.0

        tangents.append((Tx, Ty, Tz))
        bsigns.append(bsign)
        valid.append(True)

    if n_degenerate > 0 or n_isolated > 0 or n_cancelled > 0:
        logger.debug(
            "_compute_mesh_tangents: %d/%d faces had degenerate UVs; "
            "%d/%d vertices had no UV-valid faces; %d/%d had cancelling "
            "tangents (those vertices marked invalid; caller should keep "
            "donor tangent bytes).",
            n_degenerate, len(faces), n_isolated, n, n_cancelled, n,
        )

    return tangents, bsigns, valid


def _pack_pac_tangent_into_record(
    rec: bytearray,
    tangent: tuple[float, float, float],
    handedness_sign: float,
) -> None:
    """Encode a unit tangent into bytes 6-7 + bits 0-9 + bit 31 of byte +16.

    Layout — verified May 2026 from shader DXIL of
    ``CSMainSkinnedMeshStreamOutVertexData`` and ``RaytracingComputeSkinning2``.
    Full evidence (DXIL line refs, byte-exact round-trip on real game data)
    in ``test_only/research/PAC_VERTEX_RECORD_DECODED.md``.

      bytes 6-7  — signed i16. Sign = sign(Tz).
                   Magnitude = round(16383.75 * (Tx + 1.0)).
      bits 0-9   of [16-19] — Ty as 10-bit unsigned,
                   round((Ty + 1.0) * 511.5).
      bit 31     of [16-19] — handedness flag (1 if bsign < 0).

    Call ``_pack_pac_normal`` FIRST to write the new normal's bits
    10-19/20-29/30; this function then overlays the tangent bits without
    disturbing the normal.

    Strict mode — raises ValueError if tangent isn't unit length within
    ±0.05 (caller must normalize). No fallback.
    """
    if len(rec) < 20:
        raise ValueError(
            f"_pack_pac_tangent_into_record: vertex record too small "
            f"({len(rec)} bytes, need ≥ 20)."
        )

    Tx, Ty, Tz = tangent
    L = math.sqrt(Tx * Tx + Ty * Ty + Tz * Tz)
    if abs(L - 1.0) > 0.05:
        raise ValueError(
            f"_pack_pac_tangent_into_record: tangent |T| = {L:.4f} is not "
            "unit length (tolerance ±0.05). Normalize before calling."
        )

    mag = max(0, min(32767, round(16383.75 * (Tx + 1.0))))
    bytes_6_7 = -mag if Tz < 0.0 else mag
    struct.pack_into("<h", rec, 6, bytes_6_7)

    ty_enc = max(0, min(1023, round((Ty + 1.0) * 511.5)))
    bit_31_mask = 0x80000000 if handedness_sign < 0.0 else 0

    existing = struct.unpack_from("<I", rec, 16)[0]
    cleared = existing & 0x7FFFFC00          # clear bits 0-9 + bit 31
    new_packed = cleared | ty_enc | bit_31_mask
    struct.pack_into("<I", rec, 16, new_packed)


def _choose_pac_donor_indices(orig_sm: SubMesh, new_sm: SubMesh) -> list[int]:
    """Choose the closest original PAC vertex record to clone for each new vertex.

    PAC vertex records carry bone indices, bone weights, packed normals and a
    handful of engine-internal bytes that the OBJ round-trip cannot preserve.
    For every new vertex we need a *donor* — the original vertex whose record
    we clone before overwriting position/UV/normal. Exact position matches win
    when available (the common "user only moved a few verts" case); otherwise
    we fall back to the spatially nearest donor.

    Algorithm:
      1. Exact lookup via a dict keyed on positions rounded to 1e-5.
      2. For misses, a uniform spatial hash returns O(1) candidates on average
         and guarantees correctness by expanding the search shell until a
         donor is found. This replaces the previous O(n²) linear scan, which
         was tolerable for 500-vert weapons but catastrophic on 20k-vert
         character bodies.
    """
    n_orig = len(orig_sm.vertices)
    n_new = len(new_sm.vertices)
    if n_orig == 0:
        return [0] * n_new

    exact_map: dict[tuple[int, int, int], int] = {}
    for orig_idx, pos in enumerate(orig_sm.vertices):
        key = (round(pos[0] * 100000), round(pos[1] * 100000), round(pos[2] * 100000))
        # First writer wins — matches previous exact_map[...].append + [0] behaviour.
        exact_map.setdefault(key, orig_idx)

    # Short-circuit the linear scan for very small meshes where the spatial
    # index overhead isn't worth it.
    if n_orig <= 64:
        donor_indices: list[int] = []
        for new_pos in new_sm.vertices:
            key = (round(new_pos[0] * 100000), round(new_pos[1] * 100000), round(new_pos[2] * 100000))
            exact = exact_map.get(key)
            if exact is not None:
                donor_indices.append(exact)
                continue
            best_idx = 0
            best_dist = float("inf")
            for orig_idx in range(n_orig):
                ox, oy, oz = orig_sm.vertices[orig_idx]
                dx = new_pos[0] - ox
                dy = new_pos[1] - oy
                dz = new_pos[2] - oz
                dist_sq = dx * dx + dy * dy + dz * dz
                if dist_sq < best_dist:
                    best_dist = dist_sq
                    best_idx = orig_idx
            donor_indices.append(best_idx)
        return donor_indices

    # Build a uniform spatial hash. Cell size is set so the mean occupancy is
    # around the cube-root of the vertex count, which balances lookup cost
    # (cells per shell) against candidate cost (verts per cell).
    xs = [v[0] for v in orig_sm.vertices]
    ys = [v[1] for v in orig_sm.vertices]
    zs = [v[2] for v in orig_sm.vertices]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    min_z, max_z = min(zs), max(zs)

    extent = max(max_x - min_x, max_y - min_y, max_z - min_z, 1e-6)
    # Target ~8 verts per cell at equilibrium.
    target_cells_per_axis = max(2, int(round((n_orig / 8.0) ** (1.0 / 3.0))))
    cell_size = extent / target_cells_per_axis
    if cell_size < 1e-6:
        cell_size = 1e-6
    inv_cell = 1.0 / cell_size

    def _cell_key(x: float, y: float, z: float) -> tuple[int, int, int]:
        return (
            int((x - min_x) * inv_cell),
            int((y - min_y) * inv_cell),
            int((z - min_z) * inv_cell),
        )

    grid: dict[tuple[int, int, int], list[int]] = {}
    for orig_idx in range(n_orig):
        ox, oy, oz = orig_sm.vertices[orig_idx]
        grid.setdefault(_cell_key(ox, oy, oz), []).append(orig_idx)

    donor_indices = []
    max_shell = target_cells_per_axis + 1  # guarantees coverage of the whole mesh

    for new_pos in new_sm.vertices:
        key = (round(new_pos[0] * 100000), round(new_pos[1] * 100000), round(new_pos[2] * 100000))
        exact = exact_map.get(key)
        if exact is not None:
            donor_indices.append(exact)
            continue

        cx, cy, cz = _cell_key(new_pos[0], new_pos[1], new_pos[2])
        best_idx = 0
        best_dist = float("inf")

        shell = 0
        while shell <= max_shell:
            # Scan all cells in the current shell (surface of a cube of radius `shell`
            # centred on the query cell). Shell 0 is just the query cell itself.
            lo, hi = cx - shell, cx + shell
            for ix in range(lo, hi + 1):
                for iy in range(cy - shell, cy + shell + 1):
                    for iz in range(cz - shell, cz + shell + 1):
                        # Only evaluate the cube's surface on shells > 0 to avoid
                        # re-scanning interior cells from earlier shells.
                        if shell > 0 and (
                            lo < ix < hi
                            and cy - shell < iy < cy + shell
                            and cz - shell < iz < cz + shell
                        ):
                            continue
                        bucket = grid.get((ix, iy, iz))
                        if not bucket:
                            continue
                        for orig_idx in bucket:
                            ox, oy, oz = orig_sm.vertices[orig_idx]
                            dx = new_pos[0] - ox
                            dy = new_pos[1] - oy
                            dz = new_pos[2] - oz
                            dist_sq = dx * dx + dy * dy + dz * dz
                            if dist_sq < best_dist:
                                best_dist = dist_sq
                                best_idx = orig_idx

            # Termination test. We just completed shell `s`. The nearest point in
            # any cell at chebyshev distance `s+1` sits at euclidean distance
            # at least `s * cell_size` from the query (geometry of unit cubes).
            # So we can stop once best_dist < (s * cell_size)^2.
            #
            # Note: for s == 0 this condition is `best_dist < 0`, which never
            # holds — we always advance to shell 1 even when shell 0 had a hit,
            # because a chebyshev-1 cell can still contain an arbitrarily close
            # point when the query sits near its own cell's boundary.
            if best_dist < float("inf") and (shell * cell_size) ** 2 > best_dist:
                break
            shell += 1

        donor_indices.append(best_idx)

    return donor_indices


def _pac_needs_full_rebuild(original_mesh: ParsedMesh, working_mesh: ParsedMesh) -> bool:
    """Return True when the PAC import changed topology or needs a fresh serializer."""
    if len(original_mesh.submeshes) != len(working_mesh.submeshes):
        return True

    for orig_sm, new_sm in zip(original_mesh.submeshes, working_mesh.submeshes):
        if len(orig_sm.vertices) != len(new_sm.vertices):
            return True
        if len(orig_sm.faces) != len(new_sm.faces):
            return True
        if orig_sm.source_vertex_stride < 12:
            return True
        if len(orig_sm.source_vertex_offsets) != len(orig_sm.vertices):
            return True
        if orig_sm.source_descriptor_offset < 0:
            return True
    return False


def _resolve_skin_write_context(
    working_mesh: ParsedMesh,
    sm_idx: int,
    sm_name: str,
):
    """Pull the per-submesh ``pab_to_slot`` map from the imported
    mesh's stashed sidecar.

    Returns ``(pab_to_slot, donor_stride_known)``:
      * ``pab_to_slot`` — ``dict[int, int]`` populated when the
        sidecar carries the export-time mapping for this submesh,
        empty otherwise (which means strict refusal — the rebuilder
        will leave donor skin bytes alone for any vertex whose new
        bone data we can't cross-reference).
      * ``donor_stride_known`` — True when the sidecar was produced
        by an export that knows the 8-bone vertex layout. Older
        exports without this map produce ``False`` and skin
        write-back is skipped strictly (donor preserved verbatim).

    Strict 1+1: this function NEVER falls back to nearest-position
    or palette guessing. It only returns what the export side
    explicitly recorded.
    """
    sidecar = getattr(working_mesh, "_cfmeta_sidecar", None)
    if not isinstance(sidecar, dict):
        return {}, False
    submeshes = sidecar.get("submeshes") or []
    # Match by index first (the canonical export order), but also by
    # name as a defensive cross-check so inadvertent reordering at
    # import time doesn't silently mis-assign the map.
    candidate = None
    if 0 <= sm_idx < len(submeshes):
        c = submeshes[sm_idx]
        if isinstance(c, dict):
            candidate = c
    if candidate is None or candidate.get("name") != sm_name:
        for c in submeshes:
            if isinstance(c, dict) and c.get("name") == sm_name:
                candidate = c
                break
    if candidate is None:
        return {}, False
    raw = candidate.get("pab_to_slot")
    if not isinstance(raw, dict):
        return {}, False
    pab_to_slot: dict[int, int] = {}
    try:
        for k, v in raw.items():
            pab_to_slot[int(k)] = int(v)
    except (TypeError, ValueError):
        return {}, False
    return pab_to_slot, True


def _check_strict_skin_writeback(
    skin_pab_to_slot: dict[int, int],
    orig_stride: int,
    new_has_skin: bool,
    sm_idx: int,
    sm_name: str,
) -> bool:
    """Decide whether the rebuilder should write skin or refuse.

    Strict 1+1 contract:

      * Sidecar HAS ``pab_to_slot`` AND donor stride ≥ 36 AND new
        submesh HAS bone_indices → write skin (returns True).
      * Sidecar HAS ``pab_to_slot`` AND donor stride ≥ 36 AND new
        submesh has NO bone_indices → REFUSE with ``ValueError``.
        This is the case where the user imported a Forge-exported
        FBX (sidecar v2 means we wrote it) but stripped vertex
        groups from the submesh. We CANNOT silently preserve donor
        weights — that hides the deletion. The user is told to
        either re-paint or remove the submesh.
      * Sidecar has NO ``pab_to_slot`` (OBJ import, third-party
        FBX, missing sidecar) → preserve donor (returns False).
        Topology-only contract; no strict source of truth for skin.
      * Donor stride < 36 (rigid prop) → no skin to write
        (returns False).

    Centralising the decision here means both rebuild branches
    (in-place and full-rebuild) take the exact same strict path,
    and the refusal is locked in by unit tests.
    """
    have_target = bool(skin_pab_to_slot) and orig_stride >= 36
    if have_target and not new_has_skin:
        raise ValueError(
            f"PAC build failed: submesh '{sm_name}' (#{sm_idx}) "
            f"in the imported FBX has no vertex groups, but the "
            f"original PAC submesh is skin-bound (stride "
            f"{orig_stride} bytes with a "
            f"{len(skin_pab_to_slot)}-entry palette). The strict "
            f"rebuilder refuses to silently preserve the donor's "
            f"weights when an export-time skin map exists — that "
            f"would hide vertex-group deletions. Either re-paint "
            f"vertex groups in Blender (and re-export the FBX), "
            f"or remove this submesh from the PAC entirely."
        )
    return have_target and new_has_skin


def _bone_pab_indices_for_vertex(
    new_sm,
    vi: int,
    pab_to_slot: dict[int, int],
    sidecar_skeleton_bones: list[str],
) -> tuple[list[int], list[float]]:
    """Resolve a single vertex's (PAB indices, weights) for skin
    write-back, using the strict precedence:

      1. ``new_sm.bone_names[vi]`` paired with ``sidecar_skeleton_bones``
         — the FBX-import path populates names from cluster Model
         names, which the sidecar ties to PAB indices.
      2. ``new_sm.bone_indices[vi]`` directly — when the import path
         already produced PAB indices (OBJ from sidecar, or FBX with
         a sidecar that carried ``skeleton_bones`` so the importer
         resolved names eagerly).

    Returns the dropped pairs ``(pab_indices, weights)``. Pairs whose
    PAB index isn't in ``pab_to_slot`` are filtered out — they're
    bones the original PAC submesh never used a slot for, and we
    refuse to invent one. The caller decides whether to keep the
    donor's skin bytes or raise.

    Returns ``([], [])`` when the vertex carries no skin data — the
    caller treats that as "no edit, donor is correct".
    """
    bi = (
        new_sm.bone_indices[vi]
        if vi < len(new_sm.bone_indices)
        else ()
    )
    bw = (
        new_sm.bone_weights[vi]
        if vi < len(new_sm.bone_weights)
        else ()
    )
    if not bi or not bw:
        return [], []

    name_to_pab = {
        nm: i for i, nm in enumerate(sidecar_skeleton_bones) if nm
    }

    bn = (
        new_sm.bone_names[vi]
        if vi < len(getattr(new_sm, "bone_names", []) or [])
        else ()
    )

    pab_indices: list[int] = []
    weights: list[float] = []
    # Length of the source tuples may differ if names came from a
    # different cluster ordering than indices (rare, but defensive).
    n = max(len(bi), len(bn) if bn else 0)
    for k in range(n):
        # Names take precedence — they're the strict 1+1 source
        # straight from the FBX cluster's bone Model name.
        name = bn[k] if k < len(bn) else ""
        idx_via_name = name_to_pab.get(name) if name else None
        if idx_via_name is not None:
            pab = idx_via_name
        elif k < len(bi):
            pab = int(bi[k])
        else:
            continue
        if pab not in pab_to_slot:
            # Bone the original PAC submesh never bound — refuse to
            # invent a slot. Drop this contribution; the rebuild
            # will renormalise the remaining weights.
            continue
        w = float(bw[k]) if k < len(bw) else 0.0
        if w <= 0.0:
            continue
        pab_indices.append(pab)
        weights.append(w)
    return pab_indices, weights


def _apply_skin_write_back(
    rec: bytearray,
    new_sm,
    vi: int,
    pab_to_slot: dict[int, int],
    sidecar_skeleton_bones: list[str],
    *,
    sm_idx: int,
    sm_name: str,
) -> bool:
    """Strict skin write-back for a single rebuilt vertex.

    Returns True when the donor's 8-bone skin bytes (slots at
    bytes 12-15 + 20-27, weights at bytes 28-35) were overwritten
    with the user's edited skin data. Returns False when nothing
    was written — caller leaves the donor's bytes alone.

    Strict refusal:
      * Empty bone data on the new vertex → returns False
        (correct: no edit, donor preserved).
      * ``new_sm`` doesn't satisfy length invariants → ``ValueError``
        (encode rejection — never silently dropped).
      * No PAB matched ``pab_to_slot`` for this vertex → returns
        False; the caller treats that as "no slot for any new
        bone, donor preserved" (this is the only path that can
        leave a vertex unwritten while ``pab_to_slot`` is
        populated, and it's a verifiable strict outcome — the
        new bones simply don't exist in the original PAC).
    """
    pab_indices, weights = _bone_pab_indices_for_vertex(
        new_sm, vi, pab_to_slot, sidecar_skeleton_bones,
    )
    if not pab_indices:
        return False

    # Slot tuple — 8 entries. Pad with 0 (the engine treats
    # weight=0 entries as inactive regardless of slot value).
    slots = [pab_to_slot[p] for p in pab_indices[:8]]
    while len(slots) < 8:
        slots.append(0)

    weight_quant = _quantize_weights_to_u8(
        list(weights[:8]) + [0.0] * max(0, 8 - len(weights)),
    )
    try:
        _pack_pac_skin_into_record(rec, slots, weight_quant)
    except ValueError as exc:
        raise ValueError(
            f"skin encode rejected vertex #{vi} of submesh "
            f"'{sm_name}' (#{sm_idx}). Detail: {exc}"
        ) from exc
    return True


def _build_pac_in_place(
    original_mesh: ParsedMesh,
    working_mesh: ParsedMesh,
    original_data: bytes,
) -> bytes:
    """Patch a PAC binary in place while preserving its existing layout."""
    result = bytearray(original_data)
    vertex_updates: dict[int, bytes] = {}
    index_updates: dict[int, bytes] = {}

    # Top-level skeleton bone-name list shared across submeshes.
    sidecar = getattr(working_mesh, "_cfmeta_sidecar", None) or {}
    skeleton_bones_global: list[str] = []
    if isinstance(sidecar, dict):
        sb = sidecar.get("skeleton_bones") or []
        if isinstance(sb, list):
            skeleton_bones_global = [str(x) for x in sb]

    for sm_idx, (orig_sm, new_sm) in enumerate(zip(original_mesh.submeshes, working_mesh.submeshes)):
        if len(orig_sm.vertices) != len(new_sm.vertices):
            diff = len(new_sm.vertices) - len(orig_sm.vertices)
            raise ValueError(
                f"PAC submesh {sm_idx} ('{new_sm.name}') changed vertex "
                f"count ({len(orig_sm.vertices)} -> "
                f"{len(new_sm.vertices)}, {'+' if diff > 0 else ''}"
                f"{diff}). PAC vertex slots are donor-locked; the "
                f"rebuild path can only patch in-place. Common causes:\n"
                f"  - Edited topology in Blender (added/removed verts).\n"
                f"  - Custom Split Normals (CSN) at sharp edges: "
                f"Blender's FBX exporter splits a vertex for each "
                f"unique (UV, normal) corner pair, but PAC has only "
                f"per-vertex normal storage. Remove CSN (Object Data "
                f"Properties > Geometry Data > Clear Custom Split "
                f"Normals Data) or weld duplicate corners before "
                f"re-export.\n"
                f"  - Different UV seams from the donor: same "
                f"splitting mechanism as CSN; verify the UV map "
                f"matches the original."
            )
        if len(orig_sm.faces) != len(new_sm.faces):
            raise ValueError(
                f"PAC submesh {sm_idx} changed face count "
                f"({len(orig_sm.faces)} -> {len(new_sm.faces)}). "
                "Keep the same topology when importing OBJ for PAC meshes."
            )
        if orig_sm.source_vertex_stride < 12:
            raise ValueError(
                f"PAC submesh {sm_idx} is missing source vertex metadata and cannot be rebuilt safely."
            )

        bmin, bmax = _compute_bbox(new_sm.vertices)
        extent = tuple(bmax[i] - bmin[i] for i in range(3))
        _patch_pac_descriptor_bounds(result, orig_sm.source_descriptor_offset, bmin, extent)

        new_uvs = new_sm.uvs if len(new_sm.uvs) == len(new_sm.vertices) else []
        new_normals = (
            new_sm.normals
            if len(new_sm.normals) == len(new_sm.vertices)
            else _compute_smooth_normals(new_sm.vertices, new_sm.faces)
        )
        clean_shading_records = bool(
            getattr(new_sm, "clean_donor_shading_records", False)
            or getattr(working_mesh, "clean_donor_shading_records", False)
        )
        # ── Skin write-back context ──
        # Decision logic centralised in ``_check_strict_skin_writeback``.
        # Refuses when sidecar provides a write-back map but FBX
        # dropped its vertex groups — see helper docstring.
        skin_pab_to_slot, _skin_known = _resolve_skin_write_context(
            working_mesh, sm_idx, new_sm.name,
        )
        write_skin_for_this_sm = _check_strict_skin_writeback(
            skin_pab_to_slot,
            orig_sm.source_vertex_stride,
            bool(getattr(new_sm, "bone_indices", None)),
            sm_idx, new_sm.name,
        )

        # STRICT MODE — compute MikkTSpace tangents for every vertex of the
        # submesh. Required for correct lighting/normal-mapping; failure
        # propagates as a hard error so we never silently leave the donor's
        # tangent in place when the new mesh's UVs need a different one.
        if not new_uvs:
            raise ValueError(
                f"PAC submesh {sm_idx} ('{new_sm.name}') has no UVs; cannot "
                "compute tangents. Re-export the OBJ/FBX with texture "
                "coordinates."
            )
        try:
            sm_tangents, sm_bsigns, sm_tan_valid = _compute_mesh_tangents(
                new_sm.vertices, new_uvs, new_normals, new_sm.faces
            )
        except ValueError as exc:
            raise ValueError(
                f"PAC build failed: cannot compute tangents for submesh "
                f"'{new_sm.name}' (#{sm_idx}). Detail: {exc}"
            ) from exc

        for vi, rec_off in enumerate(orig_sm.source_vertex_offsets):
            if rec_off < 0 or rec_off + orig_sm.source_vertex_stride > len(result):
                raise ValueError(
                    f"PAC vertex record {vi} for submesh {sm_idx} points outside the file."
                )

            rec = bytearray(result[rec_off:rec_off + orig_sm.source_vertex_stride])
            if clean_shading_records:
                if len(rec) >= 8:
                    struct.pack_into("<H", rec, 6, 0)
                if len(rec) >= 28:
                    rec[20:28] = b"\x00" * 8
            vx, vy, vz = new_sm.vertices[vi]
            struct.pack_into(
                "<HHH",
                rec,
                0,
                _quantize_pac_u16(vx, bmin[0], extent[0]),
                _quantize_pac_u16(vy, bmin[1], extent[1]),
                _quantize_pac_u16(vz, bmin[2], extent[2]),
            )

            if new_uvs:
                try:
                    struct.pack_into("<e", rec, 8, new_uvs[vi][0])
                    struct.pack_into("<e", rec, 10, new_uvs[vi][1])
                except (OverflowError, ValueError):
                    struct.pack_into("<e", rec, 8, 0.0)
                    struct.pack_into("<e", rec, 10, 0.0)

            if len(rec) >= 20:
                existing_normal = struct.unpack_from("<I", rec, 16)[0]
                struct.pack_into(
                    "<I",
                    rec,
                    16,
                    _pack_pac_normal(
                        new_normals[vi],
                        0 if clean_shading_records else existing_normal,
                    ),
                )
                # Encode our newly-computed tangent ONLY for vertices
                # whose tangent came from non-degenerate UVs. Vertices in
                # UV-collapsed regions keep the donor's existing tangent
                # bytes (engine-baked, trusted). STRICT MODE: any encoder
                # rejection on a "valid" tangent raises immediately.
                if sm_tan_valid[vi]:
                    try:
                        _pack_pac_tangent_into_record(
                            rec, sm_tangents[vi], sm_bsigns[vi]
                        )
                    except ValueError as exc:
                        raise ValueError(
                            f"PAC build failed: tangent encode rejected "
                            f"vertex #{vi} of submesh '{new_sm.name}' "
                            f"(#{sm_idx}). Detail: {exc}"
                        ) from exc

            # ── Strict skin write-back ──
            # Overwrite the donor's 8-bone slot+weight bytes with the
            # user's edited skin data when the sidecar provides the
            # PAB-to-slot map for this submesh. When it doesn't, the
            # donor's bytes are preserved verbatim (no fallback —
            # there's literally no strict source of truth for what
            # slot values to write without the export-time map).
            if write_skin_for_this_sm:
                _apply_skin_write_back(
                    rec, new_sm, vi,
                    skin_pab_to_slot,
                    skeleton_bones_global,
                    sm_idx=sm_idx, sm_name=new_sm.name,
                )

            payload = bytes(rec)
            prev = vertex_updates.get(rec_off)
            if prev is not None and prev != payload:
                raise ValueError(
                    "PAC import edited a shared vertex buffer inconsistently across submeshes. "
                    "Apply the same change to every linked PAC submesh before reimport."
                )
            vertex_updates[rec_off] = payload

        if orig_sm.source_index_offset >= 0:
            for fi, (a, b, c) in enumerate(new_sm.faces):
                if a >= len(new_sm.vertices) or b >= len(new_sm.vertices) or c >= len(new_sm.vertices):
                    raise ValueError(f"PAC face {fi} in submesh {sm_idx} references an out-of-range vertex.")
                face_off = orig_sm.source_index_offset + fi * 6
                if face_off + 6 > len(result):
                    raise ValueError(
                        f"PAC face record {fi} for submesh {sm_idx} points outside the file."
                    )
                payload = struct.pack("<HHH", a, b, c)
                prev = index_updates.get(face_off)
                if prev is not None and prev != payload:
                    raise ValueError(
                        "PAC import edited a shared index buffer inconsistently across submeshes."
                    )
                index_updates[face_off] = payload

    for rec_off, payload in vertex_updates.items():
        result[rec_off:rec_off + len(payload)] = payload
    for face_off, payload in index_updates.items():
        result[face_off:face_off + len(payload)] = payload

    logger.info(
        "Built PAC %s with in-place patching: %d submeshes, %d verts, %d faces",
        working_mesh.path,
        len(working_mesh.submeshes),
        sum(len(sm.vertices) for sm in working_mesh.submeshes),
        sum(len(sm.faces) for sm in working_mesh.submeshes),
    )
    return bytes(result)


def _build_pac_full_rebuild(
    original_mesh: ParsedMesh,
    working_mesh: ParsedMesh,
    original_data: bytes,
) -> bytes:
    """Rebuild PAC geometry sections from scratch for topology-changing imports."""
    # Top-level skeleton bone-name list (parallel to PAB indices) is
    # needed at per-vertex skin write-back time to map FBX cluster
    # names back to PAB indices. Kept here so both the prepare-loop
    # and the LOD-emit-loop can reach it without re-parsing the
    # sidecar twice.
    sidecar = getattr(working_mesh, "_cfmeta_sidecar", None) or {}
    skeleton_bones_global: list[str] = []
    if isinstance(sidecar, dict):
        sb = sidecar.get("skeleton_bones") or []
        if isinstance(sb, list):
            skeleton_bones_global = [str(x) for x in sb]

    sections = _parse_par_sections(original_data)
    sec_by_idx = {sec["index"]: sec for sec in sections}
    sec0 = sec_by_idx.get(0)
    if not sec0:
        raise ValueError("PAC section table is missing section 0.")

    n_lods = original_data[sec0["offset"] + 4] if sec0["size"] >= 5 else 0
    if n_lods <= 0 or n_lods > 10:
        raise ValueError(f"Invalid PAC LOD count: {n_lods}")

    descriptors = _find_pac_descriptors(original_data, sec0["offset"], sec0["size"], n_lods)
    if len(descriptors) < len(working_mesh.submeshes):
        raise ValueError("PAC descriptor count does not match the parsed submesh set.")

    sec0_data = bytearray(original_data[sec0["offset"]:sec0["offset"] + sec0["size"]])
    preserved_sections = {
        sec["index"]: original_data[sec["offset"]:sec["offset"] + sec["size"]]
        for sec in sections
        if sec["index"] > n_lods
    }

    prepared_submeshes = []
    for sm_idx, (orig_sm, new_sm, desc) in enumerate(zip(original_mesh.submeshes, working_mesh.submeshes, descriptors)):
        # ── Empty placeholder fast-path ──
        # When the user's OBJ doesn't include this submesh by name and
        # the merge step in ``_merge_partial_pac_import`` emitted an
        # empty placeholder (0 verts / 0 faces), there's no geometry
        # to upload and no tangents to compute. Patch the descriptor's
        # per-LOD vertex / index counts to zero so the runtime loader
        # treats this submesh as empty (renders nothing), and skip the
        # rest of the per-submesh prep — including the UV check and
        # MikkTSpace pass which would otherwise raise on the missing
        # UVs of the placeholder.
        if not new_sm.vertices and not new_sm.faces:
            rel_desc_off = desc.descriptor_offset - sec0["offset"]
            if rel_desc_off < 0 or rel_desc_off + 40 > len(sec0_data):
                raise ValueError(
                    f"PAC descriptor {sm_idx} points outside section 0."
                )
            vc_off = rel_desc_off + 40
            ic_off = vc_off + desc.stored_lod_count * 2
            for lod_idx in range(desc.stored_lod_count):
                struct.pack_into("<H", sec0_data, vc_off + lod_idx * 2, 0)
                struct.pack_into("<I", sec0_data, ic_off + lod_idx * 4, 0)
            # No prepared_submeshes entry → the geometry-writer loop
            # below skips this submesh entirely. Nothing gets written
            # to the LOD vertex / index buffers for this slot.
            logger.info(
                "PAC submesh %d ('%s'): emitting empty placeholder "
                "(zero-count descriptor, no geometry).",
                sm_idx, new_sm.name,
            )
            continue

        if not orig_sm.source_vertex_offsets or orig_sm.source_vertex_stride < 12:
            raise ValueError(
                f"PAC submesh {sm_idx} is missing source vertex metadata for a full rebuild."
            )

        donor_records = []
        for rec_off in orig_sm.source_vertex_offsets:
            if rec_off < 0 or rec_off + orig_sm.source_vertex_stride > len(original_data):
                raise ValueError(
                    f"PAC vertex record for submesh {sm_idx} points outside the file."
                )
            donor_records.append(original_data[rec_off:rec_off + orig_sm.source_vertex_stride])

        # Prefer the source_vertex_map recorded by the OBJ importer
        # (populated from the .cfmeta.json sidecar). It tracks the
        # ORIGINAL vertex slot each imported vertex came from, which
        # survives user edits that move vertices too far from their
        # original position for the nearest-position heuristic to
        # recover correctly. Fall back to positional donor matching
        # for legacy OBJs without a sidecar or for truly-new vertices
        # (source index == -1, e.g. user inserted geometry in Blender).
        orig_vertex_count = len(orig_sm.vertices)
        donor_indices: list[int] = []
        need_positional_fallback = False
        if new_sm.source_vertex_map and len(new_sm.source_vertex_map) == len(new_sm.vertices):
            for svm in new_sm.source_vertex_map:
                if 0 <= svm < orig_vertex_count:
                    donor_indices.append(svm)
                else:
                    # Placeholder — replaced below by positional match.
                    donor_indices.append(-1)
                    need_positional_fallback = True
        else:
            donor_indices = [-1] * len(new_sm.vertices)
            need_positional_fallback = True

        if need_positional_fallback:
            positional = _choose_pac_donor_indices(orig_sm, new_sm)
            donor_indices = [
                (pos if d < 0 else d)
                for d, pos in zip(donor_indices, positional)
            ]
        normals = (
            new_sm.normals
            if len(new_sm.normals) == len(new_sm.vertices)
            else _compute_smooth_normals(new_sm.vertices, new_sm.faces)
        )
        new_uvs = new_sm.uvs if len(new_sm.uvs) == len(new_sm.vertices) else []
        clean_shading_records = bool(
            getattr(new_sm, "clean_donor_shading_records", False)
            or getattr(working_mesh, "clean_donor_shading_records", False)
        )
        bmin, bmax = _compute_bbox(new_sm.vertices)
        extent = tuple(bmax[i] - bmin[i] for i in range(3))
        stored_lod_count = max(1, min(n_lods, orig_sm.source_lod_count or desc.stored_lod_count or n_lods))

        rel_desc_off = desc.descriptor_offset - sec0["offset"]
        if rel_desc_off < 0 or rel_desc_off + 40 > len(sec0_data):
            raise ValueError(f"PAC descriptor {sm_idx} points outside section 0.")

        _patch_pac_descriptor_bounds(sec0_data, rel_desc_off, bmin, extent)
        vc_off = rel_desc_off + 40
        ic_off = vc_off + desc.stored_lod_count * 2
        new_vert_count = len(new_sm.vertices)
        new_index_count = len(new_sm.faces) * 3
        for lod_idx in range(desc.stored_lod_count):
            struct.pack_into("<H", sec0_data, vc_off + lod_idx * 2, new_vert_count)
            struct.pack_into("<I", sec0_data, ic_off + lod_idx * 4, new_index_count)

        # Compute MikkTSpace tangents for the new mesh — strict mode, no
        # fallback. Without correct per-vertex tangents the engine renders
        # tangent-space normal maps with garbage TBN matrices, producing the
        # "white-with-sparks" lighting noise. The encoder formulas below
        # write the tangent into bytes 6-7 + bits 0-9 + bit 31 verified
        # byte-exact against shipped game data.
        if not new_uvs:
            raise ValueError(
                f"PAC submesh {sm_idx} ('{new_sm.name}') has no UVs; cannot "
                "compute tangents. Re-export the OBJ/FBX with texture "
                "coordinates."
            )
        try:
            new_tangents, new_bsigns, new_tan_valid = _compute_mesh_tangents(
                new_sm.vertices, new_uvs, normals, new_sm.faces
            )
        except ValueError as exc:
            raise ValueError(
                f"PAC build failed: cannot compute tangents for submesh "
                f"'{new_sm.name}' (#{sm_idx}). Detail: {exc}"
            ) from exc

        # ── Skin write-back context (full-rebuild path) ──
        # Same strict 1+1 contract as the in-place path; see helper.
        skin_pab_to_slot, _skin_known = _resolve_skin_write_context(
            working_mesh, sm_idx, new_sm.name,
        )
        write_skin_for_this_sm = _check_strict_skin_writeback(
            skin_pab_to_slot,
            orig_sm.source_vertex_stride,
            bool(getattr(new_sm, "bone_indices", None)),
            sm_idx, new_sm.name,
        )

        prepared_submeshes.append({
            "submesh": new_sm,
            "donor_records": donor_records,
            "donor_indices": donor_indices,
            "normals": normals,
            "uvs": new_uvs,
            "tangents": new_tangents,
            "bsigns": new_bsigns,
            "tan_valid": new_tan_valid,
            "bbox_min": bmin,
            "bbox_extent": extent,
            "stored_lod_count": stored_lod_count,
            "clean_shading_records": clean_shading_records,
            "skin_pab_to_slot": skin_pab_to_slot,
            "write_skin": write_skin_for_this_sm,
        })

    lod_payloads: dict[int, bytes] = {}
    lod_split_bytes: dict[int, int] = {}
    for sec_idx in range(1, n_lods + 1):
        lod_idx = n_lods - sec_idx
        verts_buf = bytearray()
        idx_buf = bytearray()

        for sm_idx, prepared in enumerate(prepared_submeshes):
            if lod_idx >= prepared["stored_lod_count"]:
                continue

            sm = prepared["submesh"]
            donor_records = prepared["donor_records"]
            donor_indices = prepared["donor_indices"]
            normals = prepared["normals"]
            new_uvs = prepared["uvs"]
            tangents = prepared["tangents"]
            bsigns = prepared["bsigns"]
            tan_valid = prepared["tan_valid"]
            bbox_min = prepared["bbox_min"]
            bbox_extent = prepared["bbox_extent"]
            clean_shading_records = prepared["clean_shading_records"]
            skin_pab_to_slot = prepared["skin_pab_to_slot"]
            write_skin = prepared["write_skin"]

            for vi, vertex in enumerate(sm.vertices):
                donor_rec = bytearray(donor_records[donor_indices[vi]])
                if clean_shading_records:
                    if len(donor_rec) >= 8:
                        struct.pack_into("<H", donor_rec, 6, 0)
                    if len(donor_rec) >= 28:
                        donor_rec[20:28] = b"\x00" * 8
                struct.pack_into(
                    "<HHH",
                    donor_rec,
                    0,
                    _quantize_pac_u16(vertex[0], bbox_min[0], bbox_extent[0]),
                    _quantize_pac_u16(vertex[1], bbox_min[1], bbox_extent[1]),
                    _quantize_pac_u16(vertex[2], bbox_min[2], bbox_extent[2]),
                )

                if len(donor_rec) >= 12:
                    if new_uvs:
                        try:
                            struct.pack_into("<e", donor_rec, 8, new_uvs[vi][0])
                            struct.pack_into("<e", donor_rec, 10, new_uvs[vi][1])
                        except (OverflowError, ValueError):
                            struct.pack_into("<e", donor_rec, 8, 0.0)
                            struct.pack_into("<e", donor_rec, 10, 0.0)

                if len(donor_rec) >= 20:
                    existing_normal = struct.unpack_from("<I", donor_rec, 16)[0]
                    struct.pack_into(
                        "<I",
                        donor_rec,
                        16,
                        _pack_pac_normal(
                            normals[vi],
                            0 if clean_shading_records else existing_normal,
                        ),
                    )
                    # Encode the freshly-computed tangent ONLY for vertices
                    # whose tangent came from non-degenerate UVs. For
                    # vertices marked invalid (UV-collapsed regions), the
                    # donor's existing tangent bytes are kept verbatim —
                    # they're the engine-baked values from the original
                    # mesh and are more trustworthy than any fallback we
                    # could synthesize. STRICT MODE: if the encoder
                    # rejects a "valid" tangent, raise — never silently
                    # paper over.
                    if tan_valid[vi]:
                        try:
                            _pack_pac_tangent_into_record(
                                donor_rec, tangents[vi], bsigns[vi]
                            )
                        except ValueError as exc:
                            raise ValueError(
                                f"PAC build failed: tangent encode rejected "
                                f"vertex #{vi} of submesh '{sm.name}' "
                                f"(#{sm_idx}). Detail: {exc}"
                            ) from exc

                # ── Strict skin write-back (full-rebuild) ──
                # Same 1+1 contract as the in-place path. Only fires
                # when the sidecar provided a per-submesh PAB-to-slot
                # map AND the new submesh carries skin data; donor
                # bytes are preserved otherwise.
                if write_skin:
                    _apply_skin_write_back(
                        donor_rec, sm, vi,
                        skin_pab_to_slot,
                        skeleton_bones_global,
                        sm_idx=sm_idx, sm_name=sm.name,
                    )

                verts_buf.extend(donor_rec)

            for face in sm.faces:
                a, b, c = face
                if a >= len(sm.vertices) or b >= len(sm.vertices) or c >= len(sm.vertices):
                    raise ValueError(f"PAC face in submesh {sm_idx} references an out-of-range vertex.")
                idx_buf.extend(struct.pack("<HHH", a, b, c))

        lod_split_bytes[sec_idx] = len(verts_buf)
        lod_payloads[sec_idx] = bytes(verts_buf + idx_buf)

    section_payloads: dict[int, bytes] = {0: bytes(sec0_data)}
    section_payloads.update(lod_payloads)
    section_payloads.update(preserved_sections)

    header = bytearray(original_data[:0x50])
    for slot in range(8):
        struct.pack_into("<I", header, 0x10 + slot * 8, 0)
        struct.pack_into("<I", header, 0x10 + slot * 8 + 4, 0)

    section_offsets = {0: 0x50}
    next_offset = 0x50 + len(section_payloads[0])
    for slot in range(1, 8):
        payload = section_payloads.get(slot)
        if payload is None:
            continue
        section_offsets[slot] = next_offset
        next_offset += len(payload)

    off = 5
    for lod_idx in range(n_lods):
        sec_idx = n_lods - lod_idx
        struct.pack_into("<I", sec0_data, off + lod_idx * 4, section_offsets[sec_idx])
    off += n_lods * 4
    for lod_idx in range(n_lods):
        sec_idx = n_lods - lod_idx
        split_abs = section_offsets[sec_idx] + lod_split_bytes.get(sec_idx, 0)
        struct.pack_into("<I", sec0_data, off + lod_idx * 4, split_abs)
    section_payloads[0] = bytes(sec0_data)

    assembled = bytearray(header)
    for slot in range(8):
        payload = section_payloads.get(slot)
        if payload is None:
            continue
        struct.pack_into("<I", assembled, 0x10 + slot * 8, 0)
        struct.pack_into("<I", assembled, 0x10 + slot * 8 + 4, len(payload))
        assembled.extend(payload)

    logger.info(
        "Built PAC %s with full rebuild: %d bytes, %d submeshes, %d verts, %d faces",
        working_mesh.path,
        len(assembled),
        len(working_mesh.submeshes),
        sum(len(sm.vertices) for sm in working_mesh.submeshes),
        sum(len(sm.faces) for sm in working_mesh.submeshes),
    )
    return bytes(assembled)


def build_skin_writeback_sidecar(
    original_data: bytes, vfs=None, pac_path: str = "",
    skeleton=None,
) -> dict:
    """Build the v2 ``_cfmeta_sidecar`` dict from a donor PAC.

    Strict 1+1 — every entry comes from the donor's actual bytes
    cross-referenced against a real PAB. There is no fallback path;
    a missing PAB or palette returns ``skeleton_bones=[]`` and per-
    submesh ``pab_to_slot={}`` so the caller can see exactly what
    failed instead of silently substituting guessed values.

    Call this BEFORE :func:`build_pac` and attach the result as
    ``parsed._cfmeta_sidecar``. With the sidecar present, the
    strict skin write-back in :func:`_apply_skin_write_back`
    fires and the user's edited vertex weights end up in the
    rebuilt PAC's vertex byte slots. Without it, the path that
    silently preserves donor weights kicks in — which is the
    ``SILENT FALLBACK`` symptom reported in the byte-diff verifier
    when a Blender-native FBX (no ``.cfmeta.json`` companion)
    feeds the standalone Forge.

    Three skeleton-resolution paths:

      * ``skeleton`` provided directly — use it.
      * ``vfs`` + ``pac_path`` provided — call
        :func:`core.skeleton_resolver.resolve_skeleton` with the
        donor bytes (same strict palette-coverage rule the
        Explorer FBX export uses).
      * Neither — empty sidecar, write-back stays off.
    """
    from core.mesh_parser import (
        parse_pac, derive_skin_slot_to_pab_geometric,
    )

    sidecar: dict = {
        "schema_version": "v2",
        "skeleton_bones": [],
        "submeshes": [],
    }
    if not original_data:
        return sidecar
    try:
        donor_parsed = parse_pac(original_data, pac_path or "")
    except Exception:
        return sidecar
    if not donor_parsed.submeshes:
        return sidecar

    # Skeleton resolution.
    if skeleton is None and vfs is not None and pac_path:
        try:
            from core.skeleton_resolver import (
                VfsManagerAdapter, resolve_skeleton,
            )
            adapter = VfsManagerAdapter(vfs)
            res = resolve_skeleton(
                pac_path, adapter, pac_bytes=original_data,
            )
            skeleton = res.skeleton
        except Exception:
            skeleton = None

    if skeleton is None or not getattr(skeleton, "bones", None):
        # No skeleton -> can't build pab_to_slot. Caller's build_pac
        # will fall through to donor-preserve (which is the correct
        # strict response: nothing to write back).
        for sm in donor_parsed.submeshes:
            sidecar["submeshes"].append({
                "name": sm.name,
                "pab_to_slot": {},
            })
        return sidecar

    # Decode the donor's palette in place so bone_indices become
    # PAB-space (the inverse map below requires that).
    try:
        donor_parsed._pac_bytes = original_data
        derive_skin_slot_to_pab_geometric(donor_parsed, skeleton)
    except Exception:
        # Decode failed -> empty maps below; caller sees empty
        # pab_to_slot and write-back stays off (strict refusal).
        pass

    sidecar["skeleton_bones"] = [b.name for b in skeleton.bones]
    for sm in donor_parsed.submeshes:
        pts: dict = {}
        try:
            pts = _build_pab_to_slot_for_submesh(sm, original_data) or {}
        except Exception:
            pts = {}
        sidecar["submeshes"].append({
            "name": sm.name,
            "pab_to_slot": {str(k): int(v) for k, v in pts.items()},
        })
    return sidecar


def build_pac(mesh: ParsedMesh, original_data: bytes) -> bytes:
    """Rebuild a PAC binary from a modified mesh.

    Performance note (v1.22.4): previously opened with a full
    ``copy.deepcopy(mesh)`` which walks every vertex/face/uv/normal
    tuple in the graph. On a 20 k-vertex character that's a multi-
    hundred-megabyte allocation. We replaced it with a shallow
    wrapper copy + a new submesh LIST (but the submesh OBJECTS are
    shared with the caller). The rebuild path reads submesh fields
    read-only; any mutation would go through :func:`_merge_partial_pac_import`
    which deep-copies each submesh it needs to reshape. This cuts
    the setup cost from O(n_vertices) down to O(n_submeshes)
    (typically 5-20) with zero correctness change.
    """
    if not original_data or original_data[:4] != b"PAR ":
        raise ValueError("Original PAC data required for rebuild")

    original_mesh = parse_pac(original_data, mesh.path)
    if not original_mesh.submeshes:
        raise ValueError("Original PAC could not be parsed into usable geometry")

    working_mesh = copy.copy(mesh)
    working_mesh.submeshes = list(mesh.submeshes)
    working_mesh = _merge_partial_pac_import(original_mesh, working_mesh)
    _align_submesh_order_like_original(original_mesh, working_mesh)

    if len(original_mesh.submeshes) != len(working_mesh.submeshes):
        raise ValueError(
            "PAC import currently requires the same submesh count as the original mesh."
        )

    if _pac_needs_full_rebuild(original_mesh, working_mesh):
        return _build_pac_full_rebuild(original_mesh, working_mesh, original_data)
    return _build_pac_in_place(original_mesh, working_mesh, original_data)


def build_mesh(mesh: ParsedMesh, original_data: bytes) -> bytes:
    """Auto-detect format and rebuild binary from modified mesh.

    Args:
        mesh: Modified ParsedMesh (from import_obj or manual modification).
        original_data: Original binary data (needed for metadata preservation).

    Returns:
        New binary data ready for repack.
    """
    fmt = mesh.format.lower()
    if fmt == "pac":
        return build_pac(mesh, original_data)
    elif fmt == "pam":
        return build_pam(mesh, original_data)
    elif fmt == "pamlod":
        return build_pamlod(mesh, original_data)
    else:
        raise ValueError(f"Unsupported mesh format for rebuild: {fmt}")
