"""Automated PASS/FAIL comparators for the p0_export_correctness campaign.

Pure functions over the per-view records the p0 probe writes. No Revit, no
image decoding, no recomputation: every value these read is already produced
by something else and recorded --

    requested_axis, pre_cap_px, requested_px, cap_applied, paper_fit_in,
    export_dpi, actual_w/actual_h, dim_check, dim_check_attempts,
    backoff_stop_reason   color_id_buffer.py's sidecar "resolution" block
    applied_smooth_edges, smooth_edges_read_error
                          color_id_buffer.py's sidecar, top level
    reliability + reason, feet_per_pixel, feet_per_pixel_basis
                          decode_stage_a_color_id.build_decoded_document
    hard_edges, blended_edges, hard_ratio, off_px
                          analyze_stage_a_probe.stage_a_export_metrics
    probe overrides + restored flags
                          the probe's own rollback bookkeeping

Recomputing any of those here would mean the campaign accepts P0 against a
second implementation of the thing under test, which proves nothing about
the one that ships.

WHY THESE ARE AUTOMATED. The P0 Revit checkpoint was written as a human
read-out ("longer axis <= 10000, dim_check = pass, off_px = 0,
hard_ratio = 1.0"). Three of those four are exact comparisons a person
gains nothing by making, and the fourth is a trap: hard_ratio is
``hard / (hard + blended) if (hard + blended) else None``
(analyze_stage_a_probe.py:2000), so a capture with no measured edges at all
reports None, which the metrics table prints as "-". A reader checking for
"1.0" sees "-", reads it as "no data yet", and moves on. Nothing was
measured and nothing said so. That is why HARD_RATIO_NOT_MEASURED is a FAIL
here and never a skip, and why hard_edges > 0 is required alongside the
ratio rather than being implied by it.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from vop_interwoven.resolution_contract import MAX_STAGE_A_AXIS_PX  # noqa: E402

# The ceiling every capture is judged against, whatever sizing cap the job
# ran with. p0.override deliberately raises the SIZING cap to 15000; the
# verification ceiling does not move, which is the entire point of that job.
VERIFICATION_CEILING_PX = MAX_STAGE_A_AXIS_PX

# The cases this module grades. p0_select is enumeration only -- it produces
# a candidate list for a human to choose from and carries
# manual_review_required, so it has no automated verdict here.
AUTOMATED_CASES = ("p0_default", "p0_derived", "p0_override")
ALL_CASES = ("p0_select",) + AUTOMATED_CASES

# Stop reasons that mean the bounded backoff reached a bound, as opposed to
# ending some other way. Mirrors color_id_buffer._export_tiff.
BACKOFF_STOP_REASONS = ("retry_limit", "grid_floor")


def _check(check_id: str, status: str, reason_codes: list[str], evidence: Any = None) -> dict[str, Any]:
    """Same shape analyze_stage_a_probe._check produces, so these records
    drop straight into the analyzer's `checks` list and are graded by its
    existing FAIL/INCONCLUSIVE/PASS rollup."""
    return {"check_id": check_id, "status": status,
            "reason_codes": sorted(set(reason_codes)), "evidence": evidence}


def _verdict(ok: bool, check_id: str, reason: str, evidence: Any) -> dict[str, Any]:
    return _check(check_id, "PASS" if ok else "FAIL", [] if ok else [reason], evidence)


def _resolution(record: dict[str, Any]) -> dict[str, Any]:
    return record.get("resolution") or {}


def _metrics(record: dict[str, Any]) -> dict[str, Any]:
    return record.get("metrics") or {}


def _attempts(record: dict[str, Any]) -> list[dict[str, Any]]:
    value = _resolution(record).get("dim_check_attempts")
    return list(value) if isinstance(value, list) else []


# --- shared checks ---------------------------------------------------------

def check_hard_ratio_measured(record: dict[str, Any]) -> dict[str, Any]:
    """hard_ratio must be a number. None -- rendered "-" in the metrics
    table -- means no edge transitions were counted, which is not a passing
    capture and not a missing measurement to come back to later."""
    metrics = _metrics(record)
    ratio = metrics.get("hard_ratio")
    hard = metrics.get("hard_edges")
    if ratio is None or ratio == "-" or hard is None:
        return _check("hard_ratio_measured", "FAIL", ["HARD_RATIO_NOT_MEASURED"],
                      {"hard_ratio": ratio, "hard_edges": hard,
                       "blended_edges": metrics.get("blended_edges")})
    if not _as_int(hard, 0) > 0:
        return _check("hard_ratio_measured", "FAIL", ["NO_EDGES_MEASURED"],
                      {"hard_edges": hard, "blended_edges": metrics.get("blended_edges")})
    return _check("hard_ratio_measured", "PASS", [],
                  {"hard_ratio": ratio, "hard_edges": hard})


def check_overrides_restored(record: dict[str, Any]) -> dict[str, Any]:
    """Every probe-only override the job APPLIED must report restored.

    A job that changes the document and leaves it changed has contaminated
    every later job in the batch, so this is graded on the same footing as
    the measurement it was applied for -- not as a cleanup note. An entry
    marked applied=False changed nothing (e.g. a view with no underlay to
    disable) and has nothing to restore; it still has to be present and
    carry its reason, so "nothing needed doing" stays visible rather than
    looking like an override that was forgotten.
    """
    overrides = record.get("probe_overrides")
    if not isinstance(overrides, list) or not overrides:
        return _check("overrides_restored", "FAIL", ["NO_OVERRIDE_RECORD"], overrides)
    applied = [o for o in overrides if o.get("applied", True)]
    unrestored = [o for o in applied if o.get("restored") is not True]
    unexplained = [o for o in overrides
                   if not o.get("applied", True) and not o.get("reason")]
    if unexplained:
        return _check("overrides_restored", "FAIL", ["OVERRIDE_SKIP_UNEXPLAINED"],
                      {"overrides": overrides, "unexplained": unexplained})
    return _verdict(not unrestored, "overrides_restored",
                    "OVERRIDE_NOT_RESTORED", {"overrides": overrides,
                                              "unrestored": unrestored})


def check_underlay_off(record: dict[str, Any]) -> dict[str, Any]:
    """The capture ran with the underlay off, however that came about.

    The P0 checkpoint says "underlay disabled on the test view". A view that
    never had one satisfies that without a mutation, which is the outcome
    run 1b113822 hit on a different view. What must not pass is a capture
    where the underlay state was never established at all.
    """
    overrides = record.get("probe_overrides") or []
    entries = [o for o in overrides if o.get("override") == "underlay_disabled"]
    if not entries:
        return _check("underlay_off", "FAIL", ["UNDERLAY_STATE_NOT_RECORDED"], overrides)
    entry = entries[0]
    return _verdict(entry.get("underlay_off_for_capture") is True, "underlay_off",
                    "UNDERLAY_NOT_OFF_FOR_CAPTURE", entry)


def check_dim_check_pass(record: dict[str, Any]) -> dict[str, Any]:
    value = _resolution(record).get("dim_check")
    return _verdict(value == "pass", "dim_check", "DIM_CHECK_NOT_PASS",
                    {"dim_check": value})


def _as_int(value: Any, default: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# --- p0.default ------------------------------------------------------------

def evaluate_default(record: dict[str, Any]) -> list[dict[str, Any]]:
    resolution, metrics = _resolution(record), _metrics(record)
    checks = []

    w, h = _as_int(resolution.get("actual_w")), _as_int(resolution.get("actual_h"))
    if w is None or h is None:
        checks.append(_check("longer_axis_within_ceiling", "FAIL",
                             ["EXPORT_DIMENSIONS_NOT_RECORDED"],
                             {"actual_w": w, "actual_h": h}))
    else:
        checks.append(_verdict(max(w, h) <= VERIFICATION_CEILING_PX,
                               "longer_axis_within_ceiling", "AXIS_OVER_CEILING",
                               {"actual_w": w, "actual_h": h,
                                "ceiling_px": VERIFICATION_CEILING_PX}))

    checks.append(check_dim_check_pass(record))

    off_px = _as_int(metrics.get("off_px"))
    checks.append(_check("off_palette_pixels", "FAIL", ["OFF_PX_NOT_MEASURED"], None)
                  if off_px is None else
                  _verdict(off_px == 0, "off_palette_pixels", "OFF_PALETTE_PIXELS_PRESENT",
                           {"off_px": off_px}))

    checks.append(check_hard_ratio_measured(record))
    ratio = metrics.get("hard_ratio")
    if isinstance(ratio, (int, float)):
        checks.append(_verdict(float(ratio) == 1.0, "hard_ratio_is_one",
                               "BLENDED_EDGES_PRESENT",
                               {"hard_ratio": ratio,
                                "blended_edges": metrics.get("blended_edges")}))

    aa = record.get("applied_smooth_edges")
    checks.append(_verdict(aa != "read_failed", "anti_aliasing_state_known",
                           "SMOOTH_EDGES_READ_FAILED",
                           {"applied_smooth_edges": aa,
                            "smooth_edges_read_error": record.get("smooth_edges_read_error")}))

    missing = [k for k in ("paper_fit_in", "export_dpi") if not resolution.get(k)]
    checks.append(_verdict(not missing, "scale_provenance_recorded",
                           "SCALE_PROVENANCE_MISSING",
                           {"paper_fit_in": resolution.get("paper_fit_in"),
                            "export_dpi": resolution.get("export_dpi"),
                            "missing": missing}))

    checks.append(check_underlay_off(record))
    checks.append(check_overrides_restored(record))
    return checks


# --- p0.derived ------------------------------------------------------------

def evaluate_derived(record: dict[str, Any]) -> list[dict[str, Any]]:
    """The job that proves the cap binds on the axis PixelSize does NOT set.

    Capping the fitted axis alone is what let 8695x10028 through, so a
    prediction for the derived axis that is never compared to the file is
    the original defect restated. This compares them.
    """
    resolution = _resolution(record)
    checks = [check_dim_check_pass(record)]

    axis = resolution.get("requested_axis")
    predicted = _as_int(resolution.get("predicted_derived_px"))
    derived_actual = (_as_int(resolution.get("actual_w")) if axis == "height"
                      else _as_int(resolution.get("actual_h")))
    if predicted is None or derived_actual is None:
        checks.append(_check("derived_axis_matches_prediction", "FAIL",
                             ["DERIVED_AXIS_NOT_COMPARABLE"],
                             {"requested_axis": axis, "predicted_derived_px": predicted,
                              "derived_actual_px": derived_actual}))
    else:
        delta = abs(predicted - derived_actual)
        checks.append(_verdict(delta <= 1, "derived_axis_matches_prediction",
                               "DERIVED_AXIS_PREDICTION_WRONG",
                               {"requested_axis": axis, "predicted_derived_px": predicted,
                                "derived_actual_px": derived_actual, "delta_px": delta}))

    checks.append(_verdict(resolution.get("cap_applied") is True, "cap_applied",
                           "CAP_DID_NOT_BIND",
                           {"cap_applied": resolution.get("cap_applied"),
                            "pre_cap_px": resolution.get("pre_cap_px"),
                            "requested_px": resolution.get("requested_px")}))

    checks.append(check_hard_ratio_measured(record))
    checks.append(check_underlay_off(record))
    checks.append(check_overrides_restored(record))
    return checks


# --- p0.override -----------------------------------------------------------

def evaluate_override(record: dict[str, Any]) -> list[dict[str, Any]]:
    """The job that proves the check FIRES rather than that an export is fine.

    It deliberately requests an over-ceiling export. Success here means the
    first attempt was rejected and the run went somewhere honest afterwards:
    either a halved re-export that genuinely passes, or a bounded stop with
    the view marked failed. The one outcome that must never occur is the
    15000 px attempt being recorded as a pass.
    """
    resolution = _resolution(record)
    attempts = _attempts(record)
    checks = []

    if not attempts:
        checks.append(_check("first_attempt_rejected", "FAIL",
                             ["NO_EXPORT_ATTEMPTS_RECORDED"], None))
    else:
        first = attempts[0]
        checks.append(_verdict(first.get("dim_check") == "mismatch",
                               "first_attempt_rejected", "OVER_CEILING_REQUEST_NOT_REJECTED",
                               {"first_attempt": first}))

    checks.append(_verdict(len(attempts) > 1, "backoff_fired", "BACKOFF_DID_NOT_FIRE",
                           {"attempt_count": len(attempts),
                            "requested_px": [a.get("requested_px") for a in attempts]}))

    # No attempt at or above the ceiling may be recorded as a pass -- that is
    # the failure mode this whole job exists to rule out, and it is checked
    # over every attempt rather than just the first.
    over_ceiling_passes = [
        a for a in attempts
        if a.get("dim_check") == "pass"
        and max(_as_int(a.get("actual_w"), 0) or 0,
                _as_int(a.get("actual_h"), 0) or 0) > VERIFICATION_CEILING_PX
    ]
    checks.append(_verdict(not over_ceiling_passes, "no_over_ceiling_pass",
                           "OVER_CEILING_EXPORT_REPORTED_PASS",
                           {"offending_attempts": over_ceiling_passes,
                            "ceiling_px": VERIFICATION_CEILING_PX}))

    final = resolution.get("dim_check")
    stop_reason = resolution.get("backoff_stop_reason")
    if final == "pass":
        checks.append(_check("bounded_outcome", "PASS", [],
                             {"outcome": "later_attempt_passed",
                              "attempt_count": len(attempts)}))
    else:
        bounded = stop_reason in BACKOFF_STOP_REASONS
        failed_correctly = record.get("failure_reason") == "export_dim_mismatch"
        if not bounded:
            checks.append(_check("bounded_outcome", "FAIL", ["BACKOFF_STOP_REASON_MISSING"],
                                 {"dim_check": final, "backoff_stop_reason": stop_reason}))
        else:
            checks.append(_verdict(failed_correctly, "bounded_outcome",
                                   "VIEW_NOT_FAILED_AFTER_BACKOFF",
                                   {"dim_check": final, "backoff_stop_reason": stop_reason,
                                    "failure_reason": record.get("failure_reason"),
                                    "success": record.get("success")}))

    checks.append(check_hard_ratio_measured(record))
    checks.append(check_underlay_off(record))
    checks.append(check_overrides_restored(record))
    return checks


_EVALUATORS = {
    "p0_default": evaluate_default,
    "p0_derived": evaluate_derived,
    "p0_override": evaluate_override,
}


def evaluate_record(case: str, record: dict[str, Any]) -> list[dict[str, Any]]:
    """Grade one per-view record. Unknown cases FAIL rather than pass by
    default: a typo'd case in a campaign must not produce a green job."""
    evaluator = _EVALUATORS.get(case)
    if evaluator is None:
        return [_check("known_case", "FAIL", ["UNKNOWN_P0_CASE"], {"case": case})]
    checks = evaluator(record)
    return [dict(c, check_id="{0}.{1}".format(case, c["check_id"])) for c in checks]


def evaluate_report(native: dict[str, Any]) -> list[dict[str, Any]]:
    """Grade every per-view record in a p0 probe report.

    A report with no records at all is a FAIL, not an empty pass: the
    campaign asked for a capture and did not get one.
    """
    records = native.get("records")
    if not isinstance(records, list) or not records:
        return [_check("p0_records_present", "FAIL", ["NO_P0_RECORDS"],
                       {"record_count": 0})]
    checks = []
    for record in records:
        case = str(record.get("case") or "")
        if case == "p0_select":
            # Enumeration only: it hands a candidate list to a human and is
            # gated by manual_review_required, so there is nothing here to
            # grade automatically. Recorded as its own check so a report that
            # contains ONLY selection cannot be mistaken for a graded pass.
            checks.append(_check("p0_select.enumeration_only", "INCONCLUSIVE",
                                 ["MANUAL_SEMANTIC_REVIEW_REQUIRED"],
                                 {"candidates": len(record.get("candidates") or [])}))
            continue
        checks.extend(evaluate_record(case, record))
    return checks
