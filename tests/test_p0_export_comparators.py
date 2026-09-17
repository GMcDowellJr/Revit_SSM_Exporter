"""Offline dry-run of the p0_export_correctness comparators.

Every check is exercised in both directions on fabricated records, so the
campaign's acceptance rules are known to discriminate BEFORE any Revit time
is spent. A comparator that only ever returns PASS on a good record proves
nothing; each test here pairs the passing record with the specific mutation
that must flip it.

The fixtures are shaped exactly like what the probe assembles from the
sidecar / decoded document / metrics triple -- see
probe_stage_a_p0_export_correctness.build_record.
"""
import copy
import json
from pathlib import Path

import pytest

from tools import p0_export_comparators as cmp

REPO = Path(__file__).resolve().parent.parent


def _status(checks, suffix):
    matched = [c for c in checks if c["check_id"].endswith("." + suffix)]
    assert len(matched) == 1, "expected one {0} check, got {1}".format(
        suffix, [c["check_id"] for c in checks])
    return matched[0]["status"], matched[0]["reason_codes"]


def _overall(checks):
    return "FAIL" if any(c["status"] == "FAIL" for c in checks) else (
        "INCONCLUSIVE" if any(c["status"] == "INCONCLUSIVE" for c in checks) else "PASS")


# --------------------------------------------------------------------------
# Fixtures: one clean record per case
# --------------------------------------------------------------------------

def default_record(**overrides):
    record = {
        "case": "p0_default",
        "view_id": 528698,
        "view_name": "MOB 1 - LEVEL 2",
        "resolution": {
            "requested_axis": "width",
            "pre_cap_px": 10005,
            "requested_px": 10000,
            "cap_applied": True,
            "paper_fit_in": 66.7,
            "export_dpi": 150.0,
            "actual_w": 10000,
            "actual_h": 9640,
            "predicted_derived_px": 9640,
            "dim_check": "pass",
            "dim_check_attempts": [
                {"requested_px": 10000, "accepted_px": 10000,
                 "actual_w": 10000, "actual_h": 9640, "dim_check": "pass"},
            ],
            "backoff_stop_reason": None,
        },
        "applied_smooth_edges": False,
        "smooth_edges_read_error": None,
        "metrics": {"hard_edges": 17340, "blended_edges": 0,
                    "hard_ratio": 1.0, "off_px": 0},
        "probe_overrides": [
            {"override": "underlay_disabled", "value": True, "applied": True,
             "reason": None, "restored": True, "restore_error": None,
             "underlay_off_for_capture": True, "mechanism": "SetUnderlayRange"},
        ],
        "failure_reason": None,
        "success": True,
    }
    record.update(overrides)
    return record


def derived_record(**overrides):
    record = {
        "case": "p0_derived",
        "view_id": 871863,
        "view_name": "(N) HOSPITAL - LEVEL 2",
        "resolution": {
            "requested_axis": "width",
            "pre_cap_px": 8700,
            "requested_px": 8670,
            "cap_applied": True,
            "paper_fit_in": 58.0,
            "export_dpi": 150.0,
            "actual_w": 8670,
            "actual_h": 10000,
            "predicted_derived_px": 10000,
            "dim_check": "pass",
            "dim_check_attempts": [
                {"requested_px": 8670, "accepted_px": 8670,
                 "actual_w": 8670, "actual_h": 10000, "dim_check": "pass"},
            ],
        },
        "applied_smooth_edges": False,
        "metrics": {"hard_edges": 9100, "blended_edges": 0,
                    "hard_ratio": 1.0, "off_px": 0},
        "probe_overrides": [
            {"override": "underlay_disabled", "value": True, "applied": True,
             "restored": True, "underlay_off_for_capture": True},
        ],
        "success": True,
    }
    record.update(overrides)
    return record


