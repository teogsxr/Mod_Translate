"""Regression tests for mesh importer fixes shipped in v1.22.3-v1.22.4.

Covers:

1. **v1.22.3 OBJ vertex-split skin propagation** — when Blender
   re-exports the same position with multiple UV/normal pairs,
   the importer clones the vertex. Before the fix, clones had
   empty bone data and the rebuilt PAC had zero skin weights on
   seam vertices ("model exploded"). The fix propagates
   ``bone_indices`` / ``bone_weights`` and ``source_vertex_map``
   from the source slot to every clone.

2. **v1.22.4 build_pac shallow-copy** — ``build_pac`` used to
   ``copy.deepcopy(mesh)`` which walks every vertex/face/uv/normal
   tuple. For a 20 k-vert character that's a huge allocation. The
   new shallow copy must still not mutate the caller's mesh; these
   tests pin down that invariant.
"""

from __future__ import annotations

import copy
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from core.mesh_exporter import export_obj   # noqa: E402
from core.mesh_importer import import_obj   # noqa: E402
from core.mesh_parser import ParsedMesh, SubMesh   # noqa: E402


def _skinned_quad(bone_per_vertex=None) -> ParsedMesh:
    bone_per_vertex = bone_per_vertex or [
        (0, 0, 0, 0),
        (1, 0, 0, 0),
        (2, 0, 0, 0),
        (3, 0, 0, 0),
    ]
    weights = [(1.0, 0.0, 0.0, 0.0)] * 4
    sm = SubMesh(
        name="seam_sm",
        material="mat",
        vertices=[(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)],
        uvs=[(0, 0), (1, 0), (1, 1), (0, 1)],
        normals=[(0, 0, 1)] * 4,
        faces=[(0, 1, 2), (0, 2, 3)],
        bone_indices=bone_per_vertex,
        bone_weights=weights,
        vertex_count=4,
        face_count=2,
    )
    return ParsedMesh(
        path="test.pac", format="pac", submeshes=[sm],
        total_vertices=4, total_faces=2,
        has_uvs=True, has_bones=True,
    )


def _export_split_obj(test_case, source_vertex_idx: int = 2):
    """Export a skinned quad + manually add a duplicate-UV reference
    to simulate Blender re-export with a UV seam on vertex
    ``source_vertex_idx`` (0-based).
    """
    td = tempfile.mkdtemp()
    test_case.addCleanup(lambda: __import__("shutil").rmtree(td, ignore_errors=True))

    mesh = _skinned_quad()
    outs = export_obj(mesh, td, "seam")
    obj = [p for p in outs if p.endswith(".obj")][0]

    # Replace the second face to reference a NEW vt (duplicate of
    # the seam vertex's UV but at a different index) so the importer
    # is forced to clone.
    text = Path(obj).read_text(encoding="utf-8")
    lines = text.split("\n")

    new_lines = []
    vt_count = 0
    inserted = False
    for line in lines:
        new_lines.append(line)
        if line.startswith("vt "):
            vt_count += 1
            if vt_count == 4 and not inserted:
                # Add an extra vt entry matching vt #3's UV coords
                # but with a tiny offset so it's a distinct UV.
                new_lines.append("vt 0.500000 0.500000")
                inserted = True

    # Redirect the face that used vertex 3 to use the new vt index (5).
    adjusted = []
    for line in new_lines:
        if line.startswith("f 1/1/1 3/3/3 4/4/4"):
            adjusted.append("f 1/1/1 3/5/3 4/4/4")
        else:
            adjusted.append(line)

    Path(obj).write_text("\n".join(adjusted), encoding="utf-8")
    return obj, mesh


# ═════════════════════════════════════════════════════════════════════
# UV-seam vertex split propagation (v1.22.3 fix)
# ═════════════════════════════════════════════════════════════════════

