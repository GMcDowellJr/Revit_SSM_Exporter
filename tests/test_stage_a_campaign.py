"""The Stage A anomaly campaign is checked in, so it is checked by tests.

The whole point of driving D1-D5 and B1/B3 from a manifest is that nobody
hand-enters a case name, a view or an output directory at run time. That only
removes human error if the manifest itself is verified here, where a mistake
costs a test failure instead of a Revit session.
"""
import json
from pathlib import Path

import pytest

from tests.dynamo import probe_stage_a_drift_onset as drift
from tests.dynamo import probe_stage_a_white_blend as blend
from tests.dynamo.revit_batch_contract import ContractError, validate_batch, parse_view_reference
from tests.dynamo.revit_probe_registry import build_registry

CAMPAIGN = Path("tests/dynamo/campaigns/stage_a_color_id_anomalies.json")


@pytest.fixture(scope="module")
def batch():
    return json.loads(CAMPAIGN.read_text(encoding="utf-8"))


def test_the_campaign_satisfies_the_batch_contract(batch):
    assert validate_batch(batch) is batch


def test_every_job_validates_through_its_probes_own_parsers(batch):
    """A dry run must reject a typo before a model is even open."""
    registry = build_registry()
    for job in batch["jobs"]:
        validator = registry[job["probe_id"]].validate_settings
        validator(dict(job["settings"]), "/tmp/out")


def test_every_probe_id_is_registered(batch):
    registry = build_registry()
    assert {job["probe_id"] for job in batch["jobs"]} <= set(registry)


def test_every_job_writes_to_its_own_directory(batch):
    """Captures collided across views when several runs shared one directory."""
    directories = [job["output_directory"] for job in batch["jobs"]]
    assert len(directories) == len(set(directories))


def test_job_ids_are_unique_and_stable(batch):
    ids = [job["job_id"] for job in batch["jobs"]]
    assert len(ids) == len(set(ids))


def test_every_selected_case_is_a_real_case(batch):
    for job in batch["jobs"]:
        module = drift if job["probe_id"] == "stage_a_drift_onset" else blend
        module.select_cases(job["settings"]["selection"])


def test_the_campaign_covers_every_experiment_in_the_brief(batch):
    selected = set()
    for job in batch["jobs"]:
        module = drift if job["probe_id"] == "stage_a_drift_onset" else blend
        selected.update(module.select_cases(job["settings"]["selection"]))
    # The D cases as they stood when this campaign was written. d7_fit_direction
    # came out of Run 1's findings and belongs to Run 2; a finished campaign is
    # a record of what was run, not a list to be back-filled.
    assert {"d1_determinism", "d2_category_load", "d3_size_sweep",
            "d4_dpi_vs_pixel_size", "d5_crop_tiles"} <= selected, "a D experiment is missing"
    assert set(blend.CASES) <= selected, "a B experiment is missing"


def test_b1_runs_before_the_b3_exports(batch):
    """B1 is read-only and decides whether the B3 exports are warranted."""
    order = [job["job_id"] for job in batch["jobs"]]
    assert order.index("b1-site-plan-4") < order.index("b3-site-plan-4")


def test_the_d2_pair_is_captured_together(batch):
    """49370 is clean where 146925 drifts at the same bounds and density; one
    without the other measures nothing."""
    views = {job["job_id"]: job["view"].get("element_id") for job in batch["jobs"]}
    assert views["d2-sea-level-clean"] == 49370
    assert views["d1-sea-level-drifted"] == 146925


def test_every_view_is_pinned_by_element_id(batch):
    """A name alone can be ambiguous and needs a lookup; the id is what makes
    the campaign reproducible without one."""
    for job in batch["jobs"]:
        assert job["view"].get("element_id") is not None, job["job_id"]


def test_a_pinned_id_still_carries_its_name_as_an_assertion(batch):
    """resolve_view checks the name against the resolved view, so an id that
    has drifted onto a different or renamed view fails loudly."""
    for job in batch["jobs"]:
        assert job["view"].get("name"), job["job_id"]


