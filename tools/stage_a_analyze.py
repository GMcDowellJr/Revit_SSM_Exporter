#!/usr/bin/env python3
"""Run the Stage A analysis layer end to end, in order.

    python tools/stage_a_analyze.py <run> [<run> ...] [--out DIR] [--index-root EXPORTS]

Per run:
  1. register   -- register_stage_a_annotation over the run's capture folder
  2. grid       -- stage_a_grid
  3. derive     -- stage_a_kinds derive
Then, across all runs:
  4. inventory  -- stage_a_kinds inventory
  5. rollup     -- stage_a_grid_rollup
Then, per run again:
  6. census     -- stage_a_element_census on the run alone: element_census_<d>.csv
                   and .summary.json in the run's grid folder
Then, once per model folder (each run folder's parent):
  7. pairs      -- stage_a_element_census --root <model folder> --focus <runs>:
                   the analysed runs' pairs against their predecessors, and
                   their successors' pairs against them (a run backfilled
                   between two), computing only what is missing or stale.
                   Only those runs' censuses are checked -- the analysed
                   run's, its predecessor's (computed if missing) and its
                   successor's -- never every run in the folder: the series
                   and the predecessors are still worked out over the whole
                   folder, which hashes nothing. A run outside them is not
                   checked; `--root` alone is the full sweep. A sibling not
                   finalized, or with no grid record, is skipped and nothing
                   is written into it (census 2.1.1). Step 6 rewrites the
                   named run's census every time; its pairs are recomputed
                   only when what they read of it (the CSV, the view ids)
                   changed.
Then, per run again:
  8. timing     -- stage_a_timing_report: views_timing_<date or override>.csv / .json,
                   named as views_core is and written beside it
Then, only with ``--index-root``:
  9. index      -- stage_a_runs_index --root <export root>, so the BI sees the new
                   census and transitions. Its exit 1 (some run anywhere under
                   the root not finalized or without a roll-up) is reported, not
                   counted: it is a fact about other runs, not this analysis.

Each stage is the tool's own ``main()``, called as it would be from the
command line: nothing is reimplemented here, and every stage prints its own
lines. A view a stage refuses (exit 1) does not stop the run -- the later
stages see the refusal and the roll-up reports it. A stage that cannot run
at all (exit 2: nothing found, bad arguments, a broken invariant) stops
everything after it, because what follows would read missing or partial
inputs. The timing report reads only run_meta, views_core and the model
sidecars -- nothing the other stages write -- so it runs last, and a run it
cannot report (exit 2: no views_core, say) costs nothing else.

``--out`` gathers the inventory and roll-up of every run into one place.
Without it each run gets its own, in its own grid folder (``grid/`` in a v2
run, ``analysis_grid/`` in v1: vop_interwoven/output_layout), as each tool
does by default.

Exit: the worst stage exit code (0 every stage clean, 1 some view refused or
not gridded, 2 a stage could not run).

PARALLEL (``--workers N``). Register, grid and derive are one job per view
(tools/stage_a_jobs.py); with N > 1 each stage's jobs run on N spawned
processes, largest first, the estimated peak bytes of the jobs running at
once capped at ``--max-mem-gb``. Every printed line comes from the job's
result, in the serial order, so the output and exit codes are the serial
run's; ``--workers 1`` calls each tool's own main(), as before. The stages
still run in order -- every view registered before any is gridded -- so a
stage reads exactly what it would serially.

Default: ``min(4, CPU count)`` workers and half the physical memory, when
the memory can be read; otherwise 1 worker. Memory, not cores, is what
limits this: one 100 Mpx view at 2.56 px per cell estimates at ~7 GB for
grid and ~11 GB for derive (MEMORY_MODEL), so the cap -- not the worker
count -- decides how many such views run at once, and a view larger than
the cap runs alone.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import register_stage_a_annotation as reg  # noqa: E402
from tools import stage_a_element_census as census  # noqa: E402
from tools import stage_a_grid as grid  # noqa: E402
from tools import stage_a_grid_rollup as rollup  # noqa: E402
from tools import stage_a_jobs as jobs  # noqa: E402
from tools.stage_a_sidecar_shapes import frame_record  # noqa: E402
from tools import stage_a_kinds as kinds  # noqa: E402
from tools import stage_a_runs_index as runs_index  # noqa: E402
from tools import stage_a_timing_report as timing  # noqa: E402
from vop_interwoven import output_layout  # noqa: E402

STAGES = ("register", "grid", "derive", "inventory", "rollup", "census", "pairs", "timing")
# Run only when asked for (--index-root).
OPTIONAL_STAGES = ("index",)


# Peak bytes per job, calibrated on measured peaks (ru_maxrss, a fresh process
# per stage) of six synthetic views: 2000^2, 4000^2, 6000^2, 8000^2 at 2.56
# px per cell, 8000^2 at 6 and 4000^2 at 18 (tests/stage_a_bench.py,
# 2026-10-22). Each is the least-squares fit ROUNDED UP, so every estimate
# was at or above its measured peak on all six:
#   register ~ 70 MB + 14.9 B x (model + annotation px)
#   grid     ~ 96 MB + 19.7 B x (model + canvas px) + 190 B x cells
#   derive   ~ 143 MB + 604 B x cells (worst view 709 B per cell)
MB = 1 << 20
MEMORY_MODEL = {
    "register": {"base": 75 * MB, "per_px": 16},
    "grid": {"base": 100 * MB, "per_px": 20, "per_cell": 200},
    "derive": {"base": 150 * MB, "per_cell": 720},
}


class Unmeasured(Exception):
    """A job's size cannot be read; it is scheduled to run alone."""


