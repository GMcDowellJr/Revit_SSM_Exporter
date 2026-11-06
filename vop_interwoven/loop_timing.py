"""C2 (2026-11-02): per-view timing of the Stage A run LOOP, outside the capture.

Telemetry only. Nothing here decides anything; nothing captured changes.

WHY THIS EXISTS. Run 03-16 (1,650 views) spent a median 206 ms per view
inside ``ElapsedSec`` but outside the registered capture's ``total``
(``outside_capture_ms``), and a median 1.6 s per view between consecutive
views that no clock covered at all -- about an hour per run between them.
None of the pre-capture steps, the per-view file writes or the per-view
garbage collections had a timing key, so the hour could not be attributed.

WHAT IS TIMED. Two nested records per view:

* ``steps_ms`` -- the streaming loop's own body, top to bottom
  (``STREAMING_STEPS``): the memory marks, the ``process_document_views``
  call, the exporter callback (views_core row, run_meta summary) and the
  CLR collection after the view.
* ``pipeline.steps_ms`` -- inside that ``process_document_views`` call
  (``PIPELINE_STEPS``): its preamble, each pre-capture step the per-view
  loop runs in Stage A mode, the capture call itself, and the writes and the
  collection the pipeline makes after its loop (element map, views
  diagnostics, ``after_run_gc``, the run summary).

Each record states what its steps do not cover as an explicit
``unaccounted_ms`` (``total - sum(steps)``), never hidden. The top and bottom
of the loop are recorded as monotonic ``time.perf_counter()`` seconds, and
``gap_from_previous_ms`` is the time between the previous view's bottom and
this view's top -- the between-view interval no other clock covers.

WHERE IT GOES. ``run_meta.views[i].loop_timing``, written by the exporter at
finalize. Not into the sidecars or views_core: the loop's last steps (the
collection after the view) happen AFTER the exporter callback has written
that view's views_core row, so a record persisted there would be serialised
before it is finished (CLAUDE.md, Recurring Defect Class 4). By finalize every
view's loop has ended.

NOT THE CAPTURE'S PARTITION. These keys are additive. None of them is, or may
become, a member of ``stage_a_registered_capture.TIMING_PARTITION``: that
partition sums the phases INSIDE the capture's ``total``, and these steps are
outside it (``pipeline.capture`` CONTAINS ``total``). tests/
test_loop_timing.py turns red if one is added there.
"""
import time

SCHEMA = "vop.loop_timing.v1"

_perf = getattr(time, "perf_counter", time.time)

# The streaming loop's body, in execution order. They PARTITION the loop's
# total: sequential, non-overlapping.
STREAMING_STEPS = (
    "view_start_mark",          # MemoryTracker.mark (Process.Refresh)
    "process_document_views",   # the whole pipeline call for this one view
    "after_raster_mark",        # MemoryTracker.mark
    "on_view_complete",         # exporter: views_core row + run_meta summary
    "gc_after_view",            # MemoryTracker.mark_and_gc: CLR collect + marks
)

# Inside process_document_views, in execution order; they partition its total.
# The pre-capture steps are the ones the Stage A per-view loop runs before
# the capture call (C0's list); a step a run does not reach is absent.
PIPELINE_STEPS = (
    "preamble",                 # caches, run identity, MemoryTracker, marks
    "view_start_mark",          # MemoryTracker.mark + process_memory_sample
    "mode",                     # resolve_view_mode
    "signature",                # _view_signature: membership ids + signature
    "view_identity",            # _extract_view_identity_for_csv
    "raster_init",              # init_view_raster (bounds, basis, anno extents)
    "strategy_diag",            # StrategyDiagnostics() when configured
    "collect_elements",         # collect_view_elements
    "capture",                  # export_registered_stage_a_view (contains total)
    "diagnostics_entry",        # _stage_a_view_diagnostics (diag.to_dict)
    "element_cache_stats",      # diag.info of the element cache's stats
    "element_map_write",        # vop_view_element_map_<d>.json read+merge+write
    "views_diagnostics_write",  # views_diagnostics_<d>.json read+merge+write
    "gc_after_run",             # MemoryTracker.mark + mark_and_gc(after_run_gc);
                                # the mark alone under stage_a_gc_policy
                                # "per_view_once" (record: gc_after_run.skipped)
    "run_summary",              # run summary assembled and printed
)

# Every key this module writes into a record, for the partition guard.
RECORD_KEYS = (("schema", "loop_top_s", "loop_bottom_s", "total_ms",
                "gap_from_previous_ms", "steps_ms", "unaccounted_ms", "pipeline",
                "gc", "gated_steps", "gc_policy", "capture_total_ms",
                "capture_call_outside_total_ms")
               + STREAMING_STEPS + PIPELINE_STEPS)


def now():
    """Monotonic seconds (``time.perf_counter``)."""
    return _perf()


def ms_between(t0, t1):
    return round((float(t1) - float(t0)) * 1000.0, 3)


def unaccounted_ms(total_ms, steps_ms, partition):
    """PURE. ``total_ms`` minus the ``partition`` steps that were timed.
    None when there is no total."""
    if total_ms is None:
        return None
    return round(float(total_ms) - sum(float(steps_ms[k]) for k in partition
                                      if steps_ms.get(k) is not None), 3)


class StepTimer(object):
    """Accumulates ``steps_ms`` for one record. ``step(name, t0)`` records the
    time since ``t0`` under ``name`` (summed when a step runs twice) and
    returns now(), so consecutive steps chain without extra clock reads."""

    def __init__(self, partition):
        self.partition = tuple(partition)
        self.steps_ms = {}

    def step(self, name, t0):
        if name not in self.partition:
            raise KeyError("{0!r} is not a step of this record".format(name))
        t1 = now()
        self.steps_ms[name] = round(self.steps_ms.get(name, 0.0)
                                    + (float(t1) - float(t0)) * 1000.0, 3)
        return t1

    def record(self, total_ms):
        return {"total_ms": total_ms, "steps_ms": dict(self.steps_ms),
                "unaccounted_ms": unaccounted_ms(total_ms, self.steps_ms,
                                                 self.partition)}


def capture_total_ms(view_result):
    """The registered capture's ``timings_ms.total`` from a view result, or
    None (no registered capture, or it never reached its total)."""
    try:
        reg = (view_result or {}).get("registration") or {}
        total = (reg.get("timings_ms") or {}).get("total")
        return None if total is None else float(total)
    except (AttributeError, TypeError, ValueError):
        return None


def attach_to_summaries(view_summaries, records):
    """Put each view's finished loop record on its exporter summary, so
    ``run_meta.view_outcomes`` carries it into ``views[i].loop_timing``.
    ``records``: ``{str(view_id): record}``. A summary with no record is left
    as it is (the key stays absent, never an empty record)."""
    for summary in view_summaries or []:
        if not isinstance(summary, dict):
            continue
        rec = (records or {}).get(str(summary.get("view_id")))
        if rec is not None:
            summary["loop_timing"] = rec
    return view_summaries
