"""Seed a view's co-sized capture from the previous run (S1, 2026-11-13).

The registered capture CO-SIZES a view whose annotation export overruns the
per-axis ceiling (resolution_contract.co_sized_request): a first attempt at
the model's own request R measures the overrun, is rolled back, and a second
attempt captures both passes at ``fit_px``. Run 20261110T1151 paid 15.1 min
(3.1 %) for those discarded first attempts, mostly on the largest plans.

When the previous run of the same document co-sized a view, and nothing that
SETS its size has changed (the size key), this run's FIRST attempt is made at
last run's ``fit_px``. The capture is then the same export the unseeded
second attempt would have made: the same PixelSize, the same ticks, the same
lattice. Two checks stand between a seed and a capture:

  * BEFORE: the size key (``size_key``) -- the inputs co_sized_request and the
    first attempt's request depend on -- must be identical, and the previous
    co-sized attempt must have succeeded.
  * AFTER: the key cannot see the ANNOTATION content (section lines past the
    crop are what overrun, and they are not sizing inputs). So the seeded
    attempt is CONFIRMED only when both of its exports measure exactly what
    last run's co-sized attempt measured (``verify_seed``). Otherwise the seed
    is REFUTED and one more attempt runs at ``refuted_request``: never a
    third. A refuted seed costs what an unseeded co-sized view costs today.

Every view's record carries ``seed`` with its state, so absence never has to
be read: ``SEED_STATES``.

The previous run is found with change_skip's own index and series key
(batch_capture.index_runs, change_skip.series_key), NOT its baseline rule:
change_skip wants an EARLIER as-of date, and two back-to-back runs of one
document share a date (``previous_capture_run``).

Pure parts take plain values; ``load_seeds`` reads files.
"""

import math

SCHEMA = "vop.co_size_seed.v1"

SEEDED = "seeded"
NO_PREVIOUS = "no_previous"
KEY_MISMATCH = "key_mismatch"
PREVIOUS_SINGLE_ATTEMPT = "previous_single_attempt"
# Beyond the four the plan named, each a reason a seed is NOT used that the
# four would otherwise blur:
PREVIOUS_FAILED = "previous_failed"        # last run's co-sized attempt failed
KEY_UNREADABLE = "key_unreadable"          # this view's size key could not be read
DISABLED = "disabled"                      # color_id_buffer_co_size_seed False
NOT_APPLICABLE = "not_applicable"          # a single-pass capture: nothing to co-size
SEED_STATES = (SEEDED, NO_PREVIOUS, KEY_MISMATCH, PREVIOUS_SINGLE_ATTEMPT,
               PREVIOUS_FAILED, KEY_UNREADABLE, DISABLED, NOT_APPLICABLE)

# What a seeded attempt turned out to be (``seed["outcome"]``).
CONFIRMED = "confirmed"                    # measured as last run did: one attempt
REFUTED_OVERRUN = "refuted_overrun"        # the annotation still overran at the seed
REFUTED_CHANGED = "refuted_changed"        # it fit, but measured differently

_ROUND_FT = 6


# ======================================================================
# PURE
# ======================================================================

def size_key(view_id, geom, view_scale, export_dpi, fit_direction, max_axis_px,
             min_axis_px, config_hash):
    """PURE. Everything the FIRST attempt's request and co_sized_request depend
    on, read before attempt 1. ``geom``: frame_export_geometry() for crop A
    WITHOUT a co-sized bound (color_id_buffer.crop_a_export_geometry on the
    caller's config) -- the request R an unseeded first attempt makes.
    ``config_hash``: the run's portable config hash (a config change is a
    key change). Values are rounded so a JSON round trip compares equal."""
    crop_uv = geom.get("crop_snapped_uv") or ()
    return {
        "view_id": int(view_id),
        "requested_px": int(geom["requested_px"]),
        "crop_px": [int(v) for v in geom["crop_px"]],
        "crop_snapped_uv": [round(float(v), _ROUND_FT) for v in crop_uv],
        "view_scale": round(float(view_scale), _ROUND_FT),
        "export_dpi": round(float(export_dpi), _ROUND_FT),
        "fit_direction": str(fit_direction or "horizontal").strip().lower(),
        "max_axis_px": int(max_axis_px),
        "min_axis_px": int(min_axis_px),
        "config_hash": config_hash,
    }


