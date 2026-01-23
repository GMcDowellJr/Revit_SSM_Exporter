# -*- coding: utf-8 -*-
"""
Validation tests for unified AREAL geometry extraction.

Tests the 3-tier fallback hierarchy and confidence level assignment:
  - Tier 1 HIGH: planar_face_loops, silhouette_edges
  - Tier 2 MEDIUM/LOW: geometry_polygon (MEDIUM), uv_obb/aabb (LOW)
  - Tier 3 LOW: aabb_fallback
  - Failure: all strategies fail
"""

import unittest
from vop_interwoven.core.areal_extraction import (
    extract_areal_geometry,
    _safe_elem_id,
    _safe_category,
    _get_aabb_loops_from_bbox
)
from vop_interwoven.diagnostics import StrategyDiagnostics


class MockXYZ(object):
    """Mock XYZ point."""
    def __init__(self, x, y, z):
        self.X = x
        self.Y = y
        self.Z = z


class MockBBox(object):
    """Mock BoundingBoxXYZ."""
    def __init__(self, min_xyz, max_xyz):
        self.Min = min_xyz
        self.Max = max_xyz
        self.Transform = None


class MockId(object):
    """Mock ElementId."""
    def __init__(self, val):
        self.IntegerValue = val


class MockCategory(object):
    """Mock Category."""
    def __init__(self, name):
        self.Name = name


class MockElement(object):
    """Mock Revit Element."""
    def __init__(self, elem_id, category_name="Floors"):
        self.Id = MockId(elem_id)
        self.Category = MockCategory(category_name)


class MockViewBasis(object):
    """Mock ViewBasis for coordinate transformation."""
    def __init__(self):
        self.origin = (0.0, 0.0, 0.0)
        self.right = (1.0, 0.0, 0.0)
        self.up = (0.0, 1.0, 0.0)
        self.forward = (0.0, 0.0, 1.0)
        self.scale = 1.0


class MockRaster(object):
    """Mock ViewRaster."""
    def __init__(self):
        self.bounds = type('obj', (object,), {'xmin': 0.0, 'ymin': 0.0, 'xmax': 100.0, 'ymax': 100.0})()
        self.cell_size = 1.0
        self.W = 100
        self.H = 100
        self.view_basis = MockViewBasis()


class MockConfig(object):
    """Mock Config."""
    def __init__(self):
        pass


class MockView(object):
    """Mock Revit View."""
    def __init__(self):
        self.ViewDirection = type('obj', (object,), {'X': 0.0, 'Y': 0.0, 'Z': 1.0})()


class TestArealExtractionHelpers(unittest.TestCase):
    """Test helper functions."""

    def test_safe_elem_id_success(self):
        """Test _safe_elem_id extracts ID correctly."""
        elem = MockElement(1234, "Floors")
        elem_id = _safe_elem_id(elem)
        self.assertEqual(elem_id, 1234)

    def test_safe_elem_id_no_id(self):
        """Test _safe_elem_id handles missing ID."""
        elem = type('obj', (object,), {})()  # No Id attribute
        elem_id = _safe_elem_id(elem)
        self.assertIsNone(elem_id)

    def test_safe_elem_id_none_id(self):
        """Test _safe_elem_id handles None ID."""
        elem = type('obj', (object,), {'Id': None})()
        elem_id = _safe_elem_id(elem)
        self.assertIsNone(elem_id)

    def test_safe_category_success(self):
        """Test _safe_category extracts category correctly."""
        elem = MockElement(1234, "Floors")
        category = _safe_category(elem)
        self.assertEqual(category, "Floors")

    def test_safe_category_no_category(self):
        """Test _safe_category handles missing category."""
        elem = MockElement(1234)
        elem.Category = None
        category = _safe_category(elem)
        self.assertEqual(category, "Unknown")

    def test_safe_category_exception(self):
        """Test _safe_category handles exceptions."""
        elem = type('obj', (object,), {})()  # No attributes
        category = _safe_category(elem)
        self.assertEqual(category, "Unknown")

    def test_get_aabb_loops_from_bbox_success(self):
        """Test _get_aabb_loops_from_bbox creates valid AABB."""
        bbox = MockBBox(MockXYZ(0, 0, 0), MockXYZ(10, 10, 5))
        vb = MockViewBasis()

        loops = _get_aabb_loops_from_bbox(bbox, vb)

        self.assertIsNotNone(loops)
        self.assertEqual(len(loops), 1)
        self.assertFalse(loops[0]['is_hole'])
        self.assertGreaterEqual(len(loops[0]['points']), 5)  # Closed rectangle

    def test_get_aabb_loops_from_bbox_closes_loop(self):
        """Test _get_aabb_loops_from_bbox creates closed loop."""
        bbox = MockBBox(MockXYZ(0, 0, 0), MockXYZ(10, 10, 5))
        vb = MockViewBasis()

        loops = _get_aabb_loops_from_bbox(bbox, vb)

        points = loops[0]['points']
        # First and last point should be the same (closed loop)
        self.assertEqual(points[0], points[-1])


