"""Run inside Blender after importing a PAA-exported FBX.

Verifies that animation curves were:
  1. Imported by Blender (an Action exists on the armature)
  2. Wired to bones (fcurves reference bone rotations)
  3. Actually animating (bone matrices CHANGE between frames)
  4. Producing reasonable motion (not exploding, not frozen)

Usage:
  exec(open(r"C:\\Users\\hzeem\\Desktop\\crimsonforge\\tools\\blender_inspect_animation.py").read())

Output appears in the System Console (Window → Toggle System Console on
Windows). Look for the PASS/FAIL summary at the bottom.
"""
import bpy
import math
from mathutils import Vector


def find_imported_armature():
    for obj in bpy.data.objects:
        if obj.type == 'ARMATURE':
            return obj
    return None


def collect_fcurves(action):
    """Get all fcurves from an Action, compatible with Blender 4.4+
    (which moved fcurves under action.layers[*].strips[*].channelbags)
    AND older Blender versions (which had action.fcurves directly).
    """
    fcurves = []
    # Old API (Blender < 4.4)
    try:
        if action.fcurves is not None:
            return list(action.fcurves)
    except (AttributeError, TypeError):
        pass
    # New layered API (Blender 4.4+) — walk the slot/layer/strip tree
    try:
        for layer in action.layers:
            for strip in layer.strips:
                # 4.4+: ChannelBag holds the fcurves
                if hasattr(strip, 'channelbags'):
                    for bag in strip.channelbags:
                        if hasattr(bag, 'fcurves'):
                            fcurves.extend(bag.fcurves)
                # Some intermediate API revisions used .channels
                elif hasattr(strip, 'channels'):
                    fcurves.extend(strip.channels)
    except AttributeError:
        pass
    return fcurves


