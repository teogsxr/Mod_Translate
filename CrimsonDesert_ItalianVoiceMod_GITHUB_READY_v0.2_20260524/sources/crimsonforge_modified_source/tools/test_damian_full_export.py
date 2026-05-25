"""End-to-end FBX export test for Damian walk.

Runs the same path the Explorer's "Export Full Character FBX" menu
runs, but headless. Outputs to ./export_test/.

Usage:
    python tools/test_damian_full_export.py
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.vfs_manager import VfsManager
from core.skeleton_parser import parse_pab
from core.mesh_parser import parse_pac, apply_skin_palette
from core.pabc_skin_palette import load_skin_pabc, find_pabc_for_pac
from core.animation_parser import parse_paa_with_resolution
from core.mesh_exporter import export_fbx_with_skeleton


def lookup(vfs, pth):
    target = pth.replace("\\", "/").lower()
    for _g, pamt in vfs._pamt_cache.items():
        for e in pamt.file_entries:
            if (e.path or "").replace("\\", "/").lower() == target:
                return e
    return None


def extract_pab_bone_hashes(pab_data, n_bones):
    hashes = []
    off = 0x17
    for _ in range(n_bones):
        if off + 4 > len(pab_data):
            break
        h = struct.unpack_from('<I', pab_data, off)[0] & 0x00FFFFFF
        name_len = pab_data[off + 3]
        hashes.append(h)
        off += 4 + name_len + 4 + 256 + 40 + 1
    return hashes


def main():
    game = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Crimson Desert")
    pkg = game / "packages" if (game / "packages").is_dir() else game
    print(f"Loading VFS from {pkg}")
    vfs = VfsManager(str(pkg))
    for g in vfs.list_package_groups():
        try:
            vfs.load_pamt(g)
        except Exception:
            pass

    # Damian asset paths (verified from prior conversations)
    pac_path = "character/cd_phw_00_nude_00_0001_damian.pac"
    pab_path = "character/phw_01.pab"
    paa_path = "character/cd_damian_rd_sg_basic_01_01_nor_move_walk_f_ing_00.paa"

    print(f"\nResolving assets:")
    print(f"  PAC: {pac_path}")
    print(f"  PAB: {pab_path}")
    print(f"  PAA: {paa_path}")

    pac_e = lookup(vfs, pac_path)
    pab_e = lookup(vfs, pab_path)
    paa_e = lookup(vfs, paa_path)

    if pac_e is None:
        # Try alternative Damian PAC paths
        for guess in ("character/cd_damian_pc_basic_010_a_a.pac",
                      "character/cd_damian.pac",
                      "character/cd_damian_pc_basic_001_a_a.pac"):
            pac_e = lookup(vfs, guess)
            if pac_e:
                pac_path = guess
                break

    if not pac_e:
        # Search for any cd_damian PAC
        print("\nSearching for any cd_damian*.pac file...")
        for _g, pamt in vfs._pamt_cache.items():
            for e in pamt.file_entries:
                p = (e.path or "").replace("\\", "/").lower()
                if "cd_damian" in p and p.endswith(".pac"):
                    pac_e = e
                    pac_path = e.path
                    print(f"  Found: {pac_path}")
                    break
            if pac_e:
                break

    if not (pac_e and pab_e and paa_e):
        print(f"Missing: pac={pac_e is not None} pab={pab_e is not None} paa={paa_e is not None}")
        return 1

    print(f"\nReading data...")
    pac_data = vfs.read_entry_data(pac_e)
    pab_data = vfs.read_entry_data(pab_e)
    paa_data = vfs.read_entry_data(paa_e)
    print(f"  PAC: {len(pac_data):,} bytes")
    print(f"  PAB: {len(pab_data):,} bytes")
    print(f"  PAA: {len(paa_data):,} bytes")

    print(f"\nParsing skeleton...")
    skeleton = parse_pab(pab_data, pab_path)
    print(f"  Bones: {len(skeleton.bones)}")

    print(f"\nExtracting PAB hashes...")
    pab_hashes = extract_pab_bone_hashes(pab_data, len(skeleton.bones))
    print(f"  Hashes: {len(pab_hashes)}")

    print(f"\nParsing mesh...")
    mesh = parse_pac(pac_data, pac_path)
    print(f"  Vertices: {mesh.total_vertices:,}, Faces: {mesh.total_faces:,}, "
          f"Submeshes: {len(mesh.submeshes)}")
    # PABC palette remap disabled — interpretation of slot→PABC record
    # is incorrect. Body submesh slots 0-47 don't map cleanly to the
    # 437 PABC records (would put leg bones on chest verts).

    print(f"\nParsing animation...")
    animation = parse_paa_with_resolution(
        paa_data, paa_path, vfs=vfs, max_hops=5,
        pab_bone_hashes=pab_hashes,
        pab_bone_count=len(skeleton.bones),
    )
    print(f"  Frames: {animation.frame_count}, Bones: {animation.bone_count}, "
          f"Duration: {animation.duration:.2f}s")

    out_dir = Path(__file__).resolve().parent.parent / "export_test"
    out_dir.mkdir(exist_ok=True)
    print(f"\nExporting to {out_dir}")

    fbx_path = export_fbx_with_skeleton(
        mesh, skeleton, str(out_dir),
        name="damian_walk_test",
        scale=1.0,
        filter_unskinned_outliers=False,
        animation=animation,
        fps=30.0,
    )
    print(f"\n=== EXPORT SUCCESS ===")
    print(f"FBX written: {fbx_path}")
    print(f"Size: {Path(fbx_path).stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
