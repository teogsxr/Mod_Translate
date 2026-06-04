"""Run inside Blender's Text Editor AFTER importing the FBX.

This is the diagnostic equivalent of "what did Blender actually do with
my FBX?". For every bone in the imported armature it prints:

  - The bone's HEAD position (Blender edit-mode rest position)
  - The bone's matrix in armature space (Blender's bind-pose matrix)
  - What we INTENDED — read from the .fbx.debug.txt sidecar (the per-bone
    world_pos lines we logged at export time)
  - The DELTA between intended and actual

Then for every mesh with an Armature modifier:
  - Pick one vertex per bone-group, show its world position before AND
    after the armature modifier evaluates at frame 0
  - If the modifier moves any vertex more than a small threshold from
    its rest position, that vertex will visibly explode

How to run
----------
1. Import the FBX into a fresh Blender scene (default settings).
2. Open Text Editor → Open this file (blender_inspect_armature.py).
3. Click Run Script.
4. Read the System Console (Window → Toggle System Console on Windows).

A clean run looks like:
    Bone 'Bip01': head=(0.000, 0.972, 0.000) Δ=0.0001
    ...
    ✓ All vertices stay within 1e-3 of rest position when modifier evaluates.

If the modifier explodes vertices, the printout shows the worst offenders
with their bone-group name and weight, telling you exactly which bone
binding is wrong.
"""
import bpy
import math
import os
import re
from mathutils import Vector


def find_imported_armature():
    """Pick the first Armature object in the scene."""
    for obj in bpy.data.objects:
        if obj.type == 'ARMATURE':
            return obj
    return None


def parse_debug_sidecar(fbx_path):
    """Read the .fbx.debug.txt and pull out 'world_pos=(x,y,z)' per bone."""
    debug_path = fbx_path + '.debug.txt'
    if not os.path.exists(debug_path):
        return {}
    intended = {}
    pat = re.compile(r"\[\s*(\d+)\]\s+'([^']+)'\s+parent=\s*-?\d+\s+world_pos=\(\s*([-\d.]+),\s*([-\d.]+),\s*([-\d.]+)\)")
    with open(debug_path, encoding='utf-8') as f:
        for line in f:
            m = pat.search(line)
            if m:
                _idx, name, x, y, z = m.groups()
                intended[name] = (float(x), float(y), float(z))
    return intended


def find_fbx_path():
    """Locate the imported FBX path from blend file path or recent imports."""
    # Try the most recent import
    if bpy.data.filepath:
        d = os.path.dirname(bpy.data.filepath)
        for f in os.listdir(d):
            if f.endswith('.fbx'):
                return os.path.join(d, f)
    return None