def test_ambiguous_named_views_carry_their_element_id(batch):
    """Two views are both called "SEA LEVEL"; a name alone cannot resolve them
    and resolve_view would refuse rather than guess."""
    by_name = {}
    for job in batch["jobs"]:
        view = job["view"]
        by_name.setdefault(view.get("name"), set()).add(view.get("element_id"))
    for name, ids in by_name.items():
        if len(ids) > 1:
            assert None not in ids, f"{name!r} is ambiguous and must be pinned by element_id"


def test_no_element_id_is_invented(batch):
    """Every id must have a stated source. A wrong id silently probes the
    wrong view, which is the error this campaign exists to remove.

    49370, 146925, 871863 and 587278 come from the 2026-09-15 baseline.
    528698 (MOB 1 - LEVEL 2) and 929475 ((N) HOSPITAL - LEVEL 3) come from the
    campaign Greg supplied on 2026-09-16, read off the model itself.

    929475 supersedes an earlier 871864 he gave in chat for the same view. The
    campaign file is authoritative -- it was produced against the open model,
    and 871864 sits one above LEVEL 2's 871863, which is what an assumed
    adjacency looks like. resolve_view asserts the name against the resolved
    view either way, so a wrong id fails the dry run rather than probing the
    wrong view silently.
    """
    stated = {49370, 146925, 871863, 587278, 528698, 929475}
    for job in batch["jobs"]:
        element_id = job["view"].get("element_id")
        if element_id is not None:
            assert element_id in stated, (
                f"{job['job_id']} uses element_id {element_id}, which the baseline never "
                "stated; reference the view by name instead of guessing")


def test_the_document_title_is_set_to_a_real_model(batch):
    """Running against the wrong model must fail on the title check, not
    produce plausible-looking captures of something else. The placeholder is
    gone; what matters now is that it is still a real, specific title."""
    title = batch["document"]["expected_title"]
    assert title and "REPLACE" not in title.upper()
    assert title.strip() == title, "a stray space would never match doc.Title"


def test_the_whole_campaign_runs_in_one_invocation(batch):
    """One node run does the lot. `resume` still matters: if Revit or Dynamo
    dies partway, the next run picks up from the last completed job instead of
    repeating the multi-gigabyte ones already done."""
    policy = batch["execution_policy"]
    assert policy["max_jobs_per_run"] >= len(batch["jobs"])
    assert policy["resume"] is True


def test_one_failing_job_does_not_abandon_the_rest(batch):
    """A long unattended run must not stop at the first probe that fails; a
    failed job is recorded and the campaign continues."""
    assert batch["execution_policy"]["on_job_error"] == "continue"


def test_a_view_reference_may_be_an_element_id():
    assert parse_view_reference({"element_id": 49370})["element_id"] == 49370
    assert parse_view_reference({"element_id": 49370, "name": "SEA LEVEL"})["name"] == "SEA LEVEL"


@pytest.mark.parametrize("bad", [{"element_id": True}, {"element_id": "49370"},
                                 {"element_id": 1.5}, {"view_type": "FloorPlan"}])
def test_a_malformed_view_reference_is_rejected(bad):
    with pytest.raises(ContractError):
        parse_view_reference(bad)


# --- forcing a fresh run ----------------------------------------------------

def test_the_campaign_resumes_by_default_rather_than_recapturing(batch):
    """A re-run should continue, not repeat several gigabytes of capture."""
    policy = batch["execution_policy"]
    assert policy["resume"] is True
    assert policy.get("allow_rerun", False) is False


def test_allow_rerun_is_accepted_by_the_contract():
    """The documented way to force a fresh run has to actually validate."""
    import copy
    import json as _json
    from tests.dynamo.revit_batch_contract import validate_batch
    forced = copy.deepcopy(_json.loads(CAMPAIGN.read_text(encoding="utf-8")))
    forced["execution_policy"]["allow_rerun"] = True
    assert validate_batch(forced) is forced


