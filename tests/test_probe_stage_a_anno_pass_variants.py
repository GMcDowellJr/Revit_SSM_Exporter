"""Pure-helper tests for tests/dynamo/probe_stage_a_anno_pass_variants.py.

WHY THIS FILE IS IN ``tests/`` AND NOT ``tests/dynamo/``. ``tests/conftest.py``
refuses to collect anything under ``tests/dynamo/`` unless
``VOP_RUN_DYNAMO_TESTS=1``, so the 35 tests beside the external-sources probe do
not run in the default suite at all. The helpers asserted below need no Revit,
no Dynamo and no document -- they are arithmetic and dict comparison -- so
putting them behind that gate would mean they never run, which is this repo's
recorded "green means nothing until you know what ran" failure exactly. The
Revit-facing half of the probe is not testable here and is not tested here;
what IS testable runs every time.

What is covered: the variant plan, B' (``expanded_frame_uv``), the per-side
excursion, the snapshot diff's float tolerance, and the restore read-back
verdict. Every one of these decides something the probe cannot re-derive later:
a wrong plan silently measures the wrong variant, a wrong B' silently under-
expands the frame, and a wrong verdict silently lets a run continue over a
document it has changed.
"""
import pytest

from tests.dynamo import probe_stage_a_anno_pass_variants as probe


# ======================================================================
# variant plan
# ======================================================================

def test_every_supported_variant_has_a_plan():
    for name in probe.SUPPORTED_VARIANTS:
        plan = probe.variant_plan(name)
        assert plan["variant"] == name


def test_an_unknown_variant_raises():
    with pytest.raises(ValueError) as excinfo:
        probe.variant_plan("v9_wishful")
    assert "v9_wishful" in str(excinfo.value)


def test_the_plan_matches_the_probe_brief_variant_by_variant():
    """The table from the brief, spelled out. A membership set edited in one
    direction only -- V2 gaining the expanded frame, say -- makes two variants
    the same capture and silently drops a candidate."""
    expected = {
        probe.V0: dict(white_filter=False, smooth_edges_off=False,
                       expanded_frame=False, zero_annotation_crop_offsets=False,
                       model_suppression="hide_categories"),
        probe.V0_OFFSETS0: dict(white_filter=False, smooth_edges_off=False,
                                expanded_frame=False,
                                zero_annotation_crop_offsets=True,
                                model_suppression="hide_categories"),
        probe.V1: dict(white_filter=True, smooth_edges_off=False,
                       expanded_frame=False, zero_annotation_crop_offsets=False,
                       model_suppression="external"),
        probe.V2: dict(white_filter=True, smooth_edges_off=True,
                       expanded_frame=False, zero_annotation_crop_offsets=False,
                       model_suppression="external"),
        probe.V3: dict(white_filter=True, smooth_edges_off=True,
                       expanded_frame=True, zero_annotation_crop_offsets=False,
                       model_suppression="external"),
    }
    assert set(expected) == set(probe.SUPPORTED_VARIANTS)
    for name, fields in expected.items():
        plan = probe.variant_plan(name)
        for key, value in fields.items():
            assert plan[key] == value, (name, key, plan[key], value)


def test_any_variant_applying_the_white_filter_also_asks_for_external_suppression():
    """A variant that applied the filter while production still hid model
    categories would measure the two suppressions stacked, and V1 would be
    indistinguishable from V0 in the one respect it is testing."""
    for name in probe.SUPPORTED_VARIANTS:
        plan = probe.variant_plan(name)
        if plan["white_filter"]:
            assert plan["model_suppression"] == "external", name
        else:
            assert plan["model_suppression"] == "hide_categories", name


def test_select_variants_defaults_to_all_and_rejects_a_typo():
    assert probe.select_variants("all") == list(probe.SUPPORTED_VARIANTS)
    assert probe.select_variants(None) == list(probe.SUPPORTED_VARIANTS)
    assert probe.select_variants("v0_control, v2_white_filter_smooth_edges_off") == [
        probe.V0, probe.V2]
    with pytest.raises(ValueError):
        probe.select_variants("v0_control,v9_wishful")
    assert probe.select_variants([probe.V2, probe.V0]) == [probe.V2, probe.V0]


# ======================================================================
# paper_margin_ft
# ======================================================================

def test_paper_margin_converts_printed_inches_to_model_feet():
    # 0.5 paper inch at 1:96 is 4 model feet.
    assert probe.paper_margin_ft(0.5, 96.0) == pytest.approx(4.0)
    # and at 1:48, half that.
    assert probe.paper_margin_ft(0.5, 48.0) == pytest.approx(2.0)


def test_paper_margin_refuses_a_non_positive_scale():
    with pytest.raises(ValueError):
        probe.paper_margin_ft(0.5, 0.0)


# ======================================================================
# rect_from_corners
# ======================================================================

def test_rect_from_corners_reads_the_four_uv_pairs_production_records():
    corners = [[1.0, 2.0], [7.0, 2.0], [7.0, 9.0], [1.0, 9.0]]
    assert probe.rect_from_corners(corners) == (1.0, 2.0, 7.0, 9.0)


