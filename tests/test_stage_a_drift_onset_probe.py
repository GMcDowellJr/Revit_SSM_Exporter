"""Pure-helper coverage for the Stage A drift-onset probe (D1-D5).

Only the Revit-free planning helpers are exercised here: every Revit import in
the probe is function-local, so the module imports cleanly outside Dynamo. The
Revit-side behaviour of D1-D5 is established by running the probe, not by these
tests.
"""
import math

import pytest

from tests.dynamo import probe_stage_a_drift_onset as probe


DPI = 150.0
SCALE = 96.0
PPF = DPI * 12.0 / SCALE          # 18.75 px per model foot
BOUNDS = (0.0, 0.0, 100.0, 80.0)  # 1875 x 1500 px native


def test_importing_the_probe_starts_no_transaction_and_sets_no_output():
    assert not hasattr(probe, "OUT")
    assert probe.PROBE_NAME == "stage_a_drift_onset"
    assert probe.CASES == ("d1_determinism", "d2_category_load", "d3_size_sweep",
                           "d4_dpi_vs_pixel_size", "d5_crop_tiles")


# --- native density ---------------------------------------------------------

def test_pixels_per_foot_is_dpi_times_twelve_over_scale():
    assert probe.pixels_per_foot(150.0, 96.0) == pytest.approx(18.75)
    assert probe.pixels_per_foot(150.0, 1.0) == pytest.approx(1800.0)


@pytest.mark.parametrize("dpi,scale", [(0, 96), (150, 0), (-150, 96), (150, -1)])
def test_pixels_per_foot_rejects_nonpositive_inputs(dpi, scale):
    with pytest.raises(ValueError):
        probe.pixels_per_foot(dpi, scale)


def test_native_pixel_size_matches_the_baseline_formula():
    width, height = probe.native_pixel_size(BOUNDS, DPI, SCALE)
    assert width == pytest.approx(1875.0)
    assert height == pytest.approx(1500.0)


def test_native_pixel_size_rejects_a_degenerate_rectangle():
    with pytest.raises(ValueError):
        probe.native_pixel_size((0.0, 0.0, 0.0, 80.0), DPI, SCALE)


# --- D4: lowering DPI under the native ceiling ------------------------------

def test_dpi_is_left_alone_when_native_already_fits():
    assert probe.dpi_for_native_ceiling(BOUNDS, SCALE, DPI) == pytest.approx(DPI)


def test_dpi_is_lowered_until_the_long_axis_hits_the_ceiling():
    big = (0.0, 0.0, 2000.0, 400.0)   # 37500 x 7500 px at 150 dpi
    lowered = probe.dpi_for_native_ceiling(big, SCALE, DPI)
    assert lowered < DPI
    width, height = probe.native_pixel_size(big, lowered, SCALE)
    assert max(width, height) == pytest.approx(probe.D4_NATIVE_CEILING)


def test_the_ceiling_is_applied_to_the_long_axis_even_when_it_is_vertical():
    tall = (0.0, 0.0, 400.0, 2000.0)
    lowered = probe.dpi_for_native_ceiling(tall, SCALE, DPI)
    width, height = probe.native_pixel_size(tall, lowered, SCALE)
    assert height == pytest.approx(probe.D4_NATIVE_CEILING)
    assert width < probe.D4_NATIVE_CEILING


# --- D5: pixel-lattice tiling ----------------------------------------------

def test_tiles_cover_the_rectangle_exactly():
    tiles = probe.snap_to_pixel_lattice(BOUNDS, DPI, SCALE, 2)
    assert len(tiles) == 4
    us = sorted({t["bounds_xy"][0] for t in tiles} | {t["bounds_xy"][2] for t in tiles})
    vs = sorted({t["bounds_xy"][1] for t in tiles} | {t["bounds_xy"][3] for t in tiles})
    assert us[0] == BOUNDS[0] and us[-1] == BOUNDS[2]
    assert vs[0] == BOUNDS[1] and vs[-1] == BOUNDS[3]


def test_every_interior_seam_lands_on_a_whole_native_pixel():
    tiles = probe.snap_to_pixel_lattice(BOUNDS, DPI, SCALE, 2)
    for tile in tiles:
        assert tile["seam_residual_px"]["u_low"] == pytest.approx(0.0, abs=1e-9)
        assert tile["seam_residual_px"]["v_low"] == pytest.approx(0.0, abs=1e-9)


def test_tile_widths_sum_to_the_full_native_width():
    tiles = probe.snap_to_pixel_lattice(BOUNDS, DPI, SCALE, 2)
    row0 = sorted((t for t in tiles if t["row"] == 0), key=lambda t: t["col"])
    assert sum(t["requested_pixel_size"] for t in row0) == 1875


def test_an_awkward_extent_still_tiles_without_a_fractional_seam():
    # 100.037 ft is deliberately not a whole number of pixels wide.
    odd = (0.0, 0.0, 100.037, 80.019)
    tiles = probe.snap_to_pixel_lattice(odd, DPI, SCALE, 3)
    assert len(tiles) == 9
    for tile in tiles:
        for residual in tile["seam_residual_px"].values():
            assert residual == pytest.approx(0.0, abs=1e-9)
    # Only the last tile in each direction absorbs the fractional remainder.
    widths = [t["native_width_px"] for t in tiles if t["row"] == 0]
    assert all(abs(w - round(w)) < 1e-9 for w in widths[:-1])
    assert sum(widths) == pytest.approx((odd[2] - odd[0]) * PPF)


