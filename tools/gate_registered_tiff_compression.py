#!/usr/bin/env python3
"""Gate: the registered TIFF's lossless compression changes nothing downstream.

Copies one run's captures into two scratch runs, registers both -- one with
the raw writer the registration tool used before (``format="TIFF"``, no
compression), one with the tool's own lossless writer -- grids and rolls up
both, and reports:

  B-G1  compression tag of every registered TIFF the tool wrote
        (8 or 32946 deflate, 5 LZW, 1 raw), as counts;
  B-G2  views whose DECODED registered pixels differ from the raw write's
        (shape, dtype, values -- not bytes);
  B-G3  views whose grid .npz arrays differ, and roll-up CSV rows that
        differ (columns naming a path or a hash excluded: they differ by
        construction);
  B-G4  median and maximum registered TIFF size, raw vs compressed, and the
        count where compressed >= raw.

The run itself is never written. The scratch runs hold a raw registered TIFF
per view (~240 MB each on a large view), so point --work at a disk with room.

USAGE
    python tools/gate_registered_tiff_compression.py <run dir> [--work DIR]

Exit 0 when B-G1..B-G3 hold, 1 when any does not, 2 when nothing registered.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import statistics
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import register_stage_a_annotation as reg  # noqa: E402
from tools import registration_marks as rm  # noqa: E402
from tools import stage_a_grid as grid  # noqa: E402
from tools import stage_a_grid_rollup as rollup  # noqa: E402
from vop_interwoven import output_layout  # noqa: E402


def _cap(run):
    """The run's capture folder (either layout); None when it has none."""
    try:
        return grid.model_sidecars(run)[1] if any(
            (Path(run) / n).is_dir() for n in output_layout.CAPTURE_DIR.values()) else None
    except grid.GridRefusal:
        return None


def _grid(run):
    return Path(output_layout.grid_dir_for_capture(_cap(run)))


def _registered_tiffs(run):
    cap = _cap(run)
    infix = output_layout.REGISTERED_INFIX[output_layout.layout_of_capture_dir(cap)]
    return dict((p.name, p) for p in cap.glob("*_anno" + infix + ".tiff"))


PATHLIKE = ("path", "sha256", "dir")
# The compression tags the registration tool itself accepts -- derived from
# it, never a second list that could disagree (Codex, PR #230).
ACCEPTED_TAGS = frozenset(v for values in reg.LOSSLESS_TAG_VALUES.values()
                          for v in values)


def raw_writer(pixels, path, codecs=None):
    """The registration tool's writer before compression, verbatim."""
    Image.fromarray(pixels).save(str(path), format="TIFF")
    return "raw"


def scratch_run(run, dest):
    """``dest`` as a copy of ``run``'s captures and run records, with every
    sidecar's recorded TIFF paths re-pointed into the copy."""
    src_cap = _cap(run)
    cap = dest / src_cap.name
    cap.mkdir(parents=True)
    records = set(Path(p).name for p in output_layout.run_meta_candidates(run))
    for path in Path(run).iterdir():
        if path.is_file() and (path.name in records
                               or path.name.startswith("views_core_")):
            shutil.copy(str(path), str(dest / path.name))
    for path in src_cap.iterdir():
        name = path.name
        if (not path.is_file() or ".registered." in name or ".reg." in name
                or ".decoded." in name):
            continue
        if name.endswith(".json"):
            side = json.loads(path.read_text(encoding="utf-8"))
            for key in ("tiff_path", "model_pass_tiff_path"):
                if side.get(key):
                    side[key] = str(cap / Path(side[key].replace("\\", "/")).name)
            (cap / name).write_text(json.dumps(side), encoding="utf-8")
        elif name.endswith((".tiff", ".tif")):
            shutil.copy(str(path), str(cap / name))
    return dest


def register_all(run, writer):
    original = reg.write_lossless_tiff
    reg.write_lossless_tiff = writer
    try:
        for anno in sorted(_cap(run).glob("*" + reg.ANNO_SUFFIX + ".json")):
            reg.register(anno)
    finally:
        reg.write_lossless_tiff = original
    grid.main([str(run)])
    rollup.main([str(run)])


def rollup_rows(run):
    path = _grid(run) / rollup.output_names(_cap(run))[0]
    if not path.exists():
        return None
    with open(str(path), newline="", encoding="utf-8") as handle:
        return [dict((k, v) for k, v in r.items() if not any(s in k for s in PATHLIKE))
                for r in csv.DictReader(handle)]


