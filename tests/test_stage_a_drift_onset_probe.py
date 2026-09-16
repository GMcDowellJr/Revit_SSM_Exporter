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


def test_a_wide_view_is_lowered_until_its_width_hits_the_width_ceiling():
    wide = (0.0, 0.0, 2000.0, 400.0)   # 37500 x 7500 px at 150 dpi
    lowered = probe.dpi_for_native_ceiling(wide, SCALE, DPI)
    assert lowered < DPI
    width, height = probe.native_pixel_size(wide, lowered, SCALE)
    assert width == pytest.approx(probe.D4_WIDTH_CEILING)
    assert height <= probe.D4_HEIGHT_CEILING


def test_a_tall_view_is_lowered_until_its_height_hits_the_height_ceiling():
    """Run 1: the drift threshold is on the exported height, and the two axes
    do not share a ceiling. Comparing the longest axis against the width
    ceiling -- what this did before -- passes a view already over the height
    line, which is why D4 lowered nothing and duplicated D1."""
    tall = (0.0, 0.0, 400.0, 2000.0)   # 7500 x 37500 px at 150 dpi
    lowered = probe.dpi_for_native_ceiling(tall, SCALE, DPI)
    width, height = probe.native_pixel_size(tall, lowered, SCALE)
    assert height == pytest.approx(probe.D4_HEIGHT_CEILING)
    assert width < probe.D4_WIDTH_CEILING


def test_a_view_under_the_width_ceiling_is_still_lowered_for_its_height():
    """The Run 1 case: 8739x10079 is far under 15000 wide and still drifts."""
    hospital = (0.0, 0.0, 466.1, 537.5)   # ~8739 x 10078 px at 150 dpi
    lowered = probe.dpi_for_native_ceiling(hospital, SCALE, DPI)
    assert lowered < DPI
    width, height = probe.native_pixel_size(hospital, lowered, SCALE)
    assert height == pytest.approx(probe.D4_HEIGHT_CEILING)
    # And the density cost of staying under it is small on this view.
    assert width / 8739.0 > 0.97


def test_the_height_ceiling_sits_below_the_measured_bracket():
    """(9927, 10079] is where Run 1 puts the threshold; 9900 is safe wherever
    in that bracket it actually falls."""
    assert probe.D4_HEIGHT_CEILING < 9927


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
    # The real view name, parentheses and all. Note it sanitizes to the same
    # string an underscored spelling would, which is why a capture's file name
    # also carries the view id rather than relying on the name alone.
    assert probe._safe_name("(N) HOSPITAL - LEVEL 2") == "_N__HOSPITAL_-_LEVEL_2"
    assert probe._safe_name(None) == "view"


# --- driving production rather than mirroring it -----------------------------

def test_the_probe_captures_through_productions_own_entry_points():
    """The whole design: a probe capture IS a production capture.

    Three review rounds went to divergences between a hand-mirrored capture
    sequence and export_color_id_buffer_view. The sequence is gone; this
    asserts it does not come back.
    """
    import inspect
    source = inspect.getsource(probe.production_capture)
    assert "init_view_raster" in source
    assert "collect_view_elements" in source
    assert "export_color_id_buffer_view" in source


@pytest.mark.parametrize("gone", [
    "PRODUCTION_SUPPRESSION_MUTATIONS",   # production owns the suppression set
    "POST_COLLECTION_MUTATIONS",
    "_normalize_view_state",
    "_paint",                             # production owns the paint step
    "production_palette_step",            # production owns the palette
    "_export_tiff",                       # production owns the export options
    "_set_pixel_size",
    "apply_production_crop",              # production owns the render crop
])
def test_the_hand_mirrored_capture_machinery_is_gone(gone):
    assert not hasattr(probe, gone), (
        "{0} is back: the probe is mirroring production again instead of "
        "calling it".format(gone))


def _referenced_names(module):
    """Every name and attribute the module's CODE touches.

    Parsed rather than grepped: the module docstring legitimately names
    ImageExportOptions when listing UNCONFIRMED API assumptions, and a
    substring match would read that as the probe setting export options.
    """
    import ast
    import inspect
    names = set()
    for node in ast.walk(ast.parse(inspect.getsource(module))):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.alias):
            names.add((node.asname or node.name).split(".")[-1])
    return names