def test_rect_from_corners_also_reads_a_bare_four_tuple():
    assert probe.rect_from_corners((7.0, 9.0, 1.0, 2.0)) == (1.0, 2.0, 7.0, 9.0)


def test_rect_from_corners_returns_none_rather_than_a_degenerate_rectangle():
    # None -- never (0,0,0,0), which a caller would union in and which would
    # silently drag B' out to the view origin.
    assert probe.rect_from_corners(None) is None
    assert probe.rect_from_corners([]) is None
    assert probe.rect_from_corners("nonsense") is None
    assert probe.rect_from_corners([[1.0], [2.0]]) is None


# ======================================================================
# expanded_frame_uv  --  B'
# ======================================================================

FRAME = (0.0, 0.0, 100.0, 60.0)


def test_b_prime_is_b_plus_the_margin_when_no_driver_reaches_past_it():
    inside = [("a", (10.0, 10.0, 20.0, 20.0)), ("b", (80.0, 40.0, 90.0, 50.0))]
    prime, delta, setters = probe.expanded_frame_uv(FRAME, inside, 4.0)
    assert prime == (-4.0, -4.0, 104.0, 64.0)
    assert delta == {"left": 4.0, "right": 4.0, "top": 4.0, "bottom": 4.0}
    # No driver moved an edge, so the margin alone did -- and `setters` says so
    # by reporting None rather than naming an arbitrary interior element.
    assert setters == {"left": None, "right": None, "top": None, "bottom": None}


def test_b_prime_unions_every_driver_and_names_which_one_set_each_side():
    drivers = [
        ("grid:1", (-12.0, 5.0, 5.0, 55.0)),      # sets left
        ("tag:2", (50.0, -7.0, 60.0, 3.0)),       # sets bottom
        ("viewer:3", (95.0, 20.0, 118.0, 30.0)),  # sets right
        ("text:4", (40.0, 50.0, 45.0, 66.0)),     # sets top
        ("inside:5", (30.0, 30.0, 35.0, 35.0)),   # sets nothing
    ]
    prime, delta, setters = probe.expanded_frame_uv(FRAME, drivers, 2.0)
    assert prime == (-14.0, -9.0, 120.0, 68.0)
    assert delta["left"] == pytest.approx(14.0)
    assert delta["bottom"] == pytest.approx(9.0)
    assert delta["right"] == pytest.approx(20.0)
    assert delta["top"] == pytest.approx(8.0)
    assert setters == {"left": "grid:1", "bottom": "tag:2",
                       "right": "viewer:3", "top": "text:4"}


def test_the_margin_is_applied_once_after_the_union_not_per_driver():
    """Twelve drivers on the same extreme must not produce twelve margins.

    Applying the margin per driver would make the same 0.5 inch land as 0.5 on
    a side with one driver and 6 on a side with twelve -- a quantity whose
    value depends on how many things happened to be measured, which is not a
    margin.
    """
    one = [("d0", (-10.0, 5.0, 5.0, 55.0))]
    twelve = [("d{0}".format(i), (-10.0, 5.0, 5.0, 55.0)) for i in range(12)]
    prime_one, delta_one, _ = probe.expanded_frame_uv(FRAME, one, 3.0)
    prime_twelve, delta_twelve, _ = probe.expanded_frame_uv(FRAME, twelve, 3.0)
    assert prime_one == prime_twelve
    assert delta_one == delta_twelve
    assert delta_one["left"] == pytest.approx(13.0)


def test_b_prime_ignores_a_driver_with_no_rectangle_without_failing():
    """A driver whose bbox would not resolve is the CALLER's to report, by id
    and reason. It must not take the whole computation down, and it must not
    contribute a rectangle either."""
    drivers = [("missing", None), ("real", (-5.0, 0.0, 1.0, 10.0))]
    prime, delta, setters = probe.expanded_frame_uv(FRAME, drivers, 0.0)
    assert prime[0] == pytest.approx(-5.0)
    assert setters["left"] == "real"


def test_b_prime_with_a_zero_margin_is_exactly_the_union():
    drivers = [("a", (-1.0, -2.0, 101.0, 62.0))]
    prime, delta, _ = probe.expanded_frame_uv(FRAME, drivers, 0.0)
    assert prime == (-1.0, -2.0, 101.0, 62.0)
    assert delta == {"left": 1.0, "bottom": 2.0, "right": 1.0, "top": 2.0}


def test_b_prime_never_shrinks_b():
    """Every delta is >= 0 by construction, because B is seeded into the union.

    A B' smaller than B on any side would CLIP annotations the current frame
    already holds -- a regression dressed as a fix.
    """
    drivers = [("tiny", (40.0, 30.0, 41.0, 31.0))]
    prime, delta, _ = probe.expanded_frame_uv(FRAME, drivers, 0.0)
    assert prime == FRAME
    assert all(value >= 0.0 for value in delta.values())