def override_record(**overrides):
    """The expected shape: 15000 rejected, halved, and the halved one passes."""
    record = {
        "case": "p0_override",
        "view_id": 528698,
        "view_name": "MOB 1 - LEVEL 2",
        "resolution": {
            "requested_axis": "width",
            "pre_cap_px": 10005,
            "requested_px": 15000,
            "cap_applied": False,
            "paper_fit_in": 66.7,
            "export_dpi": 150.0,
            "actual_w": 7500,
            "actual_h": 7230,
            "dim_check": "pass",
            "dim_check_attempts": [
                {"requested_px": 15000, "accepted_px": 15000,
                 "actual_w": 15000, "actual_h": 14460, "dim_check": "mismatch"},
                {"requested_px": 7500, "accepted_px": 7500,
                 "actual_w": 7500, "actual_h": 7230, "dim_check": "pass"},
            ],
            "backoff_stop_reason": None,
        },
        "applied_smooth_edges": False,
        "metrics": {"hard_edges": 12000, "blended_edges": 0,
                    "hard_ratio": 1.0, "off_px": 0},
        "probe_overrides": [
            {"override": "underlay_disabled", "value": True, "applied": True,
             "restored": True, "underlay_off_for_capture": True},
            {"override": "color_id_buffer_cap_axis_px", "value": 15000,
             "applied": True, "restored": True},
        ],
        "failure_reason": None,
        "success": True,
    }
    record.update(overrides)
    return record


def _mutate(record, path, value):
    """Set a dotted path on a deep copy, so each case states exactly the one
    thing it changed."""
    out = copy.deepcopy(record)
    node = out
    parts = path.split(".")
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = value
    return out


# --------------------------------------------------------------------------
# p0.default
# --------------------------------------------------------------------------

def test_default_clean_record_passes():
    checks = cmp.evaluate_record("p0_default", default_record())
    assert _overall(checks) == "PASS", [c for c in checks if c["status"] != "PASS"]


@pytest.mark.parametrize("path,value,check,reason", [
    ("resolution.actual_h", 10028, "longer_axis_within_ceiling", "AXIS_OVER_CEILING"),
    ("resolution.dim_check", "mismatch", "dim_check", "DIM_CHECK_NOT_PASS"),
    ("resolution.dim_check", "read_failed", "dim_check", "DIM_CHECK_NOT_PASS"),
    ("metrics.off_px", 441, "off_palette_pixels", "OFF_PALETTE_PIXELS_PRESENT"),
    ("metrics.hard_ratio", 0.9776, "hard_ratio_is_one", "BLENDED_EDGES_PRESENT"),
    ("applied_smooth_edges", "read_failed", "anti_aliasing_state_known",
     "SMOOTH_EDGES_READ_FAILED"),
    ("resolution.paper_fit_in", None, "scale_provenance_recorded",
     "SCALE_PROVENANCE_MISSING"),
    ("resolution.export_dpi", None, "scale_provenance_recorded",
     "SCALE_PROVENANCE_MISSING"),
])
def test_default_each_rule_discriminates(path, value, check, reason):
    checks = cmp.evaluate_record("p0_default", _mutate(default_record(), path, value))
    status, reasons = _status(checks, check)
    assert status == "FAIL"
    assert reason in reasons
    assert _overall(checks) == "FAIL"


def test_a_view_with_no_underlay_needs_no_mutation_and_still_passes():
    """Run 1b113822's case, on a P0 job: the checkpoint asks for the
    underlay off, and a view that never had one satisfies that without a
    mutation. The skip must be recorded with its reason, not inferred."""
    record = copy.deepcopy(default_record())
    record["probe_overrides"] = [
        {"override": "underlay_disabled", "value": True, "applied": False,
         "reason": "no_underlay_configured", "restored": True,
         "underlay_off_for_capture": True},
    ]
    checks = cmp.evaluate_record("p0_default", record)
    assert _overall(checks) == "PASS", [c for c in checks if c["status"] != "PASS"]


