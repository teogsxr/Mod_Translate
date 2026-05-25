"""For Damian's PAC, dump per-bone vertex statistics:
  * which vertices are weighted to L UpperArm? Should be in the L upper arm region
  * which vertices are weighted to L Thigh? Should be in the L thigh region
  * which to Head? Should be in the head region

If the regions don't match, the vertex bone indices are WRONG.

Usage:
    python tools/probe_pac_weights.py
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.vfs_manager import VfsManager
from core.skeleton_parser import parse_pab
from core.mesh_parser import parse_pac


def main():
    game = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Crimson Desert")
    pkg = game / "packages" if (game / "packages").is_dir() else game
    vfs = VfsManager(str(pkg))
    for g in vfs.list_package_groups():
        try:
            vfs.load_pamt(g)
        except Exception:
            pass

    def lookup(p):
        target = p.replace("\\", "/").lower()
        for _g, pamt in vfs._pamt_cache.items():
            for e in pamt.file_entries:
                if (e.path or "").replace("\\", "/").lower() == target:
                    return e
        return None

    pac_data = vfs.read_entry_data(lookup("character/cd_phw_00_nude_00_0001_damian.pac"))
    pab_data = vfs.read_entry_data(lookup("character/phw_01.pab"))

    mesh = parse_pac(pac_data, "damian.pac")
    skel = parse_pab(pab_data, "phw_01.pab")

    print(f"Mesh: {mesh.total_vertices} verts in {len(mesh.submeshes)} submeshes")
    print(f"Mesh bbox: {mesh.bbox_min} -> {mesh.bbox_max}")
    print(f"Skel: {len(skel.bones)} bones")

    # For each submesh, show the bbox
    for sm in mesh.submeshes:
        if sm.vertices:
            xs = [v[0] for v in sm.vertices]
            ys = [v[1] for v in sm.vertices]
            zs = [v[2] for v in sm.vertices]
            print(f"  Submesh {sm.name!r:<45s}: {len(sm.vertices)} verts  "
                  f"y=[{min(ys):.2f}..{max(ys):.2f}]  "
                  f"x=[{min(xs):.2f}..{max(xs):.2f}]  "
                  f"z=[{min(zs):.2f}..{max(zs):.2f}]")

    name_to_idx = {b.name: i for i, b in enumerate(skel.bones)}

    # Test: check vertex regions for specific bones
    test_bones = ['Bip01 L Thigh', 'Bip01 R Thigh', 'Bip01 L Calf', 'Bip01 L Foot',
                  'Bip01 L UpperArm', 'Bip01 R UpperArm', 'Bip01 L Forearm', 'Bip01 L Hand',
                  'Bip01 R Hand', 'Bip01 Head', 'Bip01 Neck', 'Bip01 Spine']

    print(f"\n=== Per-bone vertex region analysis ===")
    print(f"For each bone, list bbox of vertices weighted to it (>10% weight).")
    print(f"Expected:")
    print(f"  L Thigh / L Calf / L Foot: low Y, slight +X (left side)")
    print(f"  L UpperArm / L Hand: high Y, large +X")
    print(f"  Head: highest Y, near 0 X")
    print()

    for bn in test_bones:
        bi = name_to_idx.get(bn)
        if bi is None:
            print(f"  {bn}: bone not in skel")
            continue
        # Find all vertices in any submesh weighted to bone bi
        verts_at = []
        for sm in mesh.submeshes:
            for (pos, idxs, wts) in zip(sm.vertices, sm.bone_indices, sm.bone_weights):
                for slot_bi, w in zip(idxs, wts):
                    if slot_bi == bi and w > 0.1:
                        verts_at.append(pos)
                        break
        if not verts_at:
            print(f"  bone[{bi:3d}] {bn:<22s}: NO VERTICES weighted to this bone")
            continue
        xs = [p[0] for p in verts_at]
        ys = [p[1] for p in verts_at]
        zs = [p[2] for p in verts_at]
        print(f"  bone[{bi:3d}] {bn:<22s} ({len(verts_at):>4d} verts):  "
              f"y=[{min(ys):+.2f}..{max(ys):+.2f}]  "
              f"x=[{min(xs):+.2f}..{max(xs):+.2f}]  "
              f"z=[{min(zs):+.2f}..{max(zs):+.2f}]")


if __name__ == "__main__":
    main()
