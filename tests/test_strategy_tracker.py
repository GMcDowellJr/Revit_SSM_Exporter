# -*- coding: utf-8 -*-
"""
Unit tests for Strategy Tracker diagnostics module.

Tests the StrategyDiagnostics class with mock data to verify:
- Element classification tracking
- AREAL strategy tracking
- Geometry extraction tracking
- Summary statistics calculation
- CSV export format
"""

import os
import tempfile
import unittest

from vop_interwoven.diagnostics.strategy_tracker import StrategyDiagnostics


class TestStrategyDiagnostics(unittest.TestCase):
    """Test StrategyDiagnostics class."""

    def setUp(self):
        """Create fresh diagnostics instance for each test."""
        self.diag = StrategyDiagnostics()

    def test_element_classification_tracking(self):
        """Test basic element classification tracking."""
        # Record various element classifications
        self.diag.record_element_classification(1001, 'TINY', 'Doors')
        self.diag.record_element_classification(1002, 'TINY', 'Windows')
        self.diag.record_element_classification(2001, 'LINEAR', 'Walls')
        self.diag.record_element_classification(2002, 'LINEAR', 'Walls')
        self.diag.record_element_classification(3001, 'AREAL', 'Floors')
        self.diag.record_element_classification(3002, 'AREAL', 'Floors')
        self.diag.record_element_classification(3003, 'AREAL', 'Walls')

        # Verify classification counts
        self.assertEqual(self.diag.classification_counts['TINY'], 2)
        self.assertEqual(self.diag.classification_counts['LINEAR'], 2)
        self.assertEqual(self.diag.classification_counts['AREAL'], 3)

        # Verify per-category counts
        self.assertEqual(self.diag.category_classification['Doors']['TINY'], 1)
        self.assertEqual(self.diag.category_classification['Windows']['TINY'], 1)
        self.assertEqual(self.diag.category_classification['Walls']['LINEAR'], 2)
        self.assertEqual(self.diag.category_classification['Walls']['AREAL'], 1)
        self.assertEqual(self.diag.category_classification['Floors']['AREAL'], 2)

        # Verify total elements
        self.assertEqual(len(self.diag.element_records), 7)

    def test_areal_strategy_tracking(self):
        """Test AREAL strategy success/failure tracking."""
        # Record AREAL elements with different strategies
        self.diag.record_element_classification(3001, 'AREAL', 'Floors')
        self.diag.record_element_classification(3002, 'AREAL', 'Floors')
        self.diag.record_element_classification(3003, 'AREAL', 'Walls')
        self.diag.record_element_classification(3004, 'AREAL', 'Walls')

        # Record strategy attempts
        self.diag.record_areal_strategy(3001, 'planar_face', True, 'Floors')
        self.diag.record_areal_strategy(3002, 'planar_face', True, 'Floors')
        self.diag.record_areal_strategy(3003, 'planar_face', False, 'Walls')
        self.diag.record_areal_strategy(3003, 'silhouette', True, 'Walls')
        self.diag.record_areal_strategy(3004, 'planar_face', False, 'Walls')
        self.diag.record_areal_strategy(3004, 'silhouette', False, 'Walls')
        self.diag.record_areal_strategy(3004, 'geometry_polygon', True, 'Walls')

        # Verify strategy counts
        self.assertEqual(self.diag.areal_strategy_counts['planar_face_success'], 2)
        self.assertEqual(self.diag.areal_strategy_counts['planar_face_failure'], 2)
        self.assertEqual(self.diag.areal_strategy_counts['silhouette_success'], 1)
        self.assertEqual(self.diag.areal_strategy_counts['silhouette_failure'], 1)
        self.assertEqual(self.diag.areal_strategy_counts['geometry_polygon_success'], 1)

        # Verify per-category strategy counts
        self.assertEqual(self.diag.category_areal_strategy['Floors']['planar_face_success'], 2)
        self.assertEqual(self.diag.category_areal_strategy['Walls']['planar_face_failure'], 2)

    def test_geometry_extraction_tracking(self):
        """Test geometry extraction outcome tracking."""
        # Record elements with various extraction outcomes
        self.diag.record_element_classification(1001, 'AREAL', 'Floors')
        self.diag.record_element_classification(1002, 'AREAL', 'Walls')
        self.diag.record_element_classification(1003, 'AREAL', 'Walls')
        self.diag.record_element_classification(1004, 'TINY', 'Doors')
        self.diag.record_element_classification(1005, 'LINEAR', 'Walls')

        # Record extraction outcomes
        self.diag.record_geometry_extraction(1001, 'success', 'Floors')
        self.diag.record_geometry_extraction(1002, 'success', 'Walls')
        self.diag.record_geometry_extraction(1003, 'no_geometry', 'Walls')
        self.diag.record_geometry_extraction(1004, 'success', 'Doors')
        self.diag.record_geometry_extraction(1005, 'insufficient_points', 'Walls',
                                            {'error': 'Only 2 points found'})

        # Verify extraction outcome counts
        self.assertEqual(self.diag.extraction_outcome_counts['success'], 3)
        self.assertEqual(self.diag.extraction_outcome_counts['no_geometry'], 1)
        self.assertEqual(self.diag.extraction_outcome_counts['insufficient_points'], 1)

        # Verify per-category extraction outcomes
        self.assertEqual(self.diag.category_extraction_outcome['Floors']['success'], 1)
        self.assertEqual(self.diag.category_extraction_outcome['Walls']['success'], 1)
        self.assertEqual(self.diag.category_extraction_outcome['Walls']['no_geometry'], 1)
        self.assertEqual(self.diag.category_extraction_outcome['Walls']['insufficient_points'], 1)

    def test_get_summary(self):
        """Test summary statistics calculation."""
        # Create mock data (100 elements)
        for i in range(40):
            self.diag.record_element_classification(1000 + i, 'TINY', 'Doors')
            self.diag.record_geometry_extraction(1000 + i, 'success', 'Doors')

        for i in range(30):
            self.diag.record_element_classification(2000 + i, 'LINEAR', 'Walls')
            self.diag.record_geometry_extraction(2000 + i, 'success', 'Walls')

        for i in range(30):
            elem_id = 3000 + i
            self.diag.record_element_classification(elem_id, 'AREAL', 'Floors')

            # 20 successful planar_face
            if i < 20:
                self.diag.record_areal_strategy(elem_id, 'planar_face', True, 'Floors')
                self.diag.record_geometry_extraction(elem_id, 'success', 'Floors')
            # 5 failed planar_face, successful silhouette
            elif i < 25:
                self.diag.record_areal_strategy(elem_id, 'planar_face', False, 'Floors')
                self.diag.record_areal_strategy(elem_id, 'silhouette', True, 'Floors')
                self.diag.record_geometry_extraction(elem_id, 'success', 'Floors')
            # 5 failed planar_face, failed silhouette, no geometry
            else:
                self.diag.record_areal_strategy(elem_id, 'planar_face', False, 'Floors')
                self.diag.record_areal_strategy(elem_id, 'silhouette', False, 'Floors')
                self.diag.record_geometry_extraction(elem_id, 'no_geometry', 'Floors')

        # Get summary
        summary = self.diag.get_summary()

        # Verify totals
        self.assertEqual(summary['total_elements'], 100)

        # Verify classification counts
        self.assertEqual(summary['classification_counts']['TINY'], 40)
        self.assertEqual(summary['classification_counts']['LINEAR'], 30)
        self.assertEqual(summary['classification_counts']['AREAL'], 30)

        # Verify classification rates
        self.assertAlmostEqual(summary['classification_rates']['TINY'], 40.0, places=1)
        self.assertAlmostEqual(summary['classification_rates']['LINEAR'], 30.0, places=1)
        self.assertAlmostEqual(summary['classification_rates']['AREAL'], 30.0, places=1)

        # Verify AREAL strategy success rates
        planar_stats = summary['areal_strategy_rates']['planar_face']
        self.assertEqual(planar_stats['success_count'], 20)
        self.assertEqual(planar_stats['failure_count'], 10)
        self.assertEqual(planar_stats['total_attempts'], 30)
        self.assertAlmostEqual(planar_stats['success_rate'], 66.67, places=1)

        silhouette_stats = summary['areal_strategy_rates']['silhouette']
        self.assertEqual(silhouette_stats['success_count'], 5)
        self.assertEqual(silhouette_stats['failure_count'], 5)
        self.assertEqual(silhouette_stats['total_attempts'], 10)
        self.assertAlmostEqual(silhouette_stats['success_rate'], 50.0, places=1)

        # Verify extraction outcome counts
        self.assertEqual(summary['extraction_outcome_counts']['success'], 95)
        self.assertEqual(summary['extraction_outcome_counts']['no_geometry'], 5)

        # Verify extraction outcome rates
        self.assertAlmostEqual(summary['extraction_outcome_rates']['success'], 95.0, places=1)
        self.assertAlmostEqual(summary['extraction_outcome_rates']['no_geometry'], 5.0, places=1)

        # Verify category breakdown
        self.assertIn('Doors', summary['category_breakdown'])
        self.assertIn('Walls', summary['category_breakdown'])
        self.assertIn('Floors', summary['category_breakdown'])

        doors_stats = summary['category_breakdown']['Doors']
        self.assertEqual(doors_stats['total_elements'], 40)
        self.assertEqual(doors_stats['classification']['TINY'], 40)

    def test_csv_export(self):
        """Test CSV export format and content."""
        # Create mock data
        self.diag.record_element_classification(1001, 'TINY', 'Doors')
        self.diag.record_geometry_extraction(1001, 'success', 'Doors')

        self.diag.record_element_classification(2001, 'LINEAR', 'Walls')
        self.diag.record_geometry_extraction(2001, 'success', 'Walls')

        self.diag.record_element_classification(3001, 'AREAL', 'Floors')
        self.diag.record_areal_strategy(3001, 'planar_face', True, 'Floors')
        self.diag.record_geometry_extraction(3001, 'success', 'Floors')

        self.diag.record_element_classification(3002, 'AREAL', 'Walls')
        self.diag.record_areal_strategy(3002, 'planar_face', False, 'Walls')
        self.diag.record_areal_strategy(3002, 'silhouette', True, 'Walls')
        self.diag.record_geometry_extraction(3002, 'success', 'Walls')

        self.diag.record_element_classification(3003, 'AREAL', 'Walls')
        self.diag.record_areal_strategy(3003, 'planar_face', False, 'Walls')
        self.diag.record_geometry_extraction(3003, 'no_geometry', 'Walls')

        # Export to temporary CSV
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.csv') as tmp:
            csv_path = tmp.name

        try:
            self.diag.export_to_csv(csv_path)

            # Verify CSV was created
            self.assertTrue(os.path.exists(csv_path))

            # Read and verify CSV content
            with open(csv_path, 'r') as f:
                lines = f.readlines()

            # Verify header
            self.assertEqual(len(lines), 6)  # Header + 5 elements
            header = lines[0].strip()
            self.assertEqual(header, 'element_id,category,classification,strategy_used,confidence,extraction_outcome,failure_reason')

            # Verify first element (TINY)
            self.assertIn('1001,Doors,TINY,,,success,', lines[1])

            # Verify second element (LINEAR)
            self.assertIn('2001,Walls,LINEAR,,,success,', lines[2])

            # Verify third element (AREAL with planar_face success)
            self.assertIn('3001,Floors,AREAL,planar_face,high,success,', lines[3])

            # Verify fourth element (AREAL with silhouette success after planar_face failure)
            self.assertIn('3002,Walls,AREAL,silhouette,high,success,', lines[4])

            # Verify fifth element (AREAL with no_geometry)
            self.assertIn('3003,Walls,AREAL,,,no_geometry,no_geometry', lines[5])

        finally:
            # Clean up temporary file
            if os.path.exists(csv_path):
                os.remove(csv_path)

    def test_print_summary(self):
        """Test print_summary executes without errors."""
        # Create some mock data
        for i in range(10):
            self.diag.record_element_classification(1000 + i, 'TINY', 'Doors')
            self.diag.record_geometry_extraction(1000 + i, 'success', 'Doors')

        for i in range(15):
            elem_id = 2000 + i
            self.diag.record_element_classification(elem_id, 'AREAL', 'Floors')
            if i < 10:
                self.diag.record_areal_strategy(elem_id, 'planar_face', True, 'Floors')
                self.diag.record_geometry_extraction(elem_id, 'success', 'Floors')
            else:
                self.diag.record_areal_strategy(elem_id, 'planar_face', False, 'Floors')
                self.diag.record_geometry_extraction(elem_id, 'no_geometry', 'Floors')

        # Should execute without errors
        try:
            self.diag.print_summary()
            success = True
        except Exception as e:
            success = False
            print("print_summary() failed: {}".format(e))

        self.assertTrue(success)


if __name__ == '__main__':
    unittest.main()
