"""Deep-trace a parsed PAC body mesh to find and characterize 'spike' vertices.

Hypothesis: The body submesh has 7806 verts but only 2754 (35%) are
skinned. The other 65% have no bone weights, so they always sit at
their original PAC vertex position regardless of pose. If some of those
positions are far from the main humanoid body cluster, they manifest as
visible 'spikes' in Blender.

This script:
  1. Parses the PAC file
  2. For each submesh, computes the bounding box and centroid
  3. For each vertex, classifies it as:
     - Skinned (has bone weights) vs unskinned
     - Inside the main body cluster (within 1.5× median distance from centroid)
     - Outlier (far from centroid)
  4. For outliers, reports position, bone weights (if any), distance from
     centroid, and any suspicious patterns (clusters of outliers along
     one axis, etc.)
  5. Prints which bone names the outliers (if skinned) reference — that
     hints at what the geometry represents (cloth attach points,
     hair, eyelashes, etc.)

Usage:
    python tools/probe_body_mesh.py <path/to/character.pac>
"""
from __future__ import annotations

import math
import statistics
import sys
from collections import Counter
from pathlib import Path

# Make repo modules importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.mesh_parser import parse_pac
from core.skeleton_parser import parse_pab


def vec_dist(a, b):
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))


def analyze_submesh(sm, skel=None):
    print(f"\n{'='*72}")
    print(f"SUBMESH: {sm.name!r}")
    print(f"  vertex count: {len(sm.vertices)}")
    print(f"  face count  : {len(sm.faces)}")

    if not sm.vertices:
        print("  (empty)")
        return

    # Skinned vs unskinned
    skinned_idx = []
    unskinned_idx = []
    for vi, bones in enumerate(sm.bone_indices):
        if bones and any(b is not None for b in bones):
            skinned_idx.append(vi)
        else:
            unskinned_idx.append(vi)

    print(f"  skinned     : {len(skinned_idx)} ({100.0*len(skinned_idx)/len(sm.vertices):.1f}%)")
    print(f"  unskinned   : {len(unskinned_idx)} ({100.0*len(unskinned_idx)/len(sm.vertices):.1f}%)")

    # Bounding box of all verts
    xs = [v[0] for v in sm.vertices]
    ys = [v[1] for v in sm.vertices]
    zs = [v[2] for v in sm.vertices]
    print(f"\n  Bounding box (PAC native, Y-up):")
    print(f"    X: {min(xs):>+7.3f}  to  {max(xs):>+7.3f}  (span {max(xs)-min(xs):.3f})")
    print(f"    Y: {min(ys):>+7.3f}  to  {max(ys):>+7.3f}  (span {max(ys)-min(ys):.3f})")
    print(f"    Z: {min(zs):>+7.3f}  to  {max(zs):>+7.3f}  (span {max(zs)-min(zs):.3f})")

    # Centroid
    cx = sum(xs) / len(xs)
    cy = sum(ys) / len(ys)
    cz = sum(zs) / len(zs)
    print(f"  Centroid    : ({cx:+.3f}, {cy:+.3f}, {cz:+.3f})")

    # Distance from centroid for every vert
    dists = [vec_dist(v, (cx, cy, cz)) for v in sm.vertices]
    median_dist = statistics.median(dists)
    print(f"  Median dist from centroid: {median_dist:.3f}")
    print(f"  Max dist   from centroid: {max(dists):.3f}")

    # Define outliers as verts > 2× median distance OR > 2.0m absolute
    threshold = max(2 * median_dist, 2.0)
    print(f"  Outlier threshold: {threshold:.3f}")

    outliers = [(vi, sm.vertices[vi], dists[vi]) for vi in range(len(sm.vertices))
                if dists[vi] > threshold]
    outliers.sort(key=lambda r: -r[2])
    print(f"  OUTLIER vertex count: {len(outliers)} of {len(sm.vertices)}")

    if outliers:
        # Where are the outliers? Skinned or unskinned?
        out_skinned = sum(1 for vi, _, _ in outliers if vi in set(skinned_idx))
        out_unskinned = len(outliers) - out_skinned
        print(f"    of those:  {out_skinned} skinned  /  {out_unskinned} unskinned")

        # Per-axis outlier distribution — are they all in one direction?
        outlier_xs = [v[0] for _, v, _ in outliers]
        outlier_ys = [v[1] for _, v, _ in outliers]
        outlier_zs = [v[2] for _, v, _ in outliers]
        print(f"    Outlier X range: {min(outlier_xs):>+7.3f} to {max(outlier_xs):>+7.3f}")
        print(f"    Outlier Y range: {min(outlier_ys):>+7.3f} to {max(outlier_ys):>+7.3f}")
        print(f"    Outlier Z range: {min(outlier_zs):>+7.3f} to {max(outlier_zs):>+7.3f}")

        # Show top 15 outliers
        print(f"\n  TOP 15 OUTLIER vertices (sorted by distance from centroid):")
        print(f"    {'idx':>5}  {'pos (Y-up)':<25}  {'dist':>7}  bones[weights]")
        for vi, pos, d in outliers[:15]:
            bones = sm.bone_indices[vi] if vi < len(sm.bone_indices) else ()
            weights = sm.bone_weights[vi] if vi < len(sm.bone_weights) else ()
            bone_names = []
            for bi, w in zip(bones, weights):
                if skel and 0 <= bi < len(skel.bones):
                    bone_names.append(f"{skel.bones[bi].name}({w:.2f})")
                else:
                    bone_names.append(f"bone#{bi}({w:.2f})")
            bone_str = ", ".join(bone_names) if bone_names else "<unskinned>"
            pos_str = f"({pos[0]:+.2f},{pos[1]:+.2f},{pos[2]:+.2f})"
            print(f"    {vi:>5}  {pos_str:<25}  {d:>7.3f}  {bone_str}")

        # Most-referenced bones among outliers (hints at what they ARE)
        outlier_bone_refs = Counter()
        for vi, _, _ in outliers:
            for bi in (sm.bone_indices[vi] if vi < len(sm.bone_indices) else ()):
                if skel and 0 <= bi < len(skel.bones):
                    outlier_bone_refs[skel.bones[bi].name] += 1
                else:
                    outlier_bone_refs[f"bone#{bi}"] += 1
        if outlier_bone_refs:
            print(f"\n  Top 10 bones referenced by outlier vertices:")
            for name, count in outlier_bone_refs.most_common(10):
                print(f"    {count:>5}× {name}")

        # Cluster outliers by spatial proximity (rough k-means-ish)
        # Just count how many distinct "pockets" of outliers exist
        # by greedy bucketing within 0.3m radius
        clusters = []
        for vi, pos, _ in outliers:
            placed = False
            for cluster in clusters:
                if vec_dist(pos, cluster["centroid"]) < 0.3:
                    cluster["members"].append(vi)
                    # Update centroid running average
                    n = len(cluster["members"])
                    cluster["centroid"] = (
                        (cluster["centroid"][0] * (n-1) + pos[0]) / n,
                        (cluster["centroid"][1] * (n-1) + pos[1]) / n,
                        (cluster["centroid"][2] * (n-1) + pos[2]) / n,
                    )
                    placed = True
                    break
            if not placed:
                clusters.append({"centroid": pos, "members": [vi]})

        print(f"\n  Spatial clustering of outliers (within 0.3m): {len(clusters)} cluster(s)")
        clusters.sort(key=lambda c: -len(c["members"]))
        for i, c in enumerate(clusters[:10]):
            cx, cy, cz = c["centroid"]
            print(f"    cluster #{i}: {len(c['members'])} verts at ({cx:+.2f}, {cy:+.2f}, {cz:+.2f})")


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: probe_body_mesh.py <character.pac> [<skeleton.pab>]")
        return 1

    pac_path = Path(sys.argv[1])
    pab_path = Path(sys.argv[2]) if len(sys.argv) > 2 else None

    print(f"Parsing PAC: {pac_path}")
    data = pac_path.read_bytes()
    mesh = parse_pac(data, pac_path.name)
    print(f"  {len(mesh.submeshes)} submesh(es), {mesh.total_vertices} verts, {mesh.total_faces} faces")

    skel = None
    if pab_path:
        print(f"\nParsing PAB: {pab_path}")
        skel_data = pab_path.read_bytes()
        skel = parse_pab(skel_data, pab_path.name)
        print(f"  {len(skel.bones)} bones")

    for sm in mesh.submeshes:
        analyze_submesh(sm, skel)

    print(f"\n{'='*72}")
    print("INTERPRETATION GUIDE")
    print('='*72)
    print("""
If you see clusters of OUTLIERS that are:
  - In the head area at high Z (~1.6-1.8m): probably HAIR or EYELASH verts
  - Behind the body at -Y: probably CLOAK / CAPE / TAIL attach points
  - Below the feet at low Z: probably SHADOW DECAL or FOOTSTEP markers
  - At world origin (0,0,0): UNUSED slot, engine ignores
  - Far from body, weighted to ONE specific bone: LOD helper or attach point

If you see UNSKINNED outliers, those vertices won't deform with bones,
so they hang at their PAC bind position even when you pose the model.
That's the source of static spike geometry visible in Blender.
""")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