def test_an_unapplied_override_with_no_reason_fails():
    """"applied: false" with nothing saying why is indistinguishable from an
    override that was forgotten."""
    record = copy.deepcopy(default_record())
    record["probe_overrides"][0].update({"applied": False, "reason": None})
    status, reasons = _status(cmp.evaluate_record("p0_default", record),
                              "overrides_restored")
    assert status == "FAIL" and "OVERRIDE_SKIP_UNEXPLAINED" in reasons


def test_missing_underlay_bookkeeping_fails_rather_than_being_assumed():
    record = copy.deepcopy(default_record())
    record["probe_overrides"] = [
        {"override": "color_id_buffer_cap_axis_px", "value": 15000,
         "applied": True, "restored": True}]
    status, reasons = _status(cmp.evaluate_record("p0_default", record), "underlay_off")
    assert status == "FAIL" and "UNDERLAY_STATE_NOT_RECORDED" in reasons


def test_underlay_left_on_fails_the_capture():
    record = copy.deepcopy(default_record())
    record["probe_overrides"][0]["underlay_off_for_capture"] = False
    status, reasons = _status(cmp.evaluate_record("p0_default", record), "underlay_off")
    assert status == "FAIL" and "UNDERLAY_NOT_OFF_FOR_CAPTURE" in reasons


def test_an_unapplied_override_is_not_graded_on_the_rollback():
    """Nothing was changed, so a failed rollback says nothing about it."""
    record = copy.deepcopy(default_record())
    record["probe_overrides"] = [
        {"override": "underlay_disabled", "value": True, "applied": False,
         "reason": "no_underlay_configured", "restored": True,
         "underlay_off_for_capture": True},
        {"override": "color_id_buffer_cap_axis_px", "value": 15000,
         "applied": True, "restored": False},
    ]
    status, reasons = _status(cmp.evaluate_record("p0_default", record),
                              "overrides_restored")
    assert status == "FAIL" and "OVERRIDE_NOT_RESTORED" in reasons
    unrestored = [c for c in cmp.evaluate_record("p0_default", record)
                  if c["check_id"].endswith(".overrides_restored")][0]
    # Only the applied one is named.
    assert [o["override"] for o in unrestored["evidence"]["unrestored"]] == [
        "color_id_buffer_cap_axis_px"]


def test_default_unrestored_override_fails():
    record = copy.deepcopy(default_record())
    record["probe_overrides"][0]["restored"] = False
    status, reasons = _status(cmp.evaluate_record("p0_default", record), "overrides_restored")
    assert status == "FAIL" and "OVERRIDE_NOT_RESTORED" in reasons


def test_default_missing_export_dimensions_fails_rather_than_skips():
    record = _mutate(default_record(), "resolution.actual_w", None)
    status, reasons = _status(cmp.evaluate_record("p0_default", record),
                              "longer_axis_within_ceiling")
    assert status == "FAIL" and "EXPORT_DIMENSIONS_NOT_RECORDED" in reasons


# --------------------------------------------------------------------------
# hard_ratio -- the rule the human checkpoint could not enforce
# --------------------------------------------------------------------------

@pytest.mark.parametrize("case,fixture", [
    ("p0_default", default_record), ("p0_derived", derived_record),
    ("p0_override", override_record),
])
@pytest.mark.parametrize("value,reason", [
    (None, "HARD_RATIO_NOT_MEASURED"),
    ("-", "HARD_RATIO_NOT_MEASURED"),
])
def test_unmeasured_hard_ratio_is_a_failure_in_every_job(case, fixture, value, reason):
    """A capture with no edge transitions reports hard_ratio null, printed as
    '-'. A person checking for 1.0 reads that as 'no data yet'. It is a
    FAIL, in every job, and it is never skipped."""
    record = _mutate(fixture(), "metrics.hard_ratio", value)
    checks = cmp.evaluate_record(case, record)
    status, reasons = _status(checks, "hard_ratio_measured")
    assert status == "FAIL"
    assert reason in reasons
    assert _overall(checks) == "FAIL"


