# -*- coding: utf-8 -*-
"""
Integration tests for CSV export with strategy diagnostics.

Tests that strategy diagnostics integrates correctly into CSV export
and produces valid output columns and percentages.
"""

import unittest
from vop_interwoven.diagnostics import StrategyDiagnostics
from vop_interwoven.csv_export import build_vop_csv_row
from vop_interwoven.config import Config


class TestCSVExportDiagnostics(unittest.TestCase):
    """Test strategy diagnostics integration in CSV export."""

    def test_build_vop_csv_row_without_diagnostics(self):
        """Test that build_vop_csv_row works without strategy_diag (backward compat)."""
        # Create minimal test data
        view = None
        metrics = {
            "TotalCells": 1000,
            "Empty": 500,
            "ModelOnly": 300,
            "AnnoOnly": 100,
            "Overlap": 100,
            "Ext_Cells_Any": 0,
            "Ext_Cells_Only": 0,
            "Ext_Cells_DWG": 0,
            "Ext_Cells_RVT": 0,
        }
        anno_metrics = {
            "AnnoCells_TEXT": 50,
            "AnnoCells_TAG": 30,
            "AnnoCells_DIM": 20,
            "AnnoCells_DETAIL": 10,
            "AnnoCells_LINES": 5,
            "AnnoCells_REGION": 3,
            "AnnoCells_OTHER": 2,
        }
        config = Config()
        run_info = {
            "date": "2024-01-01",
            "run_id": "test123",
            "exporter_version": "VOP_v1.0",
            "elapsed_sec": 1.5,
            "from_cache": False,
            "cell_size_ft": 1.0,
            "cell_size_ft_requested": 1.0,
            "cell_size_ft_effective": 1.0,
            "resolution_mode": "canonical",
            "cap_triggered": False,
        }
        view_metadata = {
            "ViewId": "123456",
            "ViewName": "Test View",
            "ViewType": "FloorPlan",
        }

        # Call without strategy_diag
        row = build_vop_csv_row(view, metrics, anno_metrics, config, run_info, view_metadata=view_metadata)

        # Should have 38 columns (31 original + 7 strategy diagnostics)
        self.assertEqual(len(row), 38)

        # Last 7 columns should be strategy diagnostics (all zeros)
        strategy_cols = row[-7:]
        self.assertEqual(strategy_cols, [0, 0, 0, 0, 0, 0.0, 0.0])

    def test_build_vop_csv_row_with_diagnostics(self):
        """Test that build_vop_csv_row correctly extracts strategy statistics."""
        # Create strategy diagnostics with test data
        strategy_diag = StrategyDiagnostics()

        # Add 100 test elements
        for i in range(100):
            elem_id = 1000 + i
            category = "Walls" if i < 50 else "Floors"

            if i < 20:
                # TINY elements
                strategy_diag.record_element_classification(elem_id, 'TINY', category)
                strategy_diag.record_geometry_extraction(elem_id, 'success', category)
            elif i < 50:
                # LINEAR elements
                strategy_diag.record_element_classification(elem_id, 'LINEAR', category)
                strategy_diag.record_geometry_extraction(elem_id, 'success', category)
            else:
                # AREAL elements
                strategy_diag.record_element_classification(elem_id, 'AREAL', category)

                if i < 70:
                    # 20 successful planar_face
                    strategy_diag.record_areal_strategy(elem_id, 'planar_face', True, category)
                    strategy_diag.record_geometry_extraction(elem_id, 'success', category)
                elif i < 85:
                    # 15 successful silhouette
                    strategy_diag.record_areal_strategy(elem_id, 'planar_face', False, category)
                    strategy_diag.record_areal_strategy(elem_id, 'silhouette', True, category)
                    strategy_diag.record_geometry_extraction(elem_id, 'success', category)
                elif i < 90:
                    # 5 successful geometry_polygon
                    strategy_diag.record_areal_strategy(elem_id, 'geometry_polygon', True, category)
                    strategy_diag.record_geometry_extraction(elem_id, 'success', category)
                elif i < 95:
                    # 5 successful bbox_obb
                    strategy_diag.record_areal_strategy(elem_id, 'bbox_obb_used', True, category)
                    strategy_diag.record_geometry_extraction(elem_id, 'success', category)
                else:
                    # 5 fallback to aabb
                    strategy_diag.record_areal_strategy(elem_id, 'aabb_used', True, category)
                    strategy_diag.record_geometry_extraction(elem_id, 'no_geometry', category)

        # Create minimal test data
        view = None
        metrics = {"TotalCells": 1000}
        anno_metrics = {}
        config = Config()
        run_info = {
            "date": "2024-01-01",
            "run_id": "test123",
            "exporter_version": "VOP_v1.0",
            "elapsed_sec": 1.5,
            "from_cache": False,
            "cell_size_ft": 1.0,
            "cell_size_ft_requested": 1.0,
            "cell_size_ft_effective": 1.0,
            "resolution_mode": "canonical",
            "cap_triggered": False,
        }
        view_metadata = {
            "ViewId": "123456",
            "ViewName": "Test View",
            "ViewType": "FloorPlan",
        }

        # Call with strategy_diag
        row = build_vop_csv_row(view, metrics, anno_metrics, config, run_info,
                               view_metadata=view_metadata, strategy_diag=strategy_diag)

        # Should have 38 columns (31 original + 7 strategy diagnostics)
        self.assertEqual(len(row), 38)

        # Extract strategy diagnostic columns (last 7)
        strategy_cols = row[-7:]

        planar_face_count = strategy_cols[0]
        silhouette_count = strategy_cols[1]
        geom_extract_count = strategy_cols[2]
        bbox_obb_count = strategy_cols[3]
        aabb_count = strategy_cols[4]
        geom_success_rate = strategy_cols[5]
        areal_high_conf_rate = strategy_cols[6]

        # Verify strategy counts
        self.assertEqual(planar_face_count, 20)
        self.assertEqual(silhouette_count, 15)
        self.assertEqual(geom_extract_count, 5)
        self.assertEqual(bbox_obb_count, 5)
        self.assertEqual(aabb_count, 5)

        # Verify geometry success rate (95 success / 100 total = 95%)
        self.assertAlmostEqual(geom_success_rate, 95.0, places=1)

        # Verify AREAL high confidence rate (40 high conf / 50 AREAL = 80%)
        self.assertAlmostEqual(areal_high_conf_rate, 80.0, places=1)

    def test_strategy_statistics_in_valid_percentage_range(self):
        """Test that percentage columns are in valid 0-100 range."""
        strategy_diag = StrategyDiagnostics()

        # Add test elements with all successful extractions
        for i in range(50):
            strategy_diag.record_element_classification(1000 + i, 'AREAL', 'Walls')
            strategy_diag.record_areal_strategy(1000 + i, 'planar_face', True, 'Walls')
            strategy_diag.record_geometry_extraction(1000 + i, 'success', 'Walls')

        view = None
        metrics = {"TotalCells": 100}
        anno_metrics = {}
        config = Config()
        run_info = {
            "date": "2024-01-01",
            "run_id": "test",
            "exporter_version": "VOP_v1.0",
            "elapsed_sec": 1.0,
            "from_cache": False,
            "cell_size_ft": 1.0,
            "cell_size_ft_requested": 1.0,
            "cell_size_ft_effective": 1.0,
            "resolution_mode": "canonical",
            "cap_triggered": False,
        }
        view_metadata = {"ViewId": "1", "ViewName": "Test", "ViewType": "FloorPlan"}

        row = build_vop_csv_row(view, metrics, anno_metrics, config, run_info,
                               view_metadata=view_metadata, strategy_diag=strategy_diag)

        # Extract percentage columns
        geom_success_rate = row[-2]
        areal_high_conf_rate = row[-1]

        # Verify percentages are in valid range
        self.assertGreaterEqual(geom_success_rate, 0.0)
        self.assertLessEqual(geom_success_rate, 100.0)
        self.assertGreaterEqual(areal_high_conf_rate, 0.0)
        self.assertLessEqual(areal_high_conf_rate, 100.0)

        # With all successes, both should be 100%
        self.assertAlmostEqual(geom_success_rate, 100.0, places=1)
        self.assertAlmostEqual(areal_high_conf_rate, 100.0, places=1)

    def test_strategy_counts_sum_correctly(self):
        """Test that strategy counts sum to expected totals."""
        strategy_diag = StrategyDiagnostics()

        # Add exactly 10 AREAL elements with different strategies
        for i in range(10):
            elem_id = 1000 + i
            strategy_diag.record_element_classification(elem_id, 'AREAL', 'Walls')

            if i < 3:
                strategy_diag.record_areal_strategy(elem_id, 'planar_face', True, 'Walls')
            elif i < 5:
                strategy_diag.record_areal_strategy(elem_id, 'silhouette', True, 'Walls')
            elif i < 7:
                strategy_diag.record_areal_strategy(elem_id, 'geometry_polygon', True, 'Walls')
            elif i < 9:
                strategy_diag.record_areal_strategy(elem_id, 'bbox_obb_used', True, 'Walls')
            else:
                strategy_diag.record_areal_strategy(elem_id, 'aabb_used', True, 'Walls')

        view = None
        metrics = {"TotalCells": 100}
        anno_metrics = {}
        config = Config()
        run_info = {
            "date": "2024-01-01",
            "run_id": "test",
            "exporter_version": "VOP_v1.0",
            "elapsed_sec": 1.0,
            "from_cache": False,
            "cell_size_ft": 1.0,
            "cell_size_ft_requested": 1.0,
            "cell_size_ft_effective": 1.0,
            "resolution_mode": "canonical",
            "cap_triggered": False,
        }
        view_metadata = {"ViewId": "1", "ViewName": "Test", "ViewType": "FloorPlan"}

        row = build_vop_csv_row(view, metrics, anno_metrics, config, run_info,
                               view_metadata=view_metadata, strategy_diag=strategy_diag)

        # Extract strategy counts
        strategy_counts = row[-7:-2]  # First 5 of the 7 new columns

        # Verify individual counts
        self.assertEqual(strategy_counts[0], 3)  # planar_face
        self.assertEqual(strategy_counts[1], 2)  # silhouette
        self.assertEqual(strategy_counts[2], 2)  # geometry_extract
        self.assertEqual(strategy_counts[3], 2)  # bbox_obb
        self.assertEqual(strategy_counts[4], 1)  # aabb

        # Sum should be 10 (all AREAL elements)
        total = sum(strategy_counts)
        self.assertEqual(total, 10)

    def test_diagnostic_extraction_failure_doesnt_crash(self):
        """Test that CSV export doesn't crash if diagnostic extraction fails."""
        # Create a broken diagnostics object
        class BrokenDiagnostics(object):
            def get_summary(self):
                raise RuntimeError("Diagnostic broken!")

        view = None
        metrics = {"TotalCells": 100}
        anno_metrics = {}
        config = Config()
        run_info = {
            "date": "2024-01-01",
            "run_id": "test",
            "exporter_version": "VOP_v1.0",
            "elapsed_sec": 1.0,
            "from_cache": False,
            "cell_size_ft": 1.0,
            "cell_size_ft_requested": 1.0,
            "cell_size_ft_effective": 1.0,
            "resolution_mode": "canonical",
            "cap_triggered": False,
        }
        view_metadata = {"ViewId": "1", "ViewName": "Test", "ViewType": "FloorPlan"}

        # Should not crash, should return zeros for strategy columns
        row = build_vop_csv_row(view, metrics, anno_metrics, config, run_info,
                               view_metadata=view_metadata, strategy_diag=BrokenDiagnostics())

        # Should still have all columns with zeros for strategy stats
        self.assertEqual(len(row), 38)
        strategy_cols = row[-7:]
        self.assertEqual(strategy_cols, [0, 0, 0, 0, 0, 0.0, 0.0])


if __name__ == '__main__':
    unittest.main()