def main():
    arm = find_imported_armature()
    if not arm:
        print("✗ No Armature found in scene. Import the FBX first.")
        return

    print("=" * 70)
    print(f"Inspecting armature: {arm.name!r}")
    print(f"Bones: {len(arm.data.bones)}")
    print("=" * 70)

    fbx_path = find_fbx_path()
    intended = parse_debug_sidecar(fbx_path) if fbx_path else {}
    if intended:
        print(f"✓ Found debug sidecar with {len(intended)} bone positions")
    else:
        print(f"⚠ No .fbx.debug.txt sidecar found; deltas will be skipped")
    print()

    # ── Bone position drift ──
    arm_world = arm.matrix_world
    drifts = []
    for bone in arm.data.bones:
        head_world = arm_world @ bone.head_local
        intended_pos = intended.get(bone.name)
        if intended_pos:
            dx = head_world.x - intended_pos[0]
            dy = head_world.y - intended_pos[1]
            dz = head_world.z - intended_pos[2]
            drift = math.sqrt(dx * dx + dy * dy + dz * dz)
            drifts.append((drift, bone.name, head_world, intended_pos))

    drifts.sort(reverse=True)
    print(f"--- Top 10 bone-position drifts (intended → actual) ---")
    for drift, name, actual, intended_pos in drifts[:10]:
        print(f"  {drift:>9.5f}  {name!r:<32s}")
        print(f"            intended ({intended_pos[0]:>7.3f}, {intended_pos[1]:>7.3f}, {intended_pos[2]:>7.3f})")
        print(f"            actual   ({actual.x:>7.3f}, {actual.y:>7.3f}, {actual.z:>7.3f})")
    if drifts:
        print(f"  worst drift: {drifts[0][0]:.5f}  ({'OK' if drifts[0][0] < 1e-3 else '⚠ DRIFT'})")
    print()

    # ── Bone rest matrix dump (the actual smoking gun) ──
    # Blender's bone.matrix_local is the bone's rest matrix in armature
    # space.  At frame 0 the pose-bone matrix equals matrix_local.
    # Skinning math: v_world = matrix_local × inv(TransformLink) × v_local.
    # For NO drift, matrix_local must equal TransformLink (which is what
    # we wrote into the cluster).  Any difference is the bug.
    print(f"--- Spot-check bone matrices vs expected ---")
    # Pick interesting bones: the ones the explosion-prone vertices weighted to
    interesting = ['Bip01', 'B_Chin_06_L', 'Bip01 R Clavicle_Back',
                   'Bip01 L ClavicleTwist', 'Bip01 L Hand', 'Bip01 R Hand',
                   'Bip01 L Foot', 'Bip01 Spine', 'Bip01 Head']
    for bname in interesting:
        bone = arm.data.bones.get(bname)
        if not bone:
            continue
        head = arm_world @ bone.head_local
        tail = arm_world @ bone.tail_local
        # matrix_local is bone rest in armature space; convert to world
        m_local_world = arm_world @ bone.matrix_local
        # Print the translation column (column 3 of column-major matrix
        # = position part of the affine transform)
        tx, ty, tz = m_local_world.translation
        # Roll = rotation around bone axis
        roll = bone.matrix_local.to_euler()
        parent = bone.parent.name if bone.parent else '<root>'
        print(f"  {bname!r:<32s} parent={parent!r}")
        print(f"    head_world  = ({head.x:>+8.4f}, {head.y:>+8.4f}, {head.z:>+8.4f})")
        print(f"    tail_world  = ({tail.x:>+8.4f}, {tail.y:>+8.4f}, {tail.z:>+8.4f})")
        print(f"    bone-length = {(tail - head).length:.4f}")
        print(f"    matrix_local translation = ({tx:>+8.4f}, {ty:>+8.4f}, {tz:>+8.4f})")
        print(f"    matrix_local euler       = ({math.degrees(roll.x):>+7.2f}°, "
              f"{math.degrees(roll.y):>+7.2f}°, {math.degrees(roll.z):>+7.2f}°)")
    print()

    # ── Vertex explosion check + WHERE the vertex SHOULD be vs where it goes ──
    print(f"--- Skin deformation at frame 0 (should be no-op) ---")
    bpy.context.scene.frame_set(0)
    for obj in bpy.data.objects:
        if obj.type != 'MESH':
            continue
        mods = [m for m in obj.modifiers if m.type == 'ARMATURE']
        if not mods:
            continue

        print(f"  Mesh {obj.name!r}: {len(obj.data.vertices)} verts, "
              f"{len(obj.vertex_groups)} vertex groups")

        orig = [v.co.copy() for v in obj.data.vertices]
        depsgraph = bpy.context.evaluated_depsgraph_get()
        eval_obj = obj.evaluated_get(depsgraph)
        eval_mesh = eval_obj.to_mesh()
        deformed = [v.co.copy() for v in eval_mesh.vertices]
        eval_obj.to_mesh_clear()

        worst = 0.0
        worst_idx = -1
        worst_groups = []
        worst_orig = None
        worst_deformed = None
        for i in range(min(len(orig), len(deformed))):
            d = (orig[i] - deformed[i]).length
            if d > worst:
                worst = d
                worst_idx = i
                worst_orig = orig[i].copy()
                worst_deformed = deformed[i].copy()
                groups = []
                for g in obj.data.vertices[i].groups:
                    gname = obj.vertex_groups[g.group].name
                    groups.append((gname, g.weight))
                worst_groups = groups

        print(f"    worst rest-pose drift: {worst:.5f} at vertex {worst_idx}")
        if worst_orig and worst_deformed:
            print(f"      v_local (FBX position): ({worst_orig.x:>+8.4f}, "
                  f"{worst_orig.y:>+8.4f}, {worst_orig.z:>+8.4f})")
            print(f"      v_after armature mod:   ({worst_deformed.x:>+8.4f}, "
                  f"{worst_deformed.y:>+8.4f}, {worst_deformed.z:>+8.4f})")
            print(f"      ratio (deformed/local): "
                  f"({(worst_deformed.x/worst_orig.x if abs(worst_orig.x)>1e-6 else 0):.4f}, "
                  f"{(worst_deformed.y/worst_orig.y if abs(worst_orig.y)>1e-6 else 0):.4f}, "
                  f"{(worst_deformed.z/worst_orig.z if abs(worst_orig.z)>1e-6 else 0):.4f})")
        if worst_groups:
            print(f"    that vertex is weighted to:")
            for gname, w in worst_groups[:5]:
                # Show the bone's armature-space position too
                bone = arm.data.bones.get(gname)
                if bone:
                    bp = arm_world @ bone.head_local
                    print(f"      {gname!r:<35s}  weight={w:.3f}  "
                          f"bone_head_world=({bp.x:.3f},{bp.y:.3f},{bp.z:.3f})")
                else:
                    print(f"      {gname!r:<35s}  weight={w:.3f}  (bone not found!)")
        if worst > 1e-3:
            print(f"    ⚠ VERTEX EXPLODES (>1mm drift in armature rest pose)")
        else:
            print(f"    ✓ rest pose preserved (within fp32 floor)")

    print()
    print("=" * 70)
    print("DONE — read the System Console output for details.")


if __name__ == "__main__" or True:
    main()