def test_b_prime_refuses_a_degenerate_frame_and_a_negative_margin():
    with pytest.raises(ValueError):
        probe.expanded_frame_uv((0.0, 0.0, 0.0, 10.0), [], 1.0)
    with pytest.raises(ValueError):
        probe.expanded_frame_uv(FRAME, [], -1.0)


# ======================================================================
# bbox_excursion_past_frame
# ======================================================================

def test_excursion_reports_the_furthest_reach_on_each_side_independently():
    rects = [(-3.0, 10.0, 10.0, 20.0), (-1.0, -6.0, 5.0, 5.0),
             (90.0, 30.0, 107.0, 70.0)]
    out = probe.bbox_excursion_past_frame(FRAME, rects)
    assert out["left"] == pytest.approx(3.0)
    assert out["bottom"] == pytest.approx(6.0)
    assert out["right"] == pytest.approx(7.0)
    assert out["top"] == pytest.approx(10.0)
    assert out["count"] == 3


def test_excursion_is_zero_not_negative_when_everything_is_inside():
    out = probe.bbox_excursion_past_frame(FRAME, [(10.0, 10.0, 20.0, 20.0)])
    assert out == {"left": 0.0, "right": 0.0, "bottom": 0.0, "top": 0.0,
                   "count": 1}


def test_excursion_skips_a_null_rectangle_and_does_not_count_it():
    out = probe.bbox_excursion_past_frame(FRAME, [None, (-2.0, 0.0, 1.0, 1.0)])
    assert out["count"] == 1
    assert out["left"] == pytest.approx(2.0)


# ======================================================================
# _diff  --  the snapshot comparator
# ======================================================================

def test_diff_tolerates_float_round_trip_noise_but_not_a_real_move():
    """Revit hands back doubles for crop-box corners and crop offsets.

    An exact ``!=`` would report a restore failure on the last bit of a value
    that round-tripped through the API, and the run STOPS on a restore failure
    -- so a false alarm here costs the whole view's evidence. 1e-9 ft is 3e-7
    inch: far below anything actionable, far above float noise.
    """
    assert probe._diff({"x": 1.0}, {"x": 1.0 + 1e-13}) == []
    real = probe._diff({"x": 1.0}, {"x": 1.001})
    assert len(real) == 1 and real[0]["path"] == "x"


def test_diff_reports_a_missing_or_added_key():
    out = probe._diff({"a": 1}, {"b": 1})
    paths = sorted(entry["path"] for entry in out)
    assert paths == ["a", "b"]


def test_diff_compares_lists_elementwise_and_reports_a_length_change():
    assert probe._diff({"v": [1.0, 2.0]}, {"v": [1.0, 2.0]}) == []
    changed = probe._diff({"v": [1.0, 2.0]}, {"v": [1.0, 9.0]})
    assert changed[0]["path"] == "v[1]"
    resized = probe._diff({"v": [1.0]}, {"v": [1.0, 2.0]})
    assert resized[0]["path"] == "v"


def test_diff_finds_a_change_nested_inside_a_three_valued_record():
    before = {"crop_box": {"state": "value", "value": {"active": True}}}
    after = {"crop_box": {"state": "value", "value": {"active": False}}}
    out = probe._diff(before, after)
    assert out[0]["path"] == "crop_box.value.active"


# ======================================================================
# restore_readback_verdict
# ======================================================================

def _snapshot(crop_active=True, smooth=False, offsets=(1.0, 1.0, 1.0, 1.0),
              hidden=None, filters=None, template=-1, style="Wireframe"):
    return {
        "crop_box": {"state": "value",
                     "value": {"min": [0.0, 0.0, 0.0], "max": [10.0, 10.0, 0.0],
                               "active": crop_active}},
        "smooth_edges": {"state": "value", "value": smooth},
        "annotation_crop_offsets": {
            "state": "value",
            "value": dict(zip(probe.ANNOTATION_CROP_OFFSET_PROPERTIES,
                              [float(v) for v in offsets]))},
        "annotation_crop_active": {"state": "value", "value": False},
        "model_category_visibility": {
            "state": "value",
            "value": {"hidden_by_id": hidden or {"10": False}, "unreadable": []}},
        "view_filters": {"state": "value", "value": filters or {}},
        "view_template_id": template,
        "display_style": style,
    }


def test_an_identical_pair_of_snapshots_is_restored():
    verdict = probe.restore_readback_verdict(_snapshot(), _snapshot())
    assert verdict["overall"] == "restored"
    for name in ("crop_box", "smooth_edges", "annotation_crop_offsets",
                 "model_category_visibility", "view_filters",
                 "view_template_id", "display_style"):
        assert verdict[name]["status"] == "restored", name


