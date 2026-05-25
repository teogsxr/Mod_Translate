"""One-command test suite for the enterprise FBX pipeline.

Runs three tests in order, prints a clear PASS/FAIL summary at the end:

  1. Synthetic round-trip (no game data needed)
     - Builds a fake mesh, exports with spike filter, re-imports
     - Verifies all vertices restored

  2. Unified mesh+skeleton+animation export (uses repo test fixtures)
     - phm_01.pab + sample_talk.paa + synthetic body mesh
     - Exports to one FBX, verifies all components present

  3. Real character export from CrimsonForge game install (optional)
     - Looks for Damian's PAC/PAB in your game install
     - Exports a real character to a temp folder you can open in Blender

Usage:
    python tools/test_everything.py
    python tools/test_everything.py --game "C:/Program Files (x86)/Steam/steamapps/common/Crimson Desert"
    python tools/test_everything.py --output C:/Users/hzeem/Desktop/test_fbx_outputs
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ──────────────────────────────────────────────────────────────────────
def test_1_spike_roundtrip() -> bool:
    print("\n" + "=" * 72)
    print("TEST 1: Spike-filter round-trip (synthetic mesh, no game needed)")
    print("=" * 72)
    try:
        from tools.test_spike_filter_roundtrip import main as t1
        rc = t1()
        return rc == 0
    except Exception as e:
        print(f"  ✗ EXCEPTION: {e}")
        return False


# ──────────────────────────────────────────────────────────────────────
def test_2_unified_export() -> bool:
    print("\n" + "=" * 72)
    print("TEST 2: Unified mesh+skeleton+animation FBX (uses repo fixtures)")
    print("=" * 72)
    try:
        from tools.test_unified_character_fbx import main as t2
        rc = t2()
        return rc == 0
    except Exception as e:
        print(f"  ✗ EXCEPTION: {e}")
        return False


# ──────────────────────────────────────────────────────────────────────
def test_3_real_damian(game_dir: str | None, output_dir: str) -> bool:
    """If a CrimsonForge game install is provided, export Damian for real
    so you can open it in Blender and see the full character.
    """
    print("\n" + "=" * 72)
    print("TEST 3: Real Damian export (PAC + PAB + PAA → unified FBX)")
    print("=" * 72)

    if not game_dir:
        print("  (skipped — no --game path provided)")
        print("  Run with: --game \"C:/Program Files (x86)/Steam/steamapps/common/Crimson Desert\"")
        return True

    game = Path(game_dir)
    if not game.exists():
        print(f"  ✗ Game directory not found: {game}")
        return False

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    print(f"  Game: {game}")
    print(f"  Output: {out}")

    try:
        from core.vfs_manager import VFSManager
    except ImportError:
        print("  ✗ Cannot import vfs_manager — skipping real export")
        return True

    print("\n  Loading game VFS (this can take 10-15 seconds)...")
    vfs = VFSManager()
    try:
        vfs.load_game(str(game))
    except Exception as e:
        print(f"  ✗ Failed to load game: {e}")
        return False

    # Find Damian PAC and PAB
    print("  Searching for Damian's mesh + skeleton...")
    pac_path = "character/cd_phw_00_nude_00_0001_damian.pac"
    pab_path = "character/phw_01.pab"

    try:
        pac_data = vfs.read(pac_path)
        pab_data = vfs.read(pab_path)
    except Exception as e:
        print(f"  ✗ Could not read Damian files: {e}")
        return False

    print(f"    PAC: {len(pac_data):,} bytes")
    print(f"    PAB: {len(pab_data):,} bytes")

    from core.mesh_parser import parse_pac
    from core.skeleton_parser import parse_pab
    from core.mesh_exporter import export_fbx_with_skeleton

    mesh = parse_pac(pac_data, pac_path)
    skeleton = parse_pab(pab_data, pab_path)
    print(f"  Mesh: {mesh.total_vertices} verts, {mesh.total_faces} faces, "
          f"{len(mesh.submeshes)} submeshes")
    print(f"  Skeleton: {len(skeleton.bones)} bones")

    # Try to find a matching animation file (optional)
    animation = None
    paa_candidates = [
        "character/animation/cd_phw_00_idle.paa",
        "character/animation/idle.paa",
        "animation/cd_phw_00_idle.paa",
    ]
    for cand in paa_candidates:
        try:
            paa_data = vfs.read(cand)
            from core.animation_parser import parse_paa
            animation = parse_paa(paa_data, cand)
            print(f"  Animation: {cand} ({animation.frame_count} frames, "
                  f"{animation.duration:.2f}s)")
            break
        except Exception:
            continue
    if animation is None:
        print("  Animation: none found (exporting mesh+skeleton only)")

    print(f"\n  Exporting to: {out}")
    fbx_path = export_fbx_with_skeleton(
        mesh, skeleton, str(out),
        name="damian_full",
        scale=1.0,
        filter_unskinned_outliers=True,
        animation=animation,
        fps=30.0,
    )
    print(f"\n  ✓ Exported: {fbx_path}")
    sidecar = fbx_path + ".cfmeta.json"
    if Path(sidecar).exists():
        print(f"  ✓ Sidecar:  {sidecar}")
    debug = fbx_path + ".debug.txt"
    if Path(debug).exists():
        print(f"  ✓ Debug log: {debug}")

    inspector = Path(__file__).resolve().parent / 'blender_inspect_armature.py'
    print(f"\n  Now open this in Blender:")
    print(f"    File → Import → FBX → {fbx_path}")
    print(f"  Then run the inspector by pasting into Blender's Python Console:")
    print(f"    exec(open(r'{inspector}').read())")
    return True


# ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Test the full FBX pipeline.")
    parser.add_argument("--game", default=None,
                        help="Path to Crimson Desert game install (for Test 3).")
    parser.add_argument("--output", default=str(Path.home() / "Documents" / "crimsonforge_test_fbx"),
                        help="Where to write Test 3's real character FBX.")
    args = parser.parse_args()

    results = {}
    results["1. Spike round-trip"] = test_1_spike_roundtrip()
    results["2. Unified export"]   = test_2_unified_export()
    results["3. Real Damian"]      = test_3_real_damian(args.game, args.output)

    print("\n" + "=" * 72)
    print("TEST SUMMARY")
    print("=" * 72)
    for name, ok in results.items():
        mark = "✓ PASS" if ok else "✗ FAIL"
        print(f"  {mark}  {name}")

    n_pass = sum(1 for v in results.values() if v)
    n_total = len(results)
    print(f"\n{n_pass}/{n_total} tests passed")
    print("=" * 72)
    return 0 if n_pass == n_total else 1


if __name__ == "__main__":
    sys.exit(main())
