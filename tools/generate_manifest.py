#!/usr/bin/env python3
"""
Generate golden baseline manifest from SSM/VOP exporter outputs.

This script creates a manifest.sha256 file containing hashes of CSV and PNG
outputs for golden baseline regression testing.

Usage:
    python tools/generate_manifest.py --output-dir path/to/outputs --manifest path/to/manifest.sha256

The manifest excludes volatile columns (RunId, ElapsedSec, ConfigHash) from
CSV hashes to allow deterministic comparison across runs.

Output manifest format:
    CSV  filename.csv  <sha256 hash>
    PNG  filename.png  <sha256 hash>
    BUNDLE  manifest  <combined hash>
"""

import argparse
import csv
import hashlib
import os
import sys
from pathlib import Path


def normalize_csv_for_hashing(csv_path, exclude_columns=None):
    """
    Normalize CSV content for hashing by excluding volatile columns.

    Args:
        csv_path: Path to CSV file
        exclude_columns: List of column names to exclude

    Returns:
        Normalized CSV content as bytes
    """
    exclude_columns = exclude_columns or []

    with open(csv_path, 'r', newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = []
        for row in reader:
            # Remove excluded columns
            filtered_row = {k: v for k, v in row.items() if k not in exclude_columns}
            rows.append(filtered_row)

    # Convert to stable string representation
    csv_data = []
    if rows:
        # Get headers in sorted order for stability
        headers = sorted(rows[0].keys())
        csv_data.append(','.join(headers))

        # Add data rows
        for row in rows:
            values = [str(row.get(h, '')) for h in headers]
            csv_data.append(','.join(values))

    return '\n'.join(csv_data).encode('utf-8')


def sha256_file(file_path):
    """Compute SHA256 hash of a file."""
    h = hashlib.sha256()
    with open(file_path, 'rb') as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


def sha256_content(content):
    """Compute SHA256 hash of content bytes."""
    return hashlib.sha256(content).hexdigest()


def generate_manifest(output_dir, exclude_columns=None):
    """
    Generate manifest entries from output directory.

    Args:
        output_dir: Path to directory containing CSVs and PNGs
        exclude_columns: List of CSV columns to exclude from hashing

    Returns:
        List of (type, filename, hash) tuples
    """
    exclude_columns = exclude_columns or []
    output_dir = Path(output_dir)
    entries = []

    # Find CSV files
    for csv_file in sorted(output_dir.glob('views_*.csv')):
        try:
            normalized_content = normalize_csv_for_hashing(csv_file, exclude_columns)
            file_hash = sha256_content(normalized_content)
            entries.append(('CSV', csv_file.name, file_hash))
            print(f"Hashed CSV: {csv_file.name}")
        except Exception as e:
            print(f"Warning: Failed to hash {csv_file.name}: {e}")

    # Find PNG files in occupancy subdirectory
    png_dir = output_dir / 'occupancy'
    if png_dir.exists():
        for png_file in sorted(png_dir.glob('VOP_occ_*.png')):
            try:
                file_hash = sha256_file(png_file)
                entries.append(('PNG', png_file.name, file_hash))
                print(f"Hashed PNG: {png_file.name}")
            except Exception as e:
                print(f"Warning: Failed to hash {png_file.name}: {e}")

    # Compute bundle hash (hash of all individual hashes)
    if entries:
        bundle_content = '\n'.join(f"{t} {f} {h}" for t, f, h in entries).encode('utf-8')
        bundle_hash = sha256_content(bundle_content)
        entries.append(('BUNDLE', 'manifest', bundle_hash))

    return entries


def write_manifest(entries, output_path):
    """
    Write manifest entries to file.

    Format:
        CSV  filename.csv  <hash>
        PNG  filename.png  <hash>
        BUNDLE  manifest  <hash>
    """
    with open(output_path, 'w') as f:
        for entry_type, filename, file_hash in entries:
            f.write(f"{entry_type}  {filename}  {file_hash}\n")


def main():
    parser = argparse.ArgumentParser(
        description='Generate golden baseline manifest from exporter outputs'
    )
    parser.add_argument(
        '--output-dir',
        required=True,
        help='Path to directory containing CSV and PNG outputs'
    )
    parser.add_argument(
        '--manifest',
        required=True,
        help='Path to output manifest.sha256 file'
    )
    parser.add_argument(
        '--exclude-columns',
        default='RunId,ElapsedSec,ConfigHash',
        help='Comma-separated list of CSV columns to exclude from hashing (default: RunId,ElapsedSec,ConfigHash)'
    )

    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    manifest_path = Path(args.manifest)
    exclude_columns = [c.strip() for c in args.exclude_columns.split(',') if c.strip()]

    # Validate output directory
    if not output_dir.exists():
        print(f"ERROR: Output directory not found: {output_dir}")
        return 1

    print(f"Generating manifest from: {output_dir}")
    print(f"Excluding columns: {exclude_columns}")
    print()

    # Generate manifest
    try:
        entries = generate_manifest(output_dir, exclude_columns)
    except Exception as e:
        print(f"ERROR: Failed to generate manifest: {e}")
        return 1

    if not entries:
        print("ERROR: No CSV or PNG files found")
        return 1

    # Write manifest
    try:
        write_manifest(entries, manifest_path)
        print()
        print(f"✓ Manifest written to: {manifest_path}")
        print(f"  Total entries: {len(entries)-1} files + 1 bundle hash")

        # Show bundle hash
        bundle_entry = [e for e in entries if e[0] == 'BUNDLE'][0]
        print(f"  Bundle hash: {bundle_entry[2]}")

        return 0
    except Exception as e:
        print(f"ERROR: Failed to write manifest: {e}")
        return 1


if __name__ == '__main__':
    sys.exit(main())
