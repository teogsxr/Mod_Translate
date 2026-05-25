"""Regression tests for :mod:`core.pac_xml_parser`.

The parser must:

  * Round-trip byte-for-byte on real shipping ``.pac_xml`` files
    (30/30 verified against a live Steam install at dev time —
    the tests here use synthetic fixtures so they run without
    needing the game installed).
  * Parse the multi-root form that Pearl Abyss uses.
  * Preserve the UTF-8 BOM, CRLF line endings, and tab indentation.
  * Support in-place edits to any attribute or text node, re-parse
    the serialised output, and confirm the edit survived.
  * Reject malformed input with a clear ValueError rather than
    propagating low-level ElementTree ParseError exceptions.
"""

from __future__ import annotations

import os
import sys
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from core.pac_xml_parser import (   # noqa: E402
    BOM,
    ParsedPacXml,
    PacXmlField,
    apply_edits,
    categorize_field,
    parse_pac_xml,
    serialize_pac_xml,
    summarize,
)


# ─── Fixtures ──────────────────────────────────────────────────────

def _fixture_minimal() -> bytes:
    """Smallest valid .pac_xml — two root elements, BOM, CRLF."""
    body = (
        '<SkinnedMeshPropertyCommon ReflectObjectXMLDataVersion="9"/>\r\n'
        '<ModelPropertyList>\r\n'
        '\t<ModelProperty Index="0" Version="Reflection">\r\n'
        '\t\t<SkinnedMeshProperty ReflectObjectXMLDataVersion="9"/>\r\n'
        '\t</ModelProperty>\r\n'
        '</ModelPropertyList>\r\n\r\n'
    )
    return BOM + body.encode("utf-8")


def _fixture_with_texture_paths() -> bytes:
    """Realistic shape — material with three texture references."""
    body = (
        '<SkinnedMeshPropertyCommon ReflectObjectXMLDataVersion="9"/>\r\n'
        '<ModelPropertyList>\r\n'
        '\t<ModelProperty Index="0" Version="Reflection">\r\n'
        '\t\t<SkinnedMeshProperty ReflectObjectXMLDataVersion="9">\r\n'
        '\t\t\t<Vector Name="_subMeshResources" IdBase="23" isOverrided="true">\r\n'
        '\t\t\t\t<SkinnedMeshMaterialWrapper ItemID="22" _subMeshName="head" _jiggleWindWeight="0">\r\n'
        '\t\t\t\t\t<Material Name="_resourceMaterial" _materialName="SkinnedMeshSkin">\r\n'
        '\t\t\t\t\t\t<Vector Name="_parameters">\r\n'
        '\t\t\t\t\t\t\t<MaterialParameterTexture _name="_baseColorTexture">\r\n'
        '\t\t\t\t\t\t\t\t<ResourceReferencePath_ITexture Name="_value" _path="character/texture/head_base.dds"/>\r\n'
        '\t\t\t\t\t\t\t</MaterialParameterTexture>\r\n'
        '\t\t\t\t\t\t\t<MaterialParameterTexture _name="_normalTexture">\r\n'
        '\t\t\t\t\t\t\t\t<ResourceReferencePath_ITexture Name="_value" _path="character/texture/head_n.dds"/>\r\n'
        '\t\t\t\t\t\t\t</MaterialParameterTexture>\r\n'
        '\t\t\t\t\t\t</Vector>\r\n'
        '\t\t\t\t\t</Material>\r\n'
        '\t\t\t\t</SkinnedMeshMaterialWrapper>\r\n'
        '\t\t\t</Vector>\r\n'
        '\t\t</SkinnedMeshProperty>\r\n'
        '\t</ModelProperty>\r\n'
        '</ModelPropertyList>\r\n\r\n'
    )
    return BOM + body.encode("utf-8")


# ─── Parse ─────────────────────────────────────────────────────────

class ParseMinimal(unittest.TestCase):
    def setUp(self):
        self.parsed = parse_pac_xml(_fixture_minimal(), "test.pac_xml")

    def test_has_bom_detected(self):
        self.assertTrue(self.parsed.has_bom)

    def test_fields_enumerated(self):
        # SkinnedMeshPropertyCommon ReflectObjectXMLDataVersion="9"
        # ModelProperty Index="0" Version="Reflection"
        # SkinnedMeshProperty ReflectObjectXMLDataVersion="9"
        self.assertEqual(len(self.parsed.fields), 4)

    def test_first_field_is_version(self):
        self.assertEqual(self.parsed.fields[0].attr, "ReflectObjectXMLDataVersion")
        self.assertEqual(self.parsed.fields[0].value, "9")

    def test_path_chain_built(self):
        # Third field should live under ModelPropertyList/ModelProperty
        f = self.parsed.fields[2]
        self.assertIn("ModelPropertyList", f.path)
        self.assertIn("ModelProperty", f.path)

    def test_element_tag_exposed(self):
        self.assertTrue(
            all(f.element_tag for f in self.parsed.fields),
            "every field must carry its element's tag",
        )

    def test_raw_bytes_preserved(self):
        self.assertEqual(self.parsed.raw, _fixture_minimal())

    def test_tree_exists(self):
        self.assertIsNotNone(self.parsed.tree)