def test_zero_hard_edges_fails_even_when_the_ratio_is_a_number():
    """hard_edges == 0 with blended == 0 is the same empty capture; if some
    future change made the ratio 0.0 instead of None it must still fail."""
    record = default_record()
    record["metrics"] = {"hard_edges": 0, "blended_edges": 0, "hard_ratio": 0.0, "off_px": 0}
    status, reasons = _status(cmp.evaluate_record("p0_default", record), "hard_ratio_measured")
    assert status == "FAIL" and "NO_EDGES_MEASURED" in reasons


# --------------------------------------------------------------------------
# p0.derived
# --------------------------------------------------------------------------

def test_derived_clean_record_passes():
    checks = cmp.evaluate_record("p0_derived", derived_record())
    assert _overall(checks) == "PASS", [c for c in checks if c["status"] != "PASS"]


def test_derived_tolerates_one_pixel_of_prediction_error():
    record = _mutate(derived_record(), "resolution.actual_h", 9999)
    status, _ = _status(cmp.evaluate_record("p0_derived", record),
                        "derived_axis_matches_prediction")
    assert status == "PASS"


def test_derived_rejects_two_pixels_of_prediction_error():
    record = _mutate(derived_record(), "resolution.actual_h", 9998)
    status, reasons = _status(cmp.evaluate_record("p0_derived", record),
                              "derived_axis_matches_prediction")
    assert status == "FAIL" and "DERIVED_AXIS_PREDICTION_WRONG" in reasons


def test_derived_compares_the_width_under_vertical_fit():
    """The derived axis is whichever one PixelSize did NOT set."""
    record = derived_record()
    record["resolution"].update({"requested_axis": "height", "actual_w": 10000,
                                 "actual_h": 8670, "predicted_derived_px": 10000})
    status, _ = _status(cmp.evaluate_record("p0_derived", record),
                        "derived_axis_matches_prediction")
    assert status == "PASS"
    record["resolution"]["actual_w"] = 9000
    status, _ = _status(cmp.evaluate_record("p0_derived", record),
                        "derived_axis_matches_prediction")
    assert status == "FAIL"


def test_derived_requires_the_cap_to_have_bound():
    record = _mutate(derived_record(), "resolution.cap_applied", False)
    status, reasons = _status(cmp.evaluate_record("p0_derived", record), "cap_applied")
    assert status == "FAIL" and "CAP_DID_NOT_BIND" in reasons


# --------------------------------------------------------------------------
# p0.override
# --------------------------------------------------------------------------

def test_override_rejected_then_passing_on_the_halved_retry():
    checks = cmp.evaluate_record("p0_override", override_record())
    assert _overall(checks) == "PASS", [c for c in checks if c["status"] != "PASS"]


def test_override_bounded_stop_with_the_view_failed_also_passes():
    """The other honest outcome: backoff hits a bound and the view fails."""
    record = override_record()
    record["resolution"].update({
        "dim_check": "mismatch", "backoff_stop_reason": "retry_limit",
        "actual_w": 3750, "actual_h": 3615,
        "dim_check_attempts": [
            {"requested_px": 15000, "actual_w": 15000, "actual_h": 14460, "dim_check": "mismatch"},
            {"requested_px": 7500, "actual_w": 3750, "actual_h": 3615, "dim_check": "mismatch"},
            {"requested_px": 3750, "actual_w": 1875, "actual_h": 1807, "dim_check": "mismatch"},
        ],
    })
    record["failure_reason"] = "export_dim_mismatch"
    record["success"] = False
    checks = cmp.evaluate_record("p0_override", record)
    assert _overall(checks) == "PASS", [c for c in checks if c["status"] != "PASS"]