@pytest.mark.parametrize("field,kwargs", [
    ("crop_box", {"crop_active": False}),
    ("smooth_edges", {"smooth": True}),
    ("annotation_crop_offsets", {"offsets": (0.0, 0.0, 0.0, 0.0)}),
    ("model_category_visibility", {"hidden": {"10": True}}),
    ("view_filters", {"filters": {"901": {"enabled": False, "visible": True}}}),
    ("view_template_id", {"template": 4242}),
    ("display_style", {"style": "FlatColors"}),
])
def test_each_obligation_fails_on_its_own_and_names_itself(field, kwargs):
    """Seven obligations, seven independent assertions.

    One rolled-up boolean would make "the restore failed" the whole report,
    and "the crop box came back but the annotation crop offsets did not" is
    the sentence that is actually actionable.
    """
    verdict = probe.restore_readback_verdict(_snapshot(), _snapshot(**kwargs))
    assert verdict[field]["status"] == "not_restored", verdict
    assert verdict["overall"] == "not_restored"
    others = [name for name in verdict
              if name not in (field, "overall") and isinstance(verdict[name], dict)]
    assert all(verdict[name]["status"] == "restored" for name in others), verdict


def test_an_unreadable_property_is_unverified_and_not_restored():
    """"Could not read it" is not "it came back".

    Production's own override read-back follows the same rule, and it is the
    rule that keeps a probe from certifying a view it never managed to
    inspect.
    """
    after = _snapshot()
    after["smooth_edges"] = {"state": "unavailable",
                             "reason": "GetViewDisplayModel raised"}
    verdict = probe.restore_readback_verdict(_snapshot(), after)
    assert verdict["smooth_edges"]["status"] == "unverified"
    assert "GetViewDisplayModel raised" in verdict["smooth_edges"]["reason"]
    assert verdict["overall"] == "unverified"


def test_a_not_restored_field_outranks_an_unverified_one_in_the_overall():
    after = _snapshot(crop_active=False)
    after["smooth_edges"] = {"state": "unavailable", "reason": "unreadable"}
    verdict = probe.restore_readback_verdict(_snapshot(), after)
    assert verdict["overall"] == "not_restored"


def test_the_probe_filter_is_checked_by_id_not_by_the_filter_map_diff():
    """A filter deleted from the project but left on the view -- or the
    reverse -- can leave an identical filter MAP. So the id is asked for
    directly, rather than inferred from a diff that both cases can satisfy.
    """
    before = _snapshot(filters={})
    after = _snapshot(filters={"5551": {"enabled": True, "visible": True}})
    verdict = probe.restore_readback_verdict(before, after, expect_filter_absent=5551)
    assert verdict["probe_filter_deleted"]["status"] == "not_restored"
    assert "5551" in verdict["probe_filter_deleted"]["reason"]

    clean = probe.restore_readback_verdict(_snapshot(filters={}),
                                           _snapshot(filters={}),
                                           expect_filter_absent=5551)
    assert clean["probe_filter_deleted"]["status"] == "restored"
    assert clean["overall"] == "restored"


def test_the_probe_filter_check_is_unverified_when_the_filters_cannot_be_read():
    after = _snapshot()
    after["view_filters"] = {"state": "unavailable", "reason": "GetFilters raised"}
    verdict = probe.restore_readback_verdict(_snapshot(), after,
                                             expect_filter_absent=5551)
    assert verdict["probe_filter_deleted"]["status"] == "unverified"


def test_no_probe_filter_id_means_no_probe_filter_obligation():
    """V0 and V0-offsets0 create no filter, so asserting one was deleted would
    be asserting something the variant never did."""
    verdict = probe.restore_readback_verdict(_snapshot(), _snapshot())
    assert "probe_filter_deleted" not in verdict


# ======================================================================
# model_reexport_verdict  --  the control is not optional
# ======================================================================

def _sha(value):
    return {"sha256": {"state": "value", "value": value}}


def test_the_reexport_verdict_needs_a_repeatable_control_before_it_says_anything():
    """Two exports of the untouched view already differing makes the
    post-variant comparison meaningless. It must say NOT APPLICABLE, not
    report the comparison anyway."""
    verdict = probe.model_reexport_verdict(
        {"state": "value", "value": "AAA"}, _sha("BBB"), _sha("AAA"))
    assert verdict["status"] == "not_applicable"
    assert verdict["control_repeatable"] is False
    assert "differ on this host" in verdict["reason"]


def test_the_reexport_verdict_reports_unchanged_when_the_control_holds():
    verdict = probe.model_reexport_verdict(
        {"state": "value", "value": "AAA"}, _sha("AAA"), _sha("AAA"))
    assert verdict["status"] == "unchanged"
    assert verdict["control_repeatable"] is True


def test_the_reexport_verdict_reports_changed_when_the_control_holds_and_it_moved():
    verdict = probe.model_reexport_verdict(
        {"state": "value", "value": "AAA"}, _sha("AAA"), _sha("CCC"))
    assert verdict["status"] == "changed"
    assert "renders differently" in verdict["reason"]


def test_a_missing_hash_is_not_applicable_rather_than_a_match():
    for original, control, after in (
            (None, _sha("AAA"), _sha("AAA")),
            ({"state": "value", "value": "AAA"},
             {"sha256": {"state": "unavailable", "reason": "no file"}},
             _sha("AAA")),
            ({"state": "value", "value": "AAA"}, _sha("AAA"),
             {"sha256": {"state": "unavailable", "reason": "no file"}})):
        verdict = probe.model_reexport_verdict(original, control, after)
        assert verdict["status"] == "not_applicable", verdict