def test_allow_rerun_keeps_the_prior_run_lookup_that_resume_false_discards():
    """Both re-execute everything, but resume=false also skips the check that
    refuses to continue a campaign whose earlier run used a different
    document -- the guard most worth keeping when deliberately re-capturing."""
    import inspect
    from tests.dynamo import revit_batch_executor
    source = inspect.getsource(revit_batch_executor.execute_batch)
    assert 'if policy["resume"] else {}' in source
    assert 'policy.get("allow_rerun", False)' in source
    guard = inspect.getsource(revit_batch_executor._prior_successes)
    assert "belongs to a different document" in guard


def test_every_gated_b3_variant_is_selected_alongside_b1_in_the_same_job(batch):
    """B1's finding does not cross job boundaries.

    ``b1-site-plan-4`` running earlier in the campaign is the phase gate for a
    person; it is not an input to a later job. Each job that asks for a gated
    variant has to run B1 itself, or the variant is skipped and the run still
    reports completed.
    """
    for job in batch["jobs"]:
        if job["probe_id"] != "stage_a_white_blend":
            continue
        cases = blend.select_cases(job["settings"]["selection"])
        gated = [case for case in cases if case in blend.B1_GATED_CASES]
        assert not gated or "b1_query" in cases, job["job_id"]


def test_the_underlay_experiment_is_actually_in_the_campaign(batch):
    """B-H2 rests on this one capture; a campaign that never takes it is green
    and answers nothing."""
    cases = set()
    for job in batch["jobs"]:
        if job["probe_id"] == "stage_a_white_blend":
            cases.update(blend.select_cases(job["settings"]["selection"]))
    assert {"b3_baseline", "b3_underlay_off", "b3_halftone_cleared"} <= cases


# --- Run 2: pin the threshold, and test the remedy --------------------------
#
# Run 1 put the drift onset on the exported HEIGHT, somewhere in (9927, 10079].
# Run 2 narrows that and checks the remedy the finding implies, by predicting
# each capture's outcome before it is taken. Every width below is chosen for
# the height it derives, using the aspect each view actually exported at in
# Run 1 -- so a wrong prediction falsifies the height rule rather than being
# absorbed by it.

RUN2 = Path("tests/dynamo/campaigns/stage_a_run2_threshold_and_remedy.json")

# view id -> (native width px, exported height / exported width) from Run 1
RUN1_GEOMETRY = {
    871863: (8739, 10079 / 8739.0),
    528698: (29890, 12356 / 15000.0),
    929475: (8023, 11570 / 12000.0),
}


@pytest.fixture(scope="module")
def run2():
    return json.loads(RUN2.read_text(encoding="utf-8"))


def implied_heights(job):
    native, aspect = RUN1_GEOMETRY[job["view"]["element_id"]]
    return [(entry["label"], entry["requested_pixel_size"],
             round(entry["requested_pixel_size"] * aspect))
            for entry in drift.resolve_size_sweep(job["settings"]["pixel_sizes"], native)]


def job_by_id(batch, job_id):
    return [job for job in batch["jobs"] if job["job_id"] == job_id][0]


def test_run2_satisfies_the_batch_contract_and_its_probes_parsers(run2):
    assert validate_batch(run2) is run2
    registry = build_registry()
    for job in run2["jobs"]:
        registry[job["probe_id"]].validate_settings(dict(job["settings"]), "/tmp/out")


def test_run2_does_not_reuse_run_1s_batch_id(run2):
    """Same document, same manifest root. A shared batch_id would make Run 1's
    completed jobs resume-skip Run 2's."""
    first = json.loads(CAMPAIGN.read_text(encoding="utf-8"))
    assert (run2["campaign_id"], run2["batch_id"]) != (first["campaign_id"], first["batch_id"])
    assert run2["document"]["expected_title"] == first["document"]["expected_title"]


def test_the_threshold_sweep_brackets_ten_thousand_from_both_sides(run2):
    heights = [h for _, _, h in implied_heights(job_by_id(run2, "d3-threshold-pin-hosp-2"))]
    assert min(heights) < 10000 < max(heights)
    # Run 1 left (9927, 10079]; this has to cut it down, not re-measure it.
    assert max(h for h in heights if h < 10000) > 9927
    assert min(h for h in heights if h > 10000) < 10079