def test_grid_of_one_returns_the_original_rectangle():
    tiles = probe.snap_to_pixel_lattice(BOUNDS, DPI, SCALE, 1)
    assert len(tiles) == 1
    assert tiles[0]["bounds_xy"] == [0.0, 0.0, 100.0, 80.0]


@pytest.mark.parametrize("grid", [0, -1])
def test_tiling_rejects_a_nonpositive_grid(grid):
    with pytest.raises(ValueError):
        probe.snap_to_pixel_lattice(BOUNDS, DPI, SCALE, grid)


# --- D3: size sweep ---------------------------------------------------------

def test_default_sweep_resolves_native_plus_the_three_fixed_widths():
    resolved = probe.resolve_size_sweep(None, 8123.4)
    assert [e["label"] for e in resolved] == ["native", "10000px", "12000px", "15000px"]
    assert resolved[0]["requested_pixel_size"] == 8123


def test_fractional_entries_are_multipliers_on_native():
    resolved = probe.resolve_size_sweep(["0.9", "0.95"], 10000.0)
    assert [e["label"] for e in resolved] == ["0.9x_native", "0.95x_native"]
    assert [e["requested_pixel_size"] for e in resolved] == [9000, 9500]


def test_a_comma_string_is_accepted_and_duplicates_collapse():
    resolved = probe.resolve_size_sweep("native, 10000, 10000", 10000.0)
    assert [e["requested_pixel_size"] for e in resolved] == [10000, 10000]
    assert [e["label"] for e in resolved] == ["native", "10000px"]


def test_a_width_below_revits_floor_is_rejected_loudly():
    with pytest.raises(ValueError, match="below Revit's usable floor"):
        probe.resolve_size_sweep([32], 10000.0)


# --- case selection ---------------------------------------------------------

def test_all_selects_every_case():
    assert probe.select_cases("all") == list(probe.CASES)
    assert probe.select_cases(None) == list(probe.CASES)


def test_a_subset_keeps_request_order_and_drops_duplicates():
    assert probe.select_cases("d3_size_sweep, d1_determinism, d3_size_sweep") == [
        "d3_size_sweep", "d1_determinism"]


def test_an_unknown_case_raises_rather_than_selecting_nothing():
    with pytest.raises(ValueError, match="Unknown case"):
        probe.select_cases("d9_hardware_acceleration")


# --- state diffing ----------------------------------------------------------

def test_state_diff_is_empty_for_identical_snapshots():
    snap = {"crop_box_active": True, "category_hidden": {"1": False}}
    assert probe._diff_state(snap, dict(snap)) == []


def test_state_diff_names_what_rollback_failed_to_restore():
    before = {"crop_box_active": False, "category_hidden": {"1": False}}
    after = {"crop_box_active": True, "category_hidden": {"1": False}}
    diffs = probe._diff_state(before, after)
    assert [d["key"] for d in diffs] == ["crop_box_active"]
    assert diffs[0]["before"] is False and diffs[0]["after"] is True


def test_capture_errors_do_not_masquerade_as_unrestored_state():
    before = {"crop_box_active": True, "crop_box_error": "boom"}
    after = {"crop_box_active": True}
    assert probe._diff_state(before, after) == []


def test_safe_name_strips_path_hostile_characters():
    assert probe._safe_name("SITE PLAN AT LEVEL 4") == "SITE_PLAN_AT_LEVEL_4"
    assert probe._safe_name("_N_ HOSPITAL - LEVEL 2") == "_N__HOSPITAL_-_LEVEL_2"
    assert probe._safe_name(None) == "view"


# --- production palette step (Codex #2) -------------------------------------

def test_palette_step_uses_the_global_threshold_not_the_view_element_count():
    """Production pins the step to the configured global threshold.

    Sizing the lattice to the view's own count gives a different palette, so a
    probe capture could not be compared against a production one.
    """
    from vop_interwoven.color_id_buffer import choose_step
    expected = choose_step(32767)
    for count in (1, 50, 500, 5000, 32767):
        assert probe.production_palette_step(count, 32767) == expected


def test_palette_step_falls_back_to_the_count_once_it_exceeds_the_threshold():
    from vop_interwoven.color_id_buffer import choose_step
    assert probe.production_palette_step(40000, 32767) == choose_step(40000)


def test_the_step_rule_matches_what_production_actually_computes():
    """Mirrors color_id_buffer.export_color_id_buffer_view's own expression."""
    from vop_interwoven.color_id_buffer import choose_step
    for count, threshold in ((10, 32767), (32767, 32767), (32768, 32767), (7, 100)):
        production = choose_step(threshold if count <= threshold else count)
        assert probe.production_palette_step(count, threshold) == production


# --- element id reading (Codex #4) ------------------------------------------

