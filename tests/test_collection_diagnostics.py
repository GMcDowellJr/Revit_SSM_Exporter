# -*- coding: utf-8 -*-
"""
Integration tests for collection.py diagnostic tracking.

Tests that diagnostic tracking integrates correctly into geometry extraction
functions without changing behavior or crashing on failures.
"""

import unittest
from vop_interwoven.diagnostics.strategy_tracker import StrategyDiagnostics


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


class MockGeometry(object):
    """Mock Geometry collection."""
    def __init__(self, items=None):
        self.items = items or []

    def __iter__(self):
        return iter(self.items)

    def __bool__(self):
        return True  # Geometry objects are truthy even if empty


class MockElement(object):
    """Mock Revit Element."""
    def __init__(self, elem_id, category_name="Walls", has_geometry=True):
        self.Id = MockId(elem_id)
        self.Category = MockCategory(category_name)
        self._has_geometry = has_geometry

    def get_Geometry(self, opts):
        """Mock get_Geometry."""
        if self._has_geometry:
            # Return empty geometry collection (no solids)
            return MockGeometry([])
        return None


class MockViewBasis(object):
    """Mock ViewBasis for coordinate transformation."""
    def __init__(self):
        self.origin = (0.0, 0.0, 0.0)
        self.right = (1.0, 0.0, 0.0)
        self.up = (0.0, 1.0, 0.0)
        self.forward = (0.0, 0.0, 1.0)
        self.scale = 1.0

    def transform_to_view_uvw(self, point_model):
        """Transform model-space point to view-local UVW coordinates."""
        dx = point_model[0] - self.origin[0]
        dy = point_model[1] - self.origin[1]
        dz = point_model[2] - self.origin[2]

        u = dx * self.right[0] + dy * self.right[1] + dz * self.right[2]
        v = dx * self.up[0] + dy * self.up[1] + dz * self.up[2]
        w = dx * self.forward[0] + dy * self.forward[1] + dz * self.forward[2]

        return (u, v, w)


class MockRaster(object):
    """Mock ViewRaster."""
    def __init__(self):
        self.bounds = type('obj', (object,), {'xmin': 0.0, 'ymin': 0.0, 'xmax': 100.0, 'ymax': 100.0})()
        self.cell_size = 1.0
        self.W = 100
        self.H = 100
        self.view_basis = MockViewBasis()


