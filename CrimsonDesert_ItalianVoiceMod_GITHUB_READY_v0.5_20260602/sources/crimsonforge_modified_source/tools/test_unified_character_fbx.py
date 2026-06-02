"""End-to-end test for the unified mesh + skeleton + animation FBX export.

Builds a synthetic character from real PAB + PAA samples in tools/paa_samples,
exports through the new export_fbx_with_skeleton(animation=...) path,
verifies all three components are present in the output FBX (skinned
mesh, bones, animation curves).

This is the "enterprise-level" deliverable: one FBX with everything.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.animation_parser import parse_paa
from core.mesh_exporter import export_fbx_with_skeleton
from core.mesh_parser import ParsedMesh, SubMesh
from core.skeleton_parser import parse_pab


def make_test_mesh_from_skeleton(skeleton):
    """Build a 1-tri-per-bone synthetic mesh skinned to the skeleton.

    Each triangle is at the bone's world position, weighted 100% to
    that bone. This gives us a real skinned mesh to export alongside
    the animation.
    """
    sm = SubMesh(name="test_character_body", material="test_mat")
    verts = []
    bone_indices = []
    bone_weights = []
    faces = []

    for i, bone in enumerate(skeleton.bones):
        if not bone.bind_matrix or len(bone.bind_matrix) != 16:
            continue
        # Bone world position (Y-up from PAB)
        bx, by, bz = bone.bind_matrix[12], bone.bind_matrix[13], bone.bind_matrix[14]
        v0 = (bx, by, bz)
        v1 = (bx + 0.01, by, bz)
        v2 = (bx, by, bz + 0.01)
        v_offset = len(verts)
        verts.extend([v0, v1, v2])
        bone_indices.extend([(bone.index,)] * 3)
        bone_weights.extend([(1.0,)] * 3)
        faces.append((v_offset, v_offset + 1, v_offset + 2))

    sm.vertices = verts
    sm.uvs = [(0.0, 0.0)] * len(verts)
    sm.normals = [(0.0, 1.0, 0.0)] * len(verts)
    sm.faces = faces
    sm.bone_indices = bone_indices
    sm.bone_weights = bone_weights
    sm.vertex_count = len(verts)
    sm.face_count = len(faces)
    sm.source_vertex_offsets = list(range(len(verts)))

    mesh = ParsedMesh(
        path="test_character.pac", format="pac",
        submeshes=[sm],
        total_vertices=len(verts), total_faces=len(faces),
        has_uvs=True, has_bones=True,
    )
    return mesh


def main():
    print("=" * 72)
    print("UNIFIED CHARACTER FBX EXPORT TEST (mesh + skeleton + animation)")
    print("=" * 72)

    pab_path = Path("tools/paa_samples/phm_01.pab")
    paa_path = Path("tools/paa_samples/sample_talk.paa")

    if not pab_path.exists() or not paa_path.exists():
        print("Missing test fixtures.")
        return 1

    print(f"\nLoading PAB: {pab_path}")
    skeleton = parse_pab(pab_path.read_bytes(), pab_path.name)
    print(f"  {len(skeleton.bones)} bones")

    print(f"\nLoading PAA: {paa_path}")
    animation = parse_paa(paa_path.read_bytes(), paa_path.name)
    print(f"  {animation.frame_count} frames, "
          f"{len(animation.keyframes)} keyframes, "
          f"duration {animation.duration:.3f}s, "
          f"{animation.bone_count} animated bones")

    print(f"\nBuilding synthetic mesh skinned to skeleton...")
    mesh = make_test_mesh_from_skeleton(skeleton)
    print(f"  {mesh.total_vertices} verts, {mesh.total_faces} faces")

    tmpdir = tempfile.mkdtemp(prefix="cf_unified_")
    print(f"\nExporting unified FBX to: {tmpdir}")

    fbx_path = export_fbx_with_skeleton(
        mesh, skeleton, tmpdir, name="unified_test", scale=1.0,
        filter_unskinned_outliers=False,
        animation=animation, fps=30.0,
    )

    fbx_size = Path(fbx_path).stat().st_size
    print(f"\nResult: {fbx_path}")
    print(f"  size: {fbx_size:,} bytes")

    # Sanity check FBX content by string-scanning for animation markers
    raw = Path(fbx_path).read_bytes()
    has_anim_stack = b"AnimationStack" in raw
    has_anim_layer = b"AnimationLayer" in raw
    has_anim_curve_node = b"AnimationCurveNode" in raw
    has_anim_curve = b"AnimationCurve" in raw
    has_pose = b"BindPose" in raw
    has_skin = b"Skin\x00\x01" in raw or b"Deformer\x00\x01" in raw

    print(f"\nFBX content scan:")
    print(f"  AnimationStack:     {'✓' if has_anim_stack else '✗'}")
    print(f"  AnimationLayer:     {'✓' if has_anim_layer else '✗'}")
    print(f"  AnimationCurveNode: {'✓' if has_anim_curve_node else '✗'}")
    print(f"  AnimationCurve:     {'✓' if has_anim_curve else '✗'}")
    print(f"  BindPose:           {'✓' if has_pose else '✗'}")
    print(f"  Skin/Cluster:       {'✓' if has_skin else '✗'}")

    all_present = all([has_anim_stack, has_anim_layer, has_anim_curve_node,
                       has_anim_curve, has_pose, has_skin])
    print(f"\n{'=' * 72}")
    if all_present:
        print(f"✓ ALL ENTERPRISE COMPONENTS PRESENT in single FBX file")
    else:
        print(f"✗ FAIL — missing components")
    print(f"{'=' * 72}")

    print(f"\nCleaning up {tmpdir}")
    shutil.rmtree(tmpdir, ignore_errors=True)
    return 0 if all_present else 1


if __name__ == "__main__":
    sys.exit(main() or 0)