def measure(model_frame, anno_frame):
    """PURE. What one attempt's two exports measured: the model export's
    pixel size and dimensions, and the annotation export's FIRST attempt (the
    one at the shared request). None when either was not read."""
    model_frame = model_frame or {}
    attempts = (anno_frame or {}).get("dim_check_attempts") or []
    first = attempts[0] if attempts else {}
    out = {"pixel_size": model_frame.get("pixel_size"),
           "model": [model_frame.get("actual_w"), model_frame.get("actual_h")],
           "annotation": [first.get("actual_w"), first.get("actual_h")]}
    if out["pixel_size"] is None or None in out["model"] or None in out["annotation"]:
        return None
    return out


def compact(co_sizing, key, final_measure, final_failure_reason):
    """PURE. The record run_meta's ``views[]`` entry carries for the NEXT run:
    whether the view's capture ended at a co-sized request (``fit_px``), the
    size key, and what that attempt measured."""
    co = co_sizing or {}
    co_sized = co.get("final_request") == "co_sized"
    return {"schema": SCHEMA,
            "state": co.get("state"),
            "attempts": co.get("attempts"),
            "final_request": co.get("final_request"),
            "fit_px": co.get("fit_px") if co_sized else None,
            "final_failure_reason": final_failure_reason,
            "size_key": key,
            "final_measure": final_measure if co_sized else None,
            "seed": co.get("seed")}


def seed_for_view(previous, key, from_run_id, enabled=True):
    """PURE. The seed a view gets: ``{"state", "from_run_id", "fit_px",
    "reason", "expect"}``. ``previous``: the view's compact record in the
    previous run (None when there is none); ``key``: this view's size key, or
    a string saying why it could not be read."""
    out = {"state": None, "from_run_id": from_run_id, "fit_px": None,
           "reason": None, "expect": None}
    if not enabled:
        return dict(out, state=DISABLED, reason="color_id_buffer_co_size_seed is off")
    if isinstance(key, str) or key is None:
        return dict(out, state=KEY_UNREADABLE, reason=key or "no size key")
    if not previous:
        return dict(out, state=NO_PREVIOUS,
                    reason="no record of this view in a previous run")
    if previous.get("final_request") != "co_sized" or not previous.get("fit_px"):
        return dict(out, state=PREVIOUS_SINGLE_ATTEMPT,
                    reason="the previous run captured this view at its own request")
    if previous.get("final_failure_reason") or not previous.get("final_measure"):
        return dict(out, state=PREVIOUS_FAILED,
                    reason="the previous co-sized attempt failed ({0})".format(
                        previous.get("final_failure_reason") or "no measurement"))
    prev_key = previous.get("size_key")
    if prev_key != key:
        changed = sorted(k for k in set(key) | set(prev_key or {})
                         if (prev_key or {}).get(k) != key.get(k))
        return dict(out, state=KEY_MISMATCH,
                    reason="size key differs in {0}".format(changed or "everything"))
    return dict(out, state=SEEDED, fit_px=int(previous["fit_px"]),
                expect=previous["final_measure"])


def verify_seed(seed, attempt_measure):
    """PURE. ``(confirmed, reason)``: a seeded attempt is CONFIRMED only when
    both exports measured exactly what the previous co-sized attempt did
    (same request, same model and annotation dimensions)."""
    expect = (seed or {}).get("expect")
    if attempt_measure is None:
        return False, "the seeded attempt's exports were not measured"
    if expect != attempt_measure:
        return False, "measured {0}, the previous co-sized attempt {1}".format(
            attempt_measure, expect)
    return True, None


