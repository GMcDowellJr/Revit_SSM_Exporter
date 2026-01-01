"""
Unit tests for VOP interwoven raster data structures.

Tests ViewRaster, TileMap, and related operations.
"""

import unittest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from vop_interwoven.core.raster import ViewRaster, TileMap
from vop_interwoven.core.math_utils import Bounds2D
from vop_interwoven.config import Config


class TestTileMap(unittest.TestCase):
    """Test TileMap spatial acceleration structure."""

    def setUp(self):
        """Create test tile map."""
        self.tile_map = TileMap(tile_size=16, width=64, height=64)

    def test_initialization(self):
        """Test tile map initialization."""
        self.assertEqual(self.tile_map.tile_size, 16)
        self.assertEqual(self.tile_map.tiles_x, 4)  # 64 / 16
        self.assertEqual(self.tile_map.tiles_y, 4)
        self.assertEqual(len(self.tile_map.filled_count), 16)  # 4x4 tiles
        self.assertEqual(len(self.tile_map.z_min_tile), 16)

    def test_get_tile_index(self):
        """Test tile index calculation."""
        # Cell (0, 0) -> tile 0
        self.assertEqual(self.tile_map.get_tile_index(0, 0), 0)

        # Cell (16, 0) -> tile 1 (next tile in X)
        self.assertEqual(self.tile_map.get_tile_index(16, 0), 1)

        # Cell (0, 16) -> tile 4 (next tile in Y)
        self.assertEqual(self.tile_map.get_tile_index(0, 16), 4)

        # Cell (16, 16) -> tile 5
        self.assertEqual(self.tile_map.get_tile_index(16, 16), 5)

    def test_get_tiles_for_rect(self):
        """Test tile collection for rectangle."""
        # Rectangle spanning single tile
        tiles = self.tile_map.get_tiles_for_rect(0, 0, 15, 15)
        self.assertEqual(tiles, [0])

        # Rectangle spanning 2x2 tiles
        tiles = self.tile_map.get_tiles_for_rect(0, 0, 31, 31)
        self.assertEqual(set(tiles), {0, 1, 4, 5})

        # Rectangle spanning all tiles
        tiles = self.tile_map.get_tiles_for_rect(0, 0, 63, 63)
        self.assertEqual(len(tiles), 16)

    def test_is_tile_full(self):
        """Test tile fullness detection."""
        # Initially, all tiles are empty
        self.assertFalse(self.tile_map.is_tile_full(0))

        # Fill tile partially
        self.tile_map.filled_count[0] = 100
        self.assertFalse(self.tile_map.is_tile_full(0))

        # Fill tile completely (16x16 = 256 cells)
        self.tile_map.filled_count[0] = 256
        self.assertTrue(self.tile_map.is_tile_full(0))

    def test_update_filled_count(self):
        """Test filled count updates."""
        # Update cell (5, 5) in tile 0
        self.tile_map.update_filled_count(5, 5, increment=1)
        self.assertEqual(self.tile_map.filled_count[0], 1)

        # Update again
        self.tile_map.update_filled_count(5, 5, increment=1)
        self.assertEqual(self.tile_map.filled_count[0], 2)

    def test_update_z_min(self):
        """Test minimum depth updates."""
        # Initial depth is +inf
        self.assertEqual(self.tile_map.z_min_tile[0], float("inf"))

        # Update with depth 10.0
        self.tile_map.update_z_min(5, 5, depth=10.0)
        self.assertEqual(self.tile_map.z_min_tile[0], 10.0)

        # Update with smaller depth
        self.tile_map.update_z_min(5, 5, depth=5.0)
        self.assertEqual(self.tile_map.z_min_tile[0], 5.0)

        # Update with larger depth (should not change)
        self.tile_map.update_z_min(5, 5, depth=20.0)
        self.assertEqual(self.tile_map.z_min_tile[0], 5.0)