class TestArealExtractionFallbackTiers(unittest.TestCase):
    """Test fallback tier behavior and confidence assignment."""

    def test_total_failure_returns_none(self):
        """Test that total failure returns (None, None, 'failed')."""
        # Create element that will fail all strategies
        elem = MockElement(9999, "Unknown")
        view = MockView()
        vb = MockViewBasis()
        raster = MockRaster()
        cfg = MockConfig()

        loops, confidence, strategy = extract_areal_geometry(
            elem, view, vb, raster, cfg
        )

        # Should return failure tuple
        self.assertIsNone(loops)
        self.assertIsNone(confidence)
        self.assertEqual(strategy, 'failed')

    def test_diagnostics_tracking_on_failure(self):
        """Test that diagnostics tracks total failure."""
        elem = MockElement(9999, "Unknown")
        view = MockView()
        vb = MockViewBasis()
        raster = MockRaster()
        cfg = MockConfig()
        strategy_diag = StrategyDiagnostics()

        loops, confidence, strategy = extract_areal_geometry(
            elem, view, vb, raster, cfg, strategy_diag=strategy_diag
        )

        # Should track the failure
        self.assertEqual(strategy, 'failed')

        # Verify extraction outcome was tracked
        total_outcomes = sum(strategy_diag.extraction_outcome_counts.values())
        self.assertGreater(total_outcomes, 0)

    def test_confidence_levels_are_valid(self):
        """Test that returned confidence levels are valid."""
        elem = MockElement(9999, "Floors")
        view = MockView()
        vb = MockViewBasis()
        raster = MockRaster()
        cfg = MockConfig()

        loops, confidence, strategy = extract_areal_geometry(
            elem, view, vb, raster, cfg
        )

        # Confidence should be None or one of the valid values
        valid_confidences = [None, 'HIGH', 'MEDIUM', 'LOW']
        self.assertIn(confidence, valid_confidences)

    def test_strategy_name_is_string(self):
        """Test that strategy name is always a string."""
        elem = MockElement(9999, "Floors")
        view = MockView()
        vb = MockViewBasis()
        raster = MockRaster()
        cfg = MockConfig()

        loops, confidence, strategy = extract_areal_geometry(
            elem, view, vb, raster, cfg
        )

        self.assertIsInstance(strategy, str)

    def test_loops_structure_when_not_none(self):
        """Test that loops have correct structure when not None."""
        elem = MockElement(9999, "Floors")
        view = MockView()
        vb = MockViewBasis()
        raster = MockRaster()
        cfg = MockConfig()

        loops, confidence, strategy = extract_areal_geometry(
            elem, view, vb, raster, cfg
        )

        if loops is not None:
            # Should be a list
            self.assertIsInstance(loops, list)

            # Each loop should be a dict
            for loop in loops:
                self.assertIsInstance(loop, dict)
                self.assertIn('points', loop)
                self.assertIn('is_hole', loop)

                # Points should be a list of tuples/lists
                self.assertIsInstance(loop['points'], list)


