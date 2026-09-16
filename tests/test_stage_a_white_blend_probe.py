"""Pure-helper coverage for the Stage A 0.67-white-blend probe (B1-B3).

Only the Revit-free parts run here: selection parsing, the reflection helper
that keeps UNCONFIRMED API members from being assumed, the B1 summary, and the
B3 gating that decides whether a re-export is warranted at all.
"""
import pytest

from tests.dynamo import probe_stage_a_white_blend as probe


def ogs(**fields):
    """A stand-in for OverrideGraphicSettings carrying only what B1 reads."""
    class _OGS(object):
        pass
    value = _OGS()
    for key, item in fields.items():
        setattr(value, key, item)
    return value


def element_record(element_id, halftone=None, category_halftone=None,
                   transparency=0, in_underlay=None, level="Level 2", option=None):
    return {
        "element_id": element_id,
        "element_overrides": None if halftone is None else
            {"Halftone": halftone, "SurfaceTransparency": transparency},
        "category_overrides": None if category_halftone is None else
            {"Halftone": category_halftone},
        "in_underlay_range": in_underlay,
        "level_name": level,
        "design_option_name": option,
    }


def test_importing_the_probe_starts_no_transaction_and_sets_no_output():
    assert not hasattr(probe, "OUT")
    assert probe.PROBE_NAME == "stage_a_white_blend"
    assert probe.CASES == ("b1_query", "b3_baseline", "b3_underlay_off", "b3_halftone_cleared")


def test_candidate_categories_match_the_baseline_run():
    assert probe.CANDIDATE_CATEGORY_NAMES == (
        "Walls", "Generic Models", "Roofs", "Doors", "Windows")


# --- selection and element-id parsing ---------------------------------------

def test_all_selects_every_case():
    assert probe.select_cases("all") == list(probe.CASES)
    assert probe.select_cases(None) == list(probe.CASES)


def test_an_unknown_case_raises_rather_than_selecting_nothing():
    with pytest.raises(ValueError, match="Unknown case"):
        probe.select_cases("b2_pixel_test")


def test_element_ids_parse_from_a_comma_string_and_de_duplicate():
    assert probe.parse_element_ids("587278, 871863, 587278") == [587278, 871863]


def test_element_ids_parse_from_a_list_of_ints():
    assert probe.parse_element_ids([1, 2, 3]) == [1, 2, 3]


def test_no_element_ids_means_use_the_candidate_categories():
    assert probe.parse_element_ids(None) is None
    assert probe.parse_element_ids("") is None
    assert probe.parse_element_ids([]) is None


# --- reflection over UNCONFIRMED API members --------------------------------

def test_reflection_reports_an_absent_member_as_absent():
    found = probe._reflect(object(), ("GetUnderlayBaseLevel",))
    assert found["GetUnderlayBaseLevel"] == {"present": False}


def test_reflection_calls_a_present_method_and_records_its_value():
    class View(object):
        def GetUnderlayBaseLevel(self):
            return 311
    found = probe._reflect(View(), ("GetUnderlayBaseLevel",))
    assert found["GetUnderlayBaseLevel"] == {"present": True, "callable": True, "value": 311}


def test_reflection_records_a_raising_member_instead_of_swallowing_it():
    class View(object):
        def GetUnderlayTopLevel(self):
            raise RuntimeError("not a plan view")
    found = probe._reflect(View(), ("GetUnderlayTopLevel",))
    assert found["GetUnderlayTopLevel"]["present"] is True
    assert "RuntimeError: not a plan view" in found["GetUnderlayTopLevel"]["call_error"]


def test_reflection_records_a_non_callable_attribute_as_a_value():
    class Doc(object):
        HalftoneBrightness = 50
    found = probe._reflect(Doc(), ("HalftoneBrightness",))
    assert found["HalftoneBrightness"] == {"present": True, "callable": False, "value": 50}