class _Revit2025Id(object):
    """Revit 2025: Value is the 64-bit property; IntegerValue can throw."""
    Value = 2 ** 40

    @property
    def IntegerValue(self):
        raise OverflowError("id exceeds the legacy 32-bit range")


class _LegacyId(object):
    IntegerValue = 871863


def test_a_large_id_is_read_through_value_without_touching_integervalue():
    assert probe._safe_int_id(_Revit2025Id()) == 2 ** 40


def test_a_legacy_id_with_only_integervalue_still_reads():
    assert probe._safe_int_id(_LegacyId()) == 871863


def test_a_throwing_property_is_skipped_rather_than_propagated():
    class Hostile(object):
        @property
        def Value(self):
            raise RuntimeError("disposed")

        @property
        def IntegerValue(self):
            raise RuntimeError("disposed")
    assert probe._safe_int_id(Hostile()) is None


def test_a_plain_int_is_still_accepted():
    assert probe._safe_int_id(587278) == 587278
    assert probe._safe_int_id(None) is None


# --- production view-state normalization (Codex #1) -------------------------

def test_the_suppression_set_covers_every_blend_source_production_disables():
    """Each of these exists in production to stop Revit blending pixels."""
    required = {
        "detach_template",              # unlocks everything below
        "hide_annotation_categories",
        "visible_filter_graphics_neutralized",
        "phase_filter_neutralized",
        "display_style_flat_colors",    # non-flat styles shade surfaces
        "smooth_edges_off",             # anti-aliasing
        "shadows_off",
    }
    assert required == set(probe.PRODUCTION_SUPPRESSION_MUTATIONS)


def test_category_halftone_is_applied_after_collection_not_before():
    """It needs the resolved element ids, so production runs it post-collect."""
    assert probe.POST_COLLECTION_MUTATIONS == ("category_halftone_neutralized",)
    assert "category_halftone_neutralized" not in probe.PRODUCTION_SUPPRESSION_MUTATIONS


def test_visibility_off_filters_are_left_alone_as_production_leaves_them():
    """Production disables only enabled+VISIBLE filters (color_id_buffer.py:1519-1521).

    A visibility-off filter stays enabled and keeps hiding its elements.
    Disabling it would reveal elements production hides, which are not in the
    painted set and would render with uncontrolled colours.
    """
    assert "visibility_off_filters_disabled" not in probe.PRODUCTION_SUPPRESSION_MUTATIONS
    assert "visibility_off_filters_disabled" not in probe.POST_COLLECTION_MUTATIONS


@pytest.mark.parametrize("mutation", ["ambient_occlusion_off", "sketchy_lines_off",
                                      "depth_cueing_off"])
def test_mutations_production_does_not_perform_are_not_applied(mutation):
    """Suppressing extra blend sources would make the probe cleaner than
    production, so a view that drifts in production could come back clean."""
    assert mutation not in probe.PRODUCTION_SUPPRESSION_MUTATIONS
    assert mutation not in probe.POST_COLLECTION_MUTATIONS


def test_the_template_is_detached_before_anything_it_would_block():
    """A template locks the display/VG properties every later step writes."""
    order = list(probe.PRODUCTION_SUPPRESSION_MUTATIONS)
    assert order[0] == "detach_template"


def _all_applied():
    return {name: {"status": "APPLIED"} for name in
            probe.PRODUCTION_SUPPRESSION_MUTATIONS + probe.POST_COLLECTION_MUTATIONS}


def test_a_fully_applied_normalization_reports_no_shortfall():
    mutations = _all_applied()
    mutations["shadows_off"] = {"status": "ALREADY_MATCHED"}
    assert probe.normalization_shortfall(mutations) == []


def test_a_mutation_that_never_ran_counts_as_a_shortfall():
    """Only scoring the entries present would hide a step that was skipped."""
    mutations = _all_applied()
    del mutations["phase_filter_neutralized"]
    assert probe.normalization_shortfall(mutations) == ["phase_filter_neutralized"]


def test_an_empty_normalization_names_every_expected_mutation():
    expected = sorted(set(probe.PRODUCTION_SUPPRESSION_MUTATIONS)
                      | set(probe.POST_COLLECTION_MUTATIONS))
    assert probe.normalization_shortfall({}) == expected


@pytest.mark.parametrize("status", ["FAILED", "BLOCKED_BY_TEMPLATE", "UNSUPPORTED",
                                    "NOT_REQUESTED", None])
def test_any_non_applied_status_is_named_as_a_shortfall(status):
    mutations = _all_applied()
    mutations["smooth_edges_off"] = {"status": status} if status else {}
    assert probe.normalization_shortfall(mutations) == ["smooth_edges_off"]


def test_every_suppression_mutation_is_one_the_minimum_id_probe_implements():
    """The set is dispatched by that probe; an unknown name would silently
    come back UNSUPPORTED instead of normalizing anything."""
    from tests.dynamo import probe_stage_a_minimum_id_mutations as minimum
    import inspect
    source = inspect.getsource(minimum._apply_mutation)
    for name in probe.PRODUCTION_SUPPRESSION_MUTATIONS:
        assert '"{0}"'.format(name) in source, name
