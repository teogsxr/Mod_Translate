"""End-to-end test for the spike-vertex filter round-trip.

Synthesizes a ParsedMesh + Skeleton with:
  - 100 normal body verts (skinned)
  - 20 spike verts at outlier positions (unskinned, mimicking foot
    shadow decals)
  - Faces between body verts AND faces touching the spikes

Then:
  1. Exports to FBX with filter_unskinned_outliers=True (default)
  2. Re-imports the FBX via import_fbx
  3. Verifies the imported mesh has the FULL original vertex count
     (100 body + 20 filtered = 120) and the FULL face count
  4. Compares positions: body verts unchanged, spike verts restored
     verbatim from the sidecar

If this passes, the round-trip is lossless and the user can:
  - Edit Damian's mesh in Blender (sees only the visible body, no spikes)
  - Re-export through CrimsonForge → game has full mesh including foot shadows
"""
from __future__ import annotations

import math
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.mesh_parser import ParsedMesh, SubMesh
from core.mesh_exporter import export_fbx_with_skeleton
from core.mesh_importer import import_fbx


@dataclass
class _FakeBone:
    index: int = 0
    name: str = ""
    parent_index: int = -1
    bind_matrix: tuple = ()
    inv_bind_matrix: tuple = ()
    scale: tuple = (1.0, 1.0, 1.0)
    rotation: tuple = (0.0, 0.0, 0.0, 1.0)
    position: tuple = (0.0, 0.0, 0.0)


@dataclass
class _FakeSkeleton:
    path: str = ""
    bones: list = field(default_factory=list)
    bone_count: int = 0


def make_test_mesh():
    sm = SubMesh(name="test_body", material="test_mat")
    # 100 body verts in tight cluster (10x10 grid, 1cm spacing)
    sm.vertices = [(0.01 * i, 0.5 + 0.01 * j, 0.0) for i in range(10) for j in range(10)]
    # 20 spike verts (foot shadow style — at floor level, ±22cm out)
    spike_xs = [0.22] * 10 + [-0.22] * 10
    sm.vertices.extend([(x, 0.0, 0.001 * i) for i, x in enumerate(spike_xs)])
    # Skin: body weighted to bone 0, spikes unskinned
    sm.bone_indices = [(0,)] * 100 + [()] * 20
    sm.bone_weights = [(1.0,)] * 100 + [()] * 20
    sm.uvs = [(0.0, 0.0)] * 120
    sm.normals = [(0.0, 1.0, 0.0)] * 120
    # Faces:
    #  - 50 normal body faces (i, i+1, i+10)
    #  - 5 spike faces (groups of 3 consecutive spike verts)
    sm.faces = [(i, i + 1, i + 10) for i in range(50) if i + 10 < 100]
    sm.faces.extend([(100 + i, 100 + i + 1, 100 + i + 2) for i in range(0, 18, 4)])

    sm.vertex_count = len(sm.vertices)
    sm.face_count = len(sm.faces)
    sm.source_vertex_offsets = list(range(len(sm.vertices)))

    mesh = ParsedMesh(
        path="test_synthetic.pac",
        format="pac",
        submeshes=[sm],
        total_vertices=len(sm.vertices),
        total_faces=len(sm.faces),
        has_uvs=True,
        has_bones=True,
    )
    return mesh


def make_test_skeleton():
    bones = [
        _FakeBone(
            index=0, name="Bip01", parent_index=-1,
            bind_matrix=(1, 0, 0, 0,  0, 1, 0, 0,  0, 0, 1, 0,  0, 0.5, 0, 1),
            inv_bind_matrix=(1, 0, 0, 0,  0, 1, 0, 0,  0, 0, 1, 0,  0, -0.5, 0, 1),
        )
    ]
    return _FakeSkeleton(bones=bones, bone_count=1)


def main():
    print("=" * 70)
    print("SPIKE-FILTER ROUND-TRIP TEST")
    print("=" * 70)

    mesh = make_test_mesh()
    sk = make_test_skeleton()

    print(f"\nORIGINAL mesh:")
    print(f"  vertices: {mesh.total_vertices}")
    print(f"  faces:    {mesh.total_faces}")
    sm = mesh.submeshes[0]
    print(f"  skinned verts:    {sum(1 for b in sm.bone_indices if b)}")
    print(f"  unskinned verts:  {sum(1 for b in sm.bone_indices if not b)}")

    tmpdir = tempfile.mkdtemp(prefix="cf_spike_test_")
    print(f"\nExporting to: {tmpdir}")

    # Export with filter ON
    fbx_path = export_fbx_with_skeleton(
        mesh, sk, tmpdir, name="roundtrip", scale=1.0,
        filter_unskinned_outliers=True,
    )
    print(f"  FBX written: {fbx_path}")

    sidecar_path = fbx_path + ".cfmeta.json"
    if not Path(sidecar_path).exists():
        print(f"  ✗ FAIL: sidecar not written")
        return 1
    print(f"  Sidecar written: {sidecar_path}")
    sidecar_size = Path(sidecar_path).stat().st_size
    print(f"    sidecar size: {sidecar_size} bytes")

    # Re-import
    print(f"\nRe-importing FBX...")
    imported = import_fbx(fbx_path)
    print(f"  imported submeshes: {len(imported.submeshes)}")
    print(f"  imported total verts: {imported.total_vertices}")
    print(f"  imported total faces: {imported.total_faces}")

    # Verify counts
    assert len(imported.submeshes) == 1, "submesh count changed"
    isub = imported.submeshes[0]
    print(f"\nVerifying submesh '{isub.name}':")
    print(f"  vertex count: {len(isub.vertices)} (expected {mesh.total_vertices})")
    print(f"  face count:   {len(isub.faces)} (expected {mesh.total_faces})")

    if len(isub.vertices) != mesh.total_vertices:
        print(f"  ✗ FAIL: vertex count mismatch")
        return 1
    if len(isub.faces) != mesh.total_faces:
        print(f"  ✗ FAIL: face count mismatch")
        return 1

    # Verify positions: each original PAC slot's vertex should appear
    # somewhere in the imported mesh (find by source_vertex_map).
    print(f"\nVerifying vertex positions (matched via source_vertex_map):")
    src_map = isub.source_vertex_map
    print(f"  source_vertex_map length: {len(src_map)}")
    # For each original PAC slot, find the imported vert that maps to it
    seen_slots = set()
    max_pos_err = 0.0
    for new_idx, pac_slot in enumerate(src_map):
        if pac_slot in seen_slots:
            continue
        seen_slots.add(pac_slot)
        if not (0 <= pac_slot < mesh.total_vertices):
            continue
        orig_pos = sm.vertices[pac_slot]
        new_pos = isub.vertices[new_idx]
        err = math.sqrt(sum((orig_pos[i] - new_pos[i]) ** 2 for i in range(3)))
        if err > max_pos_err:
            max_pos_err = err

    print(f"  unique PAC slots covered: {len(seen_slots)} of {mesh.total_vertices}")
    print(f"  max position error: {max_pos_err:.6g}")

    if len(seen_slots) != mesh.total_vertices:
        missing = set(range(mesh.total_vertices)) - seen_slots
        print(f"  ✗ FAIL: missing PAC slots: {sorted(missing)[:20]}...")
        return 1
    if max_pos_err > 1e-3:
        print(f"  ✗ FAIL: position drift > 1mm")
        return 1

    print(f"\n{'=' * 70}")
    print("✓ ALL ASSERTIONS PASSED — round-trip is lossless")
    print(f"{'=' * 70}")
    print(f"\nCleaning up {tmpdir}")
    shutil.rmtree(tmpdir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