def test_stringify_keeps_scalars_and_element_ids_but_never_guesses():
    assert probe._stringify(None) is None
    assert probe._stringify(True) is True
    assert probe._stringify(0.5) == 0.5

    class ElementId(object):
        IntegerValue = 587278
    assert probe._stringify(ElementId()) == 587278

    class Opaque(object):
        def __str__(self):
            return "UnderlayOrientation.LookingDown"
    assert probe._stringify(Opaque()) == "UnderlayOrientation.LookingDown"


# --- OGS reporting ----------------------------------------------------------

def test_ogs_report_is_none_for_an_absent_override():
    assert probe._ogs_report(None) is None


def test_ogs_report_reads_halftone_transparency_and_colors():
    class Color(object):
        IsValid = True
        Red, Green, Blue = 12, 34, 56
    report = probe._ogs_report(ogs(Halftone=True, SurfaceTransparency=0,
                                   ProjectionLineColor=Color()))
    assert report["Halftone"] is True
    assert report["SurfaceTransparency"] == 0
    assert report["ProjectionLineColor"] == [12, 34, 56]


def test_ogs_report_omits_fields_this_install_does_not_expose():
    report = probe._ogs_report(ogs(Halftone=False))
    assert "Halftone" in report
    assert "SurfaceTransparency" not in report


# --- B1 summary and B3 gating ----------------------------------------------

def test_underlay_alone_warrants_the_underlay_off_reexport():
    summary = probe._summarize_b1(
        {"underlay_configured": True},
        [element_record(1, halftone=False, in_underlay=True),
         element_record(2, halftone=False, in_underlay=True)])
    assert summary["underlay_off_is_warranted"] is True
    assert summary["halftone_clear_is_warranted"] is False
    assert summary["elements_on_an_underlay_level"] == [1, 2]


def test_element_halftone_warrants_the_halftone_cleared_reexport():
    summary = probe._summarize_b1(
        {"underlay_configured": False},
        [element_record(1, halftone=True), element_record(2, halftone=False)])
    assert summary["halftone_clear_is_warranted"] is True
    assert summary["elements_with_element_halftone"] == [1]
    assert summary["underlay_off_is_warranted"] is False


def test_category_halftone_alone_still_warrants_the_halftone_reexport():
    summary = probe._summarize_b1(
        {"underlay_configured": False},
        [element_record(1, halftone=False, category_halftone=True)])
    assert summary["halftone_clear_is_warranted"] is True
    assert summary["elements_with_category_halftone"] == [1]


def test_a_view_with_neither_mechanism_warrants_no_reexport_at_all():
    summary = probe._summarize_b1(
        {"underlay_configured": False},
        [element_record(1, halftone=False), element_record(2, halftone=False)])
    assert summary["underlay_off_is_warranted"] is False
    assert summary["halftone_clear_is_warranted"] is False


def test_surface_transparency_is_reported_separately_from_halftone():
    summary = probe._summarize_b1(
        {"underlay_configured": False},
        [element_record(1, halftone=False, transparency=33)])
    assert summary["elements_with_surface_transparency"] == [1]
    assert summary["halftone_clear_is_warranted"] is False


def test_summary_lists_the_distinct_levels_and_design_options_seen():
    summary = probe._summarize_b1(
        {"underlay_configured": True},
        [element_record(1, level="Level 2", option="Option A"),
         element_record(2, level="Level 3", option=None),
         element_record(3, level="Level 2", option="Option A")])
    assert summary["distinct_levels"] == ["Level 2", "Level 3"]
    assert summary["distinct_design_options"] == ["Option A"]
    assert summary["candidate_count"] == 3


def test_an_unreadable_override_never_counts_as_halftone_set():
    """A string error record must not be read as a mapping with Halftone True."""
    record = element_record(1)
    record["element_overrides"] = "ERROR InvalidOperationException: view is closed"
    summary = probe._summarize_b1({"underlay_configured": False}, [record])
    assert summary["elements_with_element_halftone"] == []
    assert summary["halftone_clear_is_warranted"] is False