@pytest.mark.parametrize("forbidden", [
    "SetElementOverrides",      # painting
    "ImageExportOptions",       # export options
    "PixelSize",
    "build_palette",            # palette generation
    "choose_step",
    "_build_flat_color_ogs",
    "SetViewDisplayModel",      # display-model suppression
    "SetCategoryOverrides",
])
def test_the_probe_does_not_perform_a_step_production_owns(forbidden):
    assert forbidden not in _referenced_names(probe), (
        "{0} is used by the probe; production owns that step".format(forbidden))


def test_capture_config_turns_stage_a_on_and_redirects_its_output(tmp_path):
    cfg = probe.build_capture_config(str(tmp_path), export_dpi=96.0)
    assert cfg.output_dir == str(tmp_path)
    assert cfg.enable_color_id_buffer_stage_a is True
    assert cfg.color_id_buffer_export_dpi == 96.0


def test_capture_config_keeps_the_default_dpi_when_none_is_given(tmp_path):
    from vop_interwoven.config import Config
    cfg = probe.build_capture_config(str(tmp_path))
    assert cfg.color_id_buffer_export_dpi == Config().color_id_buffer_export_dpi


def test_capture_config_applies_explicit_overrides(tmp_path):
    cfg = probe.build_capture_config(str(tmp_path), overrides={"cell_size_paper_in": 0.25})
    assert cfg.cell_size_paper_in == 0.25


# --- driving size through DPI (D3 / D4) -------------------------------------

@pytest.mark.parametrize("target,paper_width_in", [
    (10000, 80.0), (12000, 80.0), (15000, 80.0), (8123, 54.156), (64, 1.0),
])
def test_dpi_for_pixel_width_round_trips_through_productions_own_formula(target, paper_width_in):
    """export_color_id_buffer_view computes round(export_dpi * paper_width_in)."""
    dpi = probe.dpi_for_pixel_width(target, paper_width_in)
    assert int(round(dpi * paper_width_in)) == target


def test_driving_size_through_dpi_keeps_productions_clamp_on_the_code_path():
    """Above the ceiling the request must still be produced, so production --
    not the probe -- is what clamps it. That clamp is itself under test in D3."""
    from vop_interwoven.color_id_buffer import MAX_STAGE_A_PIXEL_SIZE
    dpi = probe.dpi_for_pixel_width(MAX_STAGE_A_PIXEL_SIZE + 5000, 80.0)
    assert int(round(dpi * 80.0)) == MAX_STAGE_A_PIXEL_SIZE + 5000


@pytest.mark.parametrize("bad", [0, -1])
def test_dpi_for_pixel_width_rejects_a_nonpositive_target(bad):
    with pytest.raises(ValueError):
        probe.dpi_for_pixel_width(bad, 80.0)


@pytest.mark.parametrize("bad", [0.0, -3.0])
def test_dpi_for_pixel_width_rejects_a_nonpositive_paper_width(bad):
    with pytest.raises(ValueError):
        probe.dpi_for_pixel_width(10000, bad)


# --- element id reading -----------------------------------------------------

class _Revit2025Id(object):
    """Revit 2025: Value is the 64-bit property; IntegerValue can throw."""
    Value = 2 ** 40

    @property
    def IntegerValue(self):
        raise OverflowError("id exceeds the legacy 32-bit range")


def test_a_large_id_is_read_through_value_without_touching_integervalue():
    assert probe._safe_int_id(_Revit2025Id()) == 2 ** 40


def test_a_legacy_id_with_only_integervalue_still_reads():
    class Legacy(object):
        IntegerValue = 871863
    assert probe._safe_int_id(Legacy()) == 871863


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


# --- D2: buckets come from production's collected load ----------------------
#
# D2 only localizes a drift onset if step N carries more drawn content than
# step N-1. Enumerating doc.Settings.Categories instead put every category the
# DOCUMENT defines into the sweep, ordered by category id -- so categories this
# view draws nothing from consumed buckets, and the category carrying the load
# could appear in an arbitrary late jump.