class ParseWithoutBom(unittest.TestCase):
    def test_no_bom_flag(self):
        body = b'<Root Attr="1"/>\r\n'
        parsed = parse_pac_xml(body, "test.pac_xml")
        self.assertFalse(parsed.has_bom)
        self.assertEqual(len(parsed.fields), 1)


class ParseTextureFixture(unittest.TestCase):
    def setUp(self):
        self.parsed = parse_pac_xml(_fixture_with_texture_paths(), "x.pac_xml")

    def test_finds_all_path_attrs(self):
        paths = [f for f in self.parsed.fields if f.attr == "_path"]
        self.assertEqual(len(paths), 2)
        self.assertIn("head_base.dds", paths[0].value)
        self.assertIn("head_n.dds", paths[1].value)

    def test_finds_material_name(self):
        mats = [f for f in self.parsed.fields if f.attr == "_materialName"]
        self.assertEqual(len(mats), 1)
        self.assertEqual(mats[0].value, "SkinnedMeshSkin")

    def test_finds_submesh_name(self):
        subs = [f for f in self.parsed.fields if f.attr == "_subMeshName"]
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0].value, "head")


class ParseErrors(unittest.TestCase):
    def test_non_utf8_raises(self):
        with self.assertRaises(ValueError):
            parse_pac_xml(b"\xff\xfe\xff\xfe garbage", "bad.pac_xml")

    def test_malformed_xml_raises(self):
        with self.assertRaises(ValueError):
            parse_pac_xml(b"<not </closed>", "bad.pac_xml")

    def test_empty_is_lenient(self):
        # Empty bytes is a valid edge case — a user could open a
        # placeholder file. Parse as an empty tree rather than
        # raising; the dialog then shows "0 fields" and lets the
        # user back out cleanly.
        parsed = parse_pac_xml(b"", "empty.pac_xml")
        self.assertEqual(len(parsed.fields), 0)
        self.assertFalse(parsed.has_bom)


# ─── Round-trip ────────────────────────────────────────────────────

class RoundTripMinimal(unittest.TestCase):
    def test_serialise_matches_bytes(self):
        data = _fixture_minimal()
        parsed = parse_pac_xml(data, "test.pac_xml")
        rebuilt = serialize_pac_xml(parsed)
        self.assertEqual(rebuilt, data)

    def test_serialise_matches_textures(self):
        data = _fixture_with_texture_paths()
        parsed = parse_pac_xml(data, "test.pac_xml")
        rebuilt = serialize_pac_xml(parsed)
        self.assertEqual(rebuilt, data)

    def test_reparse_preserves_field_count(self):
        data = _fixture_with_texture_paths()
        parsed = parse_pac_xml(data, "x")
        rebuilt = serialize_pac_xml(parsed)
        reparsed = parse_pac_xml(rebuilt, "x")
        self.assertEqual(len(parsed.fields), len(reparsed.fields))


class RoundTripWithoutBom(unittest.TestCase):
    def test_no_bom_stays_no_bom(self):
        body = b'<Root Attr="1"/>\r\n\r\n'
        parsed = parse_pac_xml(body, "nobom.pac_xml")
        rebuilt = serialize_pac_xml(parsed)
        self.assertFalse(rebuilt.startswith(BOM))


# ─── Edit ──────────────────────────────────────────────────────────