# --- B3 redundancy, gated on production's own diagnostics -------------------

def test_the_halftone_variant_is_redundant_when_production_cleared_it():
    """Production clears halftone itself, so re-clearing re-exports the baseline."""
    assert probe.production_cleared_category_halftone({"events": []}, True) is True
    assert probe.production_cleared_category_halftone(
        {"events": [{"callsite": "paint_element_override"}]}, True) is True


def test_the_halftone_variant_runs_when_productions_own_step_failed():
    """That step is guarded and only warns, so a capture can reach export with
    category halftone still set -- and then the variant is a real experiment."""
    assert probe.production_cleared_category_halftone(
        {"events": [{"callsite": "category_halftone", "message": "boom"}]}, True) is False


@pytest.mark.parametrize("payload", [None, {}, {"events": None}, {"events": "nope"},
                                     {"events": [None, "junk"]}])
def test_unreadable_diagnostics_assume_productions_normal_behaviour(payload):
    assert probe.production_cleared_category_halftone(payload, True) is True


@pytest.mark.parametrize("payload", [None, {}, {"events": []},
                                     {"events": [{"callsite": "category_halftone"}]}])
def test_no_capture_yet_is_unknown_not_success(payload):
    """A selection like b1_query,b3_halftone_cleared consults this before any
    capture has run. Reading empty diagnostics as "no error, so production
    cleared it" would skip the only export requested, on evidence that does
    not exist. Unknown runs the variant: a redundant export costs disk, a
    wrongly skipped one costs the answer."""
    assert probe.production_cleared_category_halftone(payload, False) is False


def test_the_gate_needs_both_a_capture_and_clean_diagnostics():
    assert probe.production_cleared_category_halftone({"events": []}, True) is True
    assert probe.production_cleared_category_halftone({"events": []}, False) is False


def test_a_read_only_selection_is_recognised_as_needing_no_export():
    assert issubclass(probe._NoExportCasesRequested, Exception)
    assert [c for c in probe.select_cases("b1_query") if c != "b1_query"] == []
    assert [c for c in probe.select_cases("all") if c != "b1_query"] == [
        "b3_baseline", "b3_underlay_off", "b3_halftone_cleared"]


def test_b1_is_ordered_before_every_export_case():
    """B1 must read the authored state, not production's paint."""
    assert probe.CASES[0] == "b1_query"
    assert all(case.startswith("b3_") for case in probe.CASES[1:])


# --- driving production rather than mirroring it ----------------------------

def test_the_probe_captures_through_the_shared_production_entry():
    import inspect
    source = inspect.getsource(probe._run_native)
    assert "production_capture" in source
    assert "plan_capture_geometry" in source


# SetElementOverrides is deliberately NOT forbidden here: B3's halftone
# variant clears element overrides as its experiment, which is a mutation the
# probe owns, not the paint step production owns.
@pytest.mark.parametrize("forbidden", [
    "ImageExportOptions", "PixelSize", "build_palette", "_build_flat_color_ogs",
    "choose_step", "init_view_raster",   # reached through the shared capture helper
])
def test_the_probe_does_not_perform_a_step_production_owns(forbidden):
    import ast
    import inspect
    names = set()
    for node in ast.walk(ast.parse(inspect.getsource(probe))):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    assert forbidden not in names



# --- category override snapshot / restore -----------------------------------

class _View(object):
    def __init__(self, overrides):
        self._overrides = dict(overrides)
        self.set_calls = []

    def GetCategoryOverrides(self, cat_id):
        if cat_id not in self._overrides:
            raise RuntimeError("no overrides for {0}".format(cat_id))
        return self._overrides[cat_id]

    def SetCategoryOverrides(self, cat_id, ogs):
        self.set_calls.append((cat_id, ogs))


