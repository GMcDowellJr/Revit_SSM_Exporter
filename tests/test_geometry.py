"""
Unit tests for VOP interwoven geometry classification and proxy generation.

Tests UV classification (TINY/LINEAR/AREAL) and proxy creation logic.
"""

import unittest
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from vop_interwoven.config import Config
from vop_interwoven.core.geometry import Mode, classify_by_uv, make_uv_aabb, UV_AABB
from vop_interwoven.core.math_utils import CellRect


class TestUVClassification(unittest.TestCase):
    """Test UV-based element classification."""

    def setUp(self):
        """Create default config for tests."""
        self.cfg = Config(tiny_max=2, thin_max=2)

    def test_tiny_classification(self):
        """Test TINY classification (both dimensions <= tiny_max)."""
        # 1x1 cell element
        self.assertEqual(classify_by_uv(1, 1, self.cfg), Mode.TINY)

        # 2x2 cell element (at threshold)
        self.assertEqual(classify_by_uv(2, 2, self.cfg), Mode.TINY)

        # 2x1 cell element
        self.assertEqual(classify_by_uv(2, 1, self.cfg), Mode.TINY)

        # 1x2 cell element
        self.assertEqual(classify_by_uv(1, 2, self.cfg), Mode.TINY)

    def test_linear_classification(self):
        """Test LINEAR classification (one dimension thin, one long)."""
        # 1x10 cell element (vertical linear)
        self.assertEqual(classify_by_uv(1, 10, self.cfg), Mode.LINEAR)

        # 10x1 cell element (horizontal linear)
        self.assertEqual(classify_by_uv(10, 1, self.cfg), Mode.LINEAR)

        # 2x50 cell element (thick linear)
        self.assertEqual(classify_by_uv(2, 50, self.cfg), Mode.LINEAR)

        # 50x2 cell element
        self.assertEqual(classify_by_uv(50, 2, self.cfg), Mode.LINEAR)

    def test_areal_classification(self):
        """Test AREAL classification (both dimensions large)."""
        # 10x10 cell element
        self.assertEqual(classify_by_uv(10, 10, self.cfg), Mode.AREAL)

        # 5x5 cell element
        self.assertEqual(classify_by_uv(5, 5, self.cfg), Mode.AREAL)

        # 3x3 cell element (just above tiny threshold)
        self.assertEqual(classify_by_uv(3, 3, self.cfg), Mode.AREAL)

    def test_edge_cases(self):
        """Test classification at threshold boundaries."""
        # Exactly at tiny_max
        self.assertEqual(classify_by_uv(2, 2, self.cfg), Mode.TINY)

        # Just above tiny_max
        self.assertEqual(classify_by_uv(3, 3, self.cfg), Mode.AREAL)

        # At linear threshold (2x3)
        self.assertEqual(classify_by_uv(2, 3, self.cfg), Mode.LINEAR)

        # Just above linear threshold
        self.assertEqual(classify_by_uv(3, 4, self.cfg), Mode.AREAL)

    def test_custom_thresholds(self):
        """Test classification with custom thresholds."""
        # More permissive config
        cfg_relaxed = Config(tiny_max=5, thin_max=5)

        # Would be AREAL with default, TINY with relaxed
        self.assertEqual(classify_by_uv(4, 4, cfg_relaxed), Mode.TINY)

        # Would be AREAL with default, LINEAR with relaxed
        self.assertEqual(classify_by_uv(4, 20, cfg_relaxed), Mode.LINEAR)

    def test_zero_and_negative(self):
        """Test classification with edge case dimensions."""
        # Zero dimensions should still classify (degenerate elements)
        self.assertEqual(classify_by_uv(0, 0, self.cfg), Mode.TINY)
        self.assertEqual(classify_by_uv(0, 10, self.cfg), Mode.LINEAR)


