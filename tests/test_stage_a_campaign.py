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
    assert set(drift.CASES) <= selected, "a D experiment is missing"
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

    49370, 146925, 871863 and 587278 come from the 2026-09-15 baseline;
    528698 (MOB 1 - LEVEL 2) and 871864 (_N_ HOSPITAL - LEVEL 3) were supplied
    directly by Greg on 2026-09-16 for the two views the baseline never named.
    """
    stated = {49370, 146925, 871863, 587278, 528698, 871864}
    for job in batch["jobs"]:
        element_id = job["view"].get("element_id")
        if element_id is not None:
            assert element_id in stated, (
                f"{job['job_id']} uses element_id {element_id}, which the baseline never "
                "stated; reference the view by name instead of guessing")


def test_the_document_title_is_an_unmistakable_placeholder(batch):
    """Running against the wrong model must fail on the title check, not
    produce plausible-looking captures of something else."""
    assert "REPLACE" in batch["document"]["expected_title"].upper()


def test_one_job_per_invocation_so_a_multi_gigabyte_run_can_be_stopped(batch):
    policy = batch["execution_policy"]
    assert policy["max_jobs_per_run"] == 1
    assert policy["resume"] is True


def test_a_view_reference_may_be_an_element_id():
    assert parse_view_reference({"element_id": 49370})["element_id"] == 49370
    assert parse_view_reference({"element_id": 49370, "name": "SEA LEVEL"})["name"] == "SEA LEVEL"


@pytest.mark.parametrize("bad", [{"element_id": True}, {"element_id": "49370"},
                                 {"element_id": 1.5}, {"view_type": "FloorPlan"}])
def test_a_malformed_view_reference_is_rejected(bad):
    with pytest.raises(ContractError):
        parse_view_reference(bad)
