#!/usr/bin/env python3
"""Run the Stage A analysis layer end to end, in order.

    python tools/stage_a_analyze.py <run> [<run> ...] [--out DIR]

Per run:
  1. register   -- register_stage_a_annotation over the run's capture folder
  2. grid       -- stage_a_grid
  3. derive     -- stage_a_kinds derive
Then, across all runs:
  4. inventory  -- stage_a_kinds inventory
  5. rollup     -- stage_a_grid_rollup

Each stage is the tool's own ``main()``, called as it would be from the
command line: nothing is reimplemented here, and every stage prints its own
lines. A view a stage refuses (exit 1) does not stop the run -- the later
stages see the refusal and the roll-up reports it. A stage that cannot run
at all (exit 2: nothing found, bad arguments, a broken invariant) stops
everything after it, because what follows would read missing or partial
inputs.

``--out`` is where the cross-run products go (inventory, roll-up). With one
run it defaults to that run's ``analysis_grid/``, as each tool's own default
does; with more than one it is required.

Exit: the worst stage exit code (0 every stage clean, 1 some view refused or
not gridded, 2 a stage could not run).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import register_stage_a_annotation as reg  # noqa: E402
from tools import stage_a_grid as grid  # noqa: E402
from tools import stage_a_grid_rollup as rollup  # noqa: E402
from tools import stage_a_kinds as kinds  # noqa: E402

STAGES = ("register", "grid", "derive", "inventory", "rollup")


def _stage(name, label, fn, argv, results):
    print("== {0}: {1}".format(name, label))
    try:
        code = fn([str(a) for a in argv])
    except SystemExit as ex:          # argparse errors: a stage that cannot run
        code = ex.code if isinstance(ex.code, int) else 2
    code = int(code or 0)
    results.append({"stage": name, "target": label, "exit": code})
    return code


def analyze(runs, out=None):
    """Run every stage in order; returns ``(worst exit code, per-stage
    results)``. Stops at the first stage that exits 2."""
    runs = [Path(r) for r in runs]
    if len(runs) > 1 and out is None:
        raise ValueError("--out is required with more than one run")
    results = []
    for run in runs:
        _sidecars, folder = grid.model_sidecars(run)
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
    return max(r["exit"] for r in results), results


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("runs", nargs="+", metavar="run",
                    help="a run directory or its color_id_buffer/")
    ap.add_argument("--out", default=None,
                    help="where the inventory and roll-up go (required with more "
                         "than one run; default: the run's analysis_grid)")
    args = ap.parse_args(argv)
    if len(args.runs) > 1 and not args.out:
        ap.error("--out is required with more than one run")
    code, results = analyze(args.runs, args.out)
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