class TestProxyGeneration(unittest.TestCase):
    """Test proxy generation (UV_AABB creation)."""

    def test_make_uv_aabb(self):
        """Test UV_AABB proxy creation from CellRect."""
        rect = CellRect(0, 0, 4, 4)  # 5x5 cell rectangle
        proxy = make_uv_aabb(rect)

        self.assertIsInstance(proxy, UV_AABB)
        self.assertEqual(proxy.u_min, 0.0)
        self.assertEqual(proxy.v_min, 0.0)
        self.assertEqual(proxy.u_max, 5.0)  # +1 for exclusive upper bound
        self.assertEqual(proxy.v_max, 5.0)

    def test_uv_aabb_dimensions(self):
        """Test UV_AABB width/height calculations."""
        rect = CellRect(10, 20, 15, 25)  # 6x6 cells
        proxy = make_uv_aabb(rect)

        self.assertEqual(proxy.width(), 6.0)
        self.assertEqual(proxy.height(), 6.0)

    def test_uv_aabb_center(self):
        """Test UV_AABB center calculation."""
        rect = CellRect(0, 0, 4, 4)
        proxy = make_uv_aabb(rect)

        center = proxy.center()
        self.assertEqual(center, (2.5, 2.5))  # Center of [0, 5) x [0, 5)

    def test_uv_aabb_edges(self):
        """Test UV_AABB edge generation for stamping."""
        rect = CellRect(0, 0, 1, 1)  # 2x2 cell
        proxy = make_uv_aabb(rect)

        edges = proxy.edges()
        self.assertEqual(len(edges), 4)  # 4 edges (square)

        # Check edges form a closed loop
        expected_corners = [
            (0.0, 0.0),
            (2.0, 0.0),
            (2.0, 2.0),
            (0.0, 2.0),
        ]

        # Verify each edge connects consecutive corners
        for i, edge in enumerate(edges):
            p0, p1 = edge
            self.assertEqual(p0, expected_corners[i])
            self.assertEqual(p1, expected_corners[(i + 1) % 4])

    def test_tiny_element_workflow(self):
        """Test complete workflow: classify as TINY -> generate UV_AABB."""
        cfg = Config(tiny_max=2, thin_max=2)

        # 2x2 element
        U, V = 2, 2
        mode = classify_by_uv(U, V, cfg)
        self.assertEqual(mode, Mode.TINY)

        # Generate proxy
        rect = CellRect(5, 5, 6, 6)  # 2x2 cells at (5,5)
        proxy = make_uv_aabb(rect)

        self.assertEqual(proxy.width(), 2.0)
        self.assertEqual(proxy.height(), 2.0)
        self.assertEqual(proxy.center(), (6.0, 6.0))


class TestClassificationWorkflows(unittest.TestCase):
    """Test complete classification workflows (realistic scenarios)."""

    def test_door_classification(self):
        """Test typical door classification (TINY)."""
        cfg = Config(tiny_max=2, thin_max=2)

        # Door: 3ft wide x 7ft tall, cell size = 1.5ft
        # -> 2 cells wide x 5 cells tall
        U, V = 2, 5

        mode = classify_by_uv(U, V, cfg)
        # min(2,5)=2 <= thin_max AND max(2,5)=5 > thin_max
        self.assertEqual(mode, Mode.LINEAR)

    def test_window_classification(self):
        """Test typical window classification (TINY or LINEAR)."""
        cfg = Config(tiny_max=2, thin_max=2)

        # Small window: 2ft x 2ft, cell size = 1ft
        # -> 2x2 cells
        mode = classify_by_uv(2, 2, cfg)
        self.assertEqual(mode, Mode.TINY)

        # Large window: 6ft x 2ft, cell size = 1ft
        # -> 6x2 cells
        mode = classify_by_uv(6, 2, cfg)
        self.assertEqual(mode, Mode.LINEAR)

    def test_wall_classification(self):
        """Test typical wall classification (LINEAR)."""
        cfg = Config(tiny_max=2, thin_max=2)

        # Wall: 20ft long x 0.5ft thick, cell size = 1ft
        # -> 20x1 cells
        U, V = 20, 1

        mode = classify_by_uv(U, V, cfg)
        self.assertEqual(mode, Mode.LINEAR)

    def test_floor_classification(self):
        """Test typical floor classification (AREAL)."""
        cfg = Config(tiny_max=2, thin_max=2)

        # Floor: 30ft x 40ft, cell size = 2ft
        # -> 15x20 cells
        U, V = 15, 20

        mode = classify_by_uv(U, V, cfg)
        self.assertEqual(mode, Mode.AREAL)


def run_tests():
    """Run all tests and print results."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Add all test classes
    suite.addTests(loader.loadTestsFromTestCase(TestUVClassification))
    suite.addTests(loader.loadTestsFromTestCase(TestProxyGeneration))
    suite.addTests(loader.loadTestsFromTestCase(TestClassificationWorkflows))

    # Run tests
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    return result


if __name__ == "__main__":
    result = run_tests()
    sys.exit(0 if result.wasSuccessful() else 1)