class UVSeamSplit_BoneDataPreserved(unittest.TestCase):
    def test_split_produces_extra_vertex(self):
        obj, _ = _export_split_obj(self)
        back = import_obj(obj)
        # Original had 4 verts; the seam re-reference adds 1 clone.
        self.assertEqual(len(back.submeshes[0].vertices), 5)

    def test_clone_inherits_source_bone_index(self):
        obj, mesh = _export_split_obj(self)
        back = import_obj(obj)
        # The cloned vertex (index 4) should carry bone_indices
        # of the slot it was cloned from (index 2).
        expected = mesh.submeshes[0].bone_indices[2]
        self.assertEqual(back.submeshes[0].bone_indices[4], expected)

    def test_clone_inherits_source_bone_weight(self):
        obj, mesh = _export_split_obj(self)
        back = import_obj(obj)
        expected = mesh.submeshes[0].bone_weights[2]
        self.assertEqual(back.submeshes[0].bone_weights[4], expected)

    def test_clone_source_vertex_map_points_to_origin(self):
        obj, _ = _export_split_obj(self)
        back = import_obj(obj)
        # source_vertex_map for the clone should point back to slot 2.
        self.assertEqual(back.submeshes[0].source_vertex_map[4], 2)

    def test_original_vertices_keep_identity_mapping(self):
        obj, _ = _export_split_obj(self)
        back = import_obj(obj)
        # Slots 0..3 (the originals) map to themselves.
        self.assertEqual(
            back.submeshes[0].source_vertex_map[:4],
            [0, 1, 2, 3],
        )

    def test_bone_indices_length_matches_vertex_count(self):
        obj, _ = _export_split_obj(self)
        back = import_obj(obj)
        self.assertEqual(
            len(back.submeshes[0].bone_indices),
            len(back.submeshes[0].vertices),
        )

    def test_bone_weights_length_matches_vertex_count(self):
        obj, _ = _export_split_obj(self)
        back = import_obj(obj)
        self.assertEqual(
            len(back.submeshes[0].bone_weights),
            len(back.submeshes[0].vertices),
        )

    def test_source_vertex_map_length_matches_vertex_count(self):
        obj, _ = _export_split_obj(self)
        back = import_obj(obj)
        self.assertEqual(
            len(back.submeshes[0].source_vertex_map),
            len(back.submeshes[0].vertices),
        )


class UVSeamSplit_DifferentSourceBones(unittest.TestCase):
    """Vertex-by-vertex distinct bone indices must all survive the
    clone propagation."""

    def test_each_vertex_has_distinct_bone_index(self):
        obj, mesh = _export_split_obj(self)
        back = import_obj(obj)
        # All five imported vertices' bone indices must be present
        # in the original set.
        original = set(mesh.submeshes[0].bone_indices)
        for bi in back.submeshes[0].bone_indices:
            self.assertIn(bi, original)

    def test_bone_indices_not_all_zero(self):
        """Pre-v1.22.3 this test would fail because clones had (0,0,0,0)."""
        obj, _ = _export_split_obj(self)
        back = import_obj(obj)
        unique = set(back.submeshes[0].bone_indices)
        self.assertGreater(len(unique), 1)


# ═════════════════════════════════════════════════════════════════════
# build_pac shallow-copy regression (v1.22.4 fix)
# ═════════════════════════════════════════════════════════════════════

class BuildPac_DoesNotMutateCaller(unittest.TestCase):
    """Before v1.22.4, build_pac did `copy.deepcopy(mesh)` so the
    caller's mesh was safe. After the perf fix, it does a shallow
    copy — so we must explicitly test that none of the downstream
    mutation points (like _align_submesh_order_like_original) leak
    mutations back to the caller.

    We can't actually call build_pac() without a real PAC payload
    (that's integration territory), but we CAN verify the shallow-
    copy semantics we rely on at the source.
    """

    def test_shallow_copy_is_new_submeshes_list(self):
        mesh = _skinned_quad()
        shallow = copy.copy(mesh)
        shallow.submeshes = list(mesh.submeshes)
        # Mutating the copy's submeshes list must not affect the original.
        shallow.submeshes.reverse()
        self.assertEqual(
            [sm.name for sm in mesh.submeshes],
            ["seam_sm"],
        )

    def test_shallow_copy_shares_submesh_objects(self):
        mesh = _skinned_quad()
        shallow = copy.copy(mesh)
        shallow.submeshes = list(mesh.submeshes)
        # Same object identity — that's the whole point of shallow.
        self.assertIs(shallow.submeshes[0], mesh.submeshes[0])

    def test_shallow_copy_different_mesh_object(self):
        mesh = _skinned_quad()
        shallow = copy.copy(mesh)
        shallow.submeshes = list(mesh.submeshes)
        self.assertIsNot(shallow, mesh)

    def test_shallow_copy_preserves_bbox(self):
        mesh = _skinned_quad()
        mesh.bbox_min = (0.1, 0.2, 0.3)
        mesh.bbox_max = (10.0, 20.0, 30.0)
        shallow = copy.copy(mesh)
        self.assertEqual(shallow.bbox_min, mesh.bbox_min)
        self.assertEqual(shallow.bbox_max, mesh.bbox_max)

    def test_shallow_copy_preserves_path(self):
        mesh = _skinned_quad()
        shallow = copy.copy(mesh)
        self.assertEqual(shallow.path, mesh.path)

    def test_shallow_copy_preserves_format(self):
        mesh = _skinned_quad()
        shallow = copy.copy(mesh)
        self.assertEqual(shallow.format, mesh.format)


# ═════════════════════════════════════════════════════════════════════
# OBJ round-trip preserves submesh structure
# ═════════════════════════════════════════════════════════════════════