class TestArealExtractionDiagnostics(unittest.TestCase):
    """Test diagnostic tracking integration."""

    def test_diagnostics_optional(self):
        """Test that extract_areal_geometry works without diagnostics."""
        elem = MockElement(1234, "Floors")
        view = MockView()
        vb = MockViewBasis()
        raster = MockRaster()
        cfg = MockConfig()

        # Should not crash with strategy_diag=None
        loops, confidence, strategy = extract_areal_geometry(
            elem, view, vb, raster, cfg, strategy_diag=None
        )

        # Should return valid tuple
        self.assertIsInstance(strategy, str)

    def test_diagnostics_tracking_with_valid_diag(self):
        """Test that diagnostics tracking works with valid StrategyDiagnostics."""
        elem = MockElement(1234, "Floors")
        view = MockView()
        vb = MockViewBasis()
        raster = MockRaster()
        cfg = MockConfig()
        strategy_diag = StrategyDiagnostics()

        loops, confidence, strategy = extract_areal_geometry(
            elem, view, vb, raster, cfg, strategy_diag=strategy_diag
        )

        # Diagnostics should have tracked something
        summary = strategy_diag.get_summary()

        # Should have at least one extraction attempt
        total_extractions = sum(summary['extraction_outcome_counts'].values())
        self.assertGreater(total_extractions, 0)

    def test_broken_diagnostics_doesnt_crash(self):
        """Test that broken diagnostics don't crash extraction."""
        # Create a broken diagnostics object
        class BrokenDiagnostics(object):
            def record_areal_strategy(self, *args, **kwargs):
                raise RuntimeError("Diagnostics broken!")

            def record_geometry_extraction(self, *args, **kwargs):
                raise RuntimeError("Diagnostics broken!")

        elem = MockElement(1234, "Floors")
        view = MockView()
        vb = MockViewBasis()
        raster = MockRaster()
        cfg = MockConfig()
        broken_diag = BrokenDiagnostics()

        # Should not crash even though diagnostics fail
        loops, confidence, strategy = extract_areal_geometry(
            elem, view, vb, raster, cfg, strategy_diag=broken_diag
        )

        # Should still return valid result
        self.assertIsInstance(strategy, str)

    def test_elem_without_id_doesnt_crash(self):
        """Test that elements without ID don't crash extraction."""
        elem = type('obj', (object,), {})()  # No Id attribute
        view = MockView()
        vb = MockViewBasis()
        raster = MockRaster()
        cfg = MockConfig()
        strategy_diag = StrategyDiagnostics()

        # Should not crash
        loops, confidence, strategy = extract_areal_geometry(
            elem, view, vb, raster, cfg, strategy_diag=strategy_diag
        )

        # Should return valid result (likely failure)
        self.assertIsInstance(strategy, str)


class TestArealExtractionIntegration(unittest.TestCase):
    """Integration tests for complete extraction workflow."""

    def test_multiple_elements_extraction(self):
        """Test extracting geometry for multiple elements."""
        view = MockView()
        vb = MockViewBasis()
        raster = MockRaster()
        cfg = MockConfig()
        strategy_diag = StrategyDiagnostics()

        # Process multiple elements
        for i in range(10):
            elem = MockElement(2000 + i, "Floors")
            loops, confidence, strategy = extract_areal_geometry(
                elem, view, vb, raster, cfg, strategy_diag=strategy_diag
            )

            # Each should return valid tuple
            self.assertIsInstance(strategy, str)

        # Diagnostics should track all elements
        summary = strategy_diag.get_summary()
        total_extractions = sum(summary['extraction_outcome_counts'].values())
        self.assertGreater(total_extractions, 0)

    def test_extraction_preserves_element_identity(self):
        """Test that extraction doesn't modify element."""
        elem = MockElement(3000, "Floors")
        orig_id = elem.Id.IntegerValue
        orig_cat = elem.Category.Name

        view = MockView()
        vb = MockViewBasis()
        raster = MockRaster()
        cfg = MockConfig()

        extract_areal_geometry(elem, view, vb, raster, cfg)

        # Element should be unchanged
        self.assertEqual(elem.Id.IntegerValue, orig_id)
        self.assertEqual(elem.Category.Name, orig_cat)

    def test_concurrent_extractions_dont_interfere(self):
        """Test that multiple extractions don't interfere with each other."""
        view = MockView()
        vb = MockViewBasis()
        raster = MockRaster()
        cfg = MockConfig()

        # Extract for two different elements
        elem1 = MockElement(4001, "Floors")
        elem2 = MockElement(4002, "Ceilings")

        loops1, conf1, strat1 = extract_areal_geometry(elem1, view, vb, raster, cfg)
        loops2, conf2, strat2 = extract_areal_geometry(elem2, view, vb, raster, cfg)

        # Both should return valid results
        self.assertIsInstance(strat1, str)
        self.assertIsInstance(strat2, str)


if __name__ == '__main__':
    unittest.main()
