"""ONE way to run a Stage A analysis tool's per-view work: serially, or on a
pool of processes, with the same printed lines in the same order.

A tool's ``main()`` builds a ``Plan``: the lines and exit code of its set-up
(or an early exit), one ``Job`` per view, and a ``finish`` that turns the
gathered results into its closing lines and exit code. ``run_serial`` is the
tool's own loop; ``run_pool`` (tools/stage_a_analyze.py ``--workers N``)
runs the same jobs in worker processes. Every job is a module-level function
of picklable arguments that returns ``(exit code, line, extra)`` and prints
nothing, so a result is the same wherever it ran, and the parent prints each
line in plan order -- as soon as it and every job before it have finished.

A job's own refusals are in its result, as serially. An exception a job does
not handle is raised from the gather at that job's place in the order, as
the serial loop would raise it there; the jobs not yet started are
cancelled. (Jobs after it that had already run in parallel have written
their files, where a serial run would have stopped before them.)

SCHEDULING. Jobs start largest first by their estimated peak bytes, and the
estimates of the jobs running at once never sum past ``max_bytes``; a job
larger than ``max_bytes`` alone runs alone. Order of START only: the printed
order is the plan's. Workers are spawned, not forked, on every platform, so
this runs here as it does on Windows.

LEAF MODULE: standard library only.
"""
from __future__ import annotations

import concurrent.futures as cf
import multiprocessing


class Job(object):
    """``fn(*args)`` -> ``(exit code, line or None, extra)``; ``estimate`` is
    its peak bytes, for scheduling only (0: unknown)."""

    __slots__ = ("fn", "args", "estimate")

    def __init__(self, fn, args, estimate=0):
        self.fn, self.args, self.estimate = fn, tuple(args), int(estimate or 0)


class Plan(object):
    """``early``: an exit code that ends the tool before any job (its lines
    printed first), or None. ``finish(results)`` -> ``(lines, exit code)``
    from the jobs' results in plan order; default: no lines, the worst
    job code."""

    def __init__(self, lines=(), jobs=(), early=None, finish=None):
        self.lines, self.jobs, self.early = list(lines), list(jobs), early
        self.finish = finish or (lambda results: ([], max([r[0] for r in results] or [0])))


def _close(plan, results, out):
    lines, code = plan.finish(results)
    for line in lines:
        out(line)
    return code


def run_serial(plan, out=print):
    """The tool's own loop: each job in order, its line printed as it ends."""
    for line in plan.lines:
        out(line)
    if plan.early is not None:
        return plan.early
    results = []
    for job in plan.jobs:
        result = job.fn(*job.args)
        if result[1] is not None:
            out(result[1])
        results.append(result)
    return _close(plan, results, out)


def start_order(jobs):
    """Indices of ``jobs``, largest estimate first; ties in plan order."""
    return sorted(range(len(jobs)), key=lambda n: (-jobs[n].estimate, n))


def run_pool(plan, workers, max_bytes=None, out=print, context="spawn"):
    """``run_serial``'s output and exit code, with the jobs run on up to
    ``workers`` processes, at most ``max_bytes`` of estimates at once."""
    for line in plan.lines:
        out(line)
    if plan.early is not None:
        return plan.early
    jobs = plan.jobs
    pending = start_order(jobs)
    results = [None] * len(jobs)
    done = [False] * len(jobs)
    printed = 0
    running = {}                                     # future -> job index
    ctx = multiprocessing.get_context(context)
    with cf.ProcessPoolExecutor(max_workers=max(1, int(workers)), mp_context=ctx) as pool:
        try:
            while pending or running:
                in_use = sum(jobs[n].estimate for n in running.values())
                while pending and len(running) < workers:
                    n = pending[0]
                    if running and max_bytes is not None and \
                            in_use + jobs[n].estimate > max_bytes:
                        break                          # wait for memory, in order
                    pending.pop(0)
                    running[pool.submit(jobs[n].fn, *jobs[n].args)] = n
                    in_use += jobs[n].estimate
                finished, _ = cf.wait(list(running), return_when=cf.FIRST_COMPLETED)
                for future in finished:
                    n = running.pop(future)
                    results[n] = future           # raised (if at all) in order, below
                    done[n] = True
                while printed < len(jobs) and done[printed]:
                    result = results[printed].result()
                    results[printed] = result
                    if result[1] is not None:
                        out(result[1])
                    printed += 1
        except BaseException:
            for future in running:
                future.cancel()
            pool.shutdown(wait=True, cancel_futures=True)
            raise
    return _close(plan, results, out)
