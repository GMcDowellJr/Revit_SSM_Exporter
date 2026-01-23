# -*- coding: utf-8 -*-
"""
Integration tests for pipeline strategy diagnostics.

Tests that strategy diagnostics integrates correctly into the main pipeline
without crashes or performance degradation.
"""

import unittest
from vop_interwoven.config import Config
from vop_interwoven.diagnostics import StrategyDiagnostics


class TestPipelineDiagnostics(unittest.TestCase):
    """Test strategy diagnostics integration in pipeline."""

    def test_config_default_export_strategy_diagnostics(self):
        """Test that export_strategy_diagnostics defaults to True."""
        cfg = Config()
        self.assertTrue(cfg.export_strategy_diagnostics)

    def test_config_export_strategy_diagnostics_can_be_disabled(self):
        """Test that export_strategy_diagnostics can be disabled."""
        cfg = Config(export_strategy_diagnostics=False)
        self.assertFalse(cfg.export_strategy_diagnostics)

    def test_config_export_strategy_diagnostics_to_dict(self):
        """Test that export_strategy_diagnostics is included in to_dict()."""
        cfg = Config(export_strategy_diagnostics=True)
        cfg_dict = cfg.to_dict()
        self.assertIn('export_strategy_diagnostics', cfg_dict)
        self.assertTrue(cfg_dict['export_strategy_diagnostics'])

    def test_config_export_strategy_diagnostics_from_dict(self):
        """Test that export_strategy_diagnostics can be loaded from dict."""
        cfg_dict = {'export_strategy_diagnostics': False}
        cfg = Config.from_dict(cfg_dict)
        self.assertFalse(cfg.export_strategy_diagnostics)

    def test_strategy_diagnostics_creation(self):
        """Test that StrategyDiagnostics can be created."""
        diag = StrategyDiagnostics()
        self.assertIsNotNone(diag)
        self.assertEqual(len(diag.element_records), 0)

    def test_strategy_diagnostics_tracks_classification(self):
        """Test that classification tracking works."""
        diag = StrategyDiagnostics()

        # Track some elements
        diag.record_element_classification(1001, 'TINY', 'Doors')
        diag.record_element_classification(1002, 'LINEAR', 'Walls')
        diag.record_element_classification(1003, 'AREAL', 'Floors')

        # Verify tracking
        summary = diag.get_summary()
        self.assertEqual(summary['total_elements'], 3)
        self.assertEqual(summary['classification_counts']['TINY'], 1)
        self.assertEqual(summary['classification_counts']['LINEAR'], 1)
        self.assertEqual(summary['classification_counts']['AREAL'], 1)

    def test_strategy_diagnostics_tracks_areal_strategies(self):
        """Test that AREAL strategy tracking works."""
        diag = StrategyDiagnostics()

        # Track element and strategies
        diag.record_element_classification(1001, 'AREAL', 'Floors')
        diag.record_areal_strategy(1001, 'planar_face', True, 'Floors')

        diag.record_element_classification(1002, 'AREAL', 'Walls')
        diag.record_areal_strategy(1002, 'silhouette', False, 'Walls')
        diag.record_areal_strategy(1002, 'bbox_obb_used', True, 'Walls')

        # Verify tracking
        summary = diag.get_summary()
        self.assertIn('areal_strategy_counts', summary)
        self.assertEqual(summary['areal_strategy_counts']['planar_face_success'], 1)
        self.assertEqual(summary['areal_strategy_counts']['silhouette_failure'], 1)
        self.assertEqual(summary['areal_strategy_counts']['bbox_obb_used_success'], 1)

    def test_strategy_diagnostics_print_summary_doesnt_crash(self):
        """Test that print_summary() doesn't crash."""
        diag = StrategyDiagnostics()

        # Add some data
        for i in range(10):
            diag.record_element_classification(1000 + i, 'AREAL', 'Floors')
            if i % 2 == 0:
                diag.record_areal_strategy(1000 + i, 'planar_face', True, 'Floors')
            else:
                diag.record_areal_strategy(1000 + i, 'silhouette', False, 'Floors')
                diag.record_areal_strategy(1000 + i, 'aabb_used', True, 'Floors')

        # Should not crash
        try:
            diag.print_summary()
            success = True
        except Exception as e:
            success = False
            print("print_summary() failed: {}".format(e))

        self.assertTrue(success)

    def test_strategy_diagnostics_csv_export(self):
        """Test that CSV export works."""
        import tempfile
        import os

        diag = StrategyDiagnostics()

        # Add test data
        diag.record_element_classification(1001, 'AREAL', 'Floors')
        diag.record_areal_strategy(1001, 'planar_face', True, 'Floors')
        diag.record_geometry_extraction(1001, 'success', 'Floors', {'vertices': 42})

        # Export to temporary file
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.csv') as tmp:
            csv_path = tmp.name

        try:
            diag.export_to_csv(csv_path)

            # Verify file was created
            self.assertTrue(os.path.exists(csv_path))

            # Verify content
            with open(csv_path, 'r') as f:
                content = f.read()

            # Should have header and one data row
            lines = content.strip().split('\n')
            self.assertEqual(len(lines), 2)  # Header + 1 element

            # Verify header
            self.assertIn('element_id', lines[0])
            self.assertIn('category', lines[0])
            self.assertIn('classification', lines[0])

            # Verify data row contains our element
            self.assertIn('1001', lines[1])
            self.assertIn('Floors', lines[1])
            self.assertIn('AREAL', lines[1])

        finally:
            # Clean up
            if os.path.exists(csv_path):
                os.remove(csv_path)

    def test_config_round_trip_with_diagnostics(self):
        """Test that config can round-trip with export_strategy_diagnostics."""
        cfg1 = Config(export_strategy_diagnostics=False)
        cfg_dict = cfg1.to_dict()
        cfg2 = Config.from_dict(cfg_dict)

        self.assertEqual(cfg1.export_strategy_diagnostics, cfg2.export_strategy_diagnostics)
        self.assertFalse(cfg2.export_strategy_diagnostics)

    def test_pipeline_can_create_diagnostics(self):
        """Test that pipeline logic for creating diagnostics works."""
        cfg = Config(export_strategy_diagnostics=True)

        # Simulate pipeline logic
        strategy_diag = None
        if getattr(cfg, "export_strategy_diagnostics", False):
            try:
                strategy_diag = StrategyDiagnostics()
            except Exception:
                pass

        self.assertIsNotNone(strategy_diag)
        self.assertIsInstance(strategy_diag, StrategyDiagnostics)

    def test_pipeline_can_skip_diagnostics_if_disabled(self):
        """Test that pipeline can skip diagnostics when disabled."""
        cfg = Config(export_strategy_diagnostics=False)

        # Simulate pipeline logic
        strategy_diag = None
        if getattr(cfg, "export_strategy_diagnostics", False):
            try:
                strategy_diag = StrategyDiagnostics()
            except Exception:
                pass

        self.assertIsNone(strategy_diag)

    def test_diagnostic_failure_handling(self):
        """Test that diagnostic tracking failures don't crash."""
        diag = StrategyDiagnostics()

        # Try to track with invalid parameters (should not crash due to try/except in pipeline)
        try:
            # Record classification
            diag.record_element_classification(1001, 'AREAL', 'Floors')

            # Simulate tracking that might fail in pipeline
            if diag is not None:
                try:
                    diag.record_areal_strategy(1001, 'planar_face', True, 'Floors')
                except Exception:
                    pass  # This is how pipeline handles failures

            success = True
        except Exception:
            success = False

        self.assertTrue(success)


if __name__ == '__main__':
    unittest.main()