# ======================================================================
# the registry adapter
# ======================================================================

def _adapter():
    from tests.dynamo.revit_probe_registry import build_registry
    return build_registry()["stage_a_anno_pass_variants"]


def test_the_registry_exposes_the_probe():
    from tests.dynamo.revit_probe_registry import PROBE_MODULES, build_registry
    assert PROBE_MODULES["stage_a_anno_pass_variants"] == (
        "tests.dynamo.probe_stage_a_anno_pass_variants")
    assert "stage_a_anno_pass_variants" in build_registry()


def test_validation_accepts_the_probes_real_settings():
    _adapter().validate_settings(
        {"selection": "{0},{1}".format(probe.V0, probe.V3),
         "export_dpi": 150, "expanded_frame_margin_in": 0.5,
         "authored_override_scan_max": 5000,
         "white_filter_include_view_only_model_categories": False,
         "model_reexport_check": True},
        "C:/out")


def test_validation_rejects_an_unknown_setting_before_anything_runs():
    with pytest.raises(ValueError) as excinfo:
        _adapter().validate_settings({"smooth_edges": True}, "C:/out")
    assert "smooth_edges" in str(excinfo.value)


def test_validation_rejects_a_misspelled_variant():
    """The point of validating in the dry run: a campaign typo must be caught
    before the model is open, not after five exports."""
    with pytest.raises(ValueError) as excinfo:
        _adapter().validate_settings({"selection": "v2_smooth_edges"}, "C:/out")
    assert "v2_smooth_edges" in str(excinfo.value)


def test_validation_maps_a_campaign_job_variant_onto_selection():
    resolved = _adapter().validate_settings({}, "C:/out", variant=probe.V1)
    assert resolved["selection"] == probe.V1


def test_validation_refuses_a_conflicting_variant_and_selection():
    with pytest.raises(ValueError) as excinfo:
        _adapter().validate_settings({"selection": probe.V1}, "C:/out",
                                     variant=probe.V2)
    assert "conflicting" in str(excinfo.value)


@pytest.mark.parametrize("settings", [
    {"export_dpi": 0},
    {"export_dpi": -5},
    {"export_dpi": "big"},
    {"expanded_frame_margin_in": 0},
    {"authored_override_scan_max": -1},
    {"authored_override_scan_max": True},
    {"authored_override_scan_max": 1.5},
    {"model_reexport_check": "yes"},
    {"white_filter_include_view_only_model_categories": 1},
])
def test_validation_rejects_a_malformed_value(settings):
    with pytest.raises(ValueError):
        _adapter().validate_settings(settings, "C:/out")


def test_validation_requires_an_output_directory():
    with pytest.raises(ValueError):
        _adapter().validate_settings({}, "")


# ======================================================================
# the white-override preflight
# ======================================================================

class _FullOGS(object):
    """An OverrideGraphicSettings stand-in exposing every needed setter."""

    def __init__(self, omit=()):
        self._omit = set(omit)

    def __getattr__(self, name):
        if name in self._omit or name not in probe.WHITE_OVERRIDE_SETTERS:
            raise AttributeError(name)
        return lambda *args: None


def test_the_preflight_passes_when_every_setter_and_a_solid_pattern_exist():
    record = probe.white_override_capability_record(_FullOGS(), 4242)
    assert record["state"] == "value"
    assert record["missing_setters"] == []
    assert record["solid_pattern_id"] == 4242


def test_the_preflight_names_the_missing_setters_rather_than_just_failing():
    """"V1-V3 were skipped" is not actionable.
    "SetCutBackgroundPatternId is absent on this host" is.
    """
    record = probe.white_override_capability_record(
        _FullOGS(omit=("SetCutBackgroundPatternId",
                       "SetSurfaceBackgroundPatternVisible")), 4242)
    assert record["state"] == "unavailable"
    assert set(record["missing_setters"]) == {
        "SetCutBackgroundPatternId", "SetSurfaceBackgroundPatternVisible"}
    assert "SetCutBackgroundPatternId" in record["reason"]


def test_the_preflight_refuses_a_project_with_no_solid_pattern():
    """A white override with no pattern id leaves the pattern UNCHANGED.

    That renders model fills in their authored colour while the sidecar says a
    white filter was applied -- a variant that measured nothing and reported
    success, which is the single worst outcome a probe can produce.
    """
    record = probe.white_override_capability_record(_FullOGS(), None)
    assert record["state"] == "unavailable"
    assert "solid" in record["reason"].lower()
    assert record["missing_setters"] == []


def test_the_preflight_carries_a_pattern_lookup_exception_verbatim():
    record = probe.white_override_capability_record(
        _FullOGS(), None, solid_pattern_error="RuntimeError: doc is closed")
    assert record["state"] == "unavailable"
    assert "doc is closed" in record["reason"]