def main():
    print("=" * 72)
    print("ANIMATION INSPECTOR — verify PAA→FBX animation actually plays")
    print("=" * 72)

    arm = find_imported_armature()
    if not arm:
        print("✗ No Armature in scene. Import the FBX first.")
        return

    print(f"\nArmature: {arm.name!r}")
    print(f"  bones in armature: {len(arm.data.bones)}")
    print(f"  pose bones:        {len(arm.pose.bones)}")

    # ── 1. Action attached? ──
    print("\n--- 1. Action / animation data ---")
    if not arm.animation_data:
        print("  ✗ No animation_data on the armature")
        print("    Animation curves were NOT imported by Blender.")
        print("    Likely cause: AnimationStack/Layer/Curves missing or")
        print("    wrong Connections wiring in the FBX.")
        return

    action = arm.animation_data.action
    if not action:
        print("  ✗ animation_data has no .action assigned")
        print("    Curves exist but aren't wired into the active action.")
        return

    fcurves = collect_fcurves(action)
    print(f"  ✓ Action: {action.name!r}")
    print(f"    fcurve count: {len(fcurves)}")
    fr_start, fr_end = action.frame_range
    print(f"    frame range: {int(fr_start)} - {int(fr_end)} "
          f"(span {int(fr_end - fr_start)} frames)")

    if len(fcurves) == 0:
        print("  ✗ Action has zero fcurves — animation is empty.")
        print("    (If you're on Blender 4.4+, the layered-animation API")
        print("    walk above didn't find any channelbags. Check the")
        print("    Outliner > Action editor for visible curves.)")
        return

    # ── 2. Inspect a few fcurves ──
    print("\n--- 2. fcurve sample (first 5) ---")
    for i, fc in enumerate(fcurves[:5]):
        n_keys = len(fc.keyframe_points)
        path = fc.data_path
        idx = fc.array_index
        first_val = fc.keyframe_points[0].co[1] if n_keys else None
        last_val = fc.keyframe_points[-1].co[1] if n_keys else None
        print(f"  [{i}] {path}[{idx}]  keys={n_keys}  "
              f"first={first_val:.3f}  last={last_val:.3f}"
              if first_val is not None
              else f"  [{i}] {path}[{idx}]  keys={n_keys}  (empty)")

    # Count how many bones have rotation curves
    rot_bones = set()
    loc_bones = set()
    for fc in fcurves:
        path = fc.data_path
        if 'pose.bones[' in path:
            # path is like: pose.bones["Bip01 Head"].rotation_euler
            try:
                bone_name = path.split('"')[1]
            except IndexError:
                continue
            if 'rotation' in path:
                rot_bones.add(bone_name)
            elif 'location' in path:
                loc_bones.add(bone_name)
    print(f"\n  bones with rotation curves: {len(rot_bones)}")
    print(f"  bones with location curves: {len(loc_bones)}")

    # ── 3. Sample several frames; check that bones MOVE ──
    print("\n--- 3. Motion check — sample bone positions across frames ---")
    scene = bpy.context.scene
    sample_frames = []
    if int(fr_end) > int(fr_start):
        n = 5
        for k in range(n):
            t = fr_start + (fr_end - fr_start) * (k / (n - 1))
            sample_frames.append(int(t))
    else:
        sample_frames = [int(fr_start)]

    # Pick 3 bones likely to have motion (named bones from our test)
    test_bone_names = []
    if rot_bones:
        # First few in rotation set are usually animated
        test_bone_names = list(rot_bones)[:3]
    else:
        test_bone_names = [b.name for b in arm.pose.bones[:3]]

    print(f"  Sampling at frames {sample_frames}")
    print(f"  Testing bones: {test_bone_names}")

    motion_per_bone = {}
    for bn in test_bone_names:
        if bn not in arm.pose.bones:
            continue
        positions = []
        rotations = []
        for f in sample_frames:
            scene.frame_set(f)
            pose_bone = arm.pose.bones[bn]
            world_mat = arm.matrix_world @ pose_bone.matrix
            head_world = world_mat.translation.copy()
            positions.append(head_world)
            rotations.append(pose_bone.rotation_euler.copy()
                             if hasattr(pose_bone, 'rotation_euler') else None)
        # Compute total displacement across the sample
        if len(positions) >= 2:
            total_pos_motion = sum(
                (positions[i] - positions[i-1]).length
                for i in range(1, len(positions))
            )
        else:
            total_pos_motion = 0.0
        motion_per_bone[bn] = {
            'positions': positions,
            'total_pos_motion': total_pos_motion,
        }
        print(f"  {bn!r}:")
        for f, p in zip(sample_frames, positions):
            print(f"    frame {f:>4}: ({p.x:>+7.3f}, {p.y:>+7.3f}, {p.z:>+7.3f})")
        print(f"    total displacement across sample: {total_pos_motion:.4f}m")

    # ── 4. Verdict ──
    print("\n--- 4. Verdict ---")
    motion_seen = sum(1 for d in motion_per_bone.values()
                      if d['total_pos_motion'] > 0.001)
    explosion_seen = any(
        any(abs(p.x) > 100 or abs(p.y) > 100 or abs(p.z) > 100
            for p in d['positions'])
        for d in motion_per_bone.values()
    )

    print(f"  Bones showing real motion (> 1mm): {motion_seen} of {len(motion_per_bone)}")
    print(f"  Bones with extreme positions (>100m): "
          f"{'YES — explosion' if explosion_seen else 'no'}")

    print()
    if explosion_seen:
        print("  ✗ FAIL — bone positions are exploding. Axis convention or")
        print("    coordinate scale is wrong somewhere.")
    elif motion_seen == 0:
        print("  ⚠ WARN — bones aren't moving. Either:")
        print("    (a) the picked bones aren't actually animated in this PAA")
        print("    (b) the curves are present but not driving the bones")
        print("    Try pressing Spacebar in viewport to confirm visually.")
    else:
        print("  ✓ PASS — animation is wired and bones move between frames.")
        print("    Press Spacebar in viewport to play it.")

    print("\n" + "=" * 72)
    # Reset to frame 1 so the viewport doesn't show some random sampled frame
    scene.frame_set(int(fr_start))


main()