def refuted_request(model_requested_px, seed_px, derived_at_seed_px,
                    max_axis_px, min_axis_px=64):
    """PURE. The request of the one attempt a REFUTED seed gets, from what the
    seeded attempt measured. The annotation's drawn extent is fixed in feet,
    so its derived axis scales with the request: ``D`` px at the seed ``s``
    puts it below ``(D + 1) * R / s`` at the model's own request R.

    * that bound within the ceiling: R itself -- an unseeded first attempt
      would have fit, and this IS that attempt;
    * else: ``cap_axes(R, ceil(bound))``, the co-sized request for an
      annotation no larger than the bound. It fits by construction.
      co_sized_request at R would use the MEASURED ``D_R + 1``, which this
      bound can exceed by a pixel (the seed's measurement does not pin
      ``D_R``), so this request can be the unseeded one or a pixel below it
      (tests/test_co_size_seed.py measures how often).

    Returns ``{"request_px", "derived_bound_px", "basis"}``; None when no
    usable request exists (below ``min_axis_px``)."""
    from .resolution_contract import cap_axes
    R, s, D = int(model_requested_px), int(seed_px), int(derived_at_seed_px)
    bound = (D + 1) * float(R) / float(s)
    cap = int(max_axis_px)
    if bound <= cap:
        return {"request_px": R, "derived_bound_px": bound, "basis": "fits_at_model_request"}
    fit = int(cap_axes(R, int(math.ceil(bound)), cap)["accepted_px"])
    if fit < int(min_axis_px):
        return None
    return {"request_px": fit, "derived_bound_px": bound, "basis": "co_sized_from_seed"}


def _doc_key(title):
    """PURE. Which document a run is of: change_skip's series key (the title
    without its detached suffix and archive tail), else -- a title with no
    archive date -- the title itself without the detached suffix."""
    from .change_skip import _DETACHED, series_key
    if isinstance(title, dict):
        title = title.get("value") if title.get("state") == "value" else None
    if not title:
        return None
    key = series_key(title)
    if key is not None:
        return ("series", key)
    text = str(title)
    if text.endswith(_DETACHED):
        text = text[:-len(_DETACHED)]
    return ("title", text)


def previous_capture_run(index, title, as_of_date, run_id):
    """PURE. The run to seed from: of the same document (``_doc_key``),
    finalized, an as-of date no later than this run's, not this run, and the
    latest of those by (as-of date, run id) -- run ids are the execution
    clock, so two runs of one day order by when they ran. None when there is
    none."""
    key = _doc_key(title)
    if key is None:
        return None
    rows = [r for r in index or [] if not r.get("error") and r.get("finalized")
            and r.get("run_id") != run_id and r.get("date")
            and str(r["date"]) <= str(as_of_date)
            and str(r.get("run_id") or "") < str(run_id or "~")
            and _doc_key(r.get("doc_title")) == key]
    if not rows:
        return None
    return sorted(rows, key=lambda r: (str(r["date"]), str(r.get("run_id") or "")))[-1]


# ======================================================================
# FILES
# ======================================================================

def load_seeds(output_root, doc_title, identity, config_hash):
    """The previous run's per-view compact records, as the run-level record
    the capture reads (``cfg._stage_a_co_size_seeds``) and run_meta records
    as ``co_size_seed``: ``{"schema", "from_run_id", "from_run_dir",
    "config_hash", "views": {view id str: compact}, "reason"}``. Never raises
    for a missing or unreadable previous run: no seed, with the reason."""
    from .batch_capture import index_runs, run_meta_of
    record = {"schema": SCHEMA, "from_run_id": None, "from_run_dir": None,
              "config_hash": config_hash, "views": {}, "reason": None}
    try:
        row = previous_capture_run(index_runs(output_root, max_depth=1), doc_title,
                                   identity["as_of_date"], identity["run_id"])
    except (OSError, ValueError, KeyError) as ex:
        record["reason"] = "previous run lookup failed: {0}: {1}".format(type(ex).__name__, ex)
        return record
    if row is None:
        record["reason"] = "no earlier finalized run of this document in {0}".format(output_root)
        return record
    meta, err = run_meta_of(row["run_dir"])
    record.update(from_run_id=row.get("run_id"), from_run_dir=row["run_dir"])
    if meta is None:
        record["reason"] = "previous run_meta unreadable: {0}".format(err)
        return record
    for entry in meta.get("views") or []:
        co = entry.get("co_sizing")
        if isinstance(co, dict) and entry.get("capture_status") == "success":
            record["views"][str(entry.get("view_id"))] = co
    if not record["views"]:
        record["reason"] = "the previous run records no co_sizing per view"
    return record


def run_summary(seeds):
    """PURE. What run_meta keeps of the run-level seed record: everything but
    the per-view records (those live in each view's own entry)."""
    out = dict((k, v) for k, v in (seeds or {}).items() if k != "views")
    out["views_offered"] = len((seeds or {}).get("views") or {})
    return out
