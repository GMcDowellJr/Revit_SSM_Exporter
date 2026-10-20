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
  7. pairs      -- stage_a_element_census --root <model folder>: the transitions of
                   every run there against its predecessor, computing only what
                   is missing or stale -- for a new run, its own pair (and the
                   next run's, when it was backfilled between two). A sibling
                   whose census is missing or stale is computed too, so the
                   first analysis of a model folder never run through the
                   driver costs every run's census.
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
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import register_stage_a_annotation as reg  # noqa: E402
from tools import stage_a_element_census as census  # noqa: E402
from tools import stage_a_grid as grid  # noqa: E402
from tools import stage_a_grid_rollup as rollup  # noqa: E402
from tools import stage_a_kinds as kinds  # noqa: E402
from tools import stage_a_runs_index as runs_index  # noqa: E402
from tools import stage_a_timing_report as timing  # noqa: E402
from vop_interwoven import output_layout  # noqa: E402

STAGES = ("register", "grid", "derive", "inventory", "rollup", "census", "pairs", "timing")
# Run only when asked for (--index-root).
OPTIONAL_STAGES = ("index",)


def _stage(name, label, fn, argv, results):
    print("== {0}: {1}".format(name, label))
    try:
        code = fn([str(a) for a in argv])
    except SystemExit as ex:          # argparse errors: a stage that cannot run
        code = ex.code if isinstance(ex.code, int) else 2
    code = int(code or 0)
    results.append({"stage": name, "target": label, "exit": code})
    return code


def analyze(runs, out=None, index_root=None):
    """Run every stage in order; returns ``(worst exit code, per-stage
    results)``. Stops at the first stage that exits 2."""
    runs = [Path(r) for r in runs]
    results = []
    run_dirs = []
    for run in runs:
        _sidecars, folder = grid.model_sidecars(run)
        run_dirs.append(folder.parent if folder.name in output_layout.CAPTURE_DIR.values()
                        else folder)
        for name, fn, argv in (("register", reg.main, [folder]),
                               ("grid", grid.main, [run]),
                               ("derive", kinds.main, ["derive", run])):
            if _stage(name, str(run), fn, argv, results) >= 2:
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
    # The pairs need every run of the model folder, not only these: once per
    # folder, in the order first named.
    models = []
    for run_dir in run_dirs:
        model = Path(run_dir).resolve().parent
        if model not in models:
            models.append(model)
    for model in models:
        _stage("pairs", str(model), census.main, ["--root", model], results)
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
    args = ap.parse_args(argv)
    code, results = analyze(args.runs, args.out, args.index_root)
    print("== summary")
    for r in results:
        print("  {0:<10} exit {1}  {2}".format(r["stage"], r["exit"], r["target"]))
    done = set(r["stage"] for r in results)
    skipped = [s for s in STAGES if s not in done]
    if skipped:
        print("  not run (an earlier stage could not run): {0}".format(", ".join(skipped)))
    return code


if __name__ == "__main__":
    sys.exit(main())