def test_the_height_cap_jobs_pair_a_capped_capture_with_a_known_drifted_one(run2):
    for job_id in ("d3-height-cap-mob-1", "d3-height-cap-hosp-3"):
        heights = [h for _, _, h in implied_heights(job_by_id(run2, job_id))]
        assert any(h <= drift.D4_HEIGHT_CEILING for h in heights), job_id
        assert any(h > 10079 for h in heights), job_id


def test_one_requested_width_is_predicted_clean_on_one_view_and_drifted_on_another(run2):
    """The sharpest prediction in the run, and the one that falsifies the rule
    fastest: 12000 px wide on MOB 1 derives 9885 px of height and on
    (N) HOSPITAL - LEVEL 3 derives 11570. If width mattered they would agree."""
    mob = dict((label, h) for label, _, h in implied_heights(job_by_id(run2, "d3-height-cap-mob-1")))
    hosp = dict((label, h) for label, _, h in implied_heights(job_by_id(run2, "d3-height-cap-hosp-3")))
    assert mob["12000px"] < drift.D4_HEIGHT_CEILING < hosp["12000px"]


def test_the_underlay_job_is_pinned_under_the_height_ceiling(run2):
    """(N) HOSPITAL - LEVEL 2 drifts at native, and a drifted capture's
    off-palette pixels are resample residue as well as underlay blend. The
    blend question needs a capture where they cannot be confused."""
    job = job_by_id(run2, "b3-underlay-hosp-2")
    native, aspect = RUN1_GEOMETRY[job["view"]["element_id"]]
    assert round(job["settings"]["pixel_size"] * aspect) <= drift.D4_HEIGHT_CEILING


def test_the_underlay_job_runs_b1_in_the_same_invocation(run2):
    cases = blend.select_cases(job_by_id(run2, "b3-underlay-hosp-2")["settings"]["selection"])
    assert "b1_query" in cases and "b3_underlay_off" in cases


def test_run2_drops_the_category_load_sweep(run2):
    """D2's question was whether drift depends on scene load. D3 answered it at
    fixed load, and Run 1's counter-example dissolves once the axis is read as
    height, so there is nothing left for it to decide."""
    for job in run2["jobs"]:
        if job["probe_id"] != "stage_a_drift_onset":
            continue
        assert "d2_category_load" not in drift.select_cases(job["settings"]["selection"])


def test_run2_carries_the_fit_direction_experiment(run2):
    """The one question Run 1 could not answer: under horizontal fit the height
    is always the derived axis, so "the cap is on height" and "the cap is on
    the axis Revit does not fit to" predict identically."""
    job = job_by_id(run2, "d7-fit-direction-hosp-2")
    assert "d7_fit_direction" in drift.select_cases(job["settings"]["selection"])
    native, aspect = RUN1_GEOMETRY[job["view"]["element_id"]]
    widths = [e["requested_pixel_size"]
              for e in drift.resolve_size_sweep(job["settings"]["pixel_sizes"], native)]
    # Under VERTICAL fit the request sets the height and the width is derived.
    # The deciding capture needs the height over the threshold and the derived
    # width under it; the other is the control that says vertical fit works.
    deciding = [w for w in widths if w > 10079 and (w / aspect) < 9927]
    control = [w for w in widths if w < 9927 and (w / aspect) < 9927]
    assert deciding, "no capture separates the two readings"
    assert control, "no capture establishes that vertical fit exports cleanly at all"


def test_every_run2_view_id_appeared_in_run_1(run2):
    """No invented ids: every view here is one Run 1 actually resolved."""
    first = json.loads(CAMPAIGN.read_text(encoding="utf-8"))
    known = {job["view"]["element_id"] for job in first["jobs"]}
    assert {job["view"]["element_id"] for job in run2["jobs"]} <= known