def test_override_fails_when_the_over_ceiling_request_is_accepted():
    """The outcome the whole job exists to rule out."""
    record = override_record()
    record["resolution"]["dim_check"] = "pass"
    record["resolution"]["actual_w"] = 15000
    record["resolution"]["actual_h"] = 12356
    record["resolution"]["dim_check_attempts"] = [
        {"requested_px": 15000, "accepted_px": 15000,
         "actual_w": 15000, "actual_h": 12356, "dim_check": "pass"},
    ]
    checks = cmp.evaluate_record("p0_override", record)
    first, first_reasons = _status(checks, "first_attempt_rejected")
    assert first == "FAIL" and "OVER_CEILING_REQUEST_NOT_REJECTED" in first_reasons
    nopass, nopass_reasons = _status(checks, "no_over_ceiling_pass")
    assert nopass == "FAIL" and "OVER_CEILING_EXPORT_REPORTED_PASS" in nopass_reasons
    backoff, backoff_reasons = _status(checks, "backoff_fired")
    assert backoff == "FAIL" and "BACKOFF_DID_NOT_FIRE" in backoff_reasons
    assert _overall(checks) == "FAIL"


def test_override_fails_when_backoff_exhausts_without_failing_the_view():
    record = override_record()
    record["resolution"]["dim_check"] = "mismatch"
    record["resolution"]["backoff_stop_reason"] = "grid_floor"
    record["failure_reason"] = None
    record["success"] = True
    status, reasons = _status(cmp.evaluate_record("p0_override", record), "bounded_outcome")
    assert status == "FAIL" and "VIEW_NOT_FAILED_AFTER_BACKOFF" in reasons


def test_override_fails_when_a_mismatch_reports_no_stop_reason():
    record = override_record()
    record["resolution"]["dim_check"] = "mismatch"
    record["resolution"]["backoff_stop_reason"] = None
    status, reasons = _status(cmp.evaluate_record("p0_override", record), "bounded_outcome")
    assert status == "FAIL" and "BACKOFF_STOP_REASON_MISSING" in reasons


def test_override_unrestored_cap_override_fails():
    record = copy.deepcopy(override_record())
    record["probe_overrides"][1]["restored"] = False
    status, reasons = _status(cmp.evaluate_record("p0_override", record), "overrides_restored")
    assert status == "FAIL" and "OVERRIDE_NOT_RESTORED" in reasons


# --------------------------------------------------------------------------
# Report level
# --------------------------------------------------------------------------

def test_empty_report_fails_rather_than_passing_vacuously():
    checks = cmp.evaluate_report({"records": []})
    assert _overall(checks) == "FAIL"
    assert "NO_P0_RECORDS" in checks[0]["reason_codes"]


def test_select_only_report_is_inconclusive_not_pass():
    checks = cmp.evaluate_report({"records": [
        {"case": "p0_select", "candidates": [{"view_id": 1}]}]})
    assert _overall(checks) == "INCONCLUSIVE"
    assert "MANUAL_SEMANTIC_REVIEW_REQUIRED" in checks[0]["reason_codes"]


def test_unknown_case_fails_rather_than_passing_by_default():
    checks = cmp.evaluate_record("p0_typo", default_record())
    assert _overall(checks) == "FAIL"
    assert "UNKNOWN_P0_CASE" in checks[0]["reason_codes"]


def test_full_report_mixes_cases_and_grades_each():
    checks = cmp.evaluate_report({"records": [
        default_record(), derived_record(), override_record()]})
    assert _overall(checks) == "PASS"
    prefixes = {c["check_id"].split(".")[0] for c in checks}
    assert prefixes == {"p0_default", "p0_derived", "p0_override"}


# --------------------------------------------------------------------------
# The campaign file itself
# --------------------------------------------------------------------------