class TestCollectionDiagnostics(unittest.TestCase):
    """Test diagnostic integration in collection.py functions."""

    def test_get_element_category_name_success(self):
        """Test _get_element_category_name extracts category correctly."""
        from vop_interwoven.revit.collection import _get_element_category_name

        elem = MockElement(1001, "Walls")
        category = _get_element_category_name(elem)
        self.assertEqual(category, "Walls")

    def test_get_element_category_name_no_category(self):
        """Test _get_element_category_name handles missing category."""
        from vop_interwoven.revit.collection import _get_element_category_name

        elem = MockElement(1001)
        elem.Category = None
        category = _get_element_category_name(elem)
        self.assertEqual(category, "Unknown")

    def test_get_element_category_name_exception(self):
        """Test _get_element_category_name handles exceptions."""
        from vop_interwoven.revit.collection import _get_element_category_name

        elem = type('obj', (object,), {})()  # Object with no attributes
        category = _get_element_category_name(elem)
        self.assertEqual(category, "Unknown")

    def test_extract_geometry_footprint_uv_without_diagnostics(self):
        """Test _extract_geometry_footprint_uv works with diag=None."""
        from vop_interwoven.revit.collection import _extract_geometry_footprint_uv

        elem = MockElement(1001, "Walls")
        vb = MockViewBasis()

        # Should work without crashing (returns None due to no geometry)
        result = _extract_geometry_footprint_uv(elem, vb, diag=None, strategy_diag=None)
        self.assertIsNone(result)

    def test_extract_geometry_footprint_uv_with_diagnostics(self):
        """Test _extract_geometry_footprint_uv tracks diagnostics."""
        from vop_interwoven.revit.collection import _extract_geometry_footprint_uv

        elem = MockElement(1001, "Walls", has_geometry=False)
        vb = MockViewBasis()
        strategy_diag = StrategyDiagnostics()

        # Should track geometry extraction attempt and failure
        result = _extract_geometry_footprint_uv(elem, vb, diag=None, strategy_diag=strategy_diag)
        self.assertIsNone(result)

        # Verify tracking occurred
        # NOTE: In unit test environment without Revit API, this will track 'exception'
        # due to import failure. In actual Revit environment, it would track 'no_geometry'.
        total_tracked = sum(strategy_diag.extraction_outcome_counts.values())
        self.assertGreater(total_tracked, 0, "Should have tracked at least one extraction outcome")

    def test_extract_geometry_footprint_uv_diagnostic_failure_doesnt_crash(self):
        """Test _extract_geometry_footprint_uv doesn't crash if diagnostic tracking fails."""
        from vop_interwoven.revit.collection import _extract_geometry_footprint_uv

        # Create a broken diagnostics object that raises on every method call
        class BrokenDiagnostics(object):
            def record_geometry_extraction(self, *args, **kwargs):
                raise RuntimeError("Diagnostic tracking broken!")

        elem = MockElement(1001, "Walls", has_geometry=False)
        vb = MockViewBasis()
        broken_diag = BrokenDiagnostics()

        # Should not crash even though diagnostics fail
        result = _extract_geometry_footprint_uv(elem, vb, diag=None, strategy_diag=broken_diag)
        self.assertIsNone(result)

    def test_get_element_obb_loops_without_diagnostics(self):
        """Test get_element_obb_loops works with diag=None."""
        from vop_interwoven.revit.collection import get_element_obb_loops

        elem = MockElement(1001, "Walls")
        vb = MockViewBasis()
        raster = MockRaster()
        bbox = MockBBox(MockXYZ(0, 0, 0), MockXYZ(10, 10, 10))

        # Should work without crashing
        result = get_element_obb_loops(elem, vb, raster, bbox=bbox, diag=None, strategy_diag=None)
        self.assertIsNotNone(result)
        self.assertIsInstance(result, list)

    def test_get_element_obb_loops_with_diagnostics(self):
        """Test get_element_obb_loops tracks strategy diagnostics."""
        from vop_interwoven.revit.collection import get_element_obb_loops

        elem = MockElement(1001, "Walls")
        vb = MockViewBasis()
        raster = MockRaster()
        bbox = MockBBox(MockXYZ(0, 0, 0), MockXYZ(10, 10, 10))
        strategy_diag = StrategyDiagnostics()

        # Should track strategy usage
        result = get_element_obb_loops(elem, vb, raster, bbox=bbox, diag=None, strategy_diag=strategy_diag)
        self.assertIsNotNone(result)

        # Verify strategy tracking occurred (should use bbox_obb_used or aabb_used)
        total_strategies = sum(strategy_diag.areal_strategy_counts.values())
        self.assertGreater(total_strategies, 0)

    def test_get_element_obb_loops_diagnostic_failure_doesnt_crash(self):
        """Test get_element_obb_loops doesn't crash if diagnostic tracking fails."""
        from vop_interwoven.revit.collection import get_element_obb_loops

        # Create a broken diagnostics object
        class BrokenDiagnostics(object):
            def record_areal_strategy(self, *args, **kwargs):
                raise RuntimeError("Diagnostic tracking broken!")
            def record_geometry_extraction(self, *args, **kwargs):
                raise RuntimeError("Diagnostic tracking broken!")

        elem = MockElement(1001, "Walls")
        vb = MockViewBasis()
        raster = MockRaster()
        bbox = MockBBox(MockXYZ(0, 0, 0), MockXYZ(10, 10, 10))
        broken_diag = BrokenDiagnostics()

        # Should not crash even though diagnostics fail
        result = get_element_obb_loops(elem, vb, raster, bbox=bbox, diag=None, strategy_diag=broken_diag)
        self.assertIsNotNone(result)

    def test_tracking_preserves_behavior(self):
        """Test that enabling diagnostics doesn't change function behavior."""
        from vop_interwoven.revit.collection import get_element_obb_loops

        elem = MockElement(1001, "Walls")
        vb = MockViewBasis()
        raster = MockRaster()
        bbox = MockBBox(MockXYZ(0, 0, 0), MockXYZ(10, 10, 10))

        # Call without diagnostics
        result_without = get_element_obb_loops(elem, vb, raster, bbox=bbox, diag=None, strategy_diag=None)

        # Call with diagnostics
        strategy_diag = StrategyDiagnostics()
        result_with = get_element_obb_loops(elem, vb, raster, bbox=bbox, diag=None, strategy_diag=strategy_diag)

        # Results should be equivalent (same structure and strategy)
        self.assertEqual(len(result_without), len(result_with))
        self.assertEqual(result_without[0]['strategy'], result_with[0]['strategy'])
        self.assertEqual(len(result_without[0]['points']), len(result_with[0]['points']))

    def test_multiple_elements_tracking(self):
        """Test tracking multiple elements with different outcomes."""
        from vop_interwoven.revit.collection import get_element_obb_loops

        vb = MockViewBasis()
        raster = MockRaster()
        strategy_diag = StrategyDiagnostics()

        # Process multiple elements
        for i in range(10):
            elem = MockElement(1000 + i, "Walls")
            bbox = MockBBox(MockXYZ(0, 0, 0), MockXYZ(10 + i, 10 + i, 10))
            get_element_obb_loops(elem, vb, raster, bbox=bbox, strategy_diag=strategy_diag)

        # Verify multiple elements tracked
        total_strategies = sum(strategy_diag.areal_strategy_counts.values())
        self.assertEqual(total_strategies, 10)

    def test_summary_with_tracked_data(self):
        """Test that tracked data produces valid summary statistics."""
        from vop_interwoven.revit.collection import get_element_obb_loops

        vb = MockViewBasis()
        raster = MockRaster()
        strategy_diag = StrategyDiagnostics()

        # Process elements
        for i in range(5):
            elem = MockElement(1000 + i, "Walls")
            bbox = MockBBox(MockXYZ(0, 0, 0), MockXYZ(10, 10, 10))
            get_element_obb_loops(elem, vb, raster, bbox=bbox, strategy_diag=strategy_diag)

        # Get summary
        summary = strategy_diag.get_summary()

        # Verify summary structure
        self.assertIn('areal_strategy_counts', summary)
        self.assertIn('extraction_outcome_counts', summary)

        # Should have tracked some strategies
        self.assertGreater(len(summary['areal_strategy_counts']), 0)


if __name__ == '__main__':
    unittest.main()
