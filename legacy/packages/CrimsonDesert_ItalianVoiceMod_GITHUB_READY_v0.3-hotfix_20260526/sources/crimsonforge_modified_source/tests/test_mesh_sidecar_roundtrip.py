"""Tests for the OBJ ``.cfmeta.json`` sidecar round-trip.

The sidecar (introduced in v1.22.3) carries the skin weights that
OBJ can't encode. On re-import, the importer uses the sidecar to:

  1. Populate ``SubMesh.bone_indices`` / ``bone_weights`` on the
     imported mesh (otherwise the repack would have no skin data).
  2. Track ``SubMesh.source_vertex_map`` so the PAC rebuilder picks
     the correct donor record per vertex, even when UV-seam
     splitting clones vertices.

These tests pin down every documented round-trip property the
pipeline must preserve so a future refactor can't silently break
character repacks again ("model exploded" bug).
"""

from __future__ import annotations

import json
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


def _make_quad_mesh(
    *,
    name: str = "test_sm",
    bone_indices=None,
    bone_weights=None,
    has_bones: bool = True,
) -> ParsedMesh:
    """Construct a unit-quad ParsedMesh for round-trip tests."""
    if has_bones:
        bone_indices = bone_indices or [(0, 1, 0, 0)] * 4
        bone_weights = bone_weights or [(0.6, 0.4, 0.0, 0.0)] * 4
    else:
        bone_indices = []
        bone_weights = []
    sm = SubMesh(
        name=name,
        material="mat",
        vertices=[(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)],
        uvs=[(0, 0), (1, 0), (1, 1), (0, 1)],
        normals=[(0, 0, 1)] * 4,
        faces=[(0, 1, 2), (0, 2, 3)],
        bone_indices=bone_indices,
        bone_weights=bone_weights,
        vertex_count=4,
        face_count=2,
    )
    return ParsedMesh(
        path="test.pac",
        format="pac",
        submeshes=[sm],
        total_vertices=4,
        total_faces=2,
        has_uvs=True,
        has_bones=has_bones,
    )


# ═════════════════════════════════════════════════════════════════════
# Sidecar is written for meshes WITH skin data
# ═════════════════════════════════════════════════════════════════════

class SidecarWrittenForSkinnedMesh(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self._td, ignore_errors=True))

    def test_cfmeta_json_exists_next_to_obj(self):
        mesh = _make_quad_mesh()
        outs = export_obj(mesh, self._td, "x")
        sidecars = [p for p in outs if p.endswith(".cfmeta.json")]
        self.assertEqual(len(sidecars), 1)
        self.assertTrue(os.path.isfile(sidecars[0]))

    def test_cfmeta_filename_convention(self):
        mesh = _make_quad_mesh()
        outs = export_obj(mesh, self._td, "foo")
        sidecar = [p for p in outs if p.endswith(".cfmeta.json")][0]
        self.assertEqual(os.path.basename(sidecar), "foo.obj.cfmeta.json")

    def test_cfmeta_is_valid_json(self):
        mesh = _make_quad_mesh()
        outs = export_obj(mesh, self._td, "x")
        sidecar = [p for p in outs if p.endswith(".cfmeta.json")][0]
        data = json.loads(Path(sidecar).read_text(encoding="utf-8"))
        self.assertIsInstance(data, dict)

    def test_cfmeta_has_schema_version(self):
        mesh = _make_quad_mesh()
        outs = export_obj(mesh, self._td, "x")
        sidecar = [p for p in outs if p.endswith(".cfmeta.json")][0]
        data = json.loads(Path(sidecar).read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], 1)

    def test_cfmeta_has_source_path(self):
        mesh = _make_quad_mesh()
        outs = export_obj(mesh, self._td, "x")
        sidecar = [p for p in outs if p.endswith(".cfmeta.json")][0]
        data = json.loads(Path(sidecar).read_text(encoding="utf-8"))
        self.assertEqual(data["source_path"], "test.pac")

    def test_cfmeta_has_source_format(self):
        mesh = _make_quad_mesh()
        outs = export_obj(mesh, self._td, "x")
        sidecar = [p for p in outs if p.endswith(".cfmeta.json")][0]
        data = json.loads(Path(sidecar).read_text(encoding="utf-8"))
        self.assertEqual(data["source_format"], "pac")

    def test_cfmeta_has_submeshes_list(self):
        mesh = _make_quad_mesh()
        outs = export_obj(mesh, self._td, "x")
        sidecar = [p for p in outs if p.endswith(".cfmeta.json")][0]
        data = json.loads(Path(sidecar).read_text(encoding="utf-8"))
        self.assertEqual(len(data["submeshes"]), 1)

    def test_cfmeta_records_vertex_count(self):
        mesh = _make_quad_mesh()
        outs = export_obj(mesh, self._td, "x")
        sidecar = [p for p in outs if p.endswith(".cfmeta.json")][0]
        data = json.loads(Path(sidecar).read_text(encoding="utf-8"))
        self.assertEqual(data["submeshes"][0]["vertex_count"], 4)

    def test_cfmeta_records_bone_indices(self):
        mesh = _make_quad_mesh()
        outs = export_obj(mesh, self._td, "x")
        sidecar = [p for p in outs if p.endswith(".cfmeta.json")][0]
        data = json.loads(Path(sidecar).read_text(encoding="utf-8"))
        self.assertEqual(
            data["submeshes"][0]["bone_indices"],
            [[0, 1, 0, 0]] * 4,
        )

    def test_cfmeta_records_bone_weights(self):
        mesh = _make_quad_mesh()
        outs = export_obj(mesh, self._td, "x")
        sidecar = [p for p in outs if p.endswith(".cfmeta.json")][0]
        data = json.loads(Path(sidecar).read_text(encoding="utf-8"))
        self.assertEqual(
            data["submeshes"][0]["bone_weights"],
            [[0.6, 0.4, 0.0, 0.0]] * 4,
        )

    def test_cfmeta_records_submesh_name(self):
        mesh = _make_quad_mesh(name="my_sm")
        outs = export_obj(mesh, self._td, "x")
        sidecar = [p for p in outs if p.endswith(".cfmeta.json")][0]
        data = json.loads(Path(sidecar).read_text(encoding="utf-8"))
        self.assertEqual(data["submeshes"][0]["name"], "my_sm")