def test_category_overrides_are_snapshotted_for_an_exact_undo():
    view = _View({1: "ogs-walls", 2: "ogs-roofs"})
    state = probe._category_override_state(view, [(1, "Walls"), (2, "Roofs")])
    assert state == [(1, "Walls", "ogs-walls"), (2, "Roofs", "ogs-roofs")]


def test_a_category_whose_overrides_cannot_be_read_is_left_out_of_the_snapshot():
    view = _View({1: "ogs-walls"})
    state = probe._category_override_state(view, [(1, "Walls"), (9, "Doors")])
    assert [name for _, name, _ in state] == ["Walls"]


def test_restoring_writes_back_exactly_what_was_captured():
    view = _View({1: "ogs-walls", 2: "ogs-roofs"})
    state = probe._category_override_state(view, [(1, "Walls"), (2, "Roofs")])
    assert probe._restore_category_overrides(view, state) == ["Walls", "Roofs"]
    assert view.set_calls == [(1, "ogs-walls"), (2, "ogs-roofs")]


def test_restoring_skips_a_null_snapshot_rather_than_writing_none():
    view = _View({})
    assert probe._restore_category_overrides(view, [(1, "Walls", None)]) == []
    assert view.set_calls == []


# --- id stringification must not re-probe IntegerValue (Codex round 2) ------

class _LargeId(object):
    """Revit 2025: Value carries the id; the deprecated getter throws."""
    Value = 2 ** 40

    @property
    def IntegerValue(self):
        raise OverflowError("id exceeds the legacy 32-bit range")


def test_a_large_underlay_id_stringifies_without_touching_integervalue():
    assert probe._stringify(_LargeId()) == 2 ** 40


def test_element_id_int_reads_value_first():
    assert probe._element_id_int(_LargeId()) == 2 ** 40

    class Legacy(object):
        IntegerValue = 587278
    assert probe._element_id_int(Legacy()) == 587278


def test_element_id_int_has_no_int_fallback_so_non_ids_stay_non_ids():
    """It decides *whether* a value is an id, so int-convertible is not enough."""
    assert probe._element_id_int("587278") is None
    assert probe._element_id_int(587278) is None
    assert probe._element_id_int(object()) is None


def test_a_non_id_object_still_stringifies_to_its_text():
    class Orientation(object):
        def __str__(self):
            return "UnderlayOrientation.LookingDown"
    assert probe._stringify(Orientation()) == "UnderlayOrientation.LookingDown"


def test_scalars_and_none_are_passed_through_untouched():
    assert probe._stringify(None) is None
    assert probe._stringify(True) is True
    assert probe._stringify(50) == 50
    assert probe._stringify(0.5) == 0.5


def test_reflection_of_a_large_id_property_does_not_raise():
    """_reflect stringifies whatever it reads, so it inherits the same hazard."""
    class View(object):
        def GetUnderlayBaseLevel(self):
            return _LargeId()
    found = probe._reflect(View(), ("GetUnderlayBaseLevel",))
    assert found["GetUnderlayBaseLevel"]["value"] == 2 ** 40


# --- reflection must never abort B1 (Codex round 3) -------------------------

class _ThrowingProperty(object):
    """A .NET-style property that exists but whose getter fails for this view."""

    @property
    def GetUnderlayBaseLevel(self):
        raise RuntimeError("not valid for this view type")


def test_a_present_member_whose_getter_throws_is_a_read_error_not_a_crash():
    """hasattr() invokes the getter and only swallows AttributeError, so the
    old presence check propagated and aborted the whole B1 query."""
    found = probe._reflect(_ThrowingProperty(), ("GetUnderlayBaseLevel",))
    assert found["GetUnderlayBaseLevel"]["present"] is True
    assert "RuntimeError: not valid for this view type" in \
        found["GetUnderlayBaseLevel"]["read_error"]