class _Id(object):
    def __init__(self, value):
        self.Value = value


class _Category(object):
    def __init__(self, cid, name, model=True, hideable=True):
        self.Id = _Id(cid)
        self.Name = name
        self.CategoryType = "Model" if model else "Annotation"
        self.hideable = hideable


class _Settings(object):
    def __init__(self, categories):
        self.Categories = categories


class _Doc(object):
    def __init__(self, categories):
        self.Settings = _Settings(categories)


class _View(object):
    def __init__(self, categories):
        self._by_id = dict((c.Id.Value, c) for c in categories)
        self.hidden = {}

    def CanCategoryBeHidden(self, cid):
        return self._by_id[cid.Value].hideable

    def GetCategoryHidden(self, cid):
        return bool(self.hidden.get(cid.Value, False))

    def SetCategoryHidden(self, cid, hidden):
        self.hidden[cid.Value] = bool(hidden)


class _FakeDB(object):
    CategoryType = type("CategoryType", (), {"Model": "Model"})

    @staticmethod
    def ElementId(value):
        return _Id(value)


@pytest.fixture
def d2(monkeypatch):
    """A D2 context whose only Revit contact is the fake DB above."""
    import sys
    import types
    module = types.ModuleType("Autodesk.Revit.DB")
    module.CategoryType = _FakeDB.CategoryType
    module.ElementId = _FakeDB.ElementId
    monkeypatch.setitem(sys.modules, "Autodesk", types.ModuleType("Autodesk"))
    monkeypatch.setitem(sys.modules, "Autodesk.Revit", types.ModuleType("Autodesk.Revit"))
    monkeypatch.setitem(sys.modules, "Autodesk.Revit.DB", module)
    monkeypatch.setattr(probe, "_mutate", lambda doc, name, fn: fn())
    monkeypatch.setattr(probe, "_set_view_crop", lambda view, bounds: tuple(bounds))

    def build(categories, load):
        doc, view = _Doc(categories), _View(categories)
        monkeypatch.setattr(probe, "production_category_load",
                            lambda *a, **k: (dict(load), 0, sum(load.values())))
        captured = []

        def capture(cfg, case, label):
            captured.append({"label": label,
                             "visible": sorted(c.Name for c in categories
                                               if not view.GetCategoryHidden(c.Id))})
            return {"tiff_path": label + ".tiff", "label": label}

        ctx = {"doc": doc, "view": view, "cfg": object(), "capture": capture,
               "bounds_xy": BOUNDS, "diag": None}
        return ctx, captured
    return build


def test_d2_buckets_are_ordered_by_production_load_not_category_id(d2):
    # Ids ascending, load descending -- the two orders disagree on purpose.
    categories = [_Category(10, "Walls"), _Category(20, "Roofs"), _Category(30, "Floors")]
    ctx, _ = d2(categories, {10: 5, 20: 900, 30: 40})
    _, detail = probe._case_d2(ctx, steps=3)
    assert [c["name"] for c in detail["categories"]] == ["Roofs", "Floors", "Walls"]
    assert [c["collected_element_count"] for c in detail["categories"]] == [900, 40, 5]


def test_d2_leaves_out_categories_this_view_draws_nothing_from(d2):
    categories = [_Category(10, "Walls"), _Category(20, "Casework"), _Category(30, "Floors")]
    ctx, _ = d2(categories, {10: 7, 30: 3})
    _, detail = probe._case_d2(ctx, steps=4)
    assert [c["name"] for c in detail["categories"]] == ["Walls", "Floors"]
    # Two loaded categories: the sweep is two buckets, not four.
    assert len(detail["steps"]) == 3     # step0 (nothing) + one per bucket


def test_d2_reports_how_much_content_each_step_revealed(d2):
    categories = [_Category(10, "Walls"), _Category(20, "Roofs")]
    ctx, captured = d2(categories, {10: 100, 20: 25})
    _, detail = probe._case_d2(ctx, steps=2)
    counts = [step["revealed_element_count"] for step in detail["steps"]]
    assert counts == [0, 100, 125]
    assert [c["label"] for c in captured] == ["step0", "step1", "step2"]