# ═════════════════════════════════════════════════════════════════════
# Sidecar is NOT written when there's no skin data
# ═════════════════════════════════════════════════════════════════════

class SidecarSkippedForNonSkinnedMesh(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self._td, ignore_errors=True))

    def test_no_sidecar_when_bone_indices_empty(self):
        mesh = _make_quad_mesh(has_bones=False)
        outs = export_obj(mesh, self._td, "x")
        sidecars = [p for p in outs if p.endswith(".cfmeta.json")]
        self.assertEqual(sidecars, [])

    def test_obj_still_written_without_bones(self):
        mesh = _make_quad_mesh(has_bones=False)
        outs = export_obj(mesh, self._td, "x")
        objs = [p for p in outs if p.endswith(".obj")]
        self.assertEqual(len(objs), 1)

    def test_mtl_still_written_without_bones(self):
        mesh = _make_quad_mesh(has_bones=False)
        outs = export_obj(mesh, self._td, "x")
        mtls = [p for p in outs if p.endswith(".mtl")]
        self.assertEqual(len(mtls), 1)


# ═════════════════════════════════════════════════════════════════════
# Import reads sidecar and restores skin data
# ═════════════════════════════════════════════════════════════════════

class ImportReadsSidecar(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self._td, ignore_errors=True))

    def test_bone_indices_round_trip(self):
        mesh = _make_quad_mesh()
        outs = export_obj(mesh, self._td, "x")
        obj = [p for p in outs if p.endswith(".obj")][0]
        back = import_obj(obj)
        self.assertEqual(
            back.submeshes[0].bone_indices,
            mesh.submeshes[0].bone_indices,
        )

    def test_bone_weights_round_trip(self):
        mesh = _make_quad_mesh()
        outs = export_obj(mesh, self._td, "x")
        obj = [p for p in outs if p.endswith(".obj")][0]
        back = import_obj(obj)
        self.assertEqual(
            back.submeshes[0].bone_weights,
            mesh.submeshes[0].bone_weights,
        )

    def test_source_vertex_map_is_identity(self):
        mesh = _make_quad_mesh()
        outs = export_obj(mesh, self._td, "x")
        obj = [p for p in outs if p.endswith(".obj")][0]
        back = import_obj(obj)
        self.assertEqual(back.submeshes[0].source_vertex_map, [0, 1, 2, 3])

    def test_vertex_count_preserved(self):
        mesh = _make_quad_mesh()
        outs = export_obj(mesh, self._td, "x")
        obj = [p for p in outs if p.endswith(".obj")][0]
        back = import_obj(obj)
        self.assertEqual(len(back.submeshes[0].vertices), 4)

    def test_face_count_preserved(self):
        mesh = _make_quad_mesh()
        outs = export_obj(mesh, self._td, "x")
        obj = [p for p in outs if p.endswith(".obj")][0]
        back = import_obj(obj)
        self.assertEqual(len(back.submeshes[0].faces), 2)

    def test_submesh_name_preserved(self):
        mesh = _make_quad_mesh(name="cloak_body")
        outs = export_obj(mesh, self._td, "x")
        obj = [p for p in outs if p.endswith(".obj")][0]
        back = import_obj(obj)
        self.assertEqual(back.submeshes[0].name, "cloak_body")

    def test_multiple_submeshes_matched_by_name(self):
        sm1 = SubMesh(
            name="A", vertices=[(0, 0, 0)] * 3, uvs=[(0, 0)] * 3,
            normals=[(0, 0, 1)] * 3, faces=[(0, 1, 2)],
            bone_indices=[(0, 0, 0, 0)] * 3,
            bone_weights=[(1.0, 0, 0, 0)] * 3,
            vertex_count=3, face_count=1,
        )
        sm2 = SubMesh(
            name="B", vertices=[(2, 0, 0)] * 3, uvs=[(0, 0)] * 3,
            normals=[(0, 0, 1)] * 3, faces=[(0, 1, 2)],
            bone_indices=[(2, 3, 0, 0)] * 3,
            bone_weights=[(0.5, 0.5, 0, 0)] * 3,
            vertex_count=3, face_count=1,
        )
        mesh = ParsedMesh(
            path="x.pac", format="pac", submeshes=[sm1, sm2],
            total_vertices=6, total_faces=2,
            has_uvs=True, has_bones=True,
        )
        outs = export_obj(mesh, self._td, "x")
        obj = [p for p in outs if p.endswith(".obj")][0]
        back = import_obj(obj)
        # Each submesh gets its OWN bone data, correctly routed by name.
        by_name = {sm.name: sm for sm in back.submeshes}
        self.assertEqual(by_name["A"].bone_indices[0], (0, 0, 0, 0))
        self.assertEqual(by_name["B"].bone_indices[0], (2, 3, 0, 0))


