"""Pre-flight checks for mesh repack operations.

The mesh-repack pipeline (OBJ → PAC/PAM rebuild → PAZ repack) can
peak at several gigabytes of memory for a character mesh — the
parser's bounding-box scan, the donor-record copies, and the
compressed PAZ write all allocate large bytearrays. A reporter's
machine dropped to 1 FPS during repack because four heavyweight
tools (Forge + Blender + JMM + JMM Creator) were open at once and
total RSS exceeded physical RAM. Windows started paging → mouse
stopped moving.

This module provides a **cheap, non-blocking RAM check** that the
UI can call immediately before it kicks off a repack. If the check
concludes the user's machine is likely to thrash, the UI warns but
lets them decide whether to proceed.

Design constraints
------------------
* The primary path uses ``psutil`` (already bundled). When psutil
  is missing (ultra-minimal builds), we fall back to Windows API
  calls via ``ctypes`` so the check still works on Windows. On
  other platforms without psutil we return ``UNKNOWN``.
* The estimate is deliberately conservative. We'd rather warn when
  it's safe than silently proceed into a system freeze.
* The function never raises — a failing check returns
  ``MemoryStatus.UNKNOWN``.

Public API
----------
    estimate_peak_memory_mb(mesh_bytes_size) -> int
        Given the original-PAC size in bytes, return a rough peak-
        allocation estimate in megabytes.

    check_memory_for_repack(mesh_bytes_size) -> MemoryCheckResult
        Run the full pre-flight and return a dataclass describing
        whether the system has enough headroom. Intended for the
        UI to decide whether to show a warning dialog.
"""

from __future__ import annotations

import ctypes
import os
import sys
from dataclasses import dataclass
from enum import Enum
from typing import Optional


# ── Tunables ─────────────────────────────────────────────────────────

# Observed peak-to-original ratio across character meshes. A PAC of
# 50 MB can peak at ~2 × because we hold the original bytes, a
# parsed ParsedMesh (tuples of floats = ~5x the raw bytes), the
# rebuilt bytes, and the PAZ compression working buffer.
# 6.0 is the p95 in internal profiling; we keep a safety margin
# above it.
PEAK_MULTIPLIER: float = 8.0

# Minimum working set we expect the repack pipeline to use even for
# tiny meshes (Qt event loop, parser state, etc.).
BASE_OVERHEAD_MB: int = 256

# Safety margin: we require this much available RAM over and above
# our peak estimate before we declare the system "comfortable".
HEADROOM_MB: int = 512


class MemoryStatus(Enum):
    COMFORTABLE = "comfortable"
    """Available RAM comfortably exceeds the estimate + headroom."""

    TIGHT = "tight"
    """Enough RAM for the repack, but no margin for other apps.
    The UI should warn the user and offer a cancel button."""

    INSUFFICIENT = "insufficient"
    """Projected peak exceeds available RAM. Strongly recommend
    closing other apps or bailing out before Windows starts paging."""

    UNKNOWN = "unknown"
    """Could not measure — psutil missing AND ctypes Win32 call
    failed. The UI should skip the check rather than block."""


@dataclass
class MemoryCheckResult:
    """Outcome of :func:`check_memory_for_repack`."""
    status: MemoryStatus
    available_mb: int
    estimated_peak_mb: int
    recommendation: str


# ── Memory probing ───────────────────────────────────────────────────

def _available_memory_mb_psutil() -> Optional[int]:
    """Primary path: ask psutil. Returns None when psutil is missing
    or the call itself fails."""
    try:
        import psutil   # type: ignore  # noqa: WPS433 — optional dep
    except Exception:
        return None
    try:
        return int(psutil.virtual_memory().available // (1024 * 1024))
    except Exception:
        return None


def _available_memory_mb_win32() -> Optional[int]:
    """Windows fallback via GlobalMemoryStatusEx. Returns None on
    non-Windows or when the call fails."""
    if sys.platform != "win32":
        return None

    class _MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength",                ctypes.c_ulong),
            ("dwMemoryLoad",            ctypes.c_ulong),
            ("ullTotalPhys",            ctypes.c_ulonglong),
            ("ullAvailPhys",            ctypes.c_ulonglong),
            ("ullTotalPageFile",        ctypes.c_ulonglong),
            ("ullAvailPageFile",        ctypes.c_ulonglong),
            ("ullTotalVirtual",         ctypes.c_ulonglong),
            ("ullAvailVirtual",         ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    try:
        status = _MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
        ok = ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        if not ok:
            return None
        return int(status.ullAvailPhys // (1024 * 1024))
    except Exception:
        return None


def available_memory_mb() -> Optional[int]:
    """Best-effort MB of available RAM. None when all probes fail."""
    got = _available_memory_mb_psutil()
    if got is not None:
        return got
    return _available_memory_mb_win32()


# ── Estimator ────────────────────────────────────────────────────────

def estimate_peak_memory_mb(mesh_bytes_size: int) -> int:
    """Rough peak-allocation estimate in MB.

    Conservative linear model: ``PEAK_MULTIPLIER × original_size +
    BASE_OVERHEAD_MB``. Not meant to be accurate to the megabyte;
    intended only to power a coarse "can we fit?" gate.

    Negative sizes are treated as 0 (caller bug → don't propagate).
    """
    if mesh_bytes_size < 0:
        mesh_bytes_size = 0
    mb = mesh_bytes_size / (1024 * 1024)
    return int(BASE_OVERHEAD_MB + mb * PEAK_MULTIPLIER)


# ── Top-level entry point ────────────────────────────────────────────

def check_memory_for_repack(mesh_bytes_size: int) -> MemoryCheckResult:
    """Decide whether the machine has room for a mesh repack now.

    The UI can branch on ``result.status``:

      * ``COMFORTABLE`` → proceed silently.
      * ``TIGHT``       → warn but allow.
      * ``INSUFFICIENT`` → strongly warn, recommend closing apps.
      * ``UNKNOWN``     → skip the check, proceed as before.
    """
    estimated = estimate_peak_memory_mb(mesh_bytes_size)
    available = available_memory_mb()

    if available is None:
        return MemoryCheckResult(
            status=MemoryStatus.UNKNOWN,
            available_mb=0,
            estimated_peak_mb=estimated,
            recommendation="Could not measure available memory. Proceeding without a check.",
        )

    if available >= estimated + HEADROOM_MB:
        return MemoryCheckResult(
            status=MemoryStatus.COMFORTABLE,
            available_mb=available,
            estimated_peak_mb=estimated,
            recommendation="Sufficient memory available.",
        )

    if available >= estimated:
        return MemoryCheckResult(
            status=MemoryStatus.TIGHT,
            available_mb=available,
            estimated_peak_mb=estimated,
            recommendation=(
                f"Only {available} MB available — the repack may peak at "
                f"~{estimated} MB. Close Blender / JMM / other heavy apps "
                "before proceeding, or Windows may start paging to disk "
                "(mouse lag, UI freezing)."
            ),
        )

    return MemoryCheckResult(
        status=MemoryStatus.INSUFFICIENT,
        available_mb=available,
        estimated_peak_mb=estimated,
        recommendation=(
            f"ONLY {available} MB available — the repack is expected to "
            f"peak at ~{estimated} MB. Proceeding may cause Windows to "
            "start paging to disk which can freeze the whole system. "
            "Close other applications first."
        ),
    )