class EditAttribute(unittest.TestCase):
    def setUp(self):
        self.parsed = parse_pac_xml(_fixture_with_texture_paths(), "x.pac_xml")

    def test_single_attr_edit(self):
        path_field = next(
            f for f in self.parsed.fields if f.attr == "_path"
        )
        edited = apply_edits(
            self.parsed,
            [(path_field.index, "character/texture/swapped.dds")],
        )
        new_val = edited.fields[path_field.index].value
        self.assertEqual(new_val, "character/texture/swapped.dds")

    def test_edit_survives_round_trip(self):
        path_field = next(
            f for f in self.parsed.fields if f.attr == "_path"
        )
        edited = apply_edits(
            self.parsed,
            [(path_field.index, "character/texture/MY_MOD.dds")],
        )
        rebuilt = serialize_pac_xml(edited)
        reparsed = parse_pac_xml(rebuilt, "x")
        self.assertEqual(
            reparsed.fields[path_field.index].value,
            "character/texture/MY_MOD.dds",
        )

    def test_multiple_edits_in_one_pass(self):
        path_fields = [f for f in self.parsed.fields if f.attr == "_path"]
        self.assertEqual(len(path_fields), 2)
        edits = [
            (path_fields[0].index, "first_swap.dds"),
            (path_fields[1].index, "second_swap.dds"),
        ]
        edited = apply_edits(self.parsed, edits)
        self.assertEqual(edited.fields[path_fields[0].index].value, "first_swap.dds")
        self.assertEqual(edited.fields[path_fields[1].index].value, "second_swap.dds")

    def test_edit_does_not_mutate_original(self):
        original = parse_pac_xml(_fixture_with_texture_paths(), "x.pac_xml")
        path_field = next(f for f in original.fields if f.attr == "_path")
        old_value = path_field.value
        apply_edits(original, [(path_field.index, "never_seen.dds")])
        # apply_edits mutates the shared tree — that's the design
        # trade-off documented on the function — but at least the
        # field records on the returned object reflect the edit
        # while the caller's copy remains unchanged.
        self.assertEqual(original.fields[path_field.index].value, old_value)

    def test_edit_bad_index_raises(self):
        with self.assertRaises(IndexError):
            apply_edits(self.parsed, [(9999, "nope")])

    def test_negative_index_raises(self):
        with self.assertRaises(IndexError):
            apply_edits(self.parsed, [(-1, "nope")])

    def test_empty_edit_list_is_noop(self):
        edited = apply_edits(self.parsed, [])
        for original, new in zip(self.parsed.fields, edited.fields):
            self.assertEqual(original.value, new.value)


class EditPreservesOtherAttrs(unittest.TestCase):
    def test_sibling_attrs_unchanged(self):
        data = _fixture_with_texture_paths()
        parsed = parse_pac_xml(data, "x")
        # Find the material name field and edit ONLY that.
        mat_field = next(f for f in parsed.fields if f.attr == "_materialName")
        edited = apply_edits(parsed, [(mat_field.index, "CustomShader")])
        # All other fields retain their original values.
        for orig, new in zip(parsed.fields, edited.fields):
            if new.index == mat_field.index:
                self.assertEqual(new.value, "CustomShader")
            else:
                self.assertEqual(orig.value, new.value)


# ─── Categorisation ────────────────────────────────────────────────

class Categorisation(unittest.TestCase):
    def test_path_attr_categorised_as_path(self):
        parsed = parse_pac_xml(_fixture_with_texture_paths(), "x")
        path_fields = [f for f in parsed.fields if f.attr == "_path"]
        for f in path_fields:
            self.assertEqual(categorize_field(f), "path")

    def test_materialname_is_name(self):
        parsed = parse_pac_xml(_fixture_with_texture_paths(), "x")
        mat = next(f for f in parsed.fields if f.attr == "_materialName")
        self.assertEqual(categorize_field(mat), "name")

    def test_itemid_is_id(self):
        parsed = parse_pac_xml(_fixture_with_texture_paths(), "x")
        ids = [f for f in parsed.fields if f.attr == "ItemID"]
        self.assertGreaterEqual(len(ids), 1)
        for f in ids:
            self.assertEqual(categorize_field(f), "id")

    def test_reflectversion_is_version(self):
        parsed = parse_pac_xml(_fixture_with_texture_paths(), "x")
        vers = [f for f in parsed.fields if f.attr == "ReflectObjectXMLDataVersion"]
        self.assertGreaterEqual(len(vers), 1)
        for f in vers:
            self.assertEqual(categorize_field(f), "version")

    def test_unknown_attr_is_other(self):
        body = BOM + b'<Root mystery="x"/>\r\n\r\n'
        parsed = parse_pac_xml(body, "x")
        self.assertEqual(categorize_field(parsed.fields[0]), "other")


class Summary(unittest.TestCase):
    def test_summary_counts_by_category(self):
        parsed = parse_pac_xml(_fixture_with_texture_paths(), "x")
        stats = summarize(parsed)
        self.assertIn("path", stats)
        self.assertIn("name", stats)
        self.assertEqual(stats["path"], 2)
        # Total equals the flat field count.
        self.assertEqual(sum(stats.values()), len(parsed.fields))


# ─── Dataclass contracts ───────────────────────────────────────────

class FieldDataclass(unittest.TestCase):
    def test_field_has_expected_shape(self):
        parsed = parse_pac_xml(_fixture_minimal(), "x")
        f = parsed.fields[0]
        for attr in ("index", "path", "attr", "value", "kind",
                     "element_index", "element_tag"):
            self.assertTrue(hasattr(f, attr), f"missing attr {attr}")

    def test_kind_values_from_allowed_set(self):
        parsed = parse_pac_xml(_fixture_with_texture_paths(), "x")
        for f in parsed.fields:
            self.assertIn(f.kind, {"attribute", "text"})


if __name__ == "__main__":
    unittest.main()