def test_missing_override_setters_checks_every_name_in_the_set():
    """Each entry in the set is falsified INDIVIDUALLY.

    An "every setter is present" assertion made against a stub that exposes
    everything cannot tell whether the set has the right names in it -- the
    same lesson as the `except X as e:` undercount and the missing
    GetInstanceGeometry token, one layer up.
    """
    assert probe.missing_override_setters(_FullOGS()) == []
    for name in probe.WHITE_OVERRIDE_SETTERS:
        assert probe.missing_override_setters(_FullOGS(omit=(name,))) == [name], name
    # And the set is not empty, or the loop above would assert nothing.
    assert len(probe.WHITE_OVERRIDE_SETTERS) >= 16


# ======================================================================
# FINDING 1 (PR #215, P1): did the variant MEASURE its candidate?
# ======================================================================

def test_a_variant_that_got_what_it_asked_for_is_measured():
    plan = probe.variant_plan(probe.V2)
    check = probe.variant_measurement_check(
        plan, {"applied_smooth_edges": False, "model_suppression_mode": "external"})
    assert check["measured"] is True
    assert check["unmet"] == []
    # And it says WHAT it checked, so a mutation that stops checking something
    # is visible in the record rather than only in a boolean.
    assert any("applied_smooth_edges" in item for item in check["checked"])
    assert any("model_suppression_mode" in item for item in check["checked"])


@pytest.mark.parametrize("applied", ["read_failed", "unchanged (failed)",
                                     "not_attempted", None, True])
def test_v2_did_not_measure_when_smooth_edges_was_not_confirmed_off(applied):
    """Production does NOT raise when the ViewDisplayModel read or write fails.

    That is correct -- an unconfirmed AA state costs decode confidence, not the
    export -- but it means V2/V3 can return a real TIFF that measured the same
    behaviour as V1. Anything but a confirmed ``False`` is not AA off.
    """
    plan = probe.variant_plan(probe.V2)
    check = probe.variant_measurement_check(
        plan, {"applied_smooth_edges": applied,
               "model_suppression_mode": "external"})
    assert check["measured"] is False
    assert len(check["unmet"]) == 1
    assert check["unmet"][0]["production_reported"] == applied
    assert "F3" in check["unmet"][0]["why_it_matters"]


def test_a_white_filter_variant_did_not_measure_when_production_hid_categories():
    """The same defect one switch over, which the review did not name.

    A V1-V3 capture reporting ``hide_categories`` hid model categories and
    disabled the probe's own white filter: it measured V0's suppression under
    V1's name. Fixing only the SmoothEdges half would have left this one.
    """
    plan = probe.variant_plan(probe.V1)
    check = probe.variant_measurement_check(
        plan, {"model_suppression_mode": "hide_categories"})
    assert check["measured"] is False
    assert check["unmet"][0]["production_reported"] == "hide_categories"
    assert "V0's suppression" in check["unmet"][0]["why_it_matters"]


def test_the_control_variants_are_measured_by_their_own_standard():
    """V0 asks for no mutation, so it must not be judged against one.

    Without this the fix would mark every control DID_NOT_MEASURE and the
    baseline every other variant is read against would vanish.
    """
    for name in (probe.V0, probe.V0_OFFSETS0):
        check = probe.variant_measurement_check(
            probe.variant_plan(name),
            {"model_suppression_mode": "hide_categories",
             "applied_smooth_edges": "not_attempted"})
        assert check["measured"] is True, name


def test_both_switches_are_checked_independently():
    plan = probe.variant_plan(probe.V3)
    check = probe.variant_measurement_check(
        plan, {"applied_smooth_edges": "read_failed",
               "model_suppression_mode": "hide_categories"})
    assert check["measured"] is False
    assert len(check["unmet"]) == 2


def test_missing_metadata_is_not_measured_rather_than_assumed_fine():
    """An annotation pass that raised leaves no metadata at all. That is not
    evidence that the mutation applied."""
    check = probe.variant_measurement_check(probe.variant_plan(probe.V2), {})
    assert check["measured"] is False
    check_none = probe.variant_measurement_check(probe.variant_plan(probe.V2), None)
    assert check_none["measured"] is False


# ======================================================================
# FINDING 2 (PR #215, P2): finalize, THEN write
# ======================================================================

def _native_report(variant_conclusions):
    return {
        "variants": [
            {"variant": "v{0}".format(index), "skipped": False,
             "document_safe": conclusion != "FAIL",
             "conclusion": conclusion,
             "measurement": {"measured": conclusion != "DID_NOT_MEASURE",
                             "unmet": ([{"requested": "SmoothEdges off"}]
                                       if conclusion == "DID_NOT_MEASURE" else [])}}
            for index, conclusion in enumerate(variant_conclusions)],
        "conclusion": "INCONCLUSIVE",
    }


