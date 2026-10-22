"""tools/stage_a_jobs.py: a plan run on a pool prints what it prints serially,
in the same order, with the same exit code; the memory cap holds; a job's
exception surfaces at its place in the order.

The jobs are module-level functions here so spawned workers can import them.
"""
import json
import time
from pathlib import Path

import pytest

from tools import stage_a_jobs as jobs


def toy(n, delay, code=0, log=None):
    """Sleeps ``delay``; records its [start, end] in ``log`` (a directory)."""
    start = time.time()
    time.sleep(delay)
    if log is not None:
        Path(log, "{0}.json".format(n)).write_text(json.dumps([start, time.time()]))
    return code, "job {0}".format(n), n * 10


def boom(n):
    raise RuntimeError("job {0} broke".format(n))


def _plan(specs, log=None):
    return jobs.Plan(lines=["header"],
                     jobs=[jobs.Job(toy, (n, d, c, log), estimate=e)
                           for n, (d, c, e) in enumerate(specs)],
                     finish=lambda rs: (["extras " + ",".join(str(r[2]) for r in rs)],
                                        max(r[0] for r in rs)))


def _collect(run, plan, *args):
    lines = []
    code = run(plan, *args, out=lines.append)
    return code, lines


def test_the_pool_prints_the_serial_lines_in_plan_order():
    """Later jobs finish FIRST (decreasing delays): the printed order, the
    gathered extras and the exit code are still the serial run's."""
    specs = [(0.6, 0, 0), (0.4, 1, 0), (0.2, 0, 0), (0.0, 0, 0)]
    serial = _collect(jobs.run_serial, _plan(specs))
    pooled = _collect(jobs.run_pool, _plan(specs), 4)
    assert serial == pooled
    assert serial == (1, ["header", "job 0", "job 1", "job 2", "job 3", "extras 0,10,20,30"])


def test_an_early_exit_runs_no_job():
    plan = jobs.Plan(lines=["nothing to do"], jobs=[jobs.Job(boom, (0,))], early=2)
    assert _collect(jobs.run_pool, plan, 2) == (2, ["nothing to do"])
    assert _collect(jobs.run_serial, plan) == (2, ["nothing to do"])


def test_jobs_start_largest_first_and_running_estimates_never_pass_the_cap(tmp_path):
    """Estimates 3, 6, 2, 5, 4, 12 under a cap of 9 on 4 workers: no jobs
    whose estimates sum past 9 overlap, and the largest starts first. A job
    larger than the cap runs alone (the control: 12)."""
    est = [3, 6, 2, 5, 4, 12]
    specs = [(0.3, 0, e) for e in est]
    code, lines = _collect(jobs.run_pool, _plan(specs, str(tmp_path)), 4, 9)
    assert code == 0 and lines[1:7] == ["job {0}".format(n) for n in range(6)]
    spans = dict((n, json.loads((tmp_path / "{0}.json".format(n)).read_text()))
                 for n in range(len(est)))
    for t in sorted(s for s, _e in spans.values()):
        running = [n for n, (s, e) in spans.items() if s <= t < e]
        assert sum(est[n] for n in running) <= 9 or running == [5], (t, running)
    assert min(spans, key=lambda n: spans[n][0]) == 5          # 12: largest first
    assert jobs.start_order(_plan(specs).jobs) == [5, 1, 3, 4, 0, 2]   # 12, 6, 5, 4, 3, 2


def test_a_job_exception_is_raised_at_its_place_in_the_order():
    plan = jobs.Plan(jobs=[jobs.Job(toy, (0, 0.3)), jobs.Job(boom, (1,)),
                           jobs.Job(toy, (2, 0.0))])
    for run, args in ((jobs.run_serial, ()), (jobs.run_pool, (3,))):
        lines = []
        with pytest.raises(RuntimeError, match="job 1 broke"):
            run(plan, *args, out=lines.append)
        assert lines == ["job 0"], run          # printed up to it, nothing after