# ═════════════════════════════════════════════════════════════════════
# Missing sidecar — graceful fallback
# ═════════════════════════════════════════════════════════════════════

class MissingSidecarFallback(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self._td, ignore_errors=True))

    def _export_and_strip_sidecar(self):
        mesh = _make_quad_mesh()
        outs = export_obj(mesh, self._td, "x")
        sidecar = [p for p in outs if p.endswith(".cfmeta.json")][0]
        os.remove(sidecar)
        return [p for p in outs if p.endswith(".obj")][0]

    def test_missing_sidecar_does_not_raise(self):
        obj = self._export_and_strip_sidecar()
        import_obj(obj)   # should not raise

    def test_missing_sidecar_empty_bone_indices(self):
        obj = self._export_and_strip_sidecar()
        back = import_obj(obj)
        self.assertEqual(back.submeshes[0].bone_indices, [])

    def test_missing_sidecar_empty_bone_weights(self):
        obj = self._export_and_strip_sidecar()
        back = import_obj(obj)
        self.assertEqual(back.submeshes[0].bone_weights, [])

    def test_missing_sidecar_source_map_still_identity(self):
        obj = self._export_and_strip_sidecar()
        back = import_obj(obj)
        self.assertEqual(back.submeshes[0].source_vertex_map, [0, 1, 2, 3])


# ═════════════════════════════════════════════════════════════════════
# Corrupt sidecar — graceful fallback
# ═════════════════════════════════════════════════════════════════════

