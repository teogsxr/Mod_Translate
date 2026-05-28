"""Hook into mesh_parser to print bone palettes during PAC parsing.

Usage:
    python tools/probe_pac_palette.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.vfs_manager import VfsManager
from core.skeleton_parser import parse_pab
import core.mesh_parser as mp

# Monkey-patch the parser to log palettes
_orig = mp.parse_pac

skel_global = None

def patched(data, filename=""):
    # Replicate the early parse to extract submesh palettes
    import struct
    from core.mesh_parser import _parse_par_sections, PAR_MAGIC
    if len(data) < 0x50 or data[:4] != PAR_MAGIC:
        return _orig(data, filename)

    header_size = 80
    s0_start = header_size
    off = s0_start
    flags = struct.unpack_from("<I", data, off)[0]
    n_lods = data[off + 4]
    off += 5
    if n_lods == 0 or n_lods > 10:
        return _orig(data, filename)

    lod_offsets = [struct.unpack_from("<I", data, off + i * 4)[0] for i in range(n_lods)]
    off += n_lods * 4
    split_offsets = [struct.unpack_from("<I", data, off + i * 4)[0] for i in range(n_lods)]
    off += n_lods * 4

    sorted_offsets = sorted(lod_offsets)
    boundaries = [header_size] + sorted_offsets + [len(data)]
    sections = [(boundaries[i], boundaries[i + 1]) for i in range(len(boundaries) - 1)]
    s0_end = sections[0][1]

    scan = off
    while scan < s0_end - 10:
        b = data[scan]
        if 4 < b < 100:
            test = data[scan + 1:scan + 1 + b]
            if len(test) == b and all(32 <= c < 127 for c in test):
                break
        scan += 1
    off = scan

    print(f"PAC {filename}: n_lods={n_lods}, scan starts at offset 0x{off:x}")
    sm_idx = 0
    while off < s0_end - 20 and sm_idx < 10:
        name_len = data[off]
        if name_len == 0 or name_len > 200 or off + 1 + name_len >= s0_end:
            break
        mesh_name = data[off + 1:off + 1 + name_len].decode("ascii", "replace")
        off += 1 + name_len
        if not all(32 <= ord(c) < 127 for c in mesh_name):
            break

        mat_len = data[off]
        mat_name = data[off + 1:off + 1 + mat_len].decode("ascii", "replace") if mat_len > 0 else ""
        off += 1 + mat_len

        off += 3  # flag + pad
        bbox_floats = [struct.unpack_from("<f", data, off + i * 4)[0] for i in range(8)]
        off += 32

        bone_count = data[off]
        off += 1
        bone_palette = list(data[off:off + bone_count])
        bones_size = bone_count + (bone_count % 2)
        off += bones_size

        print(f"\n--- Submesh #{sm_idx}: {mesh_name!r}")
        print(f"    Material: {mat_name!r}")
        print(f"    Bone palette ({bone_count} bones):")
        for i, gpi in enumerate(bone_palette):
            bn = skel_global.bones[gpi].name if skel_global and gpi < len(skel_global.bones) else f"BONE_{gpi}"
            print(f"      slot[{i:>3d}] -> bone[{gpi:>3d}] {bn}")

        # Skip vert/idx counts
        off += n_lods * 2
        for _ in range(n_lods):
            if off + 4 > s0_end:
                break
            val = struct.unpack_from("<I", data, off)[0]
            if val > 10_000_000:
                break
            off += 4

        sm_idx += 1

    return _orig(data, filename)


mp.parse_pac = patched


def main():
    global skel_global
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

    pac = vfs.read_entry_data(lookup("character/cd_phw_00_nude_00_0001_damian.pac"))
    pab = vfs.read_entry_data(lookup("character/phw_01.pab"))
    skel_global = parse_pab(pab, "phw_01.pab")
    print(f"PAB has {len(skel_global.bones)} bones")

    mesh = patched(pac, "damian.pac")
    print(f"\nFinal mesh: {mesh.total_vertices} verts, {len(mesh.submeshes)} submeshes")


if __name__ == "__main__":
    main()
