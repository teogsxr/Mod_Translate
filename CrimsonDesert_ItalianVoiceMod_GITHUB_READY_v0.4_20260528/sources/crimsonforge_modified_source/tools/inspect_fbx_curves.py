"""Read the exported FBX, find AnimationCurves for L Thigh / L UpperArm /
L Forearm, and dump their per-frame values. Look for:
  * jumps > 180° between adjacent frames (would cause explosion in Blender)
  * NaN / inf values
  * monotonic sliding when the actual motion is small (gimbal-lock symptom)

Usage:
    python tools/inspect_fbx_curves.py [fbx_path]
"""
from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path


# Minimal FBX binary parser (read-only, just enough to walk Objects tree)

def read_fbx_node(buf, offset, fbx_version=7400):
    """Read one FBX binary node header. Returns (name, properties, children, end_offset).

    FBX 7.4 binary node header (post-7.5 uses 64-bit offsets):
      uint32 EndOffset    (offset 0..4)
      uint32 NumProps     (offset 4..8)
      uint32 PropListLen  (offset 8..12)
      uint8  NameLen      (offset 12)
      char   Name[NameLen] (offset 13..)

    For FBX 7.5+, the EndOffset/NumProps/PropListLen are uint64 (24 bytes).
    """
    if fbx_version >= 7500:
        if offset + 25 > len(buf):
            return None
        end_offset, num_props, prop_list_len = struct.unpack_from('<QQQ', buf, offset)
        name_len = buf[offset + 24]
        name_start = offset + 25
    else:
        if offset + 13 > len(buf):
            return None
        end_offset, num_props, prop_list_len = struct.unpack_from('<III', buf, offset)
        name_len = buf[offset + 12]
        name_start = offset + 13
    if end_offset == 0:
        return None  # null sentinel
    name = buf[name_start:name_start + name_len].decode('ascii', 'replace')
    return name, num_props, prop_list_len, end_offset, name_start + name_len


def parse_property(buf, offset):
    """Parse one FBX property. Returns (type_char, value, end_offset)."""
    type_char = chr(buf[offset])
    p = offset + 1
    if type_char == 'Y':
        v = struct.unpack_from('<h', buf, p)[0]
        return type_char, v, p + 2
    if type_char == 'C':
        v = bool(buf[p])
        return type_char, v, p + 1
    if type_char == 'I':
        v = struct.unpack_from('<i', buf, p)[0]
        return type_char, v, p + 4
    if type_char == 'F':
        v = struct.unpack_from('<f', buf, p)[0]
        return type_char, v, p + 4
    if type_char == 'D':
        v = struct.unpack_from('<d', buf, p)[0]
        return type_char, v, p + 8
    if type_char == 'L':
        v = struct.unpack_from('<q', buf, p)[0]
        return type_char, v, p + 8
    if type_char in ('f', 'd', 'l', 'i', 'b'):
        # Array
        array_len, encoding, comp_len = struct.unpack_from('<III', buf, p)
        p += 12
        raw = buf[p:p + comp_len]
        if encoding == 1:
            raw = zlib.decompress(raw)
        # Decode array
        if type_char == 'f':
            arr = struct.unpack(f'<{array_len}f', raw)
        elif type_char == 'd':
            arr = struct.unpack(f'<{array_len}d', raw)
        elif type_char == 'l':
            arr = struct.unpack(f'<{array_len}q', raw)
        elif type_char == 'i':
            arr = struct.unpack(f'<{array_len}i', raw)
        else:  # 'b'
            arr = struct.unpack(f'<{array_len}b', raw)
        return type_char, arr, p + comp_len
    if type_char == 'S':
        slen = struct.unpack_from('<I', buf, p)[0]
        p += 4
        v = buf[p:p + slen].decode('utf-8', 'replace')
        return type_char, v, p + slen
    if type_char == 'R':
        rlen = struct.unpack_from('<I', buf, p)[0]
        p += 4
        return type_char, buf[p:p + rlen], p + rlen
    raise ValueError(f"Unknown FBX property type: {type_char!r} at offset 0x{offset:x}")


def walk_node(buf, offset, fbx_version, depth, callback, path=()):
    """Recursively walk FBX nodes. Calls callback(name, props, path)."""
    while offset < len(buf):
        head = read_fbx_node(buf, offset, fbx_version)
        if head is None:
            return offset + (25 if fbx_version >= 7500 else 13)
        name, num_props, prop_list_len, end_offset, prop_start = head

        # Read properties
        props = []
        p = prop_start
        for _ in range(num_props):
            try:
                tc, val, p = parse_property(buf, p)
                props.append((tc, val))
            except Exception:
                break

        new_path = path + (name,)
        callback(name, props, new_path, depth)

        # Children start after props, end at end_offset (minus null sentinel)
        children_start = prop_start + prop_list_len
        if end_offset > children_start:
            walk_node(buf, children_start, fbx_version, depth + 1, callback, new_path)

        offset = end_offset
        if offset >= len(buf):
            return offset


