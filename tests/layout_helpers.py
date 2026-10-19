"""Test helpers for the run file layout (vop_interwoven/output_layout.py)."""
from pathlib import Path

from vop_interwoven.output_layout import RUN_META_V1, run_meta_candidates


def run_meta_file(run_dir):
    """The run's one record file (run_meta.json or run_meta_<d>.json); for a
    directory that holds none yet, the v1 name, so a fixture can create one."""
    found = run_meta_candidates(run_dir)
    assert len(found) <= 1, found
    return Path(found[0]) if found else Path(run_dir) / RUN_META_V1
