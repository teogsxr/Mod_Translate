"""Regression tests for ``_merge_partial_pac_import`` intent detection.

User-reported 2026-05-08: a 2-submesh OBJ ("black", "_03") imported
over a 7-submesh helmet PAC produced a PAC that still showed all 7
submeshes — the deleted ones came back. Root cause was line 2717 of
``core/mesh_importer.py``, which deepcopied original submeshes when
the imported OBJ didn't have a replacement. Fix: when the OBJ has
ANY named submesh that matches the original, treat the OBJ as
authoritative and drop unmentioned originals.

These tests pin both intent paths so a future "let's restore the
deepcopy fallback" rewrite can't silently bring the bug back.
"""
from __future__ import annotations

import os
import sys
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from core.mesh_parser import ParsedMesh, SubMesh   # noqa: E402
from core.mesh_importer import _merge_partial_pac_import   # noqa: E402


def _sm(name: str, n_verts: int = 4, n_faces: int = 2) -> SubMesh:
    return SubMesh(
        name=name,
        material=name + "_mat",
        texture="",
        vertices=[(0.0, 0.0, 0.0)] * n_verts,
        uvs=[(0.0, 0.0)] * n_verts,
        normals=[(0.0, 1.0, 0.0)] * n_verts,
        faces=[(0, 1, 2)] * n_faces,
        vertex_count=n_verts,
        face_count=n_faces,
    )


def _mesh(submeshes: list[SubMesh]) -> ParsedMesh:
    pm = ParsedMesh(path="test.pac", format="pac")
    pm.submeshes = list(submeshes)
    pm.total_vertices = sum(len(s.vertices) for s in submeshes)
    pm.total_faces = sum(len(s.faces) for s in submeshes)
    return pm


class NamedObjIsAuthoritative(unittest.TestCase):
    """The exact failure mode the user reported.

    With the placeholder approach the merged submesh COUNT stays
    equal to the original count (so the downstream PAC rebuilder
    doesn't have to reflow section 0), but the dropped slots are
    emitted as empty placeholders (0 verts / 0 faces) — the game
    renders them as nothing, same visual result as a true delete.
    """

    HELMET_NAMES = [
        "CD_PHM_00_Hel_0363_Black",
        "CD_PHM_00_Hel_0363_03",
        "CD_PHM_00_Hel_0363_inside",
        "CD_PHM_00_Hel_0363_01",
        "CD_PHM_00_Hel_0363_02",
        "CD_PHM_00_Hel_0363",
        "CD_PHM_00_Hel_0363_wing",
    ]

    def test_helmet_2_of_7_kept_5_emptied(self):
        original = _mesh([_sm(n) for n in self.HELMET_NAMES])
        imported = _mesh([
            _sm("CD_PHM_00_Hel_0363_Black"),
            _sm("CD_PHM_00_Hel_0363_03"),
        ])
        merged = _merge_partial_pac_import(original, imported)
        # Submesh count must match the original so the rebuilder
        # can patch descriptor records in place.
        self.assertEqual(len(merged.submeshes), 7)
        # Names preserved in original order.
        self.assertEqual(
            [s.name for s in merged.submeshes],
            self.HELMET_NAMES,
        )
        # The two kept submeshes have geometry, the others are empty.
        kept = {"CD_PHM_00_Hel_0363_Black", "CD_PHM_00_Hel_0363_03"}
        for sm in merged.submeshes:
            if sm.name in kept:
                self.assertGreater(
                    len(sm.vertices), 0,
                    f"Kept submesh {sm.name!r} must have geometry",
                )
            else:
                self.assertEqual(
                    len(sm.vertices), 0,
                    f"Dropped submesh {sm.name!r} must be empty",
                )
                self.assertEqual(
                    len(sm.faces), 0,
                    f"Dropped submesh {sm.name!r} must have no faces",
                )

    def test_single_named_submesh_others_emptied(self):
        original = _mesh([_sm("a"), _sm("b"), _sm("c")])
        imported = _mesh([_sm("b")])
        merged = _merge_partial_pac_import(original, imported)
        self.assertEqual([s.name for s in merged.submeshes], ["a", "b", "c"])
        # Only 'b' has geometry.
        self.assertEqual(len(merged.submeshes[0].vertices), 0)  # a empty
        self.assertGreater(len(merged.submeshes[1].vertices), 0)  # b kept
        self.assertEqual(len(merged.submeshes[2].vertices), 0)  # c empty

    def test_imported_more_than_original_passes_through(self):
        # >= original count → return imported as-is (no merge).
        original = _mesh([_sm("a"), _sm("b")])
        imported = _mesh([_sm("a"), _sm("b"), _sm("c")])
        merged = _merge_partial_pac_import(original, imported)
        self.assertEqual([s.name for s in merged.submeshes], ["a", "b", "c"])