def main():
    fbx_path = sys.argv[1] if len(sys.argv) > 1 else 'export_test/damian_walk_test.fbx'
    buf = Path(fbx_path).read_bytes()
    print(f"FBX file: {fbx_path} ({len(buf):,} bytes)")

    # Header: "Kaydara FBX Binary  \0\x1a\x00" + uint32 version
    if not buf.startswith(b'Kaydara FBX Binary'):
        print("Not a binary FBX file")
        return 1
    fbx_version = struct.unpack_from('<I', buf, 23)[0]
    print(f"FBX version: {fbx_version}")

    # Collect all nodes by name
    objects_by_id = {}        # id -> (type, name)
    curve_node_props = {}     # id -> property metadata (name, etc)
    curve_data = {}           # id -> {'KeyTime': [], 'KeyValueFloat': []}
    connections = []          # list of (child_id, parent_id, optional_property_name)

    def cb(name, props, path, depth):
        # Track Object id->Type/Name
        if name in ('Model', 'AnimationCurve', 'AnimationCurveNode',
                    'AnimationLayer', 'AnimationStack'):
            if len(props) >= 2:
                obj_id = props[0][1]
                obj_full = props[1][1] if len(props) > 1 else ""
                obj_type = props[2][1] if len(props) > 2 else ""
                objects_by_id[obj_id] = (name, obj_full, obj_type)

        # Capture curve data inside AnimationCurve node
        if 'AnimationCurve' in path and name in ('KeyTime', 'KeyValueFloat'):
            # Find the AnimationCurve id we're inside of
            # Walk up to the AnimationCurve node — but we don't have stack here
            # Use the most-recently-seen AnimationCurve id (sloppy but works)
            curve_id = curve_data.setdefault('_current', None)
            if curve_id is not None and props and props[0][0] in ('l', 'd', 'f'):
                d = curve_data.setdefault(curve_id, {})
                d[name] = props[0][1]

        if name == 'AnimationCurve' and props:
            curve_data['_current'] = props[0][1]

        # Connections
        if name == 'C' and len(props) >= 3:
            kind = props[0][1]
            cid = props[1][1]
            pid = props[2][1]
            extra = props[3][1] if len(props) > 3 else ""
            connections.append((kind, cid, pid, extra))

    # FBX header is 27 bytes (magic + version)
    walk_node(buf, 27, fbx_version, 0, cb)

    # Find Models named after our target bones
    target_bones = [
        'Bip01 L Thigh', 'Bip01 R Thigh',
        'Bip01 L UpperArm', 'Bip01 L Forearm',
        'Bip01 Spine', 'Bip01 Head',
    ]

    for bone_name in target_bones:
        # Find Model with that name
        model_id = None
        for obj_id, (type_, full, _t2) in objects_by_id.items():
            if type_ == 'Model' and bone_name in full:
                model_id = obj_id
                break
        if model_id is None:
            print(f"\n--- {bone_name}: Model not found")
            continue

        # Find AnimationCurveNode connected to this Model with R property
        curve_node_id = None
        for kind, cid, pid, extra in connections:
            if pid == model_id and kind == 'OP' and extra == 'Lcl Rotation':
                # cid is the AnimationCurveNode
                curve_node_id = cid
                break

        if curve_node_id is None:
            print(f"\n--- {bone_name}: No Lcl Rotation curve node")
            continue

        # Find 3 AnimationCurves connected to this CurveNode (X/Y/Z)
        curves_xyz = {}
        for kind, cid, pid, extra in connections:
            if pid == curve_node_id and kind == 'OP':
                if 'X' in extra or extra == 'd|X':
                    curves_xyz['X'] = cid
                elif 'Y' in extra or extra == 'd|Y':
                    curves_xyz['Y'] = cid
                elif 'Z' in extra or extra == 'd|Z':
                    curves_xyz['Z'] = cid

        print(f"\n--- {bone_name} (model_id={model_id}, curve_node={curve_node_id})")
        print(f"  X/Y/Z curve ids: {curves_xyz}")

        for axis in ('X', 'Y', 'Z'):
            cid = curves_xyz.get(axis)
            if cid is None:
                continue
            d = curve_data.get(cid, {})
            vals = d.get('KeyValueFloat', [])
            times = d.get('KeyTime', [])
            if not vals:
                print(f"    {axis}: no KeyValueFloat found")
                continue
            print(f"    {axis} ({len(vals)} keyframes):")
            # Print first 10 frames + frames around mid
            for i in range(min(10, len(vals))):
                print(f"      f={i}  t={times[i] if i < len(times) else '?'}  v={vals[i]:+8.3f}°")
            if len(vals) > 20:
                mid = len(vals) // 2
                print(f"      ...")
                for i in range(mid - 2, min(mid + 3, len(vals))):
                    print(f"      f={i}  t={times[i] if i < len(times) else '?'}  v={vals[i]:+8.3f}°")
            # Check for jumps > 180°
            jumps = []
            for i in range(1, len(vals)):
                d_v = abs(vals[i] - vals[i - 1])
                if d_v > 180:
                    jumps.append((i, vals[i - 1], vals[i], d_v))
            if jumps:
                print(f"    !! {len(jumps)} JUMPS > 180° (would explode in Blender):")
                for i, prev, cur, dd in jumps[:5]:
                    print(f"      f={i}: {prev:+.2f} -> {cur:+.2f}  (Δ={dd:.2f}°)")


if __name__ == "__main__":
    main()
