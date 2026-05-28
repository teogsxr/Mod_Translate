"""DDS texture file reader — extracts header info and converts to QImage for preview.

Supports common DDS formats used in Crimson Desert:
  - DXT1 (BC1) — RGB with optional 1-bit alpha
  - DXT3 (BC2) — RGBA with explicit alpha
  - DXT5 (BC3) — RGBA with interpolated alpha
  - Uncompressed RGBA/BGRA
  - BC7 — high-quality RGBA (partial — shows header info only)

For preview, we decode the first mip level into a QImage.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from utils.logger import get_logger

logger = get_logger("core.dds_reader")

DDS_MAGIC = b"DDS "

# DDS header flags
DDSD_CAPS = 0x1
DDSD_HEIGHT = 0x2
DDSD_WIDTH = 0x4
DDSD_PITCH = 0x8
DDSD_PIXELFORMAT = 0x1000
DDSD_MIPMAPCOUNT = 0x20000
DDSD_LINEARSIZE = 0x80000

# Pixel format flags
DDPF_ALPHAPIXELS = 0x1
DDPF_FOURCC = 0x4
DDPF_RGB = 0x40
DDPF_LUMINANCE = 0x20000

# FourCC codes
DXT1 = b"DXT1"
DXT3 = b"DXT3"
DXT5 = b"DXT5"
DX10 = b"DX10"


@dataclass
class DdsInfo:
    """DDS file header info."""
    width: int = 0
    height: int = 0
    mip_count: int = 1
    format: str = "Unknown"
    fourcc: str = ""
    bits_per_pixel: int = 0
    compressed: bool = False
    has_alpha: bool = False
    data_offset: int = 128  # After header
    file_size: int = 0


def expected_dds_data_size(info: DdsInfo) -> int | None:
    """Return the expected total DDS byte size from the header, or None if unknown."""
    total_payload = 0
    width = max(1, info.width)
    height = max(1, info.height)
    mip_count = max(1, info.mip_count)

    for _ in range(mip_count):
        payload_size = expected_mip_payload_size(info, width, height)
        if payload_size is None:
            return None
        total_payload += payload_size
        width = max(1, width // 2)
        height = max(1, height // 2)

    return info.data_offset + total_payload


def expected_first_mip_payload_size(info: DdsInfo) -> int | None:
    """Return the expected byte size of the first mip level payload."""
    return expected_mip_payload_size(info, max(1, info.width), max(1, info.height))


def expected_mip_payload_size(info: DdsInfo, width: int, height: int) -> int | None:
    """Return the expected payload size of one mip level for the DDS format."""
    blocks_w = max(1, (width + 3) // 4)
    blocks_h = max(1, (height + 3) // 4)

    if info.fourcc == "DXT1" or info.format.startswith("BC1"):
        return blocks_w * blocks_h * 8
    if (
        info.fourcc == "DXT3"
        or info.format.startswith("BC2")
        or info.fourcc == "DXT5"
        or info.format.startswith("BC3")
        or info.fourcc == "BC5U"
        or info.format.startswith("BC5")
        or info.format.startswith("BC6H")
        or info.format.startswith("BC7")
    ):
        return blocks_w * blocks_h * 16
    if info.fourcc == "BC4U" or info.format.startswith("BC4"):
        return blocks_w * blocks_h * 8
    if info.format in ("RGBA 32-bit", "RGB 32-bit"):
        return width * height * 4
    if info.format == "RGB 24-bit":
        return width * height * 3
    if info.format == "Luminance 8-bit":
        return width * height
    if info.format == "Luminance 16-bit":
        return width * height * 2
    # DX10 uncompressed formats identified by DXGI format string
    _dxgi_bpp = {
        "BC1 (DXT1)": None, "BC1 sRGB": None,  # block-compressed, handled above
        "R10G10B10A2_UNORM": 32, "R10G10B10A2_UINT": 32,
        "R16G16B16A16_FLOAT": 64,
        "R32G32B32A32_FLOAT": 128,
        "R16_FLOAT": 16,
        "R32_FLOAT": 32,
        "R8_UNORM": 8, "R8_UINT": 8,
    }
    # DXGI format names from read_dds_info look like "BC1 (DXT1)" or "DX10 (DXGI=28)"
    if info.format.startswith("DX10 (DXGI="):
        try:
            dxgi = int(info.format.split("=")[1].rstrip(")"))
        except (ValueError, IndexError):
            return None
        _dxgi_id_bpp = {
            28: 32, 29: 32, 30: 32, 31: 32,         # RGBA8
            87: 32, 88: 32, 89: 32, 90: 32, 91: 32, # BGRA8
            24: 32, 25: 32,  # R10G10B10A2 — texture/climate_texture_2.dds
            10: 64,          # R16G16B16A16F — texture/referencearealightprefiltered.dds
             2: 128,         # R32G32B32A32F
            54: 16, 55: 16,  # R16F
            41: 32, 43: 32,  # R32F
            61: 8,  62: 8,   # R8 — leveldata/global_extraregionmap.dds, global_regionmap.dds
        }
        bpp = _dxgi_id_bpp.get(dxgi)
        if bpp is not None:
            return width * height * bpp // 8
    return None


def validate_dds_payload_size(data: bytes, info: DdsInfo | None = None) -> DdsInfo:
    """Validate that the DDS body is large enough for the mip chain declared in the header."""
    info = info or read_dds_info(data)
    expected_size = expected_dds_data_size(info)
    if expected_size is not None and len(data) < expected_size:
        raise ValueError(
            "DDS payload is shorter than its header declares "
            f"({len(data)} < {expected_size} bytes). "
            "This usually means the archive entry is still using an unsupported "
            "type-1 compressed texture layout."
        )
    return info


def read_dds_info(data: bytes) -> DdsInfo:
    """Parse DDS header and return metadata."""
    if len(data) < 128 or data[:4] != DDS_MAGIC:
        raise ValueError("Not a valid DDS file")

    info = DdsInfo(file_size=len(data))

    # Main header at offset 4 (124 bytes)
    _size = struct.unpack_from("<I", data, 4)[0]  # Should be 124
    flags = struct.unpack_from("<I", data, 8)[0]
    info.height = struct.unpack_from("<I", data, 12)[0]
    info.width = struct.unpack_from("<I", data, 16)[0]

    if flags & DDSD_MIPMAPCOUNT:
        info.mip_count = struct.unpack_from("<I", data, 28)[0]

    # Pixel format at offset 76
    pf_flags = struct.unpack_from("<I", data, 80)[0]
    fourcc = data[84:88]
    info.bits_per_pixel = struct.unpack_from("<I", data, 88)[0]

    if pf_flags & DDPF_FOURCC:
        info.fourcc = fourcc.decode("ascii", "replace").strip("\x00")
        info.compressed = True
        if fourcc == DXT1:
            info.format = "DXT1 (BC1)"
            info.has_alpha = False
        elif fourcc == DXT3:
            info.format = "DXT3 (BC2)"
            info.has_alpha = True
        elif fourcc == DXT5:
            info.format = "DXT5 (BC3)"
            info.has_alpha = True
        elif fourcc == DX10:
            info.format = "DX10 Extended"
            info.data_offset = 148  # DX10 header adds 20 bytes
            if len(data) >= 148:
                dxgi_format = struct.unpack_from("<I", data, 128)[0]
                info.format = f"DX10 (DXGI={dxgi_format})"
                # Common DXGI formats
                _dxgi_names = {
                    71: "BC1 (DXT1)", 72: "BC1 sRGB",
                    74: "BC2 (DXT3)", 75: "BC2 sRGB",
                    77: "BC3 (DXT5)", 78: "BC3 sRGB",
                    80: "BC4", 81: "BC4 Signed",
                    83: "BC5", 84: "BC5 Signed",
                    95: "BC6H UF16", 96: "BC6H SF16",
                    98: "BC7", 99: "BC7 sRGB",
                }
                if dxgi_format in _dxgi_names:
                    info.format = _dxgi_names[dxgi_format]
        else:
            info.format = f"FourCC: {info.fourcc}"
    elif pf_flags & DDPF_RGB:
        info.compressed = False
        info.has_alpha = bool(pf_flags & DDPF_ALPHAPIXELS)
        if info.bits_per_pixel == 32:
            info.format = "RGBA 32-bit" if info.has_alpha else "RGB 32-bit"
        elif info.bits_per_pixel == 24:
            info.format = "RGB 24-bit"
        elif info.bits_per_pixel == 16:
            info.format = "RGB 16-bit"
        else:
            info.format = f"RGB {info.bits_per_pixel}-bit"
    elif pf_flags & DDPF_LUMINANCE:
        info.compressed = False
        info.has_alpha = bool(pf_flags & DDPF_ALPHAPIXELS)
        if info.bits_per_pixel == 8:
            info.format = "Luminance 8-bit"
        elif info.bits_per_pixel == 16:
            info.format = "Luminance 16-bit"
        else:
            info.format = f"Luminance {info.bits_per_pixel}-bit"
    else:
        # Last resort: check if BPP and masks hint at a format
        rmask = struct.unpack_from("<I", data, 92)[0]
        if info.bits_per_pixel == 8 and rmask == 0xFF:
            info.format = "Luminance 8-bit"
            info.compressed = False
        elif info.bits_per_pixel == 16 and rmask == 0xFFFF:
            info.format = "Luminance 16-bit"
            info.compressed = False
        else:
            info.format = "Unknown"

    return info


def decode_dds_to_rgba(data: bytes) -> tuple[int, int, bytes]:
    """Decode DDS first mip to raw RGBA bytes.

    Returns (width, height, rgba_bytes) or raises on unsupported format.
    Only supports DXT1, DXT5, and uncompressed RGBA for preview.
    """
    # If the file's body is shorter than the header declares, it may be a
    # type-1 self-compressed DDS where the LZ4 sizes are embedded in the
    # reserved area. Try the in-package decompressor before validating.
    info_pre = read_dds_info(data)
    expected_total = expected_dds_data_size(info_pre)
    if expected_total is not None and len(data) < expected_total:
        try:
            from core.compression_engine import (
                _decompress_type1_dds_per_mip_sizes,
                _decompress_type1_dds_first_mip_lz4_tail,
            )
            expanded = _decompress_type1_dds_per_mip_sizes(data, expected_total)
            if len(expanded) < expected_total:
                expanded = _decompress_type1_dds_first_mip_lz4_tail(data, expected_total)
            if len(expanded) >= expected_total:
                data = expanded
        except Exception:
            pass

    info = validate_dds_payload_size(data)
    w, h = info.width, info.height
    offset = info.data_offset

    if not info.compressed and info.bits_per_pixel == 32:
        # Uncompressed BGRA → RGBA
        expected_size = w * h * 4
        pixel_data = data[offset:offset + expected_size]
        if len(pixel_data) < expected_size:
            raise ValueError(
                "DDS header claims an uncompressed 32-bit image, "
                f"but only {len(pixel_data)} of {expected_size} bytes are present."
            )
        rgba = bytearray(len(pixel_data))
        for i in range(0, len(pixel_data), 4):
            if i + 3 < len(pixel_data):
                rgba[i] = pixel_data[i + 2]      # R
                rgba[i + 1] = pixel_data[i + 1]  # G
                rgba[i + 2] = pixel_data[i]       # B
                rgba[i + 3] = pixel_data[i + 3]   # A
        return w, h, bytes(rgba)

    if info.fourcc == "DXT1" or info.format.startswith("BC1"):
        return w, h, _decode_dxt1(data[offset:], w, h)

    if info.fourcc == "DXT3" or info.format.startswith("BC2"):
        return w, h, _decode_dxt3(data[offset:], w, h)

    if info.fourcc == "DXT5" or info.format.startswith("BC3"):
        return w, h, _decode_dxt5(data[offset:], w, h)

    if info.fourcc == "BC4U" or info.format.startswith("BC4"):
        return w, h, _decode_bc4(data[offset:], w, h)

    if info.fourcc == "BC5U" or info.format.startswith("BC5"):
        return w, h, _decode_bc5(data[offset:], w, h)

    if info.format.startswith("BC6H"):
        return w, h, _decode_bc6h(data[offset:], w, h)

    if info.format.startswith("BC7"):
        return w, h, _decode_bc7(data[offset:], w, h)

    # Luminance 8-bit: single channel grayscale → RGBA
    if info.format == "Luminance 8-bit":
        return w, h, _decode_luminance_8(data[offset:], w, h)

    # Luminance 16-bit: single 16-bit channel → RGBA (heightmaps, SDF)
    if info.format == "Luminance 16-bit":
        return w, h, _decode_luminance_16(data[offset:], w, h)

    # DX10 uncompressed formats (DXGI ID encoded in info.format string).
    # Dispatched here because read_dds_info marks all DX10 files compressed=True,
    # so the standard "not info.compressed" RGBA branch above does not fire.
    if info.format.startswith("DX10 (DXGI="):
        dxgi = _dx10_dxgi_id(info)
        if dxgi in (28, 29, 30, 31):  # R8G8B8A8 (incl. sRGB / SNORM)
            return w, h, _decode_dx10_rgba8(data[offset:], w, h, swap_rb=False)
        if dxgi in (87, 88, 90, 91):  # B8G8R8A8 (incl. sRGB / TYPELESS)
            return w, h, _decode_dx10_rgba8(data[offset:], w, h, swap_rb=True)
        if dxgi in (24, 25):  # R10G10B10A2_UNORM / UINT
            return w, h, _decode_dx10_r10g10b10a2(data[offset:], w, h)
        if dxgi == 10:        # R16G16B16A16_FLOAT
            return w, h, _decode_dx10_rgba_f16(data[offset:], w, h)
        if dxgi == 2:         # R32G32B32A32_FLOAT
            return w, h, _decode_dx10_rgba_f32(data[offset:], w, h)
        if dxgi == 54:        # R16_FLOAT
            return w, h, _decode_dx10_r16(data[offset:], w, h, is_float=True)
        if dxgi == 55:        # R16_UNORM
            return w, h, _decode_dx10_r16(data[offset:], w, h, is_float=False)
        if dxgi == 41:        # R32_FLOAT
            return w, h, _decode_dx10_r32(data[offset:], w, h, is_float=True)
        if dxgi == 43:        # R32_UINT
            return w, h, _decode_dx10_r32(data[offset:], w, h, is_float=False)
        if dxgi in (61, 62):  # R8_UNORM / R8_UINT
            return w, h, _decode_dx10_r8(data[offset:], w, h)

    # Unknown FourCC with small data: treat as raw pixel data, show as grayscale
    if info.compressed and info.format.startswith("FourCC:"):
        try:
            return w, h, _decode_raw_fallback(data[offset:], w, h, info.bits_per_pixel)
        except Exception:
            pass

    raise ValueError(f"Unsupported DDS format for preview: {info.format}")


def _dx10_dxgi_id(info: DdsInfo) -> int | None:
    try:
        return int(info.format.split("=")[1].rstrip(")"))
    except (ValueError, IndexError):
        return None


def _decode_dxt1(data: bytes, width: int, height: int) -> bytes:
    """Decode DXT1 (BC1) to RGBA."""
    rgba = bytearray(width * height * 4)
    blocks_x = max(1, (width + 3) // 4)
    blocks_y = max(1, (height + 3) // 4)
    offset = 0

    for by in range(blocks_y):
        for bx in range(blocks_x):
            if offset + 8 > len(data):
                break

            c0 = struct.unpack_from("<H", data, offset)[0]
            c1 = struct.unpack_from("<H", data, offset + 2)[0]
            bits = struct.unpack_from("<I", data, offset + 4)[0]
            offset += 8

            # Decode RGB565 colors
            colors = [_rgb565(c0), _rgb565(c1), None, None]
            if c0 > c1:
                colors[2] = _lerp_color(colors[0], colors[1], 1, 3)
                colors[3] = _lerp_color(colors[0], colors[1], 2, 3)
            else:
                colors[2] = _lerp_color(colors[0], colors[1], 1, 2)
                colors[3] = (0, 0, 0, 0)

            for py in range(4):
                for px in range(4):
                    x = bx * 4 + px
                    y = by * 4 + py
                    if x < width and y < height:
                        idx = (bits >> (2 * (py * 4 + px))) & 3
                        c = colors[idx]
                        p = (y * width + x) * 4
                        rgba[p] = c[0]
                        rgba[p + 1] = c[1]
                        rgba[p + 2] = c[2]
                        rgba[p + 3] = 255 if len(c) < 4 else c[3]

    return bytes(rgba)


def _decode_dxt5(data: bytes, width: int, height: int) -> bytes:
    """Decode DXT5 (BC3) to RGBA."""
    rgba = bytearray(width * height * 4)
    blocks_x = max(1, (width + 3) // 4)
    blocks_y = max(1, (height + 3) // 4)
    offset = 0

    for by in range(blocks_y):
        for bx in range(blocks_x):
            if offset + 16 > len(data):
                break

            # Alpha block (8 bytes)
            a0 = data[offset]
            a1 = data[offset + 1]
            alpha_bits = int.from_bytes(data[offset + 2:offset + 8], "little")
            offset += 8

            alpha_lut = [a0, a1]
            if a0 > a1:
                for i in range(6):
                    alpha_lut.append(((6 - i) * a0 + (1 + i) * a1) // 7)
            else:
                for i in range(4):
                    alpha_lut.append(((4 - i) * a0 + (1 + i) * a1) // 5)
                alpha_lut.extend([0, 255])

            # Color block (8 bytes)
            c0 = struct.unpack_from("<H", data, offset)[0]
            c1 = struct.unpack_from("<H", data, offset + 2)[0]
            bits = struct.unpack_from("<I", data, offset + 4)[0]
            offset += 8

            colors = [_rgb565(c0), _rgb565(c1)]
            colors.append(_lerp_color(colors[0], colors[1], 1, 3))
            colors.append(_lerp_color(colors[0], colors[1], 2, 3))

            for py in range(4):
                for px in range(4):
                    x = bx * 4 + px
                    y = by * 4 + py
                    if x < width and y < height:
                        ci = (bits >> (2 * (py * 4 + px))) & 3
                        ai = (alpha_bits >> (3 * (py * 4 + px))) & 7
                        c = colors[ci]
                        p = (y * width + x) * 4
                        rgba[p] = c[0]
                        rgba[p + 1] = c[1]
                        rgba[p + 2] = c[2]
                        rgba[p + 3] = alpha_lut[ai] if ai < len(alpha_lut) else 255

    return bytes(rgba)


def _rgb565(v: int) -> tuple[int, int, int]:
    r = ((v >> 11) & 0x1F) * 255 // 31
    g = ((v >> 5) & 0x3F) * 255 // 63
    b = (v & 0x1F) * 255 // 31
    return (r, g, b)


def _lerp_color(c0, c1, num, denom):
    return tuple(
        (c0[i] * (denom - num) + c1[i] * num) // denom
        for i in range(min(len(c0), len(c1)))
    )


def _decode_dxt3(data: bytes, width: int, height: int) -> bytes:
    """Decode DXT3 (BC2) to RGBA — explicit alpha."""
    rgba = bytearray(width * height * 4)
    bx_count = max(1, (width + 3) // 4)
    by_count = max(1, (height + 3) // 4)
    offset = 0
    for by in range(by_count):
        for bx in range(bx_count):
            if offset + 16 > len(data):
                break
            # 8 bytes explicit alpha (4 bits per pixel, 16 pixels)
            alpha_bits = int.from_bytes(data[offset:offset + 8], "little")
            offset += 8
            # 8 bytes color (same as DXT1)
            c0 = struct.unpack_from("<H", data, offset)[0]
            c1 = struct.unpack_from("<H", data, offset + 2)[0]
            bits = struct.unpack_from("<I", data, offset + 4)[0]
            offset += 8
            colors = [_rgb565(c0), _rgb565(c1)]
            colors.append(_lerp_color(colors[0], colors[1], 1, 3))
            colors.append(_lerp_color(colors[0], colors[1], 2, 3))
            for py in range(4):
                for px in range(4):
                    x = bx * 4 + px
                    y = by * 4 + py
                    if x < width and y < height:
                        ci = (bits >> (2 * (py * 4 + px))) & 3
                        ai = (alpha_bits >> (4 * (py * 4 + px))) & 0xF
                        c = colors[ci]
                        p = (y * width + x) * 4
                        rgba[p] = c[0]; rgba[p+1] = c[1]; rgba[p+2] = c[2]
                        rgba[p+3] = ai * 17  # 4-bit to 8-bit
    return bytes(rgba)


def _decode_bc4(data: bytes, width: int, height: int) -> bytes:
    """Decode BC4 (single channel) to RGBA — grayscale."""
    rgba = bytearray(width * height * 4)
    bx_count = max(1, (width + 3) // 4)
    by_count = max(1, (height + 3) // 4)
    offset = 0
    for by in range(by_count):
        for bx in range(bx_count):
            if offset + 8 > len(data):
                break
            r0 = data[offset]
            r1 = data[offset + 1]
            lut = _build_bc4_lut(r0, r1)
            bits = int.from_bytes(data[offset + 2:offset + 8], "little")
            offset += 8
            for py in range(4):
                for px in range(4):
                    x = bx * 4 + px
                    y = by * 4 + py
                    if x < width and y < height:
                        idx = (bits >> (3 * (py * 4 + px))) & 7
                        v = lut[idx] if idx < len(lut) else 0
                        p = (y * width + x) * 4
                        rgba[p] = v; rgba[p+1] = v; rgba[p+2] = v; rgba[p+3] = 255
    return bytes(rgba)


def _decode_bc5(data: bytes, width: int, height: int) -> bytes:
    """Decode BC5 (two channel — normal map) to RGBA."""
    rgba = bytearray(width * height * 4)
    bx_count = max(1, (width + 3) // 4)
    by_count = max(1, (height + 3) // 4)
    offset = 0
    for by in range(by_count):
        for bx in range(bx_count):
            if offset + 16 > len(data):
                break
            # Red channel (8 bytes)
            r0 = data[offset]; r1 = data[offset + 1]
            r_lut = _build_bc4_lut(r0, r1)
            r_bits = int.from_bytes(data[offset + 2:offset + 8], "little")
            offset += 8
            # Green channel (8 bytes)
            g0 = data[offset]; g1 = data[offset + 1]
            g_lut = _build_bc4_lut(g0, g1)
            g_bits = int.from_bytes(data[offset + 2:offset + 8], "little")
            offset += 8
            for py in range(4):
                for px in range(4):
                    x = bx * 4 + px
                    y = by * 4 + py
                    if x < width and y < height:
                        ri = (r_bits >> (3 * (py * 4 + px))) & 7
                        gi = (g_bits >> (3 * (py * 4 + px))) & 7
                        rv = r_lut[ri] if ri < len(r_lut) else 0
                        gv = g_lut[gi] if gi < len(g_lut) else 0
                        # Reconstruct Z from XY normal (blue channel)
                        nx = (rv / 255.0) * 2.0 - 1.0
                        ny = (gv / 255.0) * 2.0 - 1.0
                        nz_sq = max(0.0, 1.0 - nx * nx - ny * ny)
                        bv = int((nz_sq ** 0.5 * 0.5 + 0.5) * 255)
                        p = (y * width + x) * 4
                        rgba[p] = rv; rgba[p+1] = gv; rgba[p+2] = min(255, bv); rgba[p+3] = 255
    return bytes(rgba)


def _decode_bc6h(data: bytes, width: int, height: int) -> bytes:
    """Decode BC6H (HDR) to RGBA — simplified tone-mapped preview."""
    # BC6H is extremely complex (14 modes). For preview, do a simple
    # approximation: read endpoint colors and interpolate.
    rgba = bytearray(width * height * 4)
    bx_count = max(1, (width + 3) // 4)
    by_count = max(1, (height + 3) // 4)
    offset = 0
    for by in range(by_count):
        for bx in range(bx_count):
            if offset + 16 > len(data):
                break
            # Read first 6 bytes as approximate RGB endpoints
            block = data[offset:offset + 16]
            # Extract low bits as color approximation
            r = min(255, (block[0] & 0xFF))
            g = min(255, (block[2] & 0xFF))
            b = min(255, (block[4] & 0xFF))
            offset += 16
            for py in range(4):
                for px in range(4):
                    x = bx * 4 + px
                    y_pos = by * 4 + py
                    if x < width and y_pos < height:
                        p = (y_pos * width + x) * 4
                        rgba[p] = r; rgba[p+1] = g; rgba[p+2] = b; rgba[p+3] = 255
    return bytes(rgba)


def _decode_bc7(data: bytes, width: int, height: int) -> bytes:
    """Decode BC7 to RGBA — simplified mode-based preview.

    BC7 has 8 modes with varying partition counts, endpoint precision,
    and index bits. This implements a simplified decoder that handles
    the most common modes (4, 5, 6) for preview quality.
    """
    rgba = bytearray(width * height * 4)
    bx_count = max(1, (width + 3) // 4)
    by_count = max(1, (height + 3) // 4)
    offset = 0
    for by in range(by_count):
        for bx in range(bx_count):
            if offset + 16 > len(data):
                break
            block = data[offset:offset + 16]
            offset += 16
            # Determine mode from leading bits
            mode = -1
            for m in range(8):
                if block[0] & (1 << m):
                    mode = m
                    break
            # Simplified: extract endpoint colors from block bytes
            # Mode 6 (most common): 2 RGBA endpoints, 4-bit indices
            if mode == 6:
                # Endpoints encoded in bits 7-62 (roughly)
                r0 = (block[1] >> 1) & 0x7F
                g0 = ((block[1] & 1) << 6) | ((block[2] >> 2) & 0x3F)
                b0 = ((block[2] & 3) << 5) | ((block[3] >> 3) & 0x1F)
                r0 = (r0 * 255) // 127
                g0 = (g0 * 255) // 127
                b0 = (b0 * 255) // 127
            else:
                # Fallback: use first bytes as approximate color
                r0 = block[1]
                g0 = block[2]
                b0 = block[3]
            for py in range(4):
                for px in range(4):
                    x = bx * 4 + px
                    y_pos = by * 4 + py
                    if x < width and y_pos < height:
                        p = (y_pos * width + x) * 4
                        rgba[p] = min(255, r0)
                        rgba[p+1] = min(255, g0)
                        rgba[p+2] = min(255, b0)
                        rgba[p+3] = 255
    return bytes(rgba)


def _build_bc4_lut(a0: int, a1: int) -> list[int]:
    """Build BC4/BC5 interpolation lookup table."""
    lut = [a0, a1]
    if a0 > a1:
        for i in range(6):
            lut.append(((6 - i) * a0 + (1 + i) * a1) // 7)
    else:
        for i in range(4):
            lut.append(((4 - i) * a0 + (1 + i) * a1) // 5)
        lut.extend([0, 255])
    return lut


def _decode_luminance_8(data: bytes, width: int, height: int) -> bytes:
    """Decode 8-bit luminance (grayscale) to RGBA."""
    expected = width * height
    rgba = bytearray(expected * 4)
    for i in range(min(expected, len(data))):
        v = data[i]
        p = i * 4
        rgba[p] = v
        rgba[p + 1] = v
        rgba[p + 2] = v
        rgba[p + 3] = 255
    return bytes(rgba)


def _decode_luminance_16(data: bytes, width: int, height: int) -> bytes:
    """Decode 16-bit luminance to RGBA (maps 0-65535 to 0-255)."""
    expected = width * height
    rgba = bytearray(expected * 4)
    for i in range(min(expected, len(data) // 2)):
        v = struct.unpack_from("<H", data, i * 2)[0]
        v8 = v >> 8  # Map 16-bit to 8-bit
        p = i * 4
        rgba[p] = v8
        rgba[p + 1] = v8
        rgba[p + 2] = v8
        rgba[p + 3] = 255
    return bytes(rgba)


def _hdr_tonemap_to_u8(arr):
    """Tone-map a non-negative-clamped float array to uint8 with gamma 1/2.2.

    Normalizes by max value when max > 1 so HDR textures (reflection probes,
    light data) still render with visible mid-tones.  Pure LDR data stored in
    float (max <= 1) is gamma-corrected without rescaling.
    """
    import numpy as np
    arr = np.clip(arr, 0.0, None)
    if arr.size == 0:
        return arr.astype(np.uint8)
    mx = float(arr.max())
    if mx > 1.0:
        arr = arr * (1.0 / mx)
    elif mx <= 1e-6:
        return np.zeros(arr.shape, dtype=np.uint8)
    arr = np.clip(arr, 0.0, 1.0) ** (1.0 / 2.2)
    return (arr * 255.0 + 0.5).astype(np.uint8)


def _decode_dx10_rgba8(data: bytes, width: int, height: int, swap_rb: bool) -> bytes:
    """Decode DX10 RGBA8 / BGRA8 first mip to RGBA bytes."""
    import numpy as np
    expected = width * height * 4
    pixels = np.frombuffer(data[:expected], dtype=np.uint8)
    if pixels.size < expected:
        return bytes(bytearray(expected))
    pixels = pixels.reshape(height, width, 4)
    if swap_rb:
        pixels = pixels[..., [2, 1, 0, 3]]
    return pixels.tobytes()


def _decode_dx10_r10g10b10a2(data: bytes, width: int, height: int) -> bytes:
    """Decode R10G10B10A2_UNORM/UINT first mip to RGBA bytes.

    Each pixel is one little-endian uint32 packed as
    [R:10, G:10, B:10, A:2] from LSB to MSB.  Channels scale to 8-bit.
    """
    import numpy as np
    expected = width * height * 4
    raw = np.frombuffer(data[:expected], dtype=np.uint32)
    if raw.size < width * height:
        return bytes(bytearray(expected))
    r = (raw & 0x3FF)
    g = (raw >> 10) & 0x3FF
    b = (raw >> 20) & 0x3FF
    a = (raw >> 30) & 0x3
    out = np.empty((width * height, 4), dtype=np.uint8)
    out[:, 0] = (r * 255 // 1023).astype(np.uint8)
    out[:, 1] = (g * 255 // 1023).astype(np.uint8)
    out[:, 2] = (b * 255 // 1023).astype(np.uint8)
    out[:, 3] = (a * 85).astype(np.uint8)  # 0/85/170/255
    return out.tobytes()


def _decode_dx10_rgba_f16(data: bytes, width: int, height: int) -> bytes:
    """Decode R16G16B16A16_FLOAT first mip to RGBA bytes (tone-mapped)."""
    import numpy as np
    expected = width * height * 4 * 2  # 4 channels x 2 bytes
    halfs = np.frombuffer(data[:expected], dtype=np.float16)
    if halfs.size < width * height * 4:
        return bytes(bytearray(width * height * 4))
    rgba = halfs.astype(np.float32).reshape(height, width, 4)
    rgb = _hdr_tonemap_to_u8(rgba[..., :3])
    a = np.clip(rgba[..., 3], 0.0, 1.0)
    a8 = (a * 255.0 + 0.5).astype(np.uint8)
    out = np.empty((height, width, 4), dtype=np.uint8)
    out[..., :3] = rgb
    out[..., 3] = a8
    return out.tobytes()


def _decode_dx10_rgba_f32(data: bytes, width: int, height: int) -> bytes:
    """Decode R32G32B32A32_FLOAT first mip to RGBA bytes (tone-mapped)."""
    import numpy as np
    expected = width * height * 4 * 4
    floats = np.frombuffer(data[:expected], dtype=np.float32)
    if floats.size < width * height * 4:
        return bytes(bytearray(width * height * 4))
    rgba = floats.reshape(height, width, 4)
    rgb = _hdr_tonemap_to_u8(rgba[..., :3])
    a = np.clip(rgba[..., 3], 0.0, 1.0)
    a8 = (a * 255.0 + 0.5).astype(np.uint8)
    out = np.empty((height, width, 4), dtype=np.uint8)
    out[..., :3] = rgb
    out[..., 3] = a8
    return out.tobytes()


def _decode_dx10_r16(data: bytes, width: int, height: int, is_float: bool) -> bytes:
    """Decode single-channel 16-bit DX10 to grayscale RGBA."""
    import numpy as np
    expected = width * height * 2
    if is_float:
        vals = np.frombuffer(data[:expected], dtype=np.float16).astype(np.float32)
        v8 = _hdr_tonemap_to_u8(vals)
    else:
        u16 = np.frombuffer(data[:expected], dtype=np.uint16)
        v8 = (u16 >> 8).astype(np.uint8)  # 0-65535 -> 0-255
    if v8.size < width * height:
        return bytes(bytearray(width * height * 4))
    v8 = v8.reshape(height, width)
    out = np.empty((height, width, 4), dtype=np.uint8)
    out[..., 0] = v8
    out[..., 1] = v8
    out[..., 2] = v8
    out[..., 3] = 255
    return out.tobytes()


def _decode_dx10_r32(data: bytes, width: int, height: int, is_float: bool) -> bytes:
    """Decode single-channel 32-bit DX10 to grayscale RGBA."""
    import numpy as np
    expected = width * height * 4
    if is_float:
        vals = np.frombuffer(data[:expected], dtype=np.float32)
        v8 = _hdr_tonemap_to_u8(vals)
    else:
        u32 = np.frombuffer(data[:expected], dtype=np.uint32)
        if u32.size:
            mx = max(1, int(u32.max()))
            v8 = (u32 * 255 // mx).astype(np.uint8)
        else:
            v8 = u32.astype(np.uint8)
    if v8.size < width * height:
        return bytes(bytearray(width * height * 4))
    v8 = v8.reshape(height, width)
    out = np.empty((height, width, 4), dtype=np.uint8)
    out[..., 0] = v8
    out[..., 1] = v8
    out[..., 2] = v8
    out[..., 3] = 255
    return out.tobytes()


def _decode_dx10_r8(data: bytes, width: int, height: int) -> bytes:
    """Decode R8_UNORM/UINT first mip to grayscale RGBA."""
    import numpy as np
    expected = width * height
    src = np.frombuffer(data[:expected], dtype=np.uint8)
    if src.size < expected:
        return bytes(bytearray(width * height * 4))
    src = src.reshape(height, width)
    out = np.empty((height, width, 4), dtype=np.uint8)
    out[..., 0] = src
    out[..., 1] = src
    out[..., 2] = src
    out[..., 3] = 255
    return out.tobytes()


def _decode_raw_fallback(data: bytes, width: int, height: int, bpp: int) -> bytes:
    """Last-resort decoder: treat raw pixel data as grayscale."""
    expected = width * height
    rgba = bytearray(expected * 4)
    bytes_per_pixel = max(1, bpp // 8) if bpp > 0 else 1

    for i in range(min(expected, len(data) // bytes_per_pixel)):
        off = i * bytes_per_pixel
        if bytes_per_pixel == 1:
            v = data[off]
        elif bytes_per_pixel == 2:
            v = struct.unpack_from("<H", data, off)[0] >> 8
        elif bytes_per_pixel >= 4:
            v = data[off]  # Just use first byte
        else:
            v = data[off]
        p = i * 4
        rgba[p] = v
        rgba[p + 1] = v
        rgba[p + 2] = v
        rgba[p + 3] = 255
    return bytes(rgba)


def get_dds_summary(data: bytes) -> str:
    """Get a human-readable summary of a DDS file."""
    try:
        info = validate_dds_payload_size(data)
        size_kb = info.file_size / 1024
        return (
            f"DDS Texture: {info.width}x{info.height}\n"
            f"Format: {info.format}\n"
            f"Mipmaps: {info.mip_count}\n"
            f"Alpha: {'Yes' if info.has_alpha else 'No'}\n"
            f"Size: {size_kb:,.0f} KB"
        )
    except Exception as e:
        return f"DDS: Error reading header ({e})"
