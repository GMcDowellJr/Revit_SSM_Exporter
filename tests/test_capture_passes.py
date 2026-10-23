"""The single-pass predicate (vop_interwoven/capture_passes.py), every branch.

``plan_capture_passes`` is pure: membership counts in, the passes to run and
the declared skip record out. Each test names the branch it pins; the
mutation each one turns red on is in its docstring where it is not obvious.
"""
import pytest

from vop_interwoven import capture_passes as cp


def _counts(**kw):
    base = {"model_count": 3, "annotation_count": 2, "unresolved_count": 0,
            "pipeline_model_elements": 0, "datum_resolution_error": None,
            "view_reference_resolution_error": None, "has_crop_region": True}
    base.update(kw)
    return base


def test_a_view_with_both_memberships_runs_both():
    plan = cp.plan_capture_passes(_counts())
    assert plan["passes"] == ["model", "annotation"]
    assert plan["decision"] == "both_have_members"
    assert plan["skipped_pass"] is None


def test_a_drafting_view_skips_the_model_pass_with_the_counts_that_proved_it():
    plan = cp.plan_capture_passes(_counts(model_count=0, annotation_count=5,
                                          has_crop_region=False))
    assert plan["passes"] == ["annotation"]
    assert plan["skipped_pass"] == {
        "skipped": "model", "reason": "membership_empty", "model_count": 0,
        "annotation_count": 5, "unresolved_count": 0, "pipeline_model_elements": 0,
        "membership_rule": cp.MEMBERSHIP_RULE}


def test_a_view_with_no_annotation_skips_the_annotation_pass():
    plan = cp.plan_capture_passes(_counts(annotation_count=0))
    assert plan["passes"] == ["model"]
    assert plan["skipped_pass"]["skipped"] == "annotation"
    assert plan["skipped_pass"]["annotation_count"] == 0


def test_a_view_with_no_crop_region_and_no_annotation_skips_the_annotation_pass():
    plan = cp.plan_capture_passes(_counts(annotation_count=0, has_crop_region=False))
    assert plan["passes"] == ["model"]


@pytest.mark.parametrize("crop", [True, None])
def test_an_empty_model_on_a_cropped_or_unknown_view_still_runs_both(crop):
    """Only the ticks measure a cropped view's annotation extent; an unknown
    crop capability is not "no crop region"."""
    plan = cp.plan_capture_passes(_counts(model_count=0, has_crop_region=crop))
    assert plan["passes"] == ["model", "annotation"]
    assert plan["decision"] == "model_empty_frame_needs_ticks"


# Each case is one a reading of the bad key as 0 would turn into a SINGLE pass.
_WOULD_SKIP = {
    "model_count": dict(annotation_count=4, has_crop_region=False),
    "annotation_count": dict(model_count=3),
    "unresolved_count": dict(model_count=3, annotation_count=0),
    "pipeline_model_elements": dict(model_count=0, annotation_count=4,
                                    has_crop_region=False),
}


@pytest.mark.parametrize("key", sorted(_WOULD_SKIP))
@pytest.mark.parametrize("bad", [None, "0", -1, 1.0, True, "missing"])
def test_an_unreadable_count_runs_both_passes_never_reads_as_empty(key, bad):
    """Mutation (a): treat an unreadable count as 0 -> red."""
    counts = _counts(**_WOULD_SKIP[key])
    if bad == "missing":
        del counts[key]
    else:
        counts[key] = bad
    plan = cp.plan_capture_passes(counts)
    assert plan["passes"] == ["model", "annotation"], (key, bad)
    assert plan["decision"] == "membership_unreadable"
    # Control: the same counts with the key READ as 0 do skip a pass, so the
    # case discriminates.
    counts[key] = 0
    assert len(cp.plan_capture_passes(counts)["passes"]) == 1, key


def test_no_counts_at_all_runs_both():
    for counts in (None, {}, "nonsense"):
        assert cp.plan_capture_passes(counts)["decision"] == "membership_unreadable"


def test_an_unresolved_element_runs_both():
    plan = cp.plan_capture_passes(_counts(model_count=0, unresolved_count=1,
                                          has_crop_region=False))
    assert plan["decision"] == "membership_unresolved"
    assert plan["passes"] == ["model", "annotation"]


@pytest.mark.parametrize("key", ["datum_resolution_error",
                                 "view_reference_resolution_error"])
def test_an_incomplete_membership_rule_runs_both(key):
    """A datum the rule could not recognise is counted as model, so the
    annotation count could read 0 with grids drawn."""
    plan = cp.plan_capture_passes(_counts(annotation_count=0, **{key: "OST_Grids missing"}))
    assert plan["decision"] == "membership_rule_incomplete"


def test_pipeline_model_elements_with_a_zero_model_count_run_both():
    plan = cp.plan_capture_passes(_counts(model_count=0, pipeline_model_elements=2,
                                          has_crop_region=False))
    assert plan["decision"] == "membership_disagrees"


def test_both_memberships_empty_runs_both_as_before():
    plan = cp.plan_capture_passes(_counts(model_count=0, annotation_count=0,
                                          has_crop_region=False))
    assert plan["decision"] == "both_memberships_empty"
    assert plan["passes"] == ["model", "annotation"]


def test_every_decision_is_a_named_reason():
    seen = set()
    for kw in ({}, {"model_count": None}, {"unresolved_count": 1},
               {"datum_resolution_error": "x"},
               {"model_count": 0, "pipeline_model_elements": 1},
               {"model_count": 0}, {"model_count": 0, "annotation_count": 0}):
        seen.add(cp.plan_capture_passes(_counts(**kw))["decision"])
    assert seen == set(cp.BOTH_REASONS)


# --- the reader side: a declaration, never an absence ------------------------

def test_a_sidecar_that_declares_nothing_is_not_single_pass():
    assert cp.declared_passes({"view_id": 1}) == (None, None, None)
    assert not cp.is_single_pass({"view_id": 1}, "annotation")


def test_a_declared_single_pass_is_read_with_its_skip_record():
    skip = cp.plan_capture_passes(_counts(model_count=0, has_crop_region=False))[
        "skipped_pass"]
    side = {"capture_passes": ["annotation"], "skipped_pass": skip}
    assert cp.declared_passes(side) == (["annotation"], skip, None)
    assert cp.is_single_pass(side, "annotation")
    assert not cp.is_single_pass(side, "model")


def test_a_two_pass_declaration_is_both():
    assert cp.declared_passes({"capture_passes": ["model", "annotation"]}) == (
        ["model", "annotation"], None, None)


@pytest.mark.parametrize("side", [
    {"capture_passes": ["annotation"]},                                # no record
    {"capture_passes": ["annotation"], "skipped_pass": {"skipped": "annotation",
                                                        "reason": "membership_empty"}},
    {"capture_passes": ["annotation"], "skipped_pass": {"skipped": "model"}},
    {"capture_passes": "annotation"},
    {"capture_passes": []},
    {"capture_passes": ["model", "model"]},
    {"capture_passes": ["model", "texture"]},
])
def test_a_malformed_declaration_is_an_error_not_a_single_pass(side):
    passes, skipped, error = cp.declared_passes(side)
    assert passes is None and skipped is None and error
    assert not cp.is_single_pass(side, "annotation")