class CorruptSidecarFallback(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self._td, ignore_errors=True))

    def _export_and_replace_sidecar(self, content: str):
        mesh = _make_quad_mesh()
        outs = export_obj(mesh, self._td, "x")
        sidecar = [p for p in outs if p.endswith(".cfmeta.json")][0]
        Path(sidecar).write_text(content, encoding="utf-8")
        return [p for p in outs if p.endswith(".obj")][0]

    def test_malformed_json_falls_back_silently(self):
        obj = self._export_and_replace_sidecar("{not valid json}")
        back = import_obj(obj)
        self.assertEqual(back.submeshes[0].bone_indices, [])

    def test_empty_sidecar_falls_back_silently(self):
        obj = self._export_and_replace_sidecar("")
        back = import_obj(obj)
        self.assertEqual(back.submeshes[0].bone_indices, [])

    def test_wrong_schema_version_ignored(self):
        obj = self._export_and_replace_sidecar(
            json.dumps({
                "schema_version": 999,
                "submeshes": [{
                    "name": "test_sm",
                    "vertex_count": 4,
                    "bone_indices": [[9, 9, 9, 9]] * 4,
                    "bone_weights": [[1, 0, 0, 0]] * 4,
                }],
            })
        )
        back = import_obj(obj)
        # Because schema_version is wrong, the sidecar is rejected.
        self.assertEqual(back.submeshes[0].bone_indices, [])

    def test_not_a_dict_ignored(self):
        obj = self._export_and_replace_sidecar("[1, 2, 3]")
        back = import_obj(obj)
        self.assertEqual(back.submeshes[0].bone_indices, [])


# ═════════════════════════════════════════════════════════════════════
# Bone-count variations (various skin widths)
# ═════════════════════════════════════════════════════════════════════

class BoneWidthVariations(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self._td, ignore_errors=True))

    def test_one_bone_per_vertex(self):
        mesh = _make_quad_mesh(
            bone_indices=[(5,)] * 4,
            bone_weights=[(1.0,)] * 4,
        )
        outs = export_obj(mesh, self._td, "x")
        obj = [p for p in outs if p.endswith(".obj")][0]
        back = import_obj(obj)
        self.assertEqual(back.submeshes[0].bone_indices[0], (5,))

    def test_two_bones_per_vertex(self):
        mesh = _make_quad_mesh(
            bone_indices=[(1, 2)] * 4,
            bone_weights=[(0.7, 0.3)] * 4,
        )
        outs = export_obj(mesh, self._td, "x")
        obj = [p for p in outs if p.endswith(".obj")][0]
        back = import_obj(obj)
        self.assertEqual(back.submeshes[0].bone_indices[0], (1, 2))
        self.assertEqual(back.submeshes[0].bone_weights[0], (0.7, 0.3))

    def test_eight_bones_per_vertex(self):
        idx = (0, 1, 2, 3, 4, 5, 6, 7)
        wts = (0.2, 0.15, 0.15, 0.1, 0.1, 0.1, 0.1, 0.1)
        mesh = _make_quad_mesh(
            bone_indices=[idx] * 4,
            bone_weights=[wts] * 4,
        )
        outs = export_obj(mesh, self._td, "x")
        obj = [p for p in outs if p.endswith(".obj")][0]
        back = import_obj(obj)
        self.assertEqual(back.submeshes[0].bone_indices[0], idx)
        self.assertEqual(back.submeshes[0].bone_weights[0], wts)

    def test_mixed_width_per_vertex(self):
        mesh = _make_quad_mesh(
            bone_indices=[
                (0, 1),
                (2, 3, 4),
                (5, 6, 7, 8),
                (9,),
            ],
            bone_weights=[
                (0.5, 0.5),
                (0.4, 0.3, 0.3),
                (0.3, 0.3, 0.2, 0.2),
                (1.0,),
            ],
        )
        outs = export_obj(mesh, self._td, "x")
        obj = [p for p in outs if p.endswith(".obj")][0]
        back = import_obj(obj)
        self.assertEqual(back.submeshes[0].bone_indices[0], (0, 1))
        self.assertEqual(back.submeshes[0].bone_indices[1], (2, 3, 4))
        self.assertEqual(back.submeshes[0].bone_indices[2], (5, 6, 7, 8))
        self.assertEqual(back.submeshes[0].bone_indices[3], (9,))


# ═════════════════════════════════════════════════════════════════════
# Export stability — writing the same mesh twice produces same output
# ═════════════════════════════════════════════════════════════════════

class ExportStability(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self._td, ignore_errors=True))

    def test_cfmeta_bytes_identical_two_runs(self):
        mesh = _make_quad_mesh()
        outs1 = export_obj(mesh, self._td, "a")
        outs2 = export_obj(mesh, self._td, "b")
        s1 = [p for p in outs1 if p.endswith(".cfmeta.json")][0]
        s2 = [p for p in outs2 if p.endswith(".cfmeta.json")][0]
        # The paths differ (a vs b), but contents are identical.
        self.assertEqual(
            Path(s1).read_bytes(),
            Path(s2).read_bytes(),
        )


if __name__ == "__main__":
    unittest.main()