def test_absence_is_distinguished_from_a_failing_getter():
    absent = probe._reflect(object(), ("GetUnderlayTopLevel",))
    assert absent["GetUnderlayTopLevel"] == {"present": False}
    failing = probe._reflect(_ThrowingProperty(), ("GetUnderlayBaseLevel",))
    assert failing["GetUnderlayBaseLevel"]["present"] is True


def test_one_throwing_member_does_not_stop_the_others_being_reported():
    class Mixed(_ThrowingProperty):
        GetUnderlayTopLevel = 311
    found = probe._reflect(Mixed(), ("GetUnderlayBaseLevel", "GetUnderlayTopLevel",
                                     "SetUnderlayRange"))
    assert "read_error" in found["GetUnderlayBaseLevel"]
    assert found["GetUnderlayTopLevel"]["value"] == 311
    assert found["SetUnderlayRange"] == {"present": False}


def test_a_method_that_raises_when_called_is_still_a_call_error():
    class View(object):
        def GetUnderlayOrientation(self):
            raise ValueError("no underlay")
    found = probe._reflect(View(), ("GetUnderlayOrientation",))
    assert found["GetUnderlayOrientation"]["callable"] is True
    assert "ValueError: no underlay" in found["GetUnderlayOrientation"]["call_error"]


# --- an incompletely cleared halftone variant is not the experiment ---------

class _HalftoneView(object):
    """A view where some category refuses its override."""

    def __init__(self, refuse=()):
        self.refuse = set(refuse)
        self.category_sets = []

    def GetElementOverrides(self, eid):
        return ogs(Halftone=False, SurfaceTransparency=0,
                   SetHalftone=lambda *_a: None, SetSurfaceTransparency=lambda *_a: None)

    def SetElementOverrides(self, eid, value):
        pass

    def GetCategoryOverrides(self, cat_id):
        if cat_id in self.refuse:
            raise RuntimeError("category refuses overrides")
        return ogs(Halftone=False, SetHalftone=lambda *_a: None,
                   SetSurfaceTransparency=lambda *_a: None)

    def SetCategoryOverrides(self, cat_id, value):
        self.category_sets.append(cat_id)


class _Diag(object):
    def __init__(self):
        self.errors = []

    def error(self, **kwargs):
        self.errors.append(kwargs)


def test_clearing_every_category_reports_fully_cleared():
    view = _HalftoneView()
    diag = _Diag()
    detail = probe._clear_halftone(None, view, [], [(1, "Walls"), (2, "Roofs")], diag=diag)
    assert detail["fully_cleared"] is True
    assert detail["categories_cleared"] == ["Walls", "Roofs"]
    assert detail["failures"] == []
    assert diag.errors == []


def test_a_category_that_refuses_its_override_is_recorded_not_skipped():
    """Exporting a capture labelled halftone_cleared with halftone still active
    on some category would make the comparison say the opposite of what it
    appears to."""
    view = _HalftoneView(refuse={2})
    diag = _Diag()
    detail = probe._clear_halftone(None, view, [], [(1, "Walls"), (2, "Roofs")], diag=diag)
    assert detail["fully_cleared"] is False
    assert detail["categories_cleared"] == ["Walls"]
    assert [f["identity"] for f in detail["failures"]] == ["Roofs"]
    assert detail["categories_requested"] == ["Walls", "Roofs"]
    # Recorded in Diagnostics, per the repository's no-silent-failure rule.
    assert len(diag.errors) == 1
    assert diag.errors[0]["callsite"] == "clear_halftone"


def test_clearing_works_without_a_diagnostics_object():
    view = _HalftoneView(refuse={1})
    detail = probe._clear_halftone(None, view, [], [(1, "Walls")], diag=None)
    assert detail["fully_cleared"] is False


# --- B3 captures must share the baseline's geometry -------------------------

