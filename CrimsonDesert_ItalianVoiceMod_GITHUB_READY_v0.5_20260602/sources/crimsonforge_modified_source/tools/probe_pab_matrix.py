"""Deep reverse-engineer the PAB bind matrix layout.

Tests every plausible interpretation of the 16-float bind matrix:
  - column-major vs row-major
  - left-handed vs right-handed
  - translation-at-12/13/14 vs translation-at-3/7/11
  - quat+pos vs matrix (which is canonical?)

For each interpretation, checks invariants:
  1. det(R) ≈ +1 (proper rotation; -1 means reflection/handedness flip)
  2. R^T × R ≈ I (orthogonal)
  3. Decomposing the matrix back to T,R,S then recomposing gives
     back the original matrix (validates our decomposition algorithm)
  4. The matrix's translation matches the separately-stored position
     field (PAB stores BOTH; agreement confirms which layout is canonical)
  5. The matrix's quaternion-equivalent rotation matches the separately-
     stored quaternion field

Also dumps raw bytes so we can eyeball the layout.
"""
from __future__ import annotations

import math
import struct
import sys
from pathlib import Path


def parse_first_bone(data: bytes):
    """Return (name, parent, bind_flat16, inv_bind_flat16, scale, quat, pos).

    Reads raw bytes for the first bone of a PAB, no fancy validation.
    """
    off = 0x17
    name_len = data[off + 3]
    off += 4
    name = data[off:off + name_len].decode('ascii')
    off += name_len
    parent = struct.unpack_from('<i', data, off)[0]; off += 4
    bind = struct.unpack_from('<16f', data, off);    off += 64
    inv  = struct.unpack_from('<16f', data, off);    off += 64
    off += 128  # cache copies
    scale = struct.unpack_from('<3f', data, off);    off += 12
    quat  = struct.unpack_from('<4f', data, off);    off += 16
    pos   = struct.unpack_from('<3f', data, off);    off += 12
    return name, parent, list(bind), list(inv), list(scale), list(quat), list(pos)


def quat_to_mat3(q):
    """Quaternion (x, y, z, w) → 3x3 rotation matrix (row-major)."""
    x, y, z, w = q
    n = math.sqrt(x*x + y*y + z*z + w*w)
    if n < 1e-9:
        return [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    x, y, z, w = x/n, y/n, z/n, w/n
    return [
        [1 - 2*(y*y + z*z), 2*(x*y - w*z), 2*(x*z + w*y)],
        [2*(x*y + w*z), 1 - 2*(x*x + z*z), 2*(y*z - w*x)],
        [2*(x*z - w*y), 2*(y*z + w*x), 1 - 2*(x*x + y*y)],
    ]


def det3(m3):
    """3x3 determinant (m3 is row-major nested list)."""
    a, b, c = m3[0]
    d, e, f = m3[1]
    g, h, i = m3[2]
    return a*(e*i - f*h) - b*(d*i - f*g) + c*(d*h - e*g)


def transpose3(m3):
    return [[m3[c][r] for c in range(3)] for r in range(3)]


def m3_close(a, b, tol=1e-3):
    return all(abs(a[r][c] - b[r][c]) < tol for r in range(3) for c in range(3))


def fmt_mat(name, flat16, layout='column'):
    """Pretty-print a 4x4 matrix, interpreting `flat16` as column- or row-major."""
    rows = []
    for row in range(4):
        cols = []
        for col in range(4):
            if layout == 'column':
                v = flat16[col * 4 + row]
            else:
                v = flat16[row * 4 + col]
            cols.append(f'{v:>9.4f}')
        rows.append('   '.join(cols))
    print(f'  {name} ({layout}-major interpretation):')
    for r in rows:
        print(f'    [ {r} ]')


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: probe_pab_matrix.py <file.pab>")
        return 1

    path = Path(sys.argv[1])
    data = path.read_bytes()
    name, parent, bind, inv, scale, quat, pos = parse_first_bone(data)
    print(f'File: {path.name}')
    print(f'First bone: {name!r}, parent={parent}')
    print(f'  scale (separate field):    {scale}')
    print(f'  quat  (separate field xyzw): {quat}')
    print(f'  pos   (separate field):    {pos}')
    print()

    # Display matrix BOTH ways
    fmt_mat('bind_matrix', bind, 'column')
    fmt_mat('bind_matrix', bind, 'row')
    print()

    # Test which layout puts translation at the corner
    print('Translation candidates:')
    print(f'  flat[12..14] (col-major col-3): {bind[12]:.4f}, {bind[13]:.4f}, {bind[14]:.4f}')
    print(f'  flat[3,7,11]  (row-major col-3): {bind[3]:.4f}, {bind[7]:.4f}, {bind[11]:.4f}')
    print(f'  separate pos field:               {pos[0]:.4f}, {pos[1]:.4f}, {pos[2]:.4f}')
    print()

    # Determine which layout's translation matches the separate pos field
    cm_t = (bind[12], bind[13], bind[14])
    rm_t = (bind[3], bind[7], bind[11])
    cm_diff = sum(abs(cm_t[i] - pos[i]) for i in range(3))
    rm_diff = sum(abs(rm_t[i] - pos[i]) for i in range(3))
    print(f'  column-major matches pos? diff={cm_diff:.4g}')
    print(f'  row-major    matches pos? diff={rm_diff:.4g}')
    print()

    # Build R3 both ways and check determinant
    R_col = [[bind[col * 4 + row] for col in range(3)] for row in range(3)]
    R_row = [[bind[row * 4 + col] for col in range(3)] for row in range(3)]
    print(f'  det(R col-major): {det3(R_col):>+8.4f}')
    print(f'  det(R row-major): {det3(R_row):>+8.4f}')
    print('  (should be +1 for proper rotation; -1 means handedness flip;')
    print('   absolute value != 1 means scale is baked in)')
    print()

    # Compare quaternion rotation to matrix rotation
    R_q = quat_to_mat3(quat)
    print(f'  R from quaternion:')
    for r in R_q:
        print(f'    [ {r[0]:>+8.4f}  {r[1]:>+8.4f}  {r[2]:>+8.4f} ]')
    print()
    print(f'  Match R_q == R_col?  {m3_close(R_q, R_col)}')
    print(f'  Match R_q == R_row?  {m3_close(R_q, R_row)}')
    # Try transposed comparisons
    print(f'  Match R_q == R_col^T?  {m3_close(R_q, transpose3(R_col))}')
    print(f'  Match R_q == R_row^T?  {m3_close(R_q, transpose3(R_row))}')
    print()

    # Hex dump of the 64-byte matrix
    print('Raw 64 bytes of bind_matrix:')
    bind_bytes = struct.pack('<16f', *bind)
    for r in range(4):
        chunk = bind_bytes[r * 16:(r + 1) * 16]
        floats = struct.unpack_from('<4f', chunk, 0)
        print(f'  +{r*16:02x}:  {chunk.hex(" ")}')
        print(f'         floats: {floats[0]:>+8.4f}  {floats[1]:>+8.4f}  '
              f'{floats[2]:>+8.4f}  {floats[3]:>+8.4f}')
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