@pytest.mark.parametrize("conclusions,expected", [
    ([], "INCONCLUSIVE"),
    (["RAN", "RAN"], "RAN"),
    (["RAN", "FAIL"], "FAIL"),
    (["RAN", "ERRORED"], "ERRORED"),
    (["RAN", "DID_NOT_MEASURE"], "DID_NOT_MEASURE"),
    # A document-safety failure outranks a measurement one.
    (["DID_NOT_MEASURE", "FAIL"], "FAIL"),
])
def test_finalize_sets_the_run_conclusion(conclusions, expected):
    report = _native_report(conclusions)
    probe.finalize_native_report(report, {"model_tiff": "m.tiff"})
    assert report["conclusion"] == expected
    assert report["paths"] == {"model_tiff": "m.tiff"}
    assert report["report_finalized"] is True


def test_finalize_lists_the_variants_that_did_not_measure():
    report = _native_report(["RAN", "DID_NOT_MEASURE"])
    probe.finalize_native_report(report, {})
    assert [entry["variant"] for entry in report["variants_that_did_not_measure"]] == [
        "v1"]
    assert report["variants_that_did_not_measure"][0]["unmet"]


def test_the_persisted_json_carries_the_finalized_conclusion_and_paths(tmp_path):
    """READ THE FILE BACK, not the returned object.

    The returned in-memory envelope was always correct; the PERSISTED file --
    the one the analyzer and Greg consume -- was serialised before the
    conclusion and paths were set, so it permanently said INCONCLUSIVE with no
    paths. A test that inspected the return value could not see that, which is
    why this one reloads from disk.
    """
    import json

    report = _native_report(["RAN", "RAN"])
    probe.finalize_native_report(report, {"model_tiff": "m.tiff"})
    path = probe._write_combined(report, str(tmp_path), "View_1")
    assert path is not None

    with open(path, encoding="utf-8") as handle:
        persisted = json.load(handle)
    assert persisted["conclusion"] == "RAN"
    assert persisted["conclusion"] != "INCONCLUSIVE"
    assert persisted["paths"]["model_tiff"] == "m.tiff"
    assert persisted["report_finalized"] is True


def test_writing_an_unfinalized_report_is_REFUSED(tmp_path):
    """The refusal is what keeps the order from silently regressing.

    A future call site that serialises before finalising fails loudly here
    rather than persisting an unfinished report -- the ordering was wrong once
    and nothing about the call sequence made that visible.
    """
    report = _native_report(["RAN"])
    with pytest.raises(RuntimeError) as excinfo:
        probe._write_combined(report, str(tmp_path), "View_1")
    assert "finalize_native_report" in str(excinfo.value)
    assert not list(tmp_path.iterdir()), "nothing may be written on refusal"


def test_a_write_failure_is_recorded_and_returns_none(tmp_path):
    report = _native_report(["RAN"])
    probe.finalize_native_report(report, {})
    missing = tmp_path / "does" / "not" / "exist"
    assert probe._write_combined(report, str(missing), "View_1") is None
    assert "combined_json_write_error" in report


# ======================================================================
# the CALL SITE that consumes variant_measurement_check
# ======================================================================

_MEASURED = {"measured": True, "unmet": []}
_NOT_MEASURED = {"measured": False, "unmet": [{"requested": "SmoothEdges off"}]}


@pytest.mark.parametrize("document_safe,exceptions,tiff,measurement,expected", [
    (True, [], "a.tiff", _MEASURED, "RAN"),
    # THE CASE THE FIRST REVIEW ROUND FOUND: a real TIFF, a safe document, and
    # production declined the mutation. Previously "RAN".
    (True, [], "a.tiff", _NOT_MEASURED, "DID_NOT_MEASURE"),
    (True, [], None, _MEASURED, "INCONCLUSIVE"),
    (True, [{"stage": "annotation_pass"}], "a.tiff", _MEASURED, "ERRORED"),
    (False, [], "a.tiff", _MEASURED, "FAIL"),
    # Document safety outranks everything, including a missing measurement.
    (False, [], "a.tiff", _NOT_MEASURED, "FAIL"),
    # An exception outranks a missing measurement.
    (True, [{"stage": "x"}], "a.tiff", _NOT_MEASURED, "ERRORED"),
    # A missing measurement record is not "fine".
    (True, [], "a.tiff", {}, "DID_NOT_MEASURE"),
    (True, [], "a.tiff", None, "DID_NOT_MEASURE"),
])
def test_variant_conclusion_at_the_call_site(document_safe, exceptions, tiff,
                                             measurement, expected):
    """Bind the DECISION, not just the checker it calls.

    The first version of this fix left the gate inline in ``_run_variant``.
    Mutating its measurement branch to ``elif False:`` kept the whole suite
    green, because every test bound ``variant_measurement_check`` and nothing
    bound the site that consumes it -- CLAUDE.md's "exercise the call site",
    reproduced in the fix for a review finding about exactly this shape of
    silence.
    """
    # capture_success=True/no faults, so these cases isolate the OTHER gates.
    # Production's own verdict has its own parametrization below; leaving it
    # unset here would default to None, which now -- correctly -- disqualifies.
    assert probe.variant_conclusion(
        document_safe, exceptions, tiff, measurement,
        capture_success=True, capture_faults=[]) == expected