def test_the_probe_pins_the_crop_before_the_first_b3_capture():
    """production_capture re-runs init_view_raster every time, so a variant
    that changes what is visible would otherwise be captured at different
    bounds and density from the baseline."""
    import inspect
    source = inspect.getsource(probe._run_native)
    pin = source.index("pin_capture_geometry")
    first_capture = source.index("captures_taken = []")
    assert pin < first_capture


def test_the_probe_reports_whether_every_variant_matched_the_baseline():
    import inspect
    source = inspect.getsource(probe._run_native)
    assert "capture_geometry_matches_baseline" in source
    assert "not attributable to the mechanism" in source


# --- B1 is a precondition of the gated B3 variants, not a separate errand ---
#
# Each gated variant only runs where B1 found the mechanism present in the
# authored view, and B1's result lives in a local of _run_native. A selection
# that omits b1_query therefore SKIPS them -- and a b3_baseline alongside is
# enough for the run to be reported completed, so resume never retries the
# captures that were quietly dropped.

@pytest.mark.parametrize("selection", [
    "b3_underlay_off",
    "b3_halftone_cleared",
    "b3_baseline,b3_underlay_off,b3_halftone_cleared",
])
def test_a_gated_b3_variant_without_b1_is_refused_not_silently_skipped(selection):
    with pytest.raises(ValueError, match="b1_query"):
        probe.select_cases(selection)


def test_the_same_selection_is_accepted_once_b1_is_in_it():
    assert probe.select_cases("b1_query,b3_baseline,b3_underlay_off") == \
        ["b1_query", "b3_baseline", "b3_underlay_off"]


def test_the_baseline_alone_needs_no_b1():
    assert probe.select_cases("b3_baseline") == ["b3_baseline"]


def test_all_still_includes_b1_and_so_stays_valid():
    assert probe.select_cases("all")[0] == "b1_query"


def test_the_white_blend_probe_checks_the_module_it_captures_through():
    """This probe takes its captures through the drift probe's
    production_capture, so a staleness check covering only itself would have
    missed the 2026-09-16 case exactly: this module current, that one not."""
    names = [record["module"] for record in probe.source_records()]
    assert "probe_stage_a_white_blend" in names[0]
    assert "probe_stage_a_drift_onset" in names[1]
    assert all(record["stale"] is False for record in probe.source_records())


# --- an override property that throws must not cost the whole report --------

def test_an_override_property_that_throws_keeps_the_rest_of_the_report():
    """hasattr() invokes the getter and swallows only AttributeError, so a
    property that exists and throws used to take the entire override report
    with it -- including a readable Halftone=True, which is the value the B3
    halftone gate turns on. Third place this defect appeared."""
    class Hostile(object):
        Halftone = True

        @property
        def Transparency(self):
            raise RuntimeError("disposed")

    out = probe._ogs_report(Hostile())
    assert out["Halftone"] is True
    assert out["Transparency"].startswith("ERROR RuntimeError")


def test_an_absent_override_property_is_simply_absent():
    class Sparse(object):
        Halftone = False
    out = probe._ogs_report(Sparse())
    assert out["Halftone"] is False
    assert "Transparency" not in out


# --- an unreadable underlay warrants the experiment, it does not cancel it ---

def test_an_unreadable_underlay_parameter_still_warrants_the_variant():
    underlay = {"underlay_configured": None, "read_errors": ["VIEW_UNDERLAY_BOTTOM_ID"],
                "base_level_id": None, "top_level_id": None}
    summary = probe._summarize_b1(underlay, [])
    assert summary["underlay_off_is_warranted"] is True
    assert summary["underlay_read_errors"] == ["VIEW_UNDERLAY_BOTTOM_ID"]


def test_a_view_with_no_underlay_and_no_read_error_still_skips_the_variant():
    underlay = {"underlay_configured": False, "read_errors": [],
                "base_level_id": None, "top_level_id": None}
    assert probe._summarize_b1(underlay, [])["underlay_off_is_warranted"] is False