class TestViewRaster(unittest.TestCase):
    """Test ViewRaster data structure."""

    def setUp(self):
        """Create test view raster."""
        bounds = Bounds2D(0.0, 0.0, 64.0, 64.0)
        self.raster = ViewRaster(
            width=64, height=64, cell_size=1.0, bounds=bounds, tile_size=16
        )

    def test_initialization(self):
        """Test view raster initialization."""
        self.assertEqual(self.raster.W, 64)
        self.assertEqual(self.raster.H, 64)
        self.assertEqual(self.raster.cell_size_ft, 1.0)
        self.assertEqual(len(self.raster.model_mask), 64 * 64)
        self.assertEqual(len(self.raster.z_min), 64 * 64)
        self.assertIsNotNone(self.raster.tile)

    def test_get_cell_index(self):
        """Test cell index calculation."""
        # Cell (0, 0) -> index 0
        self.assertEqual(self.raster.get_cell_index(0, 0), 0)

        # Cell (1, 0) -> index 1
        self.assertEqual(self.raster.get_cell_index(1, 0), 1)

        # Cell (0, 1) -> index 64 (next row)
        self.assertEqual(self.raster.get_cell_index(0, 1), 64)

        # Cell (5, 3) -> index 3*64 + 5
        self.assertEqual(self.raster.get_cell_index(5, 3), 3 * 64 + 5)

        # Out of bounds -> None
        self.assertIsNone(self.raster.get_cell_index(100, 100))

    def test_set_cell_filled(self):
        """Test cell filling with depth."""
        # Initially empty
        idx = self.raster.get_cell_index(10, 10)
        self.assertFalse(self.raster.model_mask[idx])
        self.assertEqual(self.raster.z_min[idx], float("inf"))

        # Fill cell with depth
        result = self.raster.set_cell_filled(10, 10, depth=5.0)
        self.assertTrue(result)
        self.assertTrue(self.raster.model_mask[idx])
        self.assertEqual(self.raster.z_min[idx], 5.0)

        # Update with nearer depth
        self.raster.set_cell_filled(10, 10, depth=3.0)
        self.assertEqual(self.raster.z_min[idx], 3.0)

        # Update with farther depth (should not change z_min)
        self.raster.set_cell_filled(10, 10, depth=10.0)
        self.assertEqual(self.raster.z_min[idx], 3.0)

    def test_element_metadata(self):
        """Test element metadata tracking."""
        # Create metadata for element 123
        idx1 = self.raster.get_or_create_element_meta_index(123, "Walls", "HOST")
        self.assertEqual(idx1, 0)
        self.assertEqual(len(self.raster.element_meta), 1)
        self.assertEqual(self.raster.element_meta[0]["elem_id"], 123)

        # Get same element again (should return same index)
        idx2 = self.raster.get_or_create_element_meta_index(123, "Walls", "HOST")
        self.assertEqual(idx2, 0)
        self.assertEqual(len(self.raster.element_meta), 1)

        # Create metadata for different element
        idx3 = self.raster.get_or_create_element_meta_index(456, "Doors", "RVT_LINK")
        self.assertEqual(idx3, 1)
        self.assertEqual(len(self.raster.element_meta), 2)

    def test_annotation_metadata(self):
        """Test annotation metadata tracking."""
        # Create annotation metadata
        idx1 = self.raster.get_or_create_anno_meta_index(789, "TEXT")
        self.assertEqual(idx1, 0)
        self.assertEqual(len(self.raster.anno_meta), 1)

        # Get same annotation again
        idx2 = self.raster.get_or_create_anno_meta_index(789, "TEXT")
        self.assertEqual(idx2, 0)
        self.assertEqual(len(self.raster.anno_meta), 1)

    def test_finalize_anno_over_model(self):
        """Test anno_over_model derivation."""
        cfg = Config(over_model_includes_proxies=True)

        # Cell (10, 10): has annotation, no model -> False
        idx = self.raster.get_cell_index(10, 10)
        self.raster.anno_key[idx] = 0  # Has annotation

        # Cell (20, 20): has annotation + model -> True
        idx2 = self.raster.get_cell_index(20, 20)
        self.raster.anno_key[idx2] = 1
        self.raster.model_mask[idx2] = True

        # Cell (30, 30): has annotation + proxy -> True (with config flag)
        idx3 = self.raster.get_cell_index(30, 30)
        self.raster.anno_key[idx3] = 2
        self.raster.model_proxy_mask[idx3] = True

        # Finalize
        self.raster.finalize_anno_over_model(cfg)

        self.assertFalse(self.raster.anno_over_model[idx])  # Annotation only
        self.assertTrue(self.raster.anno_over_model[idx2])  # Anno + model
        self.assertTrue(self.raster.anno_over_model[idx3])  # Anno + proxy

    def test_finalize_anno_over_model_no_proxies(self):
        """Test anno_over_model with proxies excluded."""
        cfg = Config(over_model_includes_proxies=False)

        # Cell with annotation + proxy mask (but config excludes proxies)
        idx = self.raster.get_cell_index(30, 30)
        self.raster.anno_key[idx] = 0
        self.raster.model_proxy_mask[idx] = True

        self.raster.finalize_anno_over_model(cfg)

        # Should be False because model_mask is False and proxies don't count
        self.assertFalse(self.raster.anno_over_model[idx])

    def test_to_dict(self):
        """Test raster export to dictionary."""
        # Fill some cells
        self.raster.set_cell_filled(5, 5, depth=10.0)
        self.raster.get_or_create_element_meta_index(123, "Walls", "HOST")

        # Export
        data = self.raster.to_dict()

        # Check structure
        self.assertIn("width", data)
        self.assertIn("height", data)
        self.assertIn("cell_size_ft", data)
        self.assertIn("model_mask", data)
        self.assertIn("element_meta", data)

        self.assertEqual(data["width"], 64)
        self.assertEqual(data["height"], 64)
        self.assertEqual(len(data["element_meta"]), 1)


def run_tests():
    """Run all raster tests."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    suite.addTests(loader.loadTestsFromTestCase(TestTileMap))
    suite.addTests(loader.loadTestsFromTestCase(TestViewRaster))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    return result


if __name__ == "__main__":
    result = run_tests()
    sys.exit(0 if result.wasSuccessful() else 1)
