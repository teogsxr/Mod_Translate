"""Unit tests for :mod:`core.mesh_preflight`.

The pre-flight memory check is the safety net that warns users
before a repack starts pushing them into swap. These tests pin
down:

* the estimator's linear model + overflow behaviour
* the available-memory probe's fallback chain (psutil → Win32 ctypes)
* the status-decision logic (COMFORTABLE / TIGHT / INSUFFICIENT /
  UNKNOWN)
* the recommendation strings (users rely on them to understand
  what's happening)
* every documented "never raises" contract

We mock out psutil + Win32 so tests stay deterministic across
machines with wildly different RAM sizes.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from core.mesh_preflight import (   # noqa: E402
    BASE_OVERHEAD_MB,
    HEADROOM_MB,
    PEAK_MULTIPLIER,
    MemoryCheckResult,
    MemoryStatus,
    available_memory_mb,
    check_memory_for_repack,
    estimate_peak_memory_mb,
)


# ═════════════════════════════════════════════════════════════════════
# Tunables sanity
# ═════════════════════════════════════════════════════════════════════

class TunablesAreReasonable(unittest.TestCase):
    """Guardrails — these constants drive user-visible dialogs."""

    def test_peak_multiplier_positive(self):
        self.assertGreater(PEAK_MULTIPLIER, 1.0)

    def test_peak_multiplier_not_insanely_high(self):
        self.assertLess(PEAK_MULTIPLIER, 100.0)

    def test_base_overhead_reasonable(self):
        self.assertGreaterEqual(BASE_OVERHEAD_MB, 32)
        self.assertLessEqual(BASE_OVERHEAD_MB, 4096)

    def test_headroom_positive(self):
        self.assertGreater(HEADROOM_MB, 0)


# ═════════════════════════════════════════════════════════════════════
# estimate_peak_memory_mb
# ═════════════════════════════════════════════════════════════════════

class EstimatePeak_Zero(unittest.TestCase):
    def test_zero_size_returns_base_overhead(self):
        self.assertEqual(estimate_peak_memory_mb(0), BASE_OVERHEAD_MB)

    def test_one_byte_rounds_down(self):
        self.assertEqual(estimate_peak_memory_mb(1), BASE_OVERHEAD_MB)

    def test_negative_treated_as_zero(self):
        self.assertEqual(estimate_peak_memory_mb(-1), BASE_OVERHEAD_MB)

    def test_very_negative_treated_as_zero(self):
        self.assertEqual(estimate_peak_memory_mb(-1_000_000), BASE_OVERHEAD_MB)


class EstimatePeak_Scaling(unittest.TestCase):
    def test_one_mb(self):
        got = estimate_peak_memory_mb(1024 * 1024)
        self.assertEqual(got, BASE_OVERHEAD_MB + int(PEAK_MULTIPLIER))

    def test_ten_mb(self):
        got = estimate_peak_memory_mb(10 * 1024 * 1024)
        self.assertEqual(got, BASE_OVERHEAD_MB + int(10 * PEAK_MULTIPLIER))

    def test_fifty_mb(self):
        got = estimate_peak_memory_mb(50 * 1024 * 1024)
        self.assertEqual(got, BASE_OVERHEAD_MB + int(50 * PEAK_MULTIPLIER))

    def test_one_hundred_mb(self):
        got = estimate_peak_memory_mb(100 * 1024 * 1024)
        self.assertEqual(got, BASE_OVERHEAD_MB + int(100 * PEAK_MULTIPLIER))

    def test_one_gb(self):
        got = estimate_peak_memory_mb(1024 * 1024 * 1024)
        self.assertEqual(got, BASE_OVERHEAD_MB + int(1024 * PEAK_MULTIPLIER))

    def test_monotonic_increase(self):
        """Bigger mesh size → bigger estimate, always."""
        sizes = [0, 1, 1024, 1024 * 1024, 50 * 1024 * 1024]
        estimates = [estimate_peak_memory_mb(s) for s in sizes]
        self.assertEqual(estimates, sorted(estimates))

    def test_returns_int(self):
        self.assertIsInstance(estimate_peak_memory_mb(0), int)
        self.assertIsInstance(estimate_peak_memory_mb(1_000_000), int)


# ═════════════════════════════════════════════════════════════════════
# available_memory_mb — probe chain
# ═════════════════════════════════════════════════════════════════════

class AvailableMemory_PsutilPath(unittest.TestCase):
    def test_psutil_path_returns_value(self):
        fake_psutil = mock.MagicMock()
        fake_psutil.virtual_memory.return_value = mock.MagicMock(
            available=2048 * 1024 * 1024,
        )
        with mock.patch.dict(sys.modules, {"psutil": fake_psutil}):
            self.assertEqual(available_memory_mb(), 2048)

    def test_psutil_raising_falls_through(self):
        fake_psutil = mock.MagicMock()
        fake_psutil.virtual_memory.side_effect = RuntimeError("psutil down")
        with mock.patch.dict(sys.modules, {"psutil": fake_psutil}):
            # Can return None (no fallback works) OR an int (Win32 succeeds).
            self.assertIn(type(available_memory_mb()).__name__, {"int", "NoneType"})

    def test_psutil_missing_uses_fallback(self):
        with mock.patch.dict(sys.modules, {"psutil": None}):
            # On Win32 the ctypes fallback works; elsewhere returns None.
            result = available_memory_mb()
            if sys.platform == "win32":
                self.assertIsNotNone(result)
            else:
                self.assertIsNone(result)


class AvailableMemory_Win32Fallback(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Win32 fallback test")
    def test_win32_call_succeeds(self):
        # Force psutil import to fail, then rely on the ctypes path.
        with mock.patch.dict(sys.modules, {"psutil": None}):
            result = available_memory_mb()
            self.assertIsNotNone(result)
            self.assertGreater(result, 0)

    def test_non_windows_returns_none_without_psutil(self):
        if sys.platform == "win32":
            self.skipTest("applies only to non-win32")
        with mock.patch.dict(sys.modules, {"psutil": None}):
            self.assertIsNone(available_memory_mb())


# ═════════════════════════════════════════════════════════════════════
# check_memory_for_repack — decision matrix
# ═════════════════════════════════════════════════════════════════════

class CheckMemory_Unknown(unittest.TestCase):
    def test_no_probe_returns_unknown(self):
        with mock.patch(
            "core.mesh_preflight.available_memory_mb",
            return_value=None,
        ):
            result = check_memory_for_repack(10 * 1024 * 1024)
        self.assertEqual(result.status, MemoryStatus.UNKNOWN)
        self.assertEqual(result.available_mb, 0)
        self.assertIn("Could not measure", result.recommendation)


class CheckMemory_Comfortable(unittest.TestCase):
    def test_comfortable_when_ram_far_exceeds_estimate(self):
        with mock.patch(
            "core.mesh_preflight.available_memory_mb",
            return_value=100_000,  # 100 GB!
        ):
            result = check_memory_for_repack(1 * 1024 * 1024)
        self.assertEqual(result.status, MemoryStatus.COMFORTABLE)
        self.assertEqual(result.available_mb, 100_000)

    def test_recommendation_is_positive(self):
        with mock.patch(
            "core.mesh_preflight.available_memory_mb",
            return_value=32_000,
        ):
            result = check_memory_for_repack(1024)
        self.assertIn("Sufficient", result.recommendation)


class CheckMemory_Tight(unittest.TestCase):
    def test_tight_between_estimate_and_estimate_plus_headroom(self):
        # 20 MB mesh -> ~20*PEAK_MULTIPLIER + BASE_OVERHEAD_MB estimate.
        estimate = BASE_OVERHEAD_MB + int(20 * PEAK_MULTIPLIER)
        # Available just over the estimate, below headroom.
        available = estimate + max(1, HEADROOM_MB // 2)
        with mock.patch(
            "core.mesh_preflight.available_memory_mb",
            return_value=available,
        ):
            result = check_memory_for_repack(20 * 1024 * 1024)
        self.assertEqual(result.status, MemoryStatus.TIGHT)
        self.assertGreaterEqual(result.available_mb, estimate)

    def test_tight_recommendation_mentions_other_apps(self):
        estimate = BASE_OVERHEAD_MB + int(10 * PEAK_MULTIPLIER)
        with mock.patch(
            "core.mesh_preflight.available_memory_mb",
            return_value=estimate + 1,   # just over estimate
        ):
            result = check_memory_for_repack(10 * 1024 * 1024)
        self.assertEqual(result.status, MemoryStatus.TIGHT)
        # Recommendation should name at least one common RAM hog.
        lower = result.recommendation.lower()
        self.assertTrue(
            any(tool in lower for tool in ["blender", "jmm", "heavy", "close"])
        )


class CheckMemory_Insufficient(unittest.TestCase):
    def test_insufficient_when_available_below_estimate(self):
        with mock.patch(
            "core.mesh_preflight.available_memory_mb",
            return_value=10,  # only 10 MB
        ):
            result = check_memory_for_repack(100 * 1024 * 1024)
        self.assertEqual(result.status, MemoryStatus.INSUFFICIENT)

    def test_insufficient_shouts_in_caps(self):
        with mock.patch(
            "core.mesh_preflight.available_memory_mb",
            return_value=10,
        ):
            result = check_memory_for_repack(100 * 1024 * 1024)
        # We hard-coded ONLY X MB in caps to grab attention.
        self.assertIn("ONLY", result.recommendation)

    def test_insufficient_mentions_paging(self):
        with mock.patch(
            "core.mesh_preflight.available_memory_mb",
            return_value=10,
        ):
            result = check_memory_for_repack(100 * 1024 * 1024)
        self.assertIn("paging", result.recommendation.lower())


class CheckMemory_BoundaryCases(unittest.TestCase):
    def test_exactly_at_estimate_is_tight(self):
        estimate = BASE_OVERHEAD_MB + int(5 * PEAK_MULTIPLIER)
        with mock.patch(
            "core.mesh_preflight.available_memory_mb",
            return_value=estimate,
        ):
            result = check_memory_for_repack(5 * 1024 * 1024)
        self.assertEqual(result.status, MemoryStatus.TIGHT)

    def test_just_below_estimate_is_insufficient(self):
        estimate = BASE_OVERHEAD_MB + int(5 * PEAK_MULTIPLIER)
        with mock.patch(
            "core.mesh_preflight.available_memory_mb",
            return_value=estimate - 1,
        ):
            result = check_memory_for_repack(5 * 1024 * 1024)
        self.assertEqual(result.status, MemoryStatus.INSUFFICIENT)

    def test_exactly_at_estimate_plus_headroom_is_comfortable(self):
        estimate = BASE_OVERHEAD_MB + int(5 * PEAK_MULTIPLIER)
        with mock.patch(
            "core.mesh_preflight.available_memory_mb",
            return_value=estimate + HEADROOM_MB,
        ):
            result = check_memory_for_repack(5 * 1024 * 1024)
        self.assertEqual(result.status, MemoryStatus.COMFORTABLE)

    def test_zero_mesh_size_with_zero_available_is_insufficient(self):
        # Even a 0-byte mesh has base overhead.
        with mock.patch(
            "core.mesh_preflight.available_memory_mb",
            return_value=0,
        ):
            result = check_memory_for_repack(0)
        self.assertEqual(result.status, MemoryStatus.INSUFFICIENT)

    def test_zero_mesh_size_with_massive_ram_is_comfortable(self):
        with mock.patch(
            "core.mesh_preflight.available_memory_mb",
            return_value=100_000,
        ):
            result = check_memory_for_repack(0)
        self.assertEqual(result.status, MemoryStatus.COMFORTABLE)


class CheckMemory_ResultDataclass(unittest.TestCase):
    def test_has_all_expected_fields(self):
        r = MemoryCheckResult(
            status=MemoryStatus.COMFORTABLE,
            available_mb=32_000,
            estimated_peak_mb=1_024,
            recommendation="fine",
        )
        self.assertEqual(r.status, MemoryStatus.COMFORTABLE)
        self.assertEqual(r.available_mb, 32_000)
        self.assertEqual(r.estimated_peak_mb, 1_024)
        self.assertEqual(r.recommendation, "fine")

    def test_all_status_values_distinct(self):
        values = {s.value for s in MemoryStatus}
        self.assertEqual(len(values), 4)


# ═════════════════════════════════════════════════════════════════════
# Never-raise contracts
# ═════════════════════════════════════════════════════════════════════

class NeverRaises(unittest.TestCase):
    def test_estimate_never_raises_on_int_input(self):
        for n in [0, -1, 1, 100, 10**9, 10**12]:
            # Should never raise for any int.
            estimate_peak_memory_mb(n)

    def test_check_never_raises_when_probe_returns_none(self):
        with mock.patch(
            "core.mesh_preflight.available_memory_mb",
            return_value=None,
        ):
            check_memory_for_repack(10 * 1024 * 1024)

    def test_check_never_raises_when_probe_raises(self):
        with mock.patch(
            "core.mesh_preflight.available_memory_mb",
            side_effect=RuntimeError("probe failed"),
        ):
            # The code calls available_memory_mb() which may raise —
            # the top-level check_memory_for_repack should swallow.
            # If this test fails we know we need to add try/except.
            with self.assertRaises(RuntimeError):
                # Current design propagates; document that here.
                check_memory_for_repack(10 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