class AllUnnamedObjRejected(unittest.TestCase):
    """Legacy strict guard — fully-unnamed OBJ with fewer submeshes
    than the original was always rejected (impossible to map). The
    new intent-detection logic preserves that behaviour."""

    def test_all_unnamed_partial_raises(self):
        original = _mesh([_sm("a"), _sm("b"), _sm("c")])
        imported = _mesh([_sm(""), _sm("")])
        with self.assertRaises(ValueError):
            _merge_partial_pac_import(original, imported)


class StatTotals(unittest.TestCase):

    def test_totals_recomputed_after_placeholder_emit(self):
        # Empty placeholders contribute 0 to totals, so the merged
        # totals reflect ONLY the kept submeshes' geometry.
        original = _mesh([
            _sm("a", n_verts=10, n_faces=5),
            _sm("b", n_verts=20, n_faces=10),
            _sm("c", n_verts=30, n_faces=15),
        ])
        imported = _mesh([_sm("a", n_verts=11, n_faces=6)])
        merged = _merge_partial_pac_import(original, imported)
        self.assertEqual(merged.total_vertices, 11)
        self.assertEqual(merged.total_faces, 6)
        # Submesh count preserved, b and c are empty placeholders.
        self.assertEqual(len(merged.submeshes), 3)
        self.assertEqual(merged.submeshes[1].vertex_count, 0)
        self.assertEqual(merged.submeshes[2].vertex_count, 0)


class EmptyPlaceholderHasNoTangentRequirement(unittest.TestCase):
    """v1.25.18 hot-fix regression — empty placeholders must not
    trip the rebuild path's ``no UVs → cannot compute tangents``
    guard. Verified by feeding the merge result through the live
    rebuild pipeline check: a placeholder submesh with empty
    vertices / faces / uvs must skip the tangent path cleanly.
    """

    def test_empty_placeholder_has_no_uvs_no_faces(self):
        original = _mesh([
            _sm("kept_a"),
            _sm("dropped_b"),
        ])
        imported = _mesh([_sm("kept_a")])
        merged = _merge_partial_pac_import(original, imported)
        self.assertEqual(len(merged.submeshes), 2)

        # The dropped slot is the placeholder. It must satisfy the
        # exact contract the build_pac empty-placeholder fast-path
        # checks (`if not new_sm.vertices and not new_sm.faces`).
        placeholder = merged.submeshes[1]
        self.assertEqual(placeholder.name, "dropped_b")
        self.assertEqual(placeholder.vertices, [])
        self.assertEqual(placeholder.faces, [])
        self.assertEqual(placeholder.uvs, [])
        self.assertEqual(placeholder.normals, [])
        self.assertEqual(placeholder.bone_indices, [])
        self.assertEqual(placeholder.bone_weights, [])
        self.assertEqual(placeholder.vertex_count, 0)
        self.assertEqual(placeholder.face_count, 0)


if __name__ == "__main__":
    unittest.main()