def test_d2_counts_a_loaded_category_that_cannot_be_hidden_as_always_visible(d2):
    categories = [_Category(10, "Walls"), _Category(20, "Levels", hideable=False)]
    ctx, _ = d2(categories, {10: 100, 20: 8})
    _, detail = probe._case_d2(ctx, steps=2)
    assert [c["name"] for c in detail["always_visible_categories"]] == ["Levels"]
    assert detail["always_visible_element_count"] == 8
    # Step 0 is not empty: it still draws what cannot be hidden.
    assert detail["steps"][0]["revealed_element_count"] == 8


def test_d2_skips_rather_than_sweeping_a_view_with_no_hideable_load(d2):
    categories = [_Category(10, "Levels", hideable=False)]
    ctx, captured = d2(categories, {10: 8})
    exports, detail = probe._case_d2(ctx, steps=8)
    assert exports == [] and captured == []
    assert "no category carrying any of them" in detail["skipped"]


# --- artifact names are unique across a campaign, not just within a job -----
#
# Four D jobs target view 871863 and two B jobs target 587278. Per-job
# directories only keep their artifacts apart while the files stay in those
# directories, and they do not: captures get pooled for analysis, copied off
# the Revit host, and attached to a message.

def test_the_run_token_is_the_output_directory_name():
    assert probe.run_token("/x/captures/d1-hospital-level-2") == "d1-hospital-level-2"
    assert probe.run_token("C:/x/captures/b3-site-plan-4") == "b3-site-plan-4"


def test_the_run_token_sanitizes_and_degrades_to_empty():
    assert probe.run_token("/x/d3 size sweep") == "d3_size_sweep"
    assert probe.run_token("") == ""
    assert probe.run_token(None) == ""


def test_two_jobs_on_one_view_do_not_produce_the_same_capture_name():
    a = probe._stem(probe.run_token("/x/captures/d1-hospital-level-2"), 871863,
                    "d1_determinism", "rep0")
    b = probe._stem(probe.run_token("/x/captures/d4-hospital-level-2"), 871863,
                    "d1_determinism", "rep0")
    assert a != b
    assert a == "d1-hospital-level-2.871863.d1_determinism.rep0"


def test_a_stem_drops_an_empty_run_token_rather_than_leaving_a_dot():
    assert probe._stem("", 871863, "d1_determinism", "rep0") == \
        "871863.d1_determinism.rep0"


# --- a stale module must be visible, not inferred three hours later ---------
#
# Revit holds one Python interpreter open for the whole session. The campaign
# JSON is re-read from disk on every run and the probe code is not, so a module
# imported before an edit stays imported after it. On 2026-09-16 that produced
# a full set of artifacts from an old drift probe against a new campaign, with
# nothing in the output saying so.

def test_a_module_whose_file_has_not_changed_is_not_stale():
    record = probe.source_record(probe)
    assert record["module"] == "tests.dynamo.probe_stage_a_drift_onset"
    assert record["stale"] is False


def test_a_module_whose_file_changed_since_import_is_stale():
    class Loaded(object):
        __name__ = "probe_stage_a_drift_onset"
        MODULE_FILE = probe.MODULE_FILE
        MODULE_MTIME_AT_IMPORT = (probe.MODULE_MTIME_AT_IMPORT or 0) - 3600.0
    record = probe.source_record(Loaded())
    assert record["stale"] is True
    assert record["mtime_now"] != record["mtime_at_import"]


def test_an_uncheckable_module_is_unknown_rather_than_stale():
    """A pasted Dynamo node has no __file__; refusing to run on that would
    block an install the probe is meant to support."""
    class Pasted(object):
        __name__ = "pasted"
        MODULE_FILE = None
        MODULE_MTIME_AT_IMPORT = None
    assert probe.source_record(Pasted())["stale"] is False


def test_stale_sources_selects_only_the_stale_records():
    records = [{"file": "a", "stale": False}, {"file": "b", "stale": True}]
    assert probe.stale_sources(records) == [{"file": "b", "stale": True}]