def _image_size(sidecar_path, sidecar):
    """``(w, h)`` from the sidecar's frame, else the TIFF's header (Pillow
    reads the header on open; nothing is decoded)."""
    frame = frame_record(sidecar)
    w, h = frame.get("actual_w"), frame.get("actual_h")
    if isinstance(w, int) and isinstance(h, int) and w > 0 and h > 0:
        return w, h
    from PIL import Image
    with Image.open(str(reg.resolve_tiff(sidecar_path, sidecar))) as image:
        return image.size


def _image_px(sidecar_path, sidecar):
    w, h = _image_size(sidecar_path, sidecar)
    return w * h


def _load(path):
    try:
        with open(str(path), encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as ex:
        raise Unmeasured("{0}: {1}".format(path, ex))


def estimate_register(anno_path):
    anno = _load(anno_path)
    model_path = reg.model_sidecar_for(anno_path, anno)
    if model_path is None:
        raise Unmeasured("no model sidecar for {0}".format(anno_path))
    try:
        px = _image_px(anno_path, anno) + _image_px(model_path, _load(model_path))
    except (OSError, ValueError) as ex:
        raise Unmeasured(str(ex))
    m = MEMORY_MODEL["register"]
    return m["base"] + m["per_px"] * px


def estimate_grid(model_path, run_meta):
    """Model and registered-canvas pixels, and the cells their union spans
    at the view's cell size (cell_size_paper_in x view scale / 12 over the
    model image's px per ft across its crop)."""
    sidecar = _load(model_path)
    try:
        w, h = _image_size(model_path, sidecar)
    except (OSError, ValueError) as ex:
        raise Unmeasured(str(ex))
    model_px = w * h
    canvas_px = model_px
    record = Path(output_layout.registered_paths(
        Path(model_path).with_name(Path(model_path).stem + "_anno.json"))[1])
    if record.exists():
        lattice = _load(record).get("lattice") or {}
        if lattice.get("canvas_w") and lattice.get("canvas_h"):
            canvas_px = int(lattice["canvas_w"]) * int(lattice["canvas_h"])
    frame = frame_record(sidecar)
    crop, scale = frame.get("crop_uv"), frame.get("view_scale")
    cell_in = ((run_meta or {}).get("config") or {}).get("cell_size_paper_in")
    if not (crop and len(crop) == 4 and scale and cell_in and crop[2] > crop[0]):
        raise Unmeasured("{0}: no crop, view scale or cell size".format(model_path))
    px_per_cell = w / (float(crop[2]) - float(crop[0])) * float(cell_in) * float(scale) / 12.0
    cells = max(model_px, canvas_px) / max(px_per_cell, 1e-9) ** 2
    m = MEMORY_MODEL["grid"]
    return int(m["base"] + m["per_px"] * (model_px + canvas_px) + m["per_cell"] * cells)


def estimate_derive(grid_json):
    g = _load(grid_json).get("grid") or {}
    cells = int(g.get("cells_w") or 0) * int(g.get("cells_h") or 0)
    m = MEMORY_MODEL["derive"]
    return m["base"] + m["per_cell"] * cells


def physical_memory_bytes():
    """The machine's physical memory, or None where it cannot be read."""
    if sys.platform == "win32":
        import ctypes

        class _Status(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        status = _Status()
        status.dwLength = ctypes.sizeof(_Status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.ullTotalPhys)
        return None
    try:
        return int(os.sysconf("SC_PAGE_SIZE")) * int(os.sysconf("SC_PHYS_PAGES"))
    except (ValueError, OSError, AttributeError):
        return None


def _estimated(plan, estimator, cap):
    """Each job's estimate; an unmeasurable one is ``cap`` (it runs alone),
    and named."""
    notes = []
    for job in plan.jobs:
        try:
            job.estimate = estimator(job.args[0])
        except Unmeasured as ex:
            job.estimate = cap
            notes.append("  size unknown, run alone: {0}".format(ex))
    return notes


def _stage_jobs(name, label, plan, estimator, workers, cap, results):
    """``_stage`` for a planned per-view stage, on the pool."""
    print("== {0}: {1}".format(name, label))
    start = time.perf_counter()
    if plan.early is None:
        for note in _estimated(plan, estimator, cap):
            print(note)
    code = int(jobs.run_pool(plan, workers, cap) or 0)
    results.append({"stage": name, "target": label, "exit": code,
                    "seconds": time.perf_counter() - start})
    return code


def _stage(name, label, fn, argv, results):
    print("== {0}: {1}".format(name, label))
    start = time.perf_counter()
    try:
        code = fn([str(a) for a in argv])
    except SystemExit as ex:          # argparse errors: a stage that cannot run
        code = ex.code if isinstance(ex.code, int) else 2
    code = int(code or 0)
    results.append({"stage": name, "target": label, "exit": code,
                    "seconds": time.perf_counter() - start})
    return code


def analyze(runs, out=None, index_root=None, workers=1, max_bytes=None):
    """Run every stage in order; returns ``(worst exit code, per-stage
    results)``. Stops at the first stage that exits 2. ``workers`` > 1 runs
    register, grid and derive per view on a pool (module docstring)."""
    runs = [Path(r) for r in runs]
    results = []
    run_dirs = []
    for run in runs:
        _sidecars, folder = grid.model_sidecars(run)
        run_dirs.append(folder.parent if folder.name in output_layout.CAPTURE_DIR.values()
                        else folder)
        if workers <= 1:
            for name, fn, argv in (("register", reg.main, [folder]),
                                   ("grid", grid.main, [run]),
                                   ("derive", kinds.main, ["derive", run])):
                if _stage(name, str(run), fn, argv, results) >= 2:
                    return 2, results
            continue
        cap = max_bytes if max_bytes is not None else float("inf")
        reg_plan = reg.plan([str(folder)])
        if _stage_jobs("register", str(run), reg_plan, estimate_register, workers, cap,
                       results) >= 2:
            return 2, results
        grid_plan = grid.plan(str(run))
        run_meta = grid_plan.jobs[0].args[2] if grid_plan.jobs else None
        if _stage_jobs("grid", str(run), grid_plan,
                       lambda path: estimate_grid(path, run_meta), workers, cap,
                       results) >= 2:
            return 2, results
        if _stage_jobs("derive", str(run), kinds.derive_plan([str(run)]), estimate_derive,
                       workers, cap, results) >= 2:
            return 2, results
    out_args = ["--out", out] if out is not None else []
    for name, fn, argv in (("inventory", kinds.main, ["inventory"] + runs + out_args),
                           ("rollup", rollup.main, runs + out_args)):
        if _stage(name, "all runs", fn, argv, results) >= 2:
            return 2, results
    # The census reads the grids and the roll-up's own rows, never what the
    # inventory or roll-up wrote, so a run it refuses (exit 2) stops nothing.
    for run, run_dir in zip(runs, run_dirs):
        _stage("census", str(run), census.main, [run_dir], results)
    # Once per model folder, in the order first named: the folder gives the
    # series, the analysed runs in it are the focus.
    models = {}
    for run_dir in run_dirs:
        run_dir = Path(run_dir).resolve()
        models.setdefault(run_dir.parent, []).append(run_dir)
    for model, focused in models.items():
        _stage("pairs", str(model), census.main,
               ["--root", model, "--focus"] + focused, results)
    for run, run_dir in zip(runs, run_dirs):
        _stage("timing", str(run), timing.main, [run_dir], results)
    worst = max(r["exit"] for r in results)
    if index_root is not None:
        code = _stage("index", str(index_root), runs_index.main, ["--root", index_root],
                      results)
        worst = max(worst, code if code >= 2 else 0)
    return worst, results


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("runs", nargs="+", metavar="run",
                    help="a run directory or its capture folder")
    ap.add_argument("--out", default=None,
                    help="one combined inventory and roll-up of every run, here "
                         "(default: each run's own, in its own grid folder)")
    ap.add_argument("--index-root", default=None,
                    help="the export root: rebuild runs_index there once the census "
                         "and pairs are written (default: not rebuilt)")
    ap.add_argument("--workers", type=int, default=None,
                    help="processes for register, grid and derive (default: min(4, "
                         "CPU count) when physical memory can be read, else 1; 1 runs "
                         "each tool's own serial loop)")
    ap.add_argument("--max-mem-gb", type=float, default=None,
                    help="cap on the estimated peak memory of the jobs running at once "
                         "(default: half the physical memory)")
    args = ap.parse_args(argv)
    physical = physical_memory_bytes()
    workers = args.workers
    if workers is None:
        workers = min(4, os.cpu_count() or 1) if physical else 1
    if workers < 1:
        ap.error("--workers must be at least 1")
    max_bytes = (int(args.max_mem_gb * (1 << 30)) if args.max_mem_gb is not None
                 else physical // 2 if physical else None)
    # One line at a time, so a piped run (Tee-Object, a timestamping pipe)
    # sees each line when it is printed, not when a block buffer fills.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    if workers > 1:
        print("== workers: {0}, memory cap {1}".format(
            workers, "none" if max_bytes is None else "{0:.1f} GB".format(
                max_bytes / float(1 << 30))))
    code, results = analyze(args.runs, args.out, args.index_root, workers, max_bytes)
    print("== summary")
    for r in results:
        print("  {0:<10} exit {1}  {2}  ({3:.1f} s)".format(
            r["stage"], r["exit"], r["target"], r["seconds"]))
    print("  total      {0:.1f} s".format(sum(r["seconds"] for r in results)))
    done = set(r["stage"] for r in results)
    skipped = [s for s in STAGES if s not in done]
    if skipped:
        print("  not run (an earlier stage could not run): {0}".format(", ".join(skipped)))
    return code


if __name__ == "__main__":
    sys.exit(main())