def test_campaign_validates_and_schedules_as_specified():
    from tools import campaign_planner as planner
    campaign = json.loads(
        (REPO / "campaign" / "p0_export_correctness.campaign.json").read_text())
    planner.validate_campaign(campaign)

    registry = campaign["view_registry"]
    # One real view, one registry entry. The planner does not check
    # unique_id uniqueness, so a second entry for the same view validates
    # cleanly while making that view appear twice to anything keyed by
    # view_key, and giving the operator two placeholders to fill with the
    # same value and no check that they match.
    unique_ids = [v["unique_id"] for v in registry.values()]
    assert len(unique_ids) == len(set(unique_ids)), registry

    jobs = {j["job_key"]: j for stage in campaign["stages"] for j in stage["jobs"]}
    # p0.select is document-wide and reads no view geometry; it shares the
    # checkpoint view's registry entry purely to satisfy the view_key
    # requirement and to reach the document through raw_view.
    assert jobs["p0.select"]["view_key"] == jobs["p0.default"]["view_key"]
    assert "p0_enumeration_anchor" in registry[jobs["p0.select"]["view_key"]]["roles"]
    assert set(jobs) == {"p0.select", "p0.default", "p0.derived", "p0.override"}
    assert jobs["p0.select"]["manual_review_required"] is True
    for key in ("p0.default", "p0.derived", "p0.override"):
        assert jobs[key].get("manual_review_required") is not True

    deps = campaign["dependencies"]
    assert [d["job"] for d in deps] == ["p0.derived"]
    assert deps[0]["requires_job"] == "p0.select"
    # p0.default and p0.override are independent of each other and of
    # everything else -- nothing may gate them.
    gated = {d["job"] for d in deps}
    assert "p0.default" not in gated and "p0.override" not in gated

    assert jobs["p0.override"]["settings"]["cap_axis_px"] == 15000
    for key in ("p0.default", "p0.derived", "p0.override"):
        assert jobs[key]["settings"]["disable_underlay"] is True

    state = planner.initialize_state(campaign)
    batch = planner.generate_next_batch(campaign, state)
    scheduled = {j["variant"] for j in batch["jobs"]}
    assert scheduled, "the first batch must schedule work"


def test_campaign_probe_id_is_registered_on_both_sides():
    from tests.dynamo import revit_probe_registry
    from tools.analyze_stage_a_probe import SUPPORTED_PROBES
    probe_id = "stage_a_p0_export_correctness"
    assert probe_id in revit_probe_registry.PROBE_MODULES
    assert SUPPORTED_PROBES[probe_id] == "p0_export_correctness"


def test_comparator_ceiling_is_production_not_a_copy():
    from vop_interwoven.resolution_contract import MAX_STAGE_A_AXIS_PX
    assert cmp.VERIFICATION_CEILING_PX == MAX_STAGE_A_AXIS_PX == 10000


# --------------------------------------------------------------------------
# Probe-side contract (no Revit)
# --------------------------------------------------------------------------

def _probe():
    import sys
    if str(REPO / "tests" / "dynamo") not in sys.path:
        sys.path.insert(0, str(REPO / "tests" / "dynamo"))
    import probe_stage_a_p0_export_correctness as probe
    return probe


def test_probe_prediction_reproduces_the_two_measured_shapes():
    """p0.select's candidate list must agree with what the capture jobs
    actually do, so the prediction calls production's own cap_axes."""
    probe = _probe()
    # 10005 x 9645 uncapped: the fitted axis is what is over.
    fitted = probe.predict_export(66.7, 64.3, 150, "horizontal")
    assert fitted["cap_binding_axis"] == "fitted"
    assert (fitted["predicted_w"], fitted["predicted_h"]) == (10000, 9640)
    # 8700 x 10035 uncapped: the DERIVED axis is what is over -- the
    # 8695x10028 shape a fitted-axis-only cap waved through.
    derived = probe.predict_export(58.0, 66.9, 150, "horizontal")
    assert derived["cap_binding_axis"] == "derived"
    assert (derived["predicted_w"], derived["predicted_h"]) == (8670, 10000)
    assert max(derived["predicted_w"], derived["predicted_h"]) <= cmp.VERIFICATION_CEILING_PX


def test_probe_prediction_reports_no_binding_axis_when_the_view_fits():
    probe = _probe()
    fits = probe.predict_export(40.0, 30.0, 150, "horizontal")
    assert fits["cap_applied"] is False
    assert fits["cap_binding_axis"] is None


