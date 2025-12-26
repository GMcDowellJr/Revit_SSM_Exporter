#!/usr/bin/env python3
"""
Golden baseline comparison harness for SSM/VOP exporter.

Compares current run outputs against golden baseline artifacts to detect
unintended behavior changes (regressions).

Usage:
    python tools/compare_golden.py --golden tests/ssm_vop_v1 --current output/

Exit codes:
    0 - Outputs match (within tolerance)
    1 - Outputs differ (regression detected)
    2 - Error (missing files, invalid arguments, etc.)

Comparison rules:
- CSV: Exclude volatile columns (RunId, ElapsedSec, ConfigHash)
- CSV: Row order matters (must be deterministic)
- PNG: Exact pixel hash match required
- Bundle: Overall manifest hash match

Policy: See docs/golden_artifacts_rules.md for ordering semantics.
"""

import argparse
import csv
import hashlib
import os
import sys
from pathlib import Path


class Colors:
    """ANSI color codes for terminal output."""
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    RESET = '\033[0m'
    BOLD = '\033[1m'


def color(text, color_code):
    """Wrap text in ANSI color codes."""
    return f"{color_code}{text}{Colors.RESET}"


def sha256_file(file_path):
    """Compute SHA256 hash of a file."""
    h = hashlib.sha256()
    with open(file_path, 'rb') as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


