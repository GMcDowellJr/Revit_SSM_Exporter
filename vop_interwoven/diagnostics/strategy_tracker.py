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
        #               confidence, extraction_outcome, failure_reason,
        #               extraction_method, method_attempted_order}
        self.element_records = []

        # Track which elements have been recorded
        self.recorded_elements = set()

        # Per-category statistics (Phase 3.1)
        # Structure: {category: {total, success, methods: {method: count}, confidence: {level: count}}}
        self.category_stats = defaultdict(lambda: {
            'total': 0,
            'success': 0,
            'methods': defaultdict(int),
            'confidence': defaultdict(int)
        })

        # Track extraction method attempts per element (Phase 3.1)
        # Structure: {elem_id: [method1, method2, ...]}
        self.element_attempts = {}

        # Overall method statistics (Phase 3.1)
        # Structure: {method: count}
        self.method_counts = defaultdict(int)

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
                'failure_reason': None,
                'extraction_method': None,
                'method_attempted_order': None
            })

    def record_areal_strategy(self, elem_id, strategy, success, category, confidence=None):
        """
        Record AREAL strategy attempt and outcome.

        Args:
            elem_id: Element ID (int or string)
            strategy: Strategy name (e.g., 'planar_face_success', 'silhouette_success')
            success: Whether strategy succeeded (True/False)
            category: Element category name
            confidence: Optional confidence level ('HIGH', 'MEDIUM', 'LOW')
                       If not provided, defaults to 'HIGH' if success else 'LOW'
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

        # Determine confidence level
        if confidence is not None:
            # Use provided confidence (normalize to uppercase)
            conf_level = str(confidence).upper()
        else:
            # Fall back to legacy behavior
            conf_level = 'HIGH' if success else 'LOW'

        # Update element record if it exists
        for record in self.element_records:
            if record['element_id'] == elem_id:
                # Only update if not already set (first successful strategy wins)
                if record['strategy_used'] is None and success:
                    record['strategy_used'] = strategy
                    record['confidence'] = conf_level
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

    def record_method_attempt(self, elem_id, method):
        """
        Record an extraction method attempt for an element.

        Args:
            elem_id: Element ID (int or string)
            method: Extraction method attempted (e.g., 'planar_face', 'silhouette',
                   'geometry_polygon', 'bbox_obb', 'aabb')
        """
        elem_id = str(elem_id)

        # Initialize list if first attempt for this element
        if elem_id not in self.element_attempts:
            self.element_attempts[elem_id] = []

        # Record the method attempt
        self.element_attempts[elem_id].append(method)

    def record_extraction_method(self, elem_id, category, method, success, confidence=None):
        """
        Track which extraction method was used for an element.

        Args:
            elem_id: Element ID (int or string)
            category: Element category (e.g., 'Walls', 'Floors')
            method: Extraction method used (e.g., 'planar_face', 'silhouette',
                   'geometry_polygon', 'bbox_obb', 'aabb')
            success: Whether extraction succeeded (True/False)
            confidence: Optional confidence level ('HIGH', 'MEDIUM', 'LOW')
        """
        elem_id = str(elem_id)
        category = str(category) if category else 'Unknown'

        # Track overall method usage
        method_key = 'method_{}'.format(method)
        self.method_counts[method_key] += 1

        # Track per-category method usage
        cat_method_key = 'category_{}_method_{}'.format(category, method)
        self.method_counts[cat_method_key] += 1

        # Track per-category total
        cat_total_key = 'category_{}_total'.format(category)
        self.method_counts[cat_total_key] += 1

        # Update category_stats
        if category not in self.category_stats:
            self.category_stats[category] = {
                'total': 0,
                'success': 0,
                'methods': defaultdict(int),
                'confidence': defaultdict(int)
            }

        self.category_stats[category]['total'] += 1
        self.category_stats[category]['methods'][method] += 1

        if success:
            # Track per-category success
            cat_success_key = 'category_{}_success'.format(category)
            self.method_counts[cat_success_key] += 1

            # Update category_stats success count
            self.category_stats[category]['success'] += 1

            # Track confidence per category
            if confidence:
                conf_upper = str(confidence).upper()
                cat_conf_key = 'category_{}_confidence_{}'.format(category, conf_upper)
                self.method_counts[cat_conf_key] += 1
                self.category_stats[category]['confidence'][conf_upper] += 1

            # Update element record with successful extraction method
            for record in self.element_records:
                if record['element_id'] == elem_id:
                    # Only update if not already set (first successful method wins)
                    if record['extraction_method'] is None:
                        record['extraction_method'] = method
                    break

        # Update element record with method attempt order
        for record in self.element_records:
            if record['element_id'] == elem_id:
                if elem_id in self.element_attempts:
                    record['method_attempted_order'] = ','.join(self.element_attempts[elem_id])
                break

    @staticmethod
    def _safe_rate(success, total):
        """
        Calculate success rate safely, handling division by zero.

        Args:
            success: Number of successes
            total: Total number of attempts

        Returns:
            float: Success rate as percentage (0.0 if total is 0)
        """
        return (100.0 * success / total) if total > 0 else 0.0

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

        # Phase 3.1: Build enhanced category method statistics
        category_method_stats = {}
        for category, stats in self.category_stats.items():
            total = stats['total']
            success = stats['success']
            success_rate = self._safe_rate(success, total)

            category_method_stats[category] = {
                'total': total,
                'success': success,
                'success_rate': success_rate,
                'methods': dict(stats['methods']),
                'confidence': dict(stats['confidence'])
            }

        # Phase 3.1: Identify top performers (by success rate)
        sorted_categories = sorted(
            category_method_stats.items(),
            key=lambda x: (x[1]['success_rate'], x[1]['total']),
            reverse=True
        )
        top_performers = sorted_categories[:5] if sorted_categories else []

        # Phase 3.1: Identify categories needing attention (lowest success rates)
        needs_attention = sorted_categories[-5:][::-1] if len(sorted_categories) > 5 else []

        # Phase 3.1: Build overall method statistics
        method_stats = {}
        total_method_uses = 0

        # Count total uses per method
        for method_key, count in self.method_counts.items():
            if method_key.startswith('method_') and not method_key.startswith('method_category_'):
                method_name = method_key[7:]  # Remove 'method_' prefix
                method_stats[method_name] = {
                    'count': count,
                    'confidence': {'HIGH': 0, 'MEDIUM': 0, 'LOW': 0}
                }
                total_method_uses += count

        # Calculate percentages and average confidence per method
        for method_name in method_stats.keys():
            if total_method_uses > 0:
                method_stats[method_name]['percentage'] = self._safe_rate(
                    method_stats[method_name]['count'],
                    total_method_uses
                )

            # Aggregate confidence levels across all categories for this method
            for category, stats in self.category_stats.items():
                if method_name in stats['methods']:
                    for conf_level, conf_count in stats['confidence'].items():
                        # Approximate: distribute confidence proportionally to method usage
                        # This is a simplification; ideally we'd track confidence per method per category
                        method_stats[method_name]['confidence'][conf_level] += conf_count

        # Calculate average confidence for each method
        for method_name in method_stats.keys():
            total_conf = sum(method_stats[method_name]['confidence'].values())
            if total_conf > 0:
                high_pct = self._safe_rate(method_stats[method_name]['confidence']['HIGH'], total_conf)
                medium_pct = self._safe_rate(method_stats[method_name]['confidence']['MEDIUM'], total_conf)
                low_pct = self._safe_rate(method_stats[method_name]['confidence']['LOW'], total_conf)

                # Determine average confidence level
                if high_pct >= 50:
                    method_stats[method_name]['avg_confidence'] = 'HIGH'
                elif medium_pct >= 50:
                    method_stats[method_name]['avg_confidence'] = 'MEDIUM'
                elif low_pct >= 50:
                    method_stats[method_name]['avg_confidence'] = 'LOW'
                else:
                    # Mixed confidence - use weighted average
                    if high_pct > medium_pct and high_pct > low_pct:
                        method_stats[method_name]['avg_confidence'] = 'HIGH'
                    elif medium_pct > low_pct:
                        method_stats[method_name]['avg_confidence'] = 'MEDIUM'
                    else:
                        method_stats[method_name]['avg_confidence'] = 'LOW'
            else:
                method_stats[method_name]['avg_confidence'] = 'UNKNOWN'

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
            'category_breakdown': category_breakdown,
            # Phase 3.1: Enhanced category and method statistics
            'category_method_stats': category_method_stats,
            'top_performers': top_performers,
            'needs_attention': needs_attention,
            'method_stats': method_stats
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

        # Phase 3.1: Enhanced category statistics with methods
        if summary.get('category_method_stats'):
            print("\nEXTRACTION BY CATEGORY")
            print("=" * 80)
            print("{:<20} {:>7} {:>9} {:>7}  {}".format(
                "Category", "Total", "Success", "Rate", "Methods Used"
            ))
            print("{:<20} {:>7} {:>9} {:>7}  {}".format(
                "--------", "-----", "-------", "----", "------------"
            ))

            # Sort by total elements (descending)
            sorted_cats = sorted(
                summary['category_method_stats'].items(),
                key=lambda x: x[1]['total'],
                reverse=True
            )

            for category, stats in sorted_cats:
                # Build methods summary string
                methods_list = []
                for method, count in sorted(stats['methods'].items(), key=lambda x: x[1], reverse=True):
                    # Abbreviate method names for display
                    method_abbrev = method.replace('_', '')[:6]
                    methods_list.append("{}:{}".format(method_abbrev, count))

                methods_str = ', '.join(methods_list[:3])  # Show top 3 methods
                if len(methods_list) > 3:
                    methods_str += '...'

                print("{:<20} {:>7} {:>9} {:>6.1f}%  {}".format(
                    category[:19],  # Truncate long category names
                    stats['total'],
                    stats['success'],
                    stats['success_rate'],
                    methods_str
                ))

            # Show top performers
            if summary.get('top_performers'):
                print("\nTop Performers:")
                for idx, (category, stats) in enumerate(summary['top_performers'][:3], 1):
                    print("  {}. {:<20} {:>5.1f}% ({} elements)".format(
                        idx,
                        category + ':',
                        stats['success_rate'],
                        stats['total']
                    ))

            # Show categories needing attention
            if summary.get('needs_attention') and len(summary['needs_attention']) > 0:
                print("\nNeeds Attention:")
                for idx, (category, stats) in enumerate(summary['needs_attention'][:3], 1):
                    print("  {}. {:<20} {:>5.1f}% ({} elements)".format(
                        idx,
                        category + ':',
                        stats['success_rate'],
                        stats['total']
                    ))

        # Phase 3.1: Methods breakdown
        if summary.get('method_stats'):
            print("\nMETHODS BREAKDOWN")
            print("=" * 80)
            print("{:<25} {:>7} {:>7}  {}".format(
                "Method", "Count", "Pct", "Avg Confidence"
            ))
            print("{:<25} {:>7} {:>7}  {}".format(
                "------", "-----", "---", "--------------"
            ))

            # Sort by count (descending)
            sorted_methods = sorted(
                summary['method_stats'].items(),
                key=lambda x: x[1]['count'],
                reverse=True
            )

            for method, stats in sorted_methods:
                print("{:<25} {:>7} {:>6.1f}%  {}".format(
                    method,
                    stats['count'],
                    stats.get('percentage', 0.0),
                    stats.get('avg_confidence', 'UNKNOWN')
                ))

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
            - confidence: Strategy confidence (HIGH/MEDIUM/LOW)
            - extraction_outcome: Geometry extraction outcome
            - failure_reason: Reason for failure (if applicable)
            - extraction_method: Which extraction method succeeded (Phase 3.1)
            - method_attempted_order: Order of methods attempted (Phase 3.1)
        """
        import csv

        with open(filepath, 'w', newline='') as f:
            writer = csv.writer(f)

            # Write header (Phase 3.1: added extraction_method, method_attempted_order)
            writer.writerow([
                'element_id', 'category', 'classification', 'strategy_used',
                'confidence', 'extraction_outcome', 'failure_reason',
                'extraction_method', 'method_attempted_order'
            ])

            # Write element records
            for record in self.element_records:
                # Fallback: If method_attempted_order is not set, populate from element_attempts
                method_order = record.get('method_attempted_order')
                if not method_order:
                    elem_id = str(record['element_id'])
                    if elem_id in self.element_attempts and self.element_attempts[elem_id]:
                        method_order = ','.join(self.element_attempts[elem_id])
                    else:
                        method_order = ''

                writer.writerow([
                    str(record['element_id']),
                    str(record['category']),
                    str(record['classification']),
                    str(record['strategy_used']) if record['strategy_used'] else '',
                    str(record['confidence']) if record['confidence'] else '',
                    str(record['extraction_outcome']) if record['extraction_outcome'] else '',
                    str(record['failure_reason']) if record['failure_reason'] else '',
                    str(record['extraction_method']) if record['extraction_method'] else '',
                    method_order
                ])

    def export_category_summary_csv(self, filepath):
        """
        Export per-category summary statistics to CSV file (Phase 3.1).

        Args:
            filepath: Path to output CSV file (e.g., 'category_summary.csv')

        CSV columns:
            - category: Element category name
            - total: Total elements in category
            - success: Successful extractions
            - success_rate: Success rate (0.0 to 1.0)
            - planar_face: Count of planar_face method usage
            - geometry_polygon: Count of geometry_polygon method usage
            - silhouette: Count of silhouette method usage
            - bbox_obb: Count of bbox_obb method usage
            - aabb: Count of aabb method usage
            - high_conf: Count of HIGH confidence extractions
            - medium_conf: Count of MEDIUM confidence extractions
            - low_conf: Count of LOW confidence extractions
        """
        import os

        # Get summary to access category_method_stats
        summary = self.get_summary()
        category_stats = summary.get('category_method_stats', {})

        with open(filepath, 'w') as f:
            # Write header
            f.write('category,total,success,success_rate,planar_face,geometry_polygon,silhouette,bbox_obb,aabb,high_conf,medium_conf,low_conf\n')

            # Sort categories by total (descending)
            sorted_categories = sorted(
                category_stats.items(),
                key=lambda x: x[1]['total'],
                reverse=True
            )

            # Write category records
            for category, stats in sorted_categories:
                total = stats['total']
                success = stats['success']
                success_rate = stats['success_rate'] / 100.0  # Convert to 0.0-1.0 range

                # Get method counts (default to 0 if not present)
                methods = stats['methods']
                planar_face = methods.get('planar_face', 0)
                geometry_polygon = methods.get('geometry_polygon', 0)
                silhouette = methods.get('silhouette', 0)
                bbox_obb = methods.get('bbox_obb', 0)
                aabb = methods.get('aabb', 0)

                # Get confidence counts (default to 0 if not present)
                confidence = stats['confidence']
                high_conf = confidence.get('HIGH', 0)
                medium_conf = confidence.get('MEDIUM', 0)
                low_conf = confidence.get('LOW', 0)

                # Write row
                f.write('{},{},{},{:.3f},{},{},{},{},{},{},{},{}\n'.format(
                    category,
                    total,
                    success,
                    success_rate,
                    planar_face,
                    geometry_polygon,
                    silhouette,
                    bbox_obb,
                    aabb,
                    high_conf,
                    medium_conf,
                    low_conf
                ))