def test_probe_rejects_an_unknown_case_rather_than_running_all():
    probe = _probe()
    with pytest.raises(ValueError):
        probe.select_cases("p0_defualt")


def test_probe_record_carries_hard_ratio_none_through_unchanged():
    """The probe must not substitute 0.0 or 1.0 for an unmeasured ratio --
    that would manufacture exactly the reading the comparator exists to
    catch."""
    probe = _probe()
    record = probe.build_record(
        "p0_default", 1, "V",
        sidecar={"resolution": {"dim_check": "pass"}, "applied_smooth_edges": False},
        decoded={}, metrics={"edges": {"hard_edge_count": 0, "blended_edge_count": 0,
                                       "hard_edge_ratio": None},
                             "pixels": {"off_palette_px": 0}},
        overrides=[{"override": "underlay_disabled", "value": True, "restored": True}])
    assert record["metrics"]["hard_ratio"] is None
    status, reasons = _status(cmp.evaluate_record("p0_default", record),
                              "hard_ratio_measured")
    assert status == "FAIL" and "HARD_RATIO_NOT_MEASURED" in reasons


# --------------------------------------------------------------------------
# The runnable batch files
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name,expected_jobs", [
    ("p0_export_correctness_a", ["p0-select", "p0-default-mob-1", "p0-override-mob-1"]),
    ("p0_export_correctness_b", ["p0-derived"]),
])
def test_batch_files_validate_against_the_executor_contract(name, expected_jobs):
    """These are what Dynamo actually runs. The planner-schema campaign in
    campaign/ describes the same work for the planner flow, but the batch
    executor consumes a batch, not a campaign, and rejects one outright."""
    from tests.dynamo import revit_batch_contract as contract
    batch = json.loads((REPO / "tests" / "dynamo" / "campaigns" / (name + ".json")).read_text())
    contract.validate_batch(batch)
    assert [j["job_id"] for j in batch["jobs"]] == expected_jobs
    for job in batch["jobs"]:
        assert job["probe_id"] == "stage_a_p0_export_correctness"
        # The case a job runs travels in settings.selection; the batch job
        # schema is additionalProperties:false and has no `case` field.
        assert job["settings"]["selection"] in cmp.ALL_CASES


def test_batch_a_holds_every_job_that_needs_no_human_input():
    """p0.derived is the only job gated on a person choosing a view, so it
    is the only one held back -- splitting the other three out would cost a
    Revit round trip for nothing."""
    from tests.dynamo import revit_batch_contract as contract
    a = json.loads((REPO / "tests" / "dynamo" / "campaigns" /
                    "p0_export_correctness_a.json").read_text())
    contract.validate_batch(a)
    selections = {j["settings"]["selection"] for j in a["jobs"]}
    assert selections == {"p0_select", "p0_default", "p0_override"}
    # The capture jobs run against a real, resolvable view id.
    for job in a["jobs"]:
        assert job["view"]["element_id"] == 528698


def test_batch_b_is_the_only_one_carrying_a_placeholder():
    b = json.loads((REPO / "tests" / "dynamo" / "campaigns" /
                    "p0_export_correctness_b.json").read_text())
    assert b["jobs"][0]["view"]["element_id"] == 0
    assert "REPLACE" in b["jobs"][0]["view"]["name"]


def test_the_two_batches_share_one_campaign_id_and_differ_by_batch_id():
    """Resume keys on (campaign_id, batch_id), so batch B must not collide
    with A or it would be treated as a resume of it."""
    a = json.loads((REPO / "tests" / "dynamo" / "campaigns" /
                    "p0_export_correctness_a.json").read_text())
    b = json.loads((REPO / "tests" / "dynamo" / "campaigns" /
                    "p0_export_correctness_b.json").read_text())
    assert a["campaign_id"] == b["campaign_id"] == "p0-export-correctness"
    assert a["batch_id"] != b["batch_id"]
