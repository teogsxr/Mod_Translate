"""Run inside Blender after importing the FBX.

Finds and characterizes the 'spike' vertices — vertices that sit far
from the main body cluster and look like garbage geometry in the
viewport.

For each mesh:
  - Computes the centroid and median distance of all vertices
  - Flags outliers (verts > 2× median distance from centroid OR > 2.0m)
  - Reports how many are skinned vs unskinned
  - For each outlier, shows its position and which vertex groups (bones)
    it's weighted to — bone name hints at what the vertex represents
  - Optionally SELECTS them in Edit Mode so you can see them highlighted

Usage in Blender:
    exec(open(r"C:\\Users\\hzeem\\Desktop\\crimsonforge\\tools\\blender_find_spike_verts.py").read())

After running, switch to Edit Mode on a mesh — the outlier vertices
will be selected (highlighted orange).
"""
import bpy
import math
import bmesh
from collections import Counter
from mathutils import Vector


def vec_dist(a, b):
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))


def analyze_mesh(obj):
    print(f"\n{'='*72}")
    print(f"MESH: {obj.name!r}")
    verts = [v.co.copy() for v in obj.data.vertices]
    print(f"  vertex count: {len(verts)}")

    if not verts:
        return

    # Centroid (median per axis is more robust than mean)
    xs = sorted(v.x for v in verts)
    ys = sorted(v.y for v in verts)
    zs = sorted(v.z for v in verts)
    n = len(verts)
    cx = xs[n//2]
    cy = ys[n//2]
    cz = zs[n//2]
    print(f"  median centroid (Z-up): ({cx:+.3f}, {cy:+.3f}, {cz:+.3f})")

    # Bounding box
    print(f"  X range: {min(xs):>+7.3f} to {max(xs):>+7.3f}")
    print(f"  Y range: {min(ys):>+7.3f} to {max(ys):>+7.3f}")
    print(f"  Z range: {min(zs):>+7.3f} to {max(zs):>+7.3f}")

    # Distance from centroid
    dists = [vec_dist((v.x, v.y, v.z), (cx, cy, cz)) for v in verts]
    sorted_dists = sorted(dists)
    median_dist = sorted_dists[len(sorted_dists)//2]
    print(f"  median dist: {median_dist:.3f}, max dist: {max(dists):.3f}")

    threshold = max(2.0 * median_dist, 1.0)
    print(f"  outlier threshold: {threshold:.3f}")

    # Find outliers
    outlier_indices = [i for i, d in enumerate(dists) if d > threshold]
    print(f"\n  OUTLIER vertex count: {len(outlier_indices)} ({100.0*len(outlier_indices)/len(verts):.1f}%)")

    if not outlier_indices:
        print(f"  ✓ No outliers — mesh is clean")
        return outlier_indices

    # Skinned vs unskinned among outliers
    out_skinned = 0
    out_unskinned = 0
    bone_ref_counter = Counter()
    for idx in outlier_indices:
        v = obj.data.vertices[idx]
        if v.groups:
            out_skinned += 1
            for g in v.groups:
                if g.weight > 0:
                    bone_ref_counter[obj.vertex_groups[g.group].name] += 1
        else:
            out_unskinned += 1

    print(f"    skinned outliers   : {out_skinned}")
    print(f"    unskinned outliers : {out_unskinned}  ← these don't deform with bones")

    # Top 15 outliers by distance
    sorted_outliers = sorted(outlier_indices, key=lambda i: -dists[i])
    print(f"\n  TOP 15 OUTLIERS (sorted by distance from centroid):")
    print(f"    {'idx':>5}  {'pos (Z-up)':<28}  {'dist':>7}  bones[weights]")
    for idx in sorted_outliers[:15]:
        v = obj.data.vertices[idx]
        bone_str = ", ".join(
            f"{obj.vertex_groups[g.group].name}({g.weight:.2f})"
            for g in v.groups if g.weight > 0
        )
        if not bone_str:
            bone_str = "<unskinned>"
        pos_str = f"({v.co.x:+.2f}, {v.co.y:+.2f}, {v.co.z:+.2f})"
        print(f"    {idx:>5}  {pos_str:<28}  {dists[idx]:>7.3f}  {bone_str}")

    # Bones most commonly referenced by outliers
    if bone_ref_counter:
        print(f"\n  Bones most commonly referenced by outliers (hint at what they ARE):")
        for name, count in bone_ref_counter.most_common(10):
            print(f"    {count:>5}× {name}")

    # Spatial clustering of outliers
    clusters = []
    for idx in outlier_indices:
        v = obj.data.vertices[idx]
        pos = (v.co.x, v.co.y, v.co.z)
        placed = False
        for c in clusters:
            if vec_dist(pos, c["centroid"]) < 0.3:
                c["members"].append(idx)
                m = len(c["members"])
                c["centroid"] = (
                    (c["centroid"][0] * (m-1) + pos[0]) / m,
                    (c["centroid"][1] * (m-1) + pos[1]) / m,
                    (c["centroid"][2] * (m-1) + pos[2]) / m,
                )
                placed = True
                break
        if not placed:
            clusters.append({"centroid": pos, "members": [idx]})
    clusters.sort(key=lambda c: -len(c["members"]))
    print(f"\n  Spatial clusters (verts within 0.3m): {len(clusters)} cluster(s)")
    for i, c in enumerate(clusters[:10]):
        cx, cy, cz = c["centroid"]
        print(f"    cluster #{i}: {len(c['members'])} verts near ({cx:+.2f}, {cy:+.2f}, {cz:+.2f})")

    return outlier_indices


def select_outliers(obj, outlier_indices):
    """Select the outlier vertices in Edit Mode for visual inspection."""
    if not outlier_indices:
        return
    # Switch to object mode first to clear selection
    bpy.ops.object.mode_set(mode='OBJECT')
    for v in obj.data.vertices:
        v.select = False
    for idx in outlier_indices:
        if idx < len(obj.data.vertices):
            obj.data.vertices[idx].select = True
    print(f"  → {len(outlier_indices)} outlier vertices marked. Switch to Edit Mode (Tab) to see them highlighted.")


def main():
    print("=" * 72)
    print("SPIKE-VERTEX FINDER")
    print("=" * 72)

    # Find biggest mesh (probably the body) and analyze it first
    meshes = [obj for obj in bpy.data.objects if obj.type == 'MESH']
    if not meshes:
        print("No meshes in scene. Import the FBX first.")
        return

    # Sort by vertex count, biggest first
    meshes.sort(key=lambda m: -len(m.data.vertices))

    # Analyze each mesh, mark outliers on the biggest one
    biggest = meshes[0]
    for obj in meshes:
        outliers = analyze_mesh(obj)
        if obj is biggest and outliers:
            # Pre-select outliers on the biggest mesh for visual inspection
            bpy.context.view_layer.objects.active = obj
            obj.select_set(True)
            select_outliers(obj, outliers)

    print("\n" + "=" * 72)
    print(f"DONE. Active object is {biggest.name!r}.")
    print("Switch to Edit Mode (Tab) — outlier vertices are pre-selected.")
    print("They'll appear highlighted orange. That's where the 'spikes' are.")


main()
