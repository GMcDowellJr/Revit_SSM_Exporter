# -*- coding: utf-8 -*-
"""
Strategy Diagnostics Module

Tracks geometry extraction strategies and outcomes for diagnostic analysis.

Tracks:
- Element classification counts (TINY/LINEAR/AREAL)
- AREAL strategy success/failure rates
- Geometry extraction attempts and outcomes
- Per-category statistics

Usage:
    diag = StrategyDiagnostics()
    diag.record_element_classification(elem_id, 'AREAL', 'Walls')
    diag.record_areal_strategy(elem_id, 'planar_face_success', True, 'Walls')
    diag.record_geometry_extraction(elem_id, 'success', 'Walls', {'points': 42})
    summary = diag.get_summary()
    diag.print_summary()
    diag.export_to_csv('diagnostics.csv')
"""

from collections import defaultdict


class StrategyDiagnostics(object):
    """
    Tracks geometry extraction strategy diagnostics.

    Provides detailed tracking of element classification, strategy attempts,
    and geometry extraction outcomes with per-category breakdown.
    """

    def __init__(self):
        """Initialize strategy diagnostics tracker."""
        # Classification counters: {classification: count}
        self.classification_counts = defaultdict(int)

        # Per-category classification: {category: {classification: count}}
        self.category_classification = defaultdict(lambda: defaultdict(int))

        # AREAL strategy counters: {strategy: count}
        self.areal_strategy_counts = defaultdict(int)

        # Per-category AREAL strategy: {category: {strategy: count}}
        self.category_areal_strategy = defaultdict(lambda: defaultdict(int))

        # Geometry extraction outcome counters: {outcome: count}
        self.extraction_outcome_counts = defaultdict(int)

        # Per-category extraction outcomes: {category: {outcome: count}}
        self.category_extraction_outcome = defaultdict(lambda: defaultdict(int))

        # Confidence level counters: {confidence: count}
        # Tracks HIGH, MEDIUM, LOW confidence from Phase 2.2
        self.confidence_counts = defaultdict(int)

        # Per-category confidence: {category: {confidence: count}}
        self.category_confidence = defaultdict(lambda: defaultdict(int))

        # Per-element records for CSV export
        # Each record: {elem_id, category, classification, strategy_used,
        #               confidence, extraction_outcome, failure_reason}
        self.element_records = []

        # Track which elements have been recorded
        self.recorded_elements = set()

    def record_element_classification(self, elem_id, elem_class, category):
        """
        Record element classification.

        Args:
            elem_id: Element ID (int or string)
            elem_class: Classification ('TINY', 'LINEAR', 'AREAL')
            category: Element category name (e.g., 'Walls', 'Doors')
        """
        elem_id = str(elem_id)
        category = str(category) if category else 'Unknown'

        # Update counters
        self.classification_counts[elem_class] += 1
        self.category_classification[category][elem_class] += 1

        # Initialize element record if new
        if elem_id not in self.recorded_elements:
            self.recorded_elements.add(elem_id)
            self.element_records.append({
                'element_id': elem_id,
                'category': category,
                'classification': elem_class,
                'strategy_used': None,
                'confidence': None,
                'extraction_outcome': None,
                'failure_reason': None
            })

    def record_areal_strategy(self, elem_id, strategy, success, category):
        """
        Record AREAL strategy attempt and outcome.

        Args:
            elem_id: Element ID (int or string)
            strategy: Strategy name (e.g., 'planar_face_success', 'silhouette_success')
            success: Whether strategy succeeded (True/False)
            category: Element category name
        """
        elem_id = str(elem_id)
        category = str(category) if category else 'Unknown'

        # Build strategy key with success suffix
        if success:
            strategy_key = strategy if strategy.endswith('_success') else strategy + '_success'
        else:
            strategy_key = strategy if strategy.endswith('_failure') else strategy + '_failure'

        # Update counters
        self.areal_strategy_counts[strategy_key] += 1
        self.category_areal_strategy[category][strategy_key] += 1

        # Update element record if it exists
        for record in self.element_records:
            if record['element_id'] == elem_id:
                # Only update if not already set (first successful strategy wins)
                if record['strategy_used'] is None and success:
                    record['strategy_used'] = strategy
                    record['confidence'] = 'high' if success else 'low'
                break

    def record_geometry_extraction(self, elem_id, outcome, category, details=None):
        """
        Record geometry extraction attempt and outcome.

        Args:
            elem_id: Element ID (int or string)
            outcome: Outcome type ('success', 'no_geometry', 'no_solids',
                                   'insufficient_points', 'exception')
            category: Element category name
            details: Optional dict with additional details (e.g., {'points': 42, 'error': 'msg'})
        """
        elem_id = str(elem_id)
        category = str(category) if category else 'Unknown'
        details = details or {}

        # Update counters
        self.extraction_outcome_counts[outcome] += 1
        self.category_extraction_outcome[category][outcome] += 1

        # Update element record
        for record in self.element_records:
            if record['element_id'] == elem_id:
                record['extraction_outcome'] = outcome

                # Set failure reason if outcome is a failure type
                if outcome != 'success':
                    record['failure_reason'] = outcome

                # Extract additional details if provided
                if 'error' in details:
                    record['failure_reason'] = details['error']

                break

    def record_confidence(self, elem_id, confidence, category):
        """
        Record confidence level for an element.

        Args:
            elem_id: Element ID (int or string)
            confidence: Confidence level ('HIGH', 'MEDIUM', 'LOW')
            category: Element category name
        """
        elem_id = str(elem_id)
        category = str(category) if category else 'Unknown'

        # Normalize confidence to uppercase
        if confidence:
            confidence = str(confidence).upper()

            # Update counters
            self.confidence_counts[confidence] += 1
            self.category_confidence[category][confidence] += 1

            # Update element record
            for record in self.element_records:
                if record['element_id'] == elem_id:
                    record['confidence'] = confidence
                    break

    def get_summary(self):
        """
        Get summary statistics.

        Returns:
            dict: Summary statistics including:
                - total_elements: Total elements processed
                - classification_counts: Counts by classification
                - classification_rates: Percentages by classification
                - areal_strategy_counts: AREAL strategy attempt counts
                - areal_strategy_rates: AREAL strategy success rates
                - extraction_outcome_counts: Extraction outcome counts
                - extraction_outcome_rates: Extraction outcome percentages
                - category_breakdown: Per-category statistics
        """
        total_elements = len(self.element_records)

        # Calculate classification rates
        classification_rates = {}
        if total_elements > 0:
            for cls, count in self.classification_counts.items():
                classification_rates[cls] = (count * 100.0) / total_elements

        # Calculate AREAL strategy success rates
        areal_strategy_rates = {}
        strategy_base_names = set()

        # Extract base strategy names (without _success/_failure suffix)
        for strategy_key in self.areal_strategy_counts.keys():
            if strategy_key.endswith('_success'):
                base_name = strategy_key[:-8]  # Remove '_success'
                strategy_base_names.add(base_name)
            elif strategy_key.endswith('_failure'):
                base_name = strategy_key[:-8]  # Remove '_failure'
                strategy_base_names.add(base_name)

        # Calculate success rate for each strategy
        for base_name in strategy_base_names:
            success_count = self.areal_strategy_counts.get(base_name + '_success', 0)
            failure_count = self.areal_strategy_counts.get(base_name + '_failure', 0)
            total_attempts = success_count + failure_count

            if total_attempts > 0:
                success_rate = (success_count * 100.0) / total_attempts
                areal_strategy_rates[base_name] = {
                    'success_count': success_count,
                    'failure_count': failure_count,
                    'total_attempts': total_attempts,
                    'success_rate': success_rate
                }

        # Calculate extraction outcome rates
        extraction_outcome_rates = {}
        total_extractions = sum(self.extraction_outcome_counts.values())

        if total_extractions > 0:
            for outcome, count in self.extraction_outcome_counts.items():
                extraction_outcome_rates[outcome] = (count * 100.0) / total_extractions

        # Build category breakdown
        category_breakdown = {}
        for category in self.category_classification.keys():
            category_total = sum(self.category_classification[category].values())

            category_breakdown[category] = {
                'total_elements': category_total,
                'classification': dict(self.category_classification[category]),
                'areal_strategies': dict(self.category_areal_strategy[category]),
                'extraction_outcomes': dict(self.category_extraction_outcome[category])
            }

        # Calculate confidence rates
        confidence_rates = {}
        total_with_confidence = sum(self.confidence_counts.values())
        if total_with_confidence > 0:
            for conf, count in self.confidence_counts.items():
                confidence_rates[conf] = (count * 100.0) / total_with_confidence

        return {
            'total_elements': total_elements,
            'classification_counts': dict(self.classification_counts),
            'classification_rates': classification_rates,
            'confidence_counts': dict(self.confidence_counts),
            'confidence_rates': confidence_rates,
            'areal_strategy_counts': dict(self.areal_strategy_counts),
            'areal_strategy_rates': areal_strategy_rates,
            'extraction_outcome_counts': dict(self.extraction_outcome_counts),
            'extraction_outcome_rates': extraction_outcome_rates,
            'category_breakdown': category_breakdown
        }

    def print_summary(self):
        """Print summary statistics to console."""
        summary = self.get_summary()

        print("\n" + "=" * 80)
        print("STRATEGY DIAGNOSTICS SUMMARY")
        print("=" * 80)

        # Overall statistics
        print("\nOVERALL STATISTICS:")
        print("-" * 80)
        print("Total Elements Processed: {}".format(summary['total_elements']))

        # Classification breakdown
        print("\nCLASSIFICATION BREAKDOWN:")
        print("-" * 80)
        for cls in ['TINY', 'LINEAR', 'AREAL']:
            count = summary['classification_counts'].get(cls, 0)
            rate = summary['classification_rates'].get(cls, 0.0)
            print("  {:<10} {:>6} ({:>5.1f}%)".format(cls + ':', count, rate))

        # Confidence level breakdown (Phase 2.2)
        if summary.get('confidence_counts'):
            print("\nCONFIDENCE LEVEL DISTRIBUTION:")
            print("-" * 80)
            for conf in ['HIGH', 'MEDIUM', 'LOW']:
                count = summary['confidence_counts'].get(conf, 0)
                rate = summary['confidence_rates'].get(conf, 0.0)
                if count > 0:
                    print("  {:<10} {:>6} ({:>5.1f}%)".format(conf + ':', count, rate))

        # AREAL strategy breakdown
        if summary['areal_strategy_rates']:
            print("\nAREAL STRATEGY BREAKDOWN:")
            print("-" * 80)
            for strategy, stats in sorted(summary['areal_strategy_rates'].items()):
                print("  {:<30} Success: {:>4}/{:<4} ({:>5.1f}%)".format(
                    strategy + ':',
                    stats['success_count'],
                    stats['total_attempts'],
                    stats['success_rate']
                ))

        # Extraction outcome breakdown
        print("\nGEOMETRY EXTRACTION OUTCOMES:")
        print("-" * 80)
        for outcome in ['success', 'no_geometry', 'no_solids', 'insufficient_points', 'exception']:
            count = summary['extraction_outcome_counts'].get(outcome, 0)
            rate = summary['extraction_outcome_rates'].get(outcome, 0.0)
            if count > 0:
                print("  {:<25} {:>6} ({:>5.1f}%)".format(outcome + ':', count, rate))

        # Per-category breakdown
        if summary['category_breakdown']:
            print("\nPER-CATEGORY BREAKDOWN:")
            print("-" * 80)
            for category, stats in sorted(summary['category_breakdown'].items()):
                print("\n  Category: {}".format(category))
                print("    Total Elements: {}".format(stats['total_elements']))

                if stats['classification']:
                    print("    Classifications:")
                    for cls, count in sorted(stats['classification'].items()):
                        print("      {:<10} {:>4}".format(cls + ':', count))

                if stats['areal_strategies']:
                    print("    AREAL Strategies:")
                    for strategy, count in sorted(stats['areal_strategies'].items()):
                        print("      {:<30} {:>4}".format(strategy + ':', count))

        print("\n" + "=" * 80)

    def export_to_csv(self, filepath):
        """
        Export diagnostics to CSV file.

        Args:
            filepath: Path to output CSV file

        CSV columns:
            - element_id: Element ID
            - category: Element category name
            - classification: Element classification (TINY/LINEAR/AREAL)
            - strategy_used: Strategy that succeeded (for AREAL elements)
            - confidence: Strategy confidence (high/low)
            - extraction_outcome: Geometry extraction outcome
            - failure_reason: Reason for failure (if applicable)
        """
        with open(filepath, 'w') as f:
            # Write header
            f.write('element_id,category,classification,strategy_used,confidence,extraction_outcome,failure_reason\n')

            # Write element records
            for record in self.element_records:
                # Escape values that might contain commas
                elem_id = str(record['element_id'])
                category = str(record['category'])
                classification = str(record['classification'])
                strategy_used = str(record['strategy_used']) if record['strategy_used'] else ''
                confidence = str(record['confidence']) if record['confidence'] else ''
                extraction_outcome = str(record['extraction_outcome']) if record['extraction_outcome'] else ''
                failure_reason = str(record['failure_reason']) if record['failure_reason'] else ''

                # Write row
                f.write('{},{},{},{},{},{},{}\n'.format(
                    elem_id,
                    category,
                    classification,
                    strategy_used,
                    confidence,
                    extraction_outcome,
                    failure_reason
                ))