def npz_equal(a, b):
    da, db = dict(np.load(str(a))), dict(np.load(str(b)))
    return sorted(da) == sorted(db) and all(
        da[k].dtype == db[k].dtype and np.array_equal(da[k], db[k]) for k in da)


def gate(run, work):
    raw_run = scratch_run(run, work / "raw")
    packed_run = scratch_run(run, work / "packed")
    register_all(raw_run, raw_writer)
    register_all(packed_run, reg.write_lossless_tiff)

    raw_tiffs = _registered_tiffs(raw_run)
    tiffs = _registered_tiffs(packed_run)
    names = sorted(set(raw_tiffs) | set(tiffs))
    tags, mismatches, sizes = {}, [], []
    for name in names:
        if name not in raw_tiffs or name not in tiffs:
            mismatches.append(name + " (registered in one run only)")
            continue
        with Image.open(str(tiffs[name])) as image:
            tag = image.tag_v2.get(reg.TIFF_COMPRESSION_TAG)
        tags[tag] = tags.get(tag, 0) + 1
        a, b = rm.load_rgb(raw_tiffs[name]), rm.load_rgb(tiffs[name])
        if not (a.shape == b.shape and a.dtype == b.dtype and np.array_equal(a, b)):
            mismatches.append(name)
        sizes.append((raw_tiffs[name].stat().st_size, tiffs[name].stat().st_size))

    npz_diff = []
    raw_npz = dict((p.name, p) for p in _grid(raw_run).glob("*.grid.npz"))
    for p in sorted(_grid(packed_run).glob("*.grid.npz")):
        if p.name not in raw_npz or not npz_equal(raw_npz[p.name], p):
            npz_diff.append(p.name)
    npz_diff += sorted(set(raw_npz) - set(
        p.name for p in _grid(packed_run).glob("*.grid.npz")))
    raw_rows, rows = rollup_rows(raw_run), rollup_rows(packed_run)
    row_diff = (None if raw_rows is None or rows is None
                else sum(1 for a, b in zip(raw_rows, rows) if a != b)
                + abs(len(raw_rows) - len(rows)))

    raw_sizes, packed_sizes = [s[0] for s in sizes], [s[1] for s in sizes]
    return {
        "views_registered": len(sizes),
        "B-G1_compression_tags": tags,
        "B-G1_raw_count": tags.get(1, 0),
        "B-G2_pixel_mismatches": mismatches,
        "B-G3_npz_differences": npz_diff,
        "B-G3_npz_compared": len(raw_npz),
        "B-G3_rollup_rows": None if rows is None else len(rows),
        "B-G3_rollup_row_differences": row_diff,
        "B-G4_raw_bytes": {"median": statistics.median(raw_sizes) if raw_sizes else None,
                           "max": max(raw_sizes) if raw_sizes else None},
        "B-G4_compressed_bytes": {"median": statistics.median(packed_sizes) if packed_sizes else None,
                                  "max": max(packed_sizes) if packed_sizes else None},
        "B-G4_compressed_not_smaller": sum(1 for r, c in sizes if c >= r),
    }


def passes(result):
    """B-G1..B-G3 hold: every tag one the tool accepts, no raw, no pixel,
    array or roll-up difference."""
    return (result["B-G1_raw_count"] == 0
            and set(result["B-G1_compression_tags"]) <= ACCEPTED_TAGS
            and not result["B-G2_pixel_mismatches"]
            and not result["B-G3_npz_differences"]
            and result["B-G3_rollup_row_differences"] == 0)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run", help="a run directory (holding color_id/ or color_id_buffer/)")
    ap.add_argument("--work", default=None,
                    help="scratch directory (default: a new temporary one, kept)")
    args = ap.parse_args(argv)
    run = Path(args.run)
    if _cap(run) is None:
        ap.error("{0} holds no capture folder".format(run))
    work = Path(args.work) if args.work else Path(tempfile.mkdtemp(prefix="tiff_gate_"))
    work.mkdir(parents=True, exist_ok=True)
    result = gate(run, work)
    result["work"] = str(work)
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    if not result["views_registered"]:
        return 2
    return 0 if passes(result) else 1


if __name__ == "__main__":
    sys.exit(main())