def test_run_variant_uses_variant_conclusion_rather_than_its_own_chain():
    """Pin that the extraction is actually WIRED.

    ``variant_conclusion`` could be correct, tested and never called. Reading
    the source for the call is crude, and it is the only check available
    without a Revit host -- so it is done explicitly rather than assumed.
    """
    import inspect

    source = inspect.getsource(probe._run_variant)
    assert "variant_conclusion(" in source, (
        "_run_variant must delegate to variant_conclusion")
    assert "variant_measurement_check(" in source, (
        "_run_variant must compute the measurement it passes")
    # And the inline chain it replaced is gone, so there is one decision.
    assert 'report["conclusion"] = "RAN"' not in source


# ======================================================================
# FINDING A (PR #215 round 2, P1): production's own verdict on the capture
# ======================================================================

@pytest.mark.parametrize("failure_reason", [
    "annotation_frame_not_applied",
    "annotation_lattice_mismatch",
    "export_dim_mismatch",
    "annotation_overrides_unverified",
    "annotation_collection_failed",
])
def test_a_capture_production_declared_invalid_is_not_RAN(failure_reason):
    """These faults arise AFTER the requested switch was applied.

    ``variant_measurement_check`` inspects the two switches and nothing else, so
    it cannot see them -- which is why discarding production's own ``success``
    let a capture it called invalid come back ``RAN``.
    """
    assert probe.variant_conclusion(
        True, [], "a.tiff", _MEASURED,
        capture_success=False,
        capture_faults=[{"fault": failure_reason, "detail": "..."}],
    ) == "CAPTURE_FAILED"


def test_faults_present_with_success_true_is_still_a_failed_capture():
    """Belt and braces: the fault list alone is disqualifying.

    ``success`` is derived from the fault list in production, so the two cannot
    normally disagree -- and a check that trusted only the boolean would go
    silent the day they did.
    """
    assert probe.variant_conclusion(
        True, [], "a.tiff", _MEASURED, capture_success=True,
        capture_faults=[{"fault": "export_dim_mismatch"}]) == "CAPTURE_FAILED"


def test_an_unreadable_capture_success_is_not_treated_as_a_pass():
    """``None`` means the annotation pass returned nothing to read it from.

    That is not the same as True, and a capture whose validity is unknown is not
    a capture that passed.
    """
    assert probe.variant_conclusion(
        True, [], "a.tiff", _MEASURED, capture_success=None,
        capture_faults=None) == "CAPTURE_FAILED"


def test_a_clean_capture_still_reaches_RAN():
    """THE CONTROL. Without it every assertion above would also pass against a
    gate that returned CAPTURE_FAILED unconditionally, and nothing would ever
    be reported as a usable capture again."""
    assert probe.variant_conclusion(
        True, [], "a.tiff", _MEASURED, capture_success=True,
        capture_faults=[]) == "RAN"


def test_the_ordering_puts_productions_verdict_before_the_switch_check():
    """A capture that is BOTH invalid and did not measure reports the more
    fundamental fact. Both are recorded on the variant either way, so neither is
    hidden by whichever wins."""
    assert probe.variant_conclusion(
        True, [], "a.tiff", _NOT_MEASURED, capture_success=False,
        capture_faults=[{"fault": "annotation_frame_not_applied"}]
    ) == "CAPTURE_FAILED"
    # And document safety still outranks both.
    assert probe.variant_conclusion(
        False, [], "a.tiff", _NOT_MEASURED, capture_success=False,
        capture_faults=[{"fault": "annotation_frame_not_applied"}]) == "FAIL"


def test_run_variant_passes_productions_verdict_to_the_gate():
    """Pin the WIRING, not just the gate. The previous round's lesson: a correct
    decision function that the call site does not feed is a decision nothing
    makes."""
    import inspect

    source = inspect.getsource(probe._run_variant)
    assert "capture_success=" in source
    assert "capture_faults=" in source


def test_finalize_surfaces_failed_captures_and_the_run_conclusion_follows():
    report = {
        "variants": [
            {"variant": "v0", "skipped": False, "document_safe": True,
             "conclusion": "RAN", "measurement": {"measured": True, "unmet": []}},
            {"variant": "v1", "skipped": False, "document_safe": True,
             "conclusion": "CAPTURE_FAILED",
             "measurement": {"measured": True, "unmet": []},
             "annotation_pass": {"failure_reason": "annotation_frame_not_applied",
                                 "capture_faults": [{"fault": "annotation_frame_not_applied"}]}},
        ],
        "conclusion": "INCONCLUSIVE",
    }
    probe.finalize_native_report(report, {})
    assert report["conclusion"] == "CAPTURE_FAILED"
    assert [entry["variant"] for entry in report["variants_with_failed_captures"]] == [
        "v1"]
    assert report["variants_with_failed_captures"][0]["failure_reason"] == (
        "annotation_frame_not_applied")