def load_manifest(manifest_path):
    """
    Load golden manifest.sha256 file.

    Returns dict:
        {
            'csvs': {filename: hash},
            'pngs': {filename: hash},
            'bundle': hash
        }
    """
    manifest = {'csvs': {}, 'pngs': {}, 'bundle': None}

    if not os.path.exists(manifest_path):
        return manifest

    with open(manifest_path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            parts = line.split()
            if len(parts) < 3:
                continue

            file_type = parts[0]
            filename = parts[1]
            file_hash = parts[2]

            if file_type == 'CSV':
                manifest['csvs'][filename] = file_hash
            elif file_type == 'PNG':
                manifest['pngs'][filename] = file_hash
            elif file_type == 'BUNDLE':
                manifest['bundle'] = file_hash

    return manifest


def normalize_csv_for_comparison(csv_path, exclude_columns=None):
    """
    Load CSV and normalize for comparison.

    Args:
        csv_path: Path to CSV file
        exclude_columns: List of column names to exclude (e.g., ['RunId', 'ElapsedSec'])

    Returns:
        List of dicts (rows), with excluded columns removed
    """
    exclude_columns = exclude_columns or []

    with open(csv_path, 'r', newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = []
        for row in reader:
            # Remove excluded columns
            filtered_row = {k: v for k, v in row.items() if k not in exclude_columns}
            rows.append(filtered_row)

    return rows


def compare_csv_files(current_path, expected_hash, exclude_columns=None):
    """
    Compare CSV file against expected hash after normalizing.

    Args:
        current_path: Path to current CSV file
        expected_hash: Expected SHA256 hash
        exclude_columns: Columns to exclude before hashing

    Returns:
        (bool, list of differences)
    """
    exclude_columns = exclude_columns or []

    try:
        # Normalize CSV (remove excluded columns, stable ordering)
        rows = normalize_csv_for_comparison(current_path, exclude_columns)

        # Convert to stable string representation for hashing
        csv_data = []
        if rows:
            # Get headers from first row
            headers = sorted(rows[0].keys())
            csv_data.append(','.join(headers))

            # Add data rows
            for row in rows:
                values = [str(row.get(h, '')) for h in headers]
                csv_data.append(','.join(values))

        normalized_content = '\n'.join(csv_data).encode('utf-8')
        current_hash = hashlib.sha256(normalized_content).hexdigest()

    except Exception as e:
        return False, [f"Error processing CSV: {e}"]

    if current_hash == expected_hash:
        return True, []
    else:
        return False, [
            f"Hash mismatch:\n"
            f"  Expected: {expected_hash}\n"
            f"  Current:  {current_hash}"
        ]


def compare_png_files(golden_dir, current_dir, manifest):
    """
    Compare PNG files by hash.

    Returns:
        (bool, list of differences)
    """
    differences = []

    for png_name, golden_hash in manifest['pngs'].items():
        current_path = os.path.join(current_dir, 'occupancy', png_name)

        if not os.path.exists(current_path):
            differences.append(f"Missing PNG: {png_name}")
            continue

        current_hash = sha256_file(current_path)

        if current_hash != golden_hash:
            differences.append(
                f"PNG hash mismatch: {png_name}\n"
                f"  Golden:  {golden_hash}\n"
                f"  Current: {current_hash}"
            )

    if differences:
        return False, differences

    return True, []


def main():
    parser = argparse.ArgumentParser(
        description='Compare SSM/VOP exporter outputs against golden baseline'
    )
    parser.add_argument(
        '--golden',
        required=True,
        help='Path to golden baseline directory (e.g., tests/ssm_vop_v1)'
    )
    parser.add_argument(
        '--current',
        required=True,
        help='Path to current output directory'
    )
    parser.add_argument(
        '--exclude-columns',
        default='RunId,ElapsedSec,ConfigHash',
        help='Comma-separated list of CSV columns to exclude from comparison'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Show detailed output'
    )

    args = parser.parse_args()

    golden_dir = Path(args.golden)
    current_dir = Path(args.current)
    exclude_columns = [c.strip() for c in args.exclude_columns.split(',') if c.strip()]

    # Validate paths
    if not golden_dir.exists():
        print(color(f"ERROR: Golden directory not found: {golden_dir}", Colors.RED))
        return 2

    if not current_dir.exists():
        print(color(f"ERROR: Current directory not found: {current_dir}", Colors.RED))
        return 2

    # Load manifest
    manifest_path = golden_dir / 'manifest.sha256'
    if not manifest_path.exists():
        print(color(f"ERROR: Manifest not found: {manifest_path}", Colors.RED))
        return 2

    manifest = load_manifest(manifest_path)

    if args.verbose:
        print(f"Golden baseline: {golden_dir}")
        print(f"Current output: {current_dir}")
        print(f"Excluded columns: {exclude_columns}")
        print()

    # Track results
    all_passed = True
    total_checks = 0
    passed_checks = 0

    # Compare CSVs
    print(color("Comparing CSVs...", Colors.BOLD))
    for csv_name, expected_hash in manifest['csvs'].items():
        total_checks += 1

        # Current CSVs should be in current_dir
        current_csv = current_dir / csv_name

        if not current_csv.exists():
            print(color(f"  ✗ {csv_name}: Current CSV not found", Colors.RED))
            all_passed = False
            continue

        # Compare by normalized hash
        match, diffs = compare_csv_files(current_csv, expected_hash, exclude_columns)

        if match:
            print(color(f"  ✓ {csv_name}: Match", Colors.GREEN))
            passed_checks += 1
        else:
            print(color(f"  ✗ {csv_name}: Differs", Colors.RED))
            if args.verbose and diffs:
                for diff in diffs:
                    print(f"      {diff}")
            all_passed = False

    print()

    # Compare PNGs
    print(color("Comparing PNGs...", Colors.BOLD))
    match, diffs = compare_png_files(golden_dir, current_dir, manifest)
    total_checks += len(manifest['pngs'])

    if match:
        print(color(f"  ✓ All {len(manifest['pngs'])} PNGs match", Colors.GREEN))
        passed_checks += len(manifest['pngs'])
    else:
        print(color(f"  ✗ PNG differences detected", Colors.RED))
        if args.verbose and diffs:
            for diff in diffs:
                print(f"      {diff}")
        all_passed = False

    print()

    # Summary
    print(color("=" * 60, Colors.BOLD))
    if all_passed:
        print(color(f"✓ ALL CHECKS PASSED ({passed_checks}/{total_checks})", Colors.GREEN))
        print()
        print("Outputs match golden baseline.")
        print("No regressions detected.")
        return 0
    else:
        print(color(f"✗ REGRESSION DETECTED ({passed_checks}/{total_checks} passed)", Colors.RED))
        print()
        print("Current outputs differ from golden baseline.")
        print("Review differences above.")
        if not args.verbose:
            print("Run with --verbose for detailed differences.")
        return 1


if __name__ == '__main__':
    sys.exit(main())
