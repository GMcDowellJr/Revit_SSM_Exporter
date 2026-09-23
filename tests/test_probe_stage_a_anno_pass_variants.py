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

What is covered: the variant plan, the F1/F2 helpers (fiducial colours and
pair choice, the crop-loop record), the snapshot diff's float tolerance, and
the restore read-back verdict. Every one of these decides something the probe
cannot re-derive later: a wrong plan silently measures the wrong variant, a
wrong fiducial choice silently fits one axis to noise, and a wrong verdict
silently lets a run continue over a document it has changed.
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
        probe.variant_plan("v99_wishful")
    assert "v99_wishful" in str(excinfo.value)


def test_the_plan_matches_the_round_2_revised_brief_variant_by_variant():
    """The round-2 (revised) table, spelled out. A membership set edited in one
    direction only -- V7 gaining the fiducials, say -- makes two variants the
    same capture and silently drops a candidate."""
    expected = {
        probe.V0: dict(white_membership=False, smooth_edges_off=False,
                       crop_mode="frame_b", crop_box_visible=False,
                       fiducials=False, own_model_pass=False,
                       registration_marks=False, category_layer=True,
                       model_suppression="hide_categories"),
        probe.V7: dict(white_membership=True, smooth_edges_off=False,
                       crop_mode="untouched", crop_box_visible=False,
                       fiducials=False, own_model_pass=False,
                       registration_marks=False, category_layer=True,
                       model_suppression="external"),
        probe.V8: dict(white_membership=True, smooth_edges_off=True,
                       crop_mode="untouched", crop_box_visible=True,
                       fiducials=True, own_model_pass=True,
                       registration_marks=False, category_layer=True,
                       model_suppression="external"),
        # Round 3: V8 plus the marks, and V7 minus the category layer. Each
        # differs from its base in exactly one field, which is what makes the
        # pair a measurement of that field.
        probe.V9: dict(white_membership=True, smooth_edges_off=True,
                       crop_mode="untouched", crop_box_visible=True,
                       fiducials=True, own_model_pass=True,
                       registration_marks=True, category_layer=True,
                       model_suppression="external"),
        probe.V10: dict(white_membership=True, smooth_edges_off=False,
                        crop_mode="untouched", crop_box_visible=False,
                        fiducials=False, own_model_pass=False,
                        registration_marks=False, category_layer=False,
                        model_suppression="external"),
    }
    assert set(expected) == set(probe.SUPPORTED_VARIANTS)
    assert probe.SUPPORTED_VARIANTS == ("v0_control", "v7_no_crop",
                                        "v8_no_crop_fiducials",
                                        "v9_registration_marks",
                                        "v10_element_only_white")
    for base, variant, field in ((probe.V8, probe.V9, "registration_marks"),
                                 (probe.V7, probe.V10, "category_layer")):
        a, b = probe.variant_plan(base), probe.variant_plan(variant)
        assert [k for k in a if k != "variant" and a[k] != b[k]] == [field], (
            base, variant)
    for name, fields in expected.items():
        plan = probe.variant_plan(name)
        for key, value in fields.items():
            assert plan[key] == value, (name, key, plan[key], value)
    # The retired variants are NAMED with a reason, not merely absent -- a reader
    # comparing an earlier report against this one needs to know they were
    # dropped on evidence.
    assert set(probe.RETIRED_VARIANTS) == {
        "v0_offsets0", "v1_white_filter", "v2_white_filter_smooth_edges_off",
        "v3_white_filter_smooth_edges_off_expanded_frame",
        "v4_white_membership", "v5_white_membership_smooth_edges_off",
        "v6_white_membership_expanded_frame"}
    assert "falsified" in probe.RETIRED_VARIANTS["v0_offsets0"]
    assert "DROPPED" in probe.RETIRED_VARIANTS["v6_white_membership_expanded_frame"]
    for name in probe.RETIRED_VARIANTS:
        assert name not in probe.SUPPORTED_VARIANTS, name


def test_v6_and_b_prime_are_deleted_not_left_unrunnable():
    """The brief: delete the variant and say so; do not leave it un-runnable.
    A B' helper still importable would be a variant one edit away from running
    again with nothing recording that it had been dropped."""
    for name in ("V4", "V5", "V6", "EXPANDED_FRAME_VARIANTS", "expanded_frame_uv",
                 "bbox_excursion_past_frame", "frame_prime_drivers",
                 "viewer_extent_elements", "_build_frame_prime",
                 "DEFAULT_EXPANDED_FRAME_MARGIN_IN", "paper_margin_ft"):
        assert not hasattr(probe, name), name


def test_only_the_v0_control_still_writes_the_crop():
    """THE CAPTURE DOES NOT MODIFY THE CROP -- except in the control for exactly
    that behaviour."""
    for name in probe.SUPPORTED_VARIANTS:
        plan = probe.variant_plan(name)
        assert (plan["crop_mode"] == "frame_b") == (name == probe.V0), name


def test_any_variant_suppressing_by_membership_also_asks_for_external_suppression():
    """A variant that applied membership suppression while production still hid
    model categories would measure the two stacked, and V4 would be
    indistinguishable from V0 in the one respect it is testing."""
    for name in probe.SUPPORTED_VARIANTS:
        plan = probe.variant_plan(name)
        if plan["white_membership"]:
            assert plan["model_suppression"] == "external", name
        else:
            assert plan["model_suppression"] == "hide_categories", name


def test_select_variants_defaults_to_all_and_rejects_a_typo():
    assert probe.select_variants("all") == list(probe.SUPPORTED_VARIANTS)
    assert probe.select_variants(None) == list(probe.SUPPORTED_VARIANTS)
    assert probe.select_variants(
        "v0_control, v8_no_crop_fiducials") == [probe.V0, probe.V8]
    with pytest.raises(ValueError):
        probe.select_variants("v0_control,v9_wishful")
    assert probe.select_variants([probe.V7, probe.V0]) == [probe.V7, probe.V0]


# ======================================================================
# rect_from_corners
# ======================================================================

def test_rect_from_corners_reads_the_four_uv_pairs_production_records():
    corners = [[1.0, 2.0], [7.0, 2.0], [7.0, 9.0], [1.0, 9.0]]
    assert probe.rect_from_corners(corners) == (1.0, 2.0, 7.0, 9.0)


def test_rect_from_corners_also_reads_a_bare_four_tuple():
    assert probe.rect_from_corners((7.0, 9.0, 1.0, 2.0)) == (1.0, 2.0, 7.0, 9.0)


def test_rect_from_corners_returns_none_rather_than_a_degenerate_rectangle():
    # None -- never (0,0,0,0), which a caller would read as a real rectangle
    # at the view origin (a fiducial candidate, say).
    assert probe.rect_from_corners(None) is None
    assert probe.rect_from_corners([]) is None
    assert probe.rect_from_corners("nonsense") is None
    assert probe.rect_from_corners([[1.0], [2.0]]) is None


# ======================================================================
# F2 -- the reserved fiducial colours
# ======================================================================

def test_the_fiducial_colours_are_off_every_palette_lattice_but_step_1():
    """The reservation argument, asserted. Every palette colour is snapped onto
    multiples of its step, so a colour with any channel off that lattice cannot
    be one. Both fiducials carry a 251 channel (prime), so only step 1 -- a view
    of millions of annotations -- could collide."""
    for rgb in probe.FIDUCIAL_COLOURS:
        for step in (2, 3, 4, 5, 6, 7, 8):
            assert probe.colour_on_lattice(rgb, step) is False, (rgb, step)
        assert probe.colour_on_lattice(rgb, 1) is True