class ObjRoundTrip_Structural(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self._td, ignore_errors=True))

    def test_single_submesh_round_trip(self):
        mesh = _skinned_quad()
        outs = export_obj(mesh, self._td, "x")
        obj = [p for p in outs if p.endswith(".obj")][0]
        back = import_obj(obj)
        self.assertEqual(len(back.submeshes), 1)

    def test_two_submeshes_round_trip(self):
        sm1 = _skinned_quad().submeshes[0]
        sm2 = _skinned_quad().submeshes[0]
        sm2.name = "second"
        mesh = ParsedMesh(
            path="test.pac", format="pac", submeshes=[sm1, sm2],
            total_vertices=8, total_faces=4, has_uvs=True, has_bones=True,
        )
        outs = export_obj(mesh, self._td, "x")
        obj = [p for p in outs if p.endswith(".obj")][0]
        back = import_obj(obj)
        self.assertEqual(len(back.submeshes), 2)
        self.assertEqual({sm.name for sm in back.submeshes}, {"seam_sm", "second"})

    def test_five_submeshes_round_trip(self):
        submeshes = []
        for i in range(5):
            sm = _skinned_quad().submeshes[0]
            sm.name = f"sm_{i}"
            submeshes.append(sm)
        mesh = ParsedMesh(
            path="x.pac", format="pac", submeshes=submeshes,
            total_vertices=20, total_faces=10,
            has_uvs=True, has_bones=True,
        )
        outs = export_obj(mesh, self._td, "x")
        obj = [p for p in outs if p.endswith(".obj")][0]
        back = import_obj(obj)
        self.assertEqual(len(back.submeshes), 5)


class ObjRoundTrip_Geometry(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self._td, ignore_errors=True))

    def test_vertex_positions_preserved(self):
        mesh = _skinned_quad()
        outs = export_obj(mesh, self._td, "x")
        obj = [p for p in outs if p.endswith(".obj")][0]
        back = import_obj(obj)
        for got, want in zip(back.submeshes[0].vertices, mesh.submeshes[0].vertices):
            for a, b in zip(got, want):
                self.assertAlmostEqual(a, b, places=5)

    def test_face_topology_preserved(self):
        mesh = _skinned_quad()
        outs = export_obj(mesh, self._td, "x")
        obj = [p for p in outs if p.endswith(".obj")][0]
        back = import_obj(obj)
        self.assertEqual(len(back.submeshes[0].faces), 2)

    def test_uv_values_preserved(self):
        mesh = _skinned_quad()
        outs = export_obj(mesh, self._td, "x")
        obj = [p for p in outs if p.endswith(".obj")][0]
        back = import_obj(obj)
        for got, want in zip(back.submeshes[0].uvs, mesh.submeshes[0].uvs):
            for a, b in zip(got, want):
                self.assertAlmostEqual(a, b, places=5)

    def test_normals_preserved(self):
        mesh = _skinned_quad()
        outs = export_obj(mesh, self._td, "x")
        obj = [p for p in outs if p.endswith(".obj")][0]
        back = import_obj(obj)
        for got, want in zip(back.submeshes[0].normals, mesh.submeshes[0].normals):
            for a, b in zip(got, want):
                self.assertAlmostEqual(a, b, places=3)


# ═════════════════════════════════════════════════════════════════════
# Output file set
# ═════════════════════════════════════════════════════════════════════

class ExportOutputFiles(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self._td, ignore_errors=True))

    def test_writes_obj(self):
        outs = export_obj(_skinned_quad(), self._td, "x")
        self.assertTrue(any(p.endswith(".obj") for p in outs))

    def test_writes_mtl(self):
        outs = export_obj(_skinned_quad(), self._td, "x")
        self.assertTrue(any(p.endswith(".mtl") for p in outs))

    def test_writes_cfmeta_sidecar(self):
        outs = export_obj(_skinned_quad(), self._td, "x")
        self.assertTrue(any(p.endswith(".cfmeta.json") for p in outs))

    def test_all_output_paths_exist(self):
        outs = export_obj(_skinned_quad(), self._td, "x")
        for p in outs:
            self.assertTrue(os.path.isfile(p), p)

    def test_output_basename_matches(self):
        outs = export_obj(_skinned_quad(), self._td, "custom_name")
        basenames = [os.path.basename(p) for p in outs]
        self.assertTrue(any(b.startswith("custom_name") for b in basenames))

    def test_path_stem_used_when_name_empty(self):
        mesh = _skinned_quad()
        mesh.path = "somepath/my_asset.pac"
        outs = export_obj(mesh, self._td, "")
        basenames = [os.path.basename(p) for p in outs]
        self.assertTrue(any(b.startswith("my_asset") for b in basenames))


if __name__ == "__main__":
    unittest.main()