def test_colour_on_lattice_agrees_with_productions_palette():
    """Composed with production rather than with a copy of its snapping rule:
    every colour build_palette() emits is on the lattice, and a FULL palette at
    step 8 -- every colour it can ever emit -- contains neither fiducial."""
    from vop_interwoven.color_id_buffer import (
        _reserved_corner_count, build_palette,
    )
    capacity = (256 // 8) ** 3 - _reserved_corner_count(8)
    full = build_palette(capacity, step=8)
    assert len(full) == capacity
    assert all(probe.colour_on_lattice(rgb, 8) for rgb in full)
    for rgb in probe.FIDUCIAL_COLOURS:
        assert tuple(rgb) not in set(full)


def test_the_fiducial_colours_are_distinct_and_not_reserved_corners():
    from vop_interwoven.color_id_buffer import _is_reserved_corner
    assert len(set(probe.FIDUCIAL_COLOURS)) == len(probe.FIDUCIAL_COLOURS) == 2
    for rgb in probe.FIDUCIAL_COLOURS:
        assert not _is_reserved_corner(rgb), rgb
        assert rgb != (255, 255, 255)


def test_colour_on_lattice_refuses_a_non_positive_step():
    with pytest.raises(ValueError):
        probe.colour_on_lattice((8, 8, 8), 0)


def test_the_fiducial_colour_record_reports_an_actual_collision():
    """on_palette_lattice is why a collision COULD happen; collides is whether
    it DID. A step-1 capture that happened not to use the colour is not a
    collision, and one that did must say so."""
    clean = probe.fiducial_colour_record(8, [[8, 16, 24]])
    assert clean["any_collision"] is False
    assert all(entry["on_palette_lattice"] is False for entry in clean["colours"])
    hit = probe.fiducial_colour_record(1, [list(probe.FIDUCIAL_COLOURS[0])])
    assert hit["any_collision"] is True
    assert hit["colours"][0]["collides_with_assigned_colour"] is True
    assert hit["colours"][1]["collides_with_assigned_colour"] is False
    unknown = probe.fiducial_colour_record(None, [])
    assert all(entry["on_palette_lattice"] is None for entry in unknown["colours"])


# ======================================================================
# F2 -- choosing the fiducial pair
# ======================================================================

REF = (0.0, 0.0, 100.0, 60.0)
FPP = 0.05  # 6 px minimum -> 0.3 ft


def _cand(cid, u, v, half=0.5):
    return {"id": cid, "rect": (u - half, v - half, u + half, v + half)}


def test_the_pair_is_the_one_separated_most_on_the_WORSE_axis():
    """Opposite corners beat a pair that is further apart overall but on one
    row: a pair on one row determines nothing about v."""
    candidates = [
        _cand(1, 10.0, 10.0), _cand(2, 90.0, 50.0),     # diagonal: 80 / 40
        _cand(3, 5.0, 30.0), _cand(4, 95.0, 30.5),      # one row: 90 / 0.5
    ]
    choice = probe.choose_fiducial_pair(candidates, REF, FPP)
    assert choice["state"] == "value"
    assert sorted(c["id"] for c in choice["pair"]) == [1, 2]
    assert choice["separation_u_ft"] == pytest.approx(80.0)
    assert choice["separation_v_ft"] == pytest.approx(40.0)


def test_a_pool_all_on_one_row_is_unavailable_not_a_degenerate_pair():
    candidates = [_cand(i, 10.0 * i, 30.0) for i in range(1, 9)]
    choice = probe.choose_fiducial_pair(candidates, REF, FPP)
    assert choice["state"] == "unavailable"
    assert "BOTH axes" in choice["reason"]
    assert "pair" not in choice


def test_candidates_outside_too_small_or_too_large_are_rejected_and_counted():
    candidates = [
        {"id": 1, "rect": None},
        _cand(2, 0.5, 0.5),                          # inside the inset band
        _cand(3, 50.0, 30.0, half=0.1),              # 0.2 ft = 4 px < 6
        _cand(4, 50.0, 30.0, half=4.0),              # 8 ft > 5% of 100 on u
        _cand(5, 20.0, 15.0), _cand(6, 80.0, 45.0),  # the two that survive
    ]
    choice = probe.choose_fiducial_pair(candidates, REF, FPP)
    assert choice["rejections"] == {"no_rect": 1, "outside_reference": 1,
                                    "too_small": 1, "too_large": 1}
    assert choice["kept_count"] == 2
    assert sorted(c["id"] for c in choice["pair"]) == [5, 6]


def test_the_choice_is_deterministic_under_reordering():
    candidates = [_cand(i, 10.0 + (i * 37) % 80, 5.0 + (i * 23) % 50)
                  for i in range(1, 40)]
    first = probe.choose_fiducial_pair(candidates, REF, FPP)
    second = probe.choose_fiducial_pair(list(reversed(candidates)), REF, FPP)
    assert [c["id"] for c in first["pair"]] == [c["id"] for c in second["pair"]]


def test_the_pair_search_is_EXACT_against_brute_force():
    """The search bisects a threshold instead of trying every pair. That is a
    claim of optimality, so it is checked against the O(n^2) answer it replaces
    over generated pools -- a top-k shortcut would pass the hand-made cases
    above and fail here."""
    import random
    rng = random.Random(20260922)
    for trial in range(40):
        pool = [_cand(i, rng.uniform(3.0, 97.0), rng.uniform(2.0, 58.0))
                for i in range(rng.randint(2, 60))]
        choice = probe.choose_fiducial_pair(pool, REF, FPP)
        best = 0.0
        for i, a in enumerate(pool):
            for b in pool[i + 1:]:
                ca, cb = probe._rect_centre(a["rect"]), probe._rect_centre(b["rect"])
                best = max(best, min(abs(ca[0] - cb[0]), abs(ca[1] - cb[1])))
        if best <= 0.0:
            assert choice["state"] == "unavailable"
            continue
        assert choice["state"] == "value", trial
        assert choice["min_separation_ft"] == pytest.approx(best, rel=1e-9, abs=1e-9)


def test_a_degenerate_reference_or_fpp_is_unavailable():
    assert probe.choose_fiducial_pair([], (0, 0, 0, 10), FPP)["state"] == "unavailable"
    assert probe.choose_fiducial_pair([], REF, 0.0)["state"] == "unavailable"


# ======================================================================
# F1 -- the crop region's shape
# ======================================================================

def test_a_rectangular_crop_is_a_rectangle_with_two_levels_per_axis():
    record = probe.crop_loop_record([[(0, 0), (10, 0), (10, 6), (0, 6)]])
    assert record["is_rectangle"] is True
    assert record["distinct_u_levels"] == [0.0, 10.0]
    assert record["distinct_v_levels"] == [0.0, 6.0]
    assert record["bounds_uv"] == [0.0, 0.0, 10.0, 6.0]
    assert record["oblique_edge_count"] == 0


def test_a_non_rectangular_crop_is_NOT_read_as_four_edges():
    """An L-shaped crop draws its shape. Six edges, three levels a side -- a
    decoder that assumed four would match the wrong lines."""
    record = probe.crop_loop_record(
        [[(0, 0), (10, 0), (10, 3), (6, 3), (6, 6), (0, 6)]])
    assert record["is_rectangle"] is False
    assert record["edge_count"] == 6
    assert record["distinct_u_levels"] == [0.0, 6.0, 10.0]
    assert record["distinct_v_levels"] == [0.0, 3.0, 6.0]


def test_an_oblique_edge_is_named_and_matches_no_level():
    record = probe.crop_loop_record([[(0, 0), (10, 0), (5, 6)]])
    assert record["is_rectangle"] is False
    assert record["oblique_edge_count"] == 2
    assert record["distinct_u_levels"] == []


def test_a_split_crop_is_two_loops_not_a_rectangle():
    record = probe.crop_loop_record([[(0, 0), (4, 0), (4, 6), (0, 6)],
                                     [(6, 0), (10, 0), (10, 6), (6, 6)]])
    assert record["loop_count"] == 2
    assert record["is_rectangle"] is False


def test_no_loops_has_no_bounds_rather_than_a_zero_rectangle():
    record = probe.crop_loop_record([])
    assert record["bounds_uv"] is None
    assert record["is_rectangle"] is False


def test_per_side_delta_is_positive_where_the_outer_rect_reaches_past():
    delta = probe.per_side_delta((10, 10, 20, 20), (8, 9, 23, 20))
    assert delta == {"left": 2.0, "bottom": 1.0, "right": 3.0, "top": 0.0}
    inside = probe.per_side_delta((10, 10, 20, 20), (11, 10, 20, 19))
    assert inside["left"] == -1.0 and inside["top"] == -1.0


def test_the_suppression_cost_ratio_is_a_lower_bound_and_never_divides_by_zero():
    record = probe.suppression_cost_record(500.0, 1000, 2000.0, 1000)
    assert record["ratio_to_model_pass_total"] == pytest.approx(0.25)
    assert record["suppression_ms_per_element"] == pytest.approx(0.5)
    assert "LOWER bound" in record["note"]
    none = probe.suppression_cost_record(500.0, 0, 0.0, 0)
    assert none["ratio_to_model_pass_total"] is None
    assert none["suppression_ms_per_element"] is None


# ======================================================================
# F1/F2 -- what goes into the sidecar a consumer reads
# ======================================================================

def test_the_boundary_payload_says_present_and_must_be_subtracted():
    payload = probe.crop_boundary_sidecar_payload([0, 0, 10, 6], None, "annotation")
    assert payload["present"] is True
    assert payload["must_be_subtracted"] is True
    assert payload["is_documentation_content"] is False
    assert payload["drawn_at_uv"] == [0, 0, 10, 6]


def test_annotate_sidecar_adds_a_key_and_refuses_to_overwrite_one(tmp_path):
    import json
    path = tmp_path / "x_anno.json"
    path.write_text(json.dumps({"schema": "s", "capture_faults": []}))
    assert probe.annotate_sidecar(str(path), "probe_crop_boundary", {"a": 1}) is None
    data = json.loads(path.read_text())
    assert data["probe_crop_boundary"] == {"a": 1}
    assert data["schema"] == "s"
    reason = probe.annotate_sidecar(str(path), "capture_faults", ["forged"])
    assert "already carries" in reason
    assert json.loads(path.read_text())["capture_faults"] == []


def test_annotate_sidecar_returns_the_reason_when_it_cannot_write(tmp_path):
    reason = probe.annotate_sidecar(str(tmp_path / "missing.json"), "k", {})
    assert reason and "Error" in reason


# ======================================================================
# F1/F2 -- the Revit-side readers, against the shared fake DB
# ======================================================================

class _Loop(list):
    pass


class _Line(object):
    def __init__(self, a, b):
        from tests.stage_a_capture_fakes import FakeXYZ
        self._pts = (FakeXYZ(*a), FakeXYZ(*b))

    def GetEndPoint(self, index):
        return self._pts[index]


class _ShapeManager(object):
    def __init__(self, loops, has_getter=True):
        self._loops = loops
        self.ShapeSet = False
        if not has_getter:
            self.GetCropShape = None

    def GetCropShape(self):
        return self._loops


def _crop_view(loops=None, has_getter=True, visible=True):
    from tests.stage_a_capture_fakes import (
        FakeBoundingBoxXYZ, FakeViewPlan, FakeXYZ,
    )
    view = FakeViewPlan(view_id=77)
    box = FakeBoundingBoxXYZ()
    box.Min = FakeXYZ(10.0, 5.0, -1.0)
    box.Max = FakeXYZ(40.0, 25.0, 1.0)
    view.CropBox = box
    if visible is not None:
        view.CropBoxVisible = visible
    square = loops if loops is not None else [_Loop([
        _Line((10, 5, 0), (40, 5, 0)), _Line((40, 5, 0), (40, 25, 0)),
        _Line((40, 25, 0), (10, 25, 0)), _Line((10, 25, 0), (10, 5, 0))])]
    manager = _ShapeManager(square, has_getter=has_getter)
    view.GetCropRegionShapeManager = lambda: manager
    return view


_BASIS = None


def _basis():
    from vop_interwoven.revit.view_basis import ViewBasis
    return ViewBasis(origin=(0, 0, 0), right=(1, 0, 0), up=(0, 1, 0),
                     forward=(0, 0, -1))


def test_the_crop_region_record_reads_the_crop_without_writing_it():
    from tests.stage_a_capture_fakes import install_fake_revit_db
    view = _crop_view()
    box_before = view.CropBox
    with install_fake_revit_db():
        record = probe.crop_region_record(view, _basis())
    assert view.CropBox is box_before
    assert record["crop_box_uv"] == {"state": "value", "value": [10.0, 5.0, 40.0, 25.0]}
    assert record["crop_box_visible"] == {"state": "value", "value": True}
    shape = record["shape"]["value"]
    assert shape["is_rectangle"] is True
    assert shape["bounds_uv"] == [10.0, 5.0, 40.0, 25.0]
    assert shape["curve_types"] == ["_Line"]


def test_a_host_without_GetCropShape_makes_the_shape_unavailable_not_rectangular():
    from tests.stage_a_capture_fakes import install_fake_revit_db
    view = _crop_view(has_getter=False)
    with install_fake_revit_db():
        record = probe.crop_region_record(view, _basis())
    assert record["shape"]["state"] == "unavailable"
    assert "GetCropShape" in record["shape"]["reason"]
    # The box itself is still read -- one missing reader does not blank the rest.
    assert record["crop_box_uv"]["state"] == "value"


def test_a_host_without_CropBoxVisible_is_unavailable_not_false():
    view = _crop_view(visible=None)
    assert probe.crop_box_visible_record(view)["state"] == "unavailable"


def test_paint_fiducials_paints_the_reserved_colours_and_records_failures():
    from tests.stage_a_capture_fakes import (
        FakeDoc, FakeViewPlan, install_fake_revit_db,
    )

    class _PickyView(FakeViewPlan):
        def SetElementOverrides(self, eid, ogs):
            if int(eid.IntegerValue) == 9:
                raise RuntimeError("refused")
            FakeViewPlan.SetElementOverrides(self, eid, ogs)

    view = _PickyView(view_id=77)
    pair = [{"id": 5, "rect": (1, 1, 2, 2), "category": "Walls"},
            {"id": 9, "rect": (8, 8, 9, 9), "category": "Doors"}]
    with install_fake_revit_db():
        record = probe.paint_fiducials(FakeDoc(elements=[]), view, pair)
    assert record["painted_count"] == 1
    assert record["painted"][0]["rgb"] == list(probe.FIDUCIAL_COLOURS[0])
    assert record["failed"][0]["id"] == 9
    assert "refused" in record["failed"][0]["error"]
    assert 5 in view.element_overrides
    # CUT graphics too, in the reserved colour. Round 2 painted projection only,
    # so a wall cut in plan kept the white CATEGORY cut override and showed a
    # sliver; mutation: go back to _build_flat_color_ogs.
    painted = view.element_overrides[5]
    assert (painted.CutLineColor.r, painted.CutLineColor.g, painted.CutLineColor.b) == \
        probe.FIDUCIAL_COLOURS[0]
    assert (painted.CutForegroundPatternColor.r, painted.CutForegroundPatternColor.g,
            painted.CutForegroundPatternColor.b) == probe.FIDUCIAL_COLOURS[0]

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
              hidden=None, filters=None, template=-1, style="Wireframe",
              crop_visible=False, shape_x=10.0):
    return {
        "crop_box": {"state": "value",
                     "value": {"min": [0.0, 0.0, 0.0], "max": [10.0, 10.0, 0.0],
                               "active": crop_active}},
        "crop_box_visible": {"state": "value", "value": crop_visible},
        "crop_region_shape": {
            "state": "value",
            "value": {"loops_world": [[(0.0, 0.0, 0.0), (shape_x, 0.0, 0.0),
                                       (shape_x, 10.0, 0.0), (0.0, 10.0, 0.0)]],
                      "curve_types": ["Line"], "shape_set": False}},
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
    for name in ("crop_box", "crop_box_visible", "crop_region_shape",
                 "smooth_edges", "annotation_crop_offsets",
                 "model_category_visibility", "view_filters",
                 "view_template_id", "display_style",
                 "explicit_restore_steps"):
        assert verdict[name]["status"] == "restored", name


@pytest.mark.parametrize("field,kwargs", [
    ("crop_box", {"crop_active": False}),
    ("crop_box_visible", {"crop_visible": True}),
    ("crop_region_shape", {"shape_x": 12.0}),
    ("smooth_edges", {"smooth": True}),
    ("annotation_crop_offsets", {"offsets": (0.0, 0.0, 0.0, 0.0)}),
    ("model_category_visibility", {"hidden": {"10": True}}),
    ("view_filters", {"filters": {"901": {"enabled": False, "visible": True}}}),
    ("view_template_id", {"template": 4242}),
    ("display_style", {"style": "FlatColors"}),
])
def test_each_obligation_fails_on_its_own_and_names_itself(field, kwargs):
    """Nine obligations, nine independent assertions.

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
    verdict = probe.restore_readback_verdict(before, after, expect_filters_absent=[5551])
    assert verdict["probe_filter_deleted[5551]"]["status"] == "not_restored"
    assert "5551" in verdict["probe_filter_deleted[5551]"]["reason"]

    # OFF THE VIEW IS NOT ENOUGH: the element must also be gone from the
    # project. Not knowing is "unverified", never "restored".
    unknown = probe.restore_readback_verdict(
        _snapshot(filters={}), _snapshot(filters={}), expect_filters_absent=[5551])
    assert unknown["probe_filter_deleted[5551]"]["status"] == "unverified"

    clean = probe.restore_readback_verdict(
        _snapshot(filters={}), _snapshot(filters={}), expect_filters_absent=[5551],
        filter_elements_still_in_project={5551: False})
    assert clean["probe_filter_deleted[5551]"]["status"] == "restored"
    assert clean["overall"] == "restored"


def test_the_probe_filter_check_is_unverified_when_the_filters_cannot_be_read():
    after = _snapshot()
    after["view_filters"] = {"state": "unavailable", "reason": "GetFilters raised"}
    verdict = probe.restore_readback_verdict(_snapshot(), after,
                                             expect_filters_absent=[5551])
    assert verdict["probe_filter_deleted[5551]"]["status"] == "unverified"


def test_no_probe_filter_id_means_no_probe_filter_obligation():
    """V0 and V0-offsets0 create no filter, so asserting one was deleted would
    be asserting something the variant never did."""
    verdict = probe.restore_readback_verdict(_snapshot(), _snapshot())
    assert not any(k.startswith("probe_filter_deleted") for k in verdict)


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
        {"selection": "{0},{1}".format(probe.V0, probe.V8),
         "export_dpi": 150,
         "authored_override_scan_max": 5000,
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
        _adapter().validate_settings({"selection": "v2_white_filter"}, "C:/out")
    assert "v2_white_filter" in str(excinfo.value)


def test_validation_maps_a_campaign_job_variant_onto_selection():
    resolved = _adapter().validate_settings({}, "C:/out", variant=probe.V7)
    assert resolved["selection"] == probe.V7


def test_validation_refuses_a_conflicting_variant_and_selection():
    with pytest.raises(ValueError) as excinfo:
        _adapter().validate_settings({"selection": probe.V7}, "C:/out",
                                     variant=probe.V8)
    assert "conflicting" in str(excinfo.value)


@pytest.mark.parametrize("settings", [
    {"export_dpi": 0},
    {"export_dpi": -5},
    {"export_dpi": "big"},
    {"authored_override_scan_max": -1},
    {"authored_override_scan_max": True},
    {"authored_override_scan_max": 1.5},
    {"model_reexport_check": "yes"},
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

_V8_GOOD_METADATA = {"applied_smooth_edges": False,
                     "model_suppression_mode": "external",
                     "crop_mode": "untouched"}
_V8_GOOD_STATE = {"crop_box_visible": {"during_capture": True},
                  "fiducials": {"painted_count": 2}}


def test_a_variant_that_got_what_it_asked_for_is_measured():
    plan = probe.variant_plan(probe.V8)
    check = probe.variant_measurement_check(
        plan, dict(_V8_GOOD_METADATA), probe_state=dict(_V8_GOOD_STATE))
    assert check["measured"] is True, check
    assert check["unmet"] == []
    # And it says WHAT it checked, so a mutation that stops checking something
    # is visible in the record rather than only in a boolean.
    for fragment in ("applied_smooth_edges", "model_suppression_mode",
                     "crop_mode", "CropBoxVisible", "fiducials"):
        assert any(fragment in item for item in check["checked"]), fragment


@pytest.mark.parametrize("applied", ["read_failed", "unchanged (failed)",
                                     "not_attempted", None, True])
def test_v8_did_not_measure_when_smooth_edges_was_not_confirmed_off(applied):
    """Production does NOT raise when the ViewDisplayModel read or write fails.
    Anything but a confirmed ``False`` is not AA off."""
    metadata = dict(_V8_GOOD_METADATA, applied_smooth_edges=applied)
    check = probe.variant_measurement_check(
        probe.variant_plan(probe.V8), metadata, probe_state=dict(_V8_GOOD_STATE))
    assert check["measured"] is False
    assert len(check["unmet"]) == 1
    assert check["unmet"][0]["production_reported"] == applied


def test_a_membership_variant_did_not_measure_when_production_hid_categories():
    """A V7 capture reporting ``hide_categories`` hid model categories: it
    measured V0's suppression under V7's name."""
    check = probe.variant_measurement_check(
        probe.variant_plan(probe.V7),
        {"model_suppression_mode": "hide_categories", "crop_mode": "untouched"})
    assert check["measured"] is False
    assert check["unmet"][0]["production_reported"] == "hide_categories"
    assert "V0's suppression" in check["unmet"][0]["why_it_matters"]


@pytest.mark.parametrize("reported", ["frame_b", None])
def test_an_untouched_variant_did_not_measure_when_production_wrote_the_crop(reported):
    """The one thing V7/V8 exist not to do. A production build without the crop
    switch reports no crop_mode at all -- None -- and that is not 'untouched'
    either."""
    check = probe.variant_measurement_check(
        probe.variant_plan(probe.V7),
        {"model_suppression_mode": "external", "crop_mode": reported})
    assert check["measured"] is False
    assert check["unmet"][0]["production_reported"] == reported
    assert "wrote the crop" in check["unmet"][0]["why_it_matters"]


@pytest.mark.parametrize("state", [
    {},
    {"crop_box_visible": {"during_capture": False}},
    {"crop_box_visible": {"during_capture": None}},
])
def test_v8_did_not_measure_without_the_crop_boundary_confirmed_on(state):
    probe_state = dict(_V8_GOOD_STATE)
    probe_state.pop("crop_box_visible")
    probe_state.update(state)
    check = probe.variant_measurement_check(
        probe.variant_plan(probe.V8), dict(_V8_GOOD_METADATA), probe_state=probe_state)
    assert check["measured"] is False
    assert [u["requested"] for u in check["unmet"]] == ["CropBoxVisible on (F1)"]


@pytest.mark.parametrize("painted", [0, 1, None])
def test_v8_did_not_measure_without_both_fiducials_painted(painted):
    probe_state = dict(_V8_GOOD_STATE, fiducials={"painted_count": painted})
    check = probe.variant_measurement_check(
        probe.variant_plan(probe.V8), dict(_V8_GOOD_METADATA), probe_state=probe_state)
    assert check["measured"] is False
    assert "fiducials painted" in check["unmet"][0]["requested"]


def test_the_control_variant_is_measured_by_its_own_standard():
    """V0 asks for no mutation, so it must not be judged against one -- but it
    IS the frame_b control, so a V0 whose crop was NOT written is not the
    control any more."""
    plan = probe.variant_plan(probe.V0)
    check = probe.variant_measurement_check(
        plan, {"model_suppression_mode": "hide_categories",
               "applied_smooth_edges": "not_attempted", "crop_mode": "frame_b"})
    assert check["measured"] is True, check
    check_moved = probe.variant_measurement_check(
        plan, {"model_suppression_mode": "hide_categories", "crop_mode": "untouched"})
    assert check_moved["measured"] is False


def test_every_request_is_checked_independently():
    check = probe.variant_measurement_check(
        probe.variant_plan(probe.V8),
        {"applied_smooth_edges": "read_failed",
         "model_suppression_mode": "hide_categories", "crop_mode": "frame_b"},
        probe_state={})
    assert check["measured"] is False
    assert len(check["unmet"]) == 5


def test_missing_metadata_is_not_measured_rather_than_assumed_fine():
    """An annotation pass that raised leaves no metadata at all. That is not
    evidence that the mutation applied."""
    check = probe.variant_measurement_check(probe.variant_plan(probe.V7), {})
    assert check["measured"] is False
    check_none = probe.variant_measurement_check(probe.variant_plan(probe.V7), None)
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


# ======================================================================
# FINDING E (PR #215 round 3, P2): the filter ELEMENT, and failed restore steps
# ======================================================================

def test_a_filter_removed_from_the_view_but_alive_in_the_project_is_not_restored():
    """THE CASE THE DOCSTRING PROMISED AND THE CODE DID NOT CHECK.

    ``RemoveFilter`` succeeding and ``doc.Delete`` failing leaves the id absent
    from the view -- so the membership test passed -- while a project-wide
    ``ParameterFilterElement`` the probe created survives. The old comment said
    the id was asked for directly "because a filter deleted from the project but
    left associated with the view, or vice versa, can produce an identical filter
    MAP while the element survives". It only ever checked one of those two
    directions: an identity claimed in prose and never asserted.
    """
    verdict = probe.restore_readback_verdict(
        _snapshot(filters={}), _snapshot(filters={}), expect_filters_absent=[5551],
        filter_elements_still_in_project={5551: True})
    assert verdict["probe_filter_deleted[5551]"]["status"] == "not_restored"
    assert "project" in verdict["probe_filter_deleted[5551]"]["reason"]
    assert verdict["overall"] == "not_restored"


def test_an_undeterminable_filter_element_is_unverified_not_restored():
    verdict = probe.restore_readback_verdict(
        _snapshot(filters={}), _snapshot(filters={}), expect_filters_absent=[5551],
        filter_elements_still_in_project={5551: None})
    assert verdict["probe_filter_deleted[5551]"]["status"] == "unverified"
    assert verdict["overall"] == "unverified"


def test_the_view_membership_check_still_outranks_the_element_check():
    """A filter still ON the view reports that, not the element question -- the
    more specific and more actionable fact."""
    verdict = probe.restore_readback_verdict(
        _snapshot(filters={}),
        _snapshot(filters={"5551": {"enabled": True, "visible": True}}),
        expect_filters_absent=[5551], filter_elements_still_in_project={5551: True})
    assert "still on the view" in verdict["probe_filter_deleted[5551]"]["reason"]


def test_a_failed_explicit_restore_step_is_not_restored():
    """A recorded error that nothing consulted is not a check.

    ``explicit_step_errors`` held the failed ``doc.Delete`` and ``document_safe``
    read only the read-back verdicts, so a clean TransactionGroup rollback could
    let the variant conclude ``RAN`` -- falsely validating an explicit restore
    the rollback had actually rescued.
    """
    verdict = probe.restore_readback_verdict(
        _snapshot(), _snapshot(),
        explicit_step_errors=[{"step": "delete_white_filter",
                               "error": "InvalidOperationException: in use"}])
    assert verdict["explicit_restore_steps"]["status"] == "not_restored"
    assert verdict["explicit_restore_steps"]["errors"][0]["step"] == (
        "delete_white_filter")
    assert verdict["overall"] == "not_restored"


def test_no_explicit_step_errors_is_restored_not_unverified():
    """THE CONTROL: an empty error list is a pass, so the check cannot be
    satisfied by simply always failing."""
    verdict = probe.restore_readback_verdict(_snapshot(), _snapshot(),
                                             explicit_step_errors=[])
    assert verdict["explicit_restore_steps"]["status"] == "restored"
    assert verdict["overall"] == "restored"


def test_run_variant_feeds_both_new_restore_inputs():
    """Pin the wiring, as every round of this review has had to."""
    import inspect

    source = inspect.getsource(probe._run_variant)
    assert "filter_elements_still_in_project=" in source
    assert "filter_element_exists(" in source


# ======================================================================
# FINDING F (PR #215 round 3, P1): the model pass's own verdict gates the run
# ======================================================================

def test_run_native_refuses_when_the_model_pass_rejects_its_own_capture():
    """A model capture production declared invalid stops the run.

    ``export_color_id_buffer_view`` can return a TIFF *and* usable geometry with
    ``success=False`` -- an ``export_dim_mismatch`` surviving the halving backoff
    is the reachable case. The status was recorded and composed into no decision,
    so all five variants ran against a rejected foundation and the run could
    still conclude ``RAN``/``completed``.

    Checked by reading the source for the gate, because ``_run_native`` needs a
    Revit document; the behaviour it guards is asserted through
    ``finalize_native_report`` below.
    """
    import inspect

    source = inspect.getsource(probe._run_native)
    assert 'if not model_out.get("success"):' in source, (
        "_run_native must gate on the model pass's own success flag")
    # And it must refuse BEFORE the variants loop, not merely record it.
    gate = source.index('if not model_out.get("success"):')
    loop = source.index("for variant in selected:")
    assert gate < loop, "the gate must come before any variant runs"
    # The refusal names the fault rather than reporting a bare failure.
    assert "REJECTED ITS OWN CAPTURE" in source
    assert "failure_reason" in source[gate:loop]


def test_the_model_gate_finalizes_and_writes_before_returning():
    """A refused run still leaves the combined report on disk.

    The whole point of round 1's P2 fix: the one run most worth reading must not
    return with nothing written.
    """
    import inspect

    source = inspect.getsource(probe._run_native)
    gate = source.index('if not model_out.get("success"):')
    loop = source.index("for variant in selected:")
    block = source[gate:loop]
    assert "finalize_native_report(" in block
    assert "_write_combined(" in block


def test_a_successful_model_pass_does_not_trip_the_gate():
    """THE CONTROL, as source inspection: the gate is on the falsy branch only,
    so a successful model pass falls through to the variants."""
    import inspect

    source = inspect.getsource(probe._run_native)
    assert 'if model_out.get("success"):' not in source, (
        "the gate must fire on FAILURE, not on success")


# ======================================================================
# ROUND 2: membership-based white suppression
# ======================================================================

class _FakeCatId(object):
    def __init__(self, value):
        self.IntegerValue = int(value)
        self.Value = int(value)


class _FakeColor(object):
    def __init__(self, valid=True):
        self.IsValid = valid


class _FakeCatOGS(object):
    """A category OverrideGraphicSettings whose blankness is controllable."""

    def __init__(self, blank=True, halftone=False, unreadable=False):
        self._unreadable = unreadable
        self.Halftone = halftone
        value = None if blank else _FakeColor()
        self.ProjectionLineColor = value
        self.CutLineColor = None
        self.SurfaceForegroundPatternColor = None
        self.CutForegroundPatternColor = None

    def __getattribute__(self, name):
        if name != "_unreadable" and object.__getattribute__(self, "_unreadable") \
                and name == "ProjectionLineColor":
            raise RuntimeError("override unreadable")
        return object.__getattribute__(self, name)


class _CatView(object):
    """The minimum view surface ``_category_override_is_blank`` reads."""

    def __init__(self, overrides=None, raises=False, returns_none=False):
        self._overrides = overrides or {}
        self._raises = raises
        self._returns_none = returns_none

    def GetCategoryOverrides(self, cat_id):
        if self._raises:
            raise RuntimeError("GetCategoryOverrides exploded")
        if self._returns_none:
            return None
        return self._overrides.get(int(cat_id.IntegerValue), _FakeCatOGS())


def test_a_blank_category_override_reads_blank():
    state, blank, reason = probe._category_override_is_blank(
        _CatView(), _FakeCatId(-2000051))
    assert (state, blank, reason) == ("value", True, None)


def test_an_authored_category_override_reads_not_blank_and_names_the_residue():
    """The check that decides whether mechanism 3 may write.

    Writing over an authored category override would destroy graphics this probe
    cannot put back -- reapplying a captured OverrideGraphicSettings across a
    transaction boundary is the pattern that caused the curtain-panel bug.
    """
    view = _CatView({-2000051: _FakeCatOGS(blank=False)})
    state, blank, reason = probe._category_override_is_blank(
        view, _FakeCatId(-2000051))
    assert state == "value"
    assert blank is False
    assert "projection_line_color" in reason


def test_halftone_alone_makes_a_category_override_not_blank():
    view = _CatView({-2000051: _FakeCatOGS(blank=True, halftone=True)})
    _state, blank, reason = probe._category_override_is_blank(
        view, _FakeCatId(-2000051))
    assert blank is False
    assert "halftone" in reason


@pytest.mark.parametrize("view", [
    _CatView(raises=True),
    _CatView(returns_none=True),
    _CatView({-2000051: _FakeCatOGS(unreadable=True)}),
])
def test_an_unreadable_category_override_is_unavailable_not_blank(view):
    """"Could not read it" is not "it is blank".

    Treating it as blank would let mechanism 3 overwrite an override it never
    managed to inspect -- the exact destruction the blank-only rule prevents.
    """
    state, blank, reason = probe._category_override_is_blank(
        view, _FakeCatId(-2000051))
    assert state == "unavailable"
    assert blank is None
    assert reason


# ---------------------------------------------------------------- subcategories

class _FakeCategory(object):
    def __init__(self, cat_id, name, subs=None, subs_raise=False, subs_none=False):
        self.Id = _FakeCatId(cat_id)
        self.Name = name
        self._subs = subs or []
        self._subs_raise = subs_raise
        self._subs_none = subs_none

    @property
    def SubCategories(self):
        if self._subs_raise:
            raise RuntimeError("SubCategories exploded")
        return None if self._subs_none else self._subs


def test_subcategories_are_walked_not_just_the_parent():
    """Greg observed roof fascia surviving a white filter and going white only
    when the SUBCATEGORY was overridden alongside the parent. So the walk has to
    reach them."""
    fascia = _FakeCategory(-2000039, "Fascia")
    roof = _FakeCategory(-2000035, "Roofs", subs=[fascia])
    subs, error = probe._subcategories_of(roof)
    assert error is None
    assert [s.Name for s in subs] == ["Fascia"]


def test_an_unreadable_subcategory_list_yields_a_REASON_not_an_empty_list():
    """An unwalked subcategory is the roof-fascia case arriving silently. A bare
    empty list would be indistinguishable from a category that genuinely has
    none."""
    subs, error = probe._subcategories_of(
        _FakeCategory(-2000035, "Roofs", subs_raise=True))
    assert subs == []
    assert "exploded" in error

    subs, error = probe._subcategories_of(
        _FakeCategory(-2000035, "Roofs", subs_none=True))
    assert subs == []
    assert error == "SubCategories is None"


def test_a_category_with_no_subcategories_reports_no_error():
    """THE CONTROL: an empty list with error None is a real answer, so the
    reason channel cannot be satisfied by always reporting one."""
    subs, error = probe._subcategories_of(_FakeCategory(-2000051, "Lines"))
    assert subs == []
    assert error is None


# ------------------------------------------------------- wiring of the mechanism

def test_run_variant_suppresses_by_membership():
    """Pin the WIRING, which every round of review on this probe has had to.

    A correct suppression function the call site does not invoke is suppression
    nothing performs.
    """
    import inspect

    source = inspect.getsource(probe._run_variant)
    assert "apply_membership_white_suppression(" in source
    assert 'model_context["model_members"]' in source
    assert 'link_categories=model_context.get("link_categories")' in source
    # Round 3: the group's rollback is the restore. No explicit reverse is
    # left to drift from what was applied -- driven, not grepped, in
    # tests/test_probe_anno_pass_run_variant.py.
    assert not hasattr(probe, "reverse_membership_white_suppression")
    # And the retired category-filter entry point is gone from the call site.
    assert "create_white_model_filter" not in source


def test_run_native_resolves_the_membership_split_once_and_shares_it():
    """Four variants must suppress the SAME set, or they are not comparable.

    A per-variant split would let two variants suppress different sets and then
    be read against each other as if they had not.
    """
    import inspect

    source = inspect.getsource(probe._run_native)
    assert "split_stage_a_pass_membership(" in source
    assert source.count("split_stage_a_pass_membership(") == 1, (
        "the split must be resolved once, not per variant")
    gate = source.index("split_stage_a_pass_membership(")
    loop = source.index("for variant in selected:")
    assert gate < loop
    assert '"model_members": model_members' in source


def test_run_native_discovers_link_categories_through_production():
    """The link route reuses production's linked-doc category policy rather than
    forming a second opinion about which categories a link contributes."""
    import inspect

    assert "discover_link_categories(" in inspect.getsource(probe._run_native)
    source = inspect.getsource(probe.discover_link_categories)
    assert "_model_categories_in_linked_doc" in source
    assert "_resolve_colorable_category_predicate" in source
    # A view with no links must SAY the mechanism is unexercised rather than
    # letting a clean run read as evidence that the link path works.
    assert "UNEXERCISED" in source


def test_the_suppression_reuses_productions_link_filter_mechanism():
    """Not a second link mechanism -- production's own, called with white."""
    import inspect

    source = inspect.getsource(probe.apply_membership_white_suppression)
    assert "_apply_link_category_filters" in source
    assert "(cat, WHITE) for cat in link_categories" in source
    # Element overrides come first; the category/subcategory pass is a
    # COMPLEMENT, and Revit's Element > Category precedence is what lets the
    # annotation paint still win in a shared category.
    assert source.index("SetElementOverrides") < source.index("SetCategoryOverrides")


def test_unreached_is_a_named_list_on_every_branch():
    """"Unreached" must never be an absence. Every branch that fails to suppress
    something appends a record with a reason, and the count is published."""
    import inspect

    source = inspect.getsource(probe.apply_membership_white_suppression)
    # One append per failure route: element id unreadable, element override
    # raised, link category failed, link mechanism raised, category unreadable,
    # authored category, refused category, category raised, subcategories
    # unreadable, element category unreadable.
    assert source.count('record["unreached"].append(') >= 9
    assert 'record["unreached_count"] = len(record["unreached"])' in source


def test_mechanism_3_only_writes_over_a_blank_override():
    """The rule that makes the explicit restore correct AND non-destructive."""
    import inspect

    source = inspect.getsource(probe.apply_membership_white_suppression)
    assert "_category_override_is_blank(" in source
    assert 'record["category_overrides"]["skipped_authored"]' in source


# ======================================================================
# The suppression, driven BEHAVIOURALLY rather than by reading its source
# ======================================================================
#
# The source-inspection tests above bind PRESENCE, not logic: four mutations of
# the real loops (stop walking subcategories, overwrite authored overrides, treat
# an unreadable override as blank, neuter the call site) all left them green,
# because `if False:` and `None and f(...)` keep the strings they assert on. That
# is the weak-test class this probe's review rounds kept finding, one layer out.
#
# These drive the actual function against the shared fake Revit DB and assert on
# the record it produces.


from tests.stage_a_capture_fakes import (            # noqa: E402
    FakeCategory, FakeDoc, FakeElement, FakeElementId, FakeViewPlan,
    install_fake_revit_db,
)


def _sub(cat_id, name):
    """A subcategory: the same shape the walk reads off ``cat.SubCategories``.

    ``SubCategories`` is set to ``[]`` rather than left absent because a real
    Revit ``Category`` ALWAYS exposes it, possibly empty. The shared fake does
    not, and the clean-suppression control below caught that as a spurious
    "unreached" -- a fixture artifact, not a behaviour. Leaving it absent would
    have made every fixture look like the unreadable-subcategories case.
    """
    cat = FakeCategory(name, cat_id, cat_type="Model")
    cat.SubCategories = []
    return cat


_LINES = _sub(-2000051, "Lines")


class _SuppressionView(FakeViewPlan):
    """Records every override write, and can make a category authored/unreadable."""

    def __init__(self, view_id, authored=(), unreadable=(), refuse=(),
                 subcategories=None):
        FakeViewPlan.__init__(self, view_id)
        self.element_override_writes = []
        self.category_override_writes = []
        self._authored = set(int(v) for v in authored)
        self._unreadable = set(int(v) for v in unreadable)
        self._refuse = set(int(v) for v in refuse)
        self._subcategories = subcategories or {}

    def SetElementOverrides(self, eid, ogs):
        self.element_override_writes.append(int(eid.IntegerValue))
        FakeViewPlan.SetElementOverrides(self, eid, ogs)

    def GetCategoryOverrides(self, cat_id):
        value = int(cat_id.IntegerValue)
        if value in self._unreadable:
            raise RuntimeError("GetCategoryOverrides refused")
        ogs = FakeViewPlan.GetCategoryOverrides(self, cat_id)
        if value in self._authored:
            ogs.ProjectionLineColor = _FakeColor()
        return ogs

    def SetCategoryOverrides(self, cat_id, ogs):
        value = int(cat_id.IntegerValue)
        if value in self._refuse:
            raise Exception("Category cannot be overridden")
        self.category_override_writes.append(value)
        FakeViewPlan.SetCategoryOverrides(self, cat_id, ogs)


def _suppress(view=None, elements=None, link_categories=None, exclude_ids=None):
    """Run the real suppression against the fake DB."""
    roof_with_fascia = FakeCategory("Roofs", -2000035, cat_type="Model")
    roof_with_fascia.SubCategories = [_sub(-2000039, "Fascia"),
                                      _sub(-2000040, "Soffit")]
    elements = elements if elements is not None else [
        FakeElement(9001, roof_with_fascia),
        FakeElement(9002, roof_with_fascia),
        FakeElement(9003, _LINES),
    ]
    view = view if view is not None else _SuppressionView(4242)
    doc = FakeDoc(elements=elements, link_instances=[],
                  categories=[roof_with_fascia, _LINES])
    with install_fake_revit_db():
        record = probe.apply_membership_white_suppression(
            doc, view, 4242, elements, link_categories=link_categories,
            exclude_ids=exclude_ids)
    return record, view, doc


def test_every_model_member_gets_an_element_override():
    record, view, _doc = _suppress()
    assert record["element_overrides"]["attempted"] == 3
    assert record["element_overrides"]["applied"] == 3
    assert record["element_overrides"]["failed"] == []
    assert sorted(view.element_override_writes) == [9001, 9002, 9003]
    assert record["element_override_count"] == 3


def test_the_parent_AND_every_subcategory_is_overridden():
    """Greg's roof-fascia observation, asserted behaviourally.

    Mutating the walk to ``[(cat, False)]`` -- parent only -- left the
    source-inspection tests green. This one goes red.
    """
    record, view, _doc = _suppress()
    parents = {e["category_id"] for e in
               record["category_overrides"]["parents_applied"]}
    subs = {e["category_id"] for e in
            record["category_overrides"]["subcategories_applied"]}
    assert parents == {-2000035, -2000051}
    assert subs == {-2000039, -2000040}, "Fascia and Soffit must both be reached"
    assert set(view.category_override_writes) == parents | subs
    assert record["category_override_count"] == 4


def test_an_authored_category_override_is_left_alone_and_named_unreached():
    """Mutating the blank check to ``if False:`` overwrote authored graphics and
    kept every source-inspection test green."""
    view = _SuppressionView(4242, authored=[-2000039])
    record, view, _doc = _suppress(view=view)
    assert -2000039 not in view.category_override_writes, (
        "an AUTHORED override must never be overwritten")
    skipped = {e["category_id"] for e in
               record["category_overrides"]["skipped_authored"]}
    assert skipped == {-2000039}
    unreached = {u["id"]: u["reason"] for u in record["unreached"]}
    assert -2000039 in unreached
    assert "AUTHORED" in unreached[-2000039]


def test_an_unreadable_category_override_is_left_alone_and_named_unreached():
    """"Could not read it" must not be treated as blank -- that would overwrite an
    override the probe never inspected."""
    view = _SuppressionView(4242, unreadable=[-2000040])
    record, view, _doc = _suppress(view=view)
    assert -2000040 not in view.category_override_writes
    failed = {e["category_id"] for e in record["category_overrides"]["failed"]}
    assert -2000040 in failed
    unreached = {u["id"]: u["reason"] for u in record["unreached"]}
    assert "unreadable" in unreached[-2000040]


def test_a_refused_category_is_recorded_as_refused_and_unreached():
    view = _SuppressionView(4242, refuse=[-2000051])
    record, view, _doc = _suppress(view=view)
    refused = {e["category_id"] for e in record["category_overrides"]["refused"]}
    assert refused == {-2000051}
    unreached = {u["id"]: u["reason"] for u in record["unreached"]}
    assert "refuses" in unreached[-2000051]
    # Its ELEMENT override still landed -- the mechanisms are independent.
    assert 9003 in view.element_override_writes


def test_a_clean_suppression_reaches_everything_and_unreached_is_empty():
    """THE CONTROL. Without it every assertion above would also pass against a
    function that recorded everything as unreached and suppressed nothing."""
    record, _view, _doc = _suppress()
    assert record["unreached"] == []
    assert record["unreached_count"] == 0
    assert record["element_overrides"]["applied"] == 3
    assert record["category_override_count"] == 4


def test_an_element_whose_override_raises_is_named_unreached():
    class _FailingView(_SuppressionView):
        def SetElementOverrides(self, eid, ogs):
            if int(eid.IntegerValue) == 9002:
                raise Exception("InvalidOperationException: element is pinned")
            _SuppressionView.SetElementOverrides(self, eid, ogs)

    record, view, _doc = _suppress(view=_FailingView(4242))
    assert record["element_overrides"]["applied"] == 2
    assert [f["id"] for f in record["element_overrides"]["failed"]] == [9002]
    unreached = {u["id"]: u["reason"] for u in record["unreached"]}
    assert "SetElementOverrides raised" in unreached[9002]


def test_a_category_whose_subcategories_cannot_be_read_is_named_unreached():
    """An unwalked subcategory is the roof-fascia case arriving silently."""

    class _Broken(object):
        Id = FakeElementId(-2000035)
        Name = "Roofs"

        @property
        def SubCategories(self):
            raise RuntimeError("SubCategories exploded")

    record, _view, _doc = _suppress(
        elements=[FakeElement(9001, _Broken())])
    unreached = [u for u in record["unreached"]
                 if u["kind"] == "subcategories_of"]
    assert unreached, record["unreached"]
    assert "SubCategories" in unreached[0]["reason"]


def test_the_retired_white_filter_setting_is_REFUSED_not_ignored():
    """A campaign JSON still carrying it must fail the dry run.

    The setting configured the category-based white filter, which round 2
    retires: suppression runs off the membership split, so "should Detail Items
    and Lines be included" has no premise. Silently ignoring it would let a
    campaign think it had asked for something.
    """
    with pytest.raises(ValueError) as excinfo:
        _adapter().validate_settings(
            {"white_filter_include_view_only_model_categories": True}, "C:/out")
    assert "white_filter_include_view_only_model_categories" in str(excinfo.value)


def test_the_round_2_variants_validate_through_the_registry():
    for name in probe.SUPPORTED_VARIANTS:
        resolved = _adapter().validate_settings({"selection": name}, "C:/out")
        assert resolved["selection"] == name


def test_a_retired_variant_name_is_refused_by_the_registry():
    """v1/v2/v3 and v0_offsets0 are not runnable any more, and asking for one
    must say so rather than running nothing."""
    for name in probe.RETIRED_VARIANTS:
        with pytest.raises(ValueError) as excinfo:
            _adapter().validate_settings({"selection": name}, "C:/out")
        assert name in str(excinfo.value)


# ======================================================================
# F1's ruler: the view's own crop-region element
# ======================================================================

def _viewer(elem_id, name, owner=None):
    from tests.stage_a_capture_fakes import FakeCategory, FakeElement
    return FakeElement(elem_id, FakeCategory("Views", -2000279, cat_type="Annotation"),
                       name=name, owner_view_id=owner)


def test_the_crop_region_element_is_the_viewer_named_like_the_view():
    from tests.stage_a_capture_fakes import (
        FakeCategory, FakeDoc, FakeElement, FakeViewPlan, install_fake_revit_db,
    )
    view = FakeViewPlan(view_id=77, name="Plan A")
    members = [_viewer(1, "Plan A"), _viewer(2, "Section 3"),
               FakeElement(3, FakeCategory("Walls", 10))]
    with install_fake_revit_db():
        record = probe.crop_region_elements(FakeDoc(elements=members), view, members)
    assert record["crop_element_ids"] == [1]
    assert record["viewers_in_model_set_count"] == 2


def test_no_matching_viewer_is_an_empty_finding_not_an_error():
    from tests.stage_a_capture_fakes import FakeDoc, FakeViewPlan, install_fake_revit_db
    view = FakeViewPlan(view_id=77, name="Plan A")
    with install_fake_revit_db():
        record = probe.crop_region_elements(FakeDoc(elements=[]), view, [])
    assert record["state"] == "value"
    assert record["crop_element_ids"] == []


def test_an_excluded_element_gets_no_override_and_lends_no_category():
    """Excluded means untouched: no element override, and its category is not
    overridden on its account -- a category override is exactly how its
    linework would still go white."""
    viewer = _viewer(8001, "Plan A")
    record, view, _doc = _suppress(
        elements=[viewer, FakeElement(9003, _LINES)],
        view=_SuppressionView(4242))
    assert 8001 in view.element_override_writes
    record_x, view_x, _doc = _suppress(
        elements=[_viewer(8001, "Plan A"), FakeElement(9003, _LINES)],
        view=_SuppressionView(4242), exclude_ids=[8001])
    assert 8001 not in view_x.element_override_writes
    assert -2000279 not in view_x.category_override_writes
    assert record_x["excluded_element_ids"] == [8001]
    assert all(entry.get("id") != 8001 for entry in record_x["unreached"])


# ======================================================================
# mechanism 2's discovery, driven (round 2: it never ran)
# ======================================================================

def test_link_discovery_reaches_production_with_diag_not_the_document():
    """Round 2 called _resolve_colorable_category_predicate(doc): the document
    landed in production's ``diag`` slot and every view reported
    "'Document' object has no attribute 'warn'". Driven here against the fake
    DB, with a link instance present so the loop body runs. Mutation: pass the
    document back in."""
    from tests.stage_a_capture_fakes import (
        FakeDiag, FakeDoc, FakeRevitLinkInstance, FakeViewPlan, install_fake_revit_db,
    )

    class _Link(FakeRevitLinkInstance):
        def GetLinkDocument(self):
            return None          # unloaded: counted, contributes nothing

    doc = FakeDoc(elements=[], link_instances=[_Link(501, "Arch.rvt")])
    with install_fake_revit_db():
        cats, record = probe.discover_link_categories(
            doc, FakeViewPlan(view_id=77), diag=FakeDiag(), view_id=77)
    assert record["state"] == "value", record
    assert record["instances"] == 1
    assert cats == []


def test_the_suppression_times_every_mechanism_and_counts_its_calls():
    """Round 2 measured the whole suppression at 1.08-1.14x the ENTIRE model
    pass; one number cannot say which part to make cheaper. Every phase is
    timed, and the call counts are what separate "slow" from "many"."""
    record, view, _doc = _suppress()
    timings = record["timings_ms"]
    for key in ("build_override_ms", "element_overrides_ms",
                "link_category_filters_ms", "category_collect_ms",
                "subcategory_walk_ms", "category_override_reads_ms",
                "category_override_writes_ms"):
        assert key in timings and timings[key] >= 0.0, key
    counts = record["api_call_counts"]
    assert counts["element_override_writes"] == record["element_overrides"]["attempted"]
    assert counts["category_override_writes"] == len(view.category_override_writes)
    # Every category and subcategory considered is read once before any write.
    assert counts["category_override_reads"] >= counts["category_override_writes"]
    assert counts["subcategory_lists_read"] == 2   # Roofs, Lines


def test_the_crop_lookup_also_searches_OST_Views_and_says_which_matched():
    """Round 2: Revit refused a category override on "Views" (-2000278) on both
    views, so OST_Views elements ARE in the model set. The lookup may not assume
    the documented OST_Viewers is the only home."""
    from tests.stage_a_capture_fakes import (
        FakeCategory, FakeDoc, FakeElement, FakeViewPlan, install_fake_revit_db,
    )
    view = FakeViewPlan(view_id=77, name="Plan A")
    views_member = FakeElement(9, FakeCategory("Views", -2000278, cat_type="Annotation"),
                               name="Plan A")
    with install_fake_revit_db():
        record = probe.crop_region_elements(FakeDoc(elements=[views_member]), view,
                                            [views_member])
    assert record["crop_element_ids"] == [9]
    assert record["viewers_in_model_set"][0]["category"] == "OST_Views"


# ======================================================================
# ROUND 3: registration marks (V9), pure
# ======================================================================

def test_the_mark_colour_is_off_every_palette_lattice_and_distinct():
    for step in range(2, 9):
        assert probe.colour_on_lattice(probe.MARK_COLOUR, step) is False, step
    assert probe.MARK_COLOUR not in probe.FIDUCIAL_COLOURS
    assert len(set(probe.MARK_COLOUR)) == 3  # not grey: a grey is a boundary candidate


def _layout(uv=(0.0, 0.0, 100.0, 50.0), fpp=0.1, **kw):
    return probe.registration_mark_segments(uv, fpp, **kw)


def test_marks_are_eight_ticks_two_per_corner_one_of_each_orientation():
    layout = _layout()
    assert layout["state"] == "value"
    segments = layout["segments"]
    assert len(segments) == 8
    for corner, _su, _sv in probe.MARK_CORNERS:
        mine = [s for s in segments if s["corner"] == corner]
        assert sorted(s["orientation"] for s in mine) == ["horizontal", "vertical"]


def test_marks_sit_inside_the_reference_by_the_inset_and_never_touch():
    """A tick on the crop edge is clipped on the image border -- what removed the
    elevation's horizontal crop edges in round 2 -- and two touching ticks are
    one connected component the model-capture analysis cannot split."""
    fpp = 0.1
    layout = _layout(fpp=fpp)
    inset = probe.MARK_INSET_PX * fpp
    gap = probe.MARK_GAP_PX * fpp
    for seg in layout["segments"]:
        for u, v in (seg["uv0"], seg["uv1"]):
            assert inset - 1e-9 <= u <= 100.0 - inset + 1e-9
            assert inset - 1e-9 <= v <= 50.0 - inset + 1e-9
    for corner, _su, _sv in probe.MARK_CORNERS:
        h, v = sorted((s for s in layout["segments"] if s["corner"] == corner),
                      key=lambda s: s["orientation"])
        # The horizontal tick stops short of the vertical one's u by the gap,
        # and the vertical tick stops short of the horizontal one's v.
        assert min(abs(x - v["level_uv"]) for x in h["span_uv"]) == pytest.approx(gap)
        assert min(abs(y - h["level_uv"]) for y in v["span_uv"]) == pytest.approx(gap)


def test_each_axis_gets_four_ticks_at_two_levels_so_the_fit_has_a_residual():
    layout = _layout()
    for orientation in ("horizontal", "vertical"):
        levels = [s["level_uv"] for s in layout["segments"]
                  if s["orientation"] == orientation]
        assert len(levels) == 4 and len(set(round(x, 9) for x in levels)) == 2


def test_the_arm_shrinks_for_a_small_crop_and_a_tiny_one_is_refused():
    full = _layout(uv=(0.0, 0.0, 100.0, 100.0), fpp=0.1)
    assert full["arm_px"] == probe.MARK_ARM_PX
    small = _layout(uv=(0.0, 0.0, 20.0, 20.0), fpp=0.1)   # 200 px square
    assert probe.MARK_MIN_ARM_PX <= small["arm_px"] < probe.MARK_ARM_PX
    tiny = _layout(uv=(0.0, 0.0, 10.0, 10.0), fpp=0.1)    # 100 px square
    assert tiny["state"] == "unavailable" and "too small" in tiny["reason"]


def test_marks_without_a_reference_or_lattice_are_unavailable_not_guessed():
    assert _layout(uv=None)["state"] == "unavailable"
    assert _layout(fpp=None)["state"] == "unavailable"
    assert _layout(uv=(5.0, 5.0, 5.0, 9.0))["state"] == "unavailable"


def _v9_state(created=8, hidden=False, lines_visible=True):
    return {"registration_marks": {"expected_count": 8, "created_count": created,
                                   "lines_category_hidden_in_view": hidden},
            "own_model_lines_visible": lines_visible,
            "crop_box_visible": {"during_capture": True},
            "fiducials": {"painted_count": 2}}


def _v9_metadata():
    return {"applied_smooth_edges": False, "model_suppression_mode": "external",
            "crop_mode": "untouched"}


def test_v9_measured_only_when_every_mark_drew_in_both_passes():
    plan = probe.variant_plan(probe.V9)
    assert probe.variant_measurement_check(
        plan, _v9_metadata(), _v9_state())["measured"] is True
    for state in (_v9_state(created=7), _v9_state(hidden=True),
                  _v9_state(hidden=None), _v9_state(lines_visible=False),
                  _v9_state(lines_visible=None)):
        check = probe.variant_measurement_check(plan, _v9_metadata(), state)
        assert check["measured"] is False, state
