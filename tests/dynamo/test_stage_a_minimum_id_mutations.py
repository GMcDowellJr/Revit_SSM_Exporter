import builtins
import csv
import json
import sys
import types
import pytest
from pathlib import Path

from tests.dynamo.probe_stage_a_minimum_id_mutations import (
    _add_repo_root_to_path,
    _palette,
    _ensure_typing_module,
    _hide_annotation_categories,
    MUTATION_CATALOG,
    STATUS_ALREADY,
    STATUS_APPLIED,
    STATUS_FAILED,
    STATUS_TEMPLATE,
    backward_elimination_round,
    classify_mutation_evidence,
    dimensions_comparable,
    generate_stage1_variants,
    generate_stage2_variants,
    mutation_status_record,
    pixel_data_sha256,
    resolution_runs,
)


class FakeElementId:
    """Stands in for Autodesk.Revit.DB.ElementId - only IntegerValue and
    equality are needed by _hide_annotation_categories."""

    def __init__(self, value):
        self.IntegerValue = value

    def __eq__(self, other):
        return isinstance(other, FakeElementId) and other.IntegerValue == self.IntegerValue

    def __hash__(self):
        return hash(self.IntegerValue)


FakeElementId.InvalidElementId = FakeElementId(-1)


class FakeCategoryType:
    Annotation = "Annotation"
    Model = "Model"


class FakeBuiltInCategory:
    OST_DetailComponents = 111
    OST_Lines = 112


class FakeCategory:
    def __init__(self, id_value, name, category_type=FakeCategoryType.Annotation):
        self.Id = FakeElementId(id_value)
        self.Name = name
        self.CategoryType = category_type


class FakeSettings:
    def __init__(self, categories):
        self.Categories = categories


class FakeDoc:
    def __init__(self, categories):
        self.Settings = FakeSettings(categories)


class FakeSubTransaction:
    """Stands in for Autodesk.Revit.DB.SubTransaction. _category_template_
    controls_visibility relies only on Start()/RollBack() existing and the
    fake view's CanCategoryBeHidden() re-reading current ViewTemplateId, so
    no real undo bookkeeping is needed here - the production function
    restores ViewTemplateId itself regardless of SubTransaction behavior."""

    def __init__(self, doc):
        self._doc = doc

    def Start(self):
        pass

    def RollBack(self):
        pass


class FakeCategoryView:
    """Stands in for the subset of Autodesk.Revit.DB.View used by
    _hide_annotation_categories: per-category hidden state, ViewTemplateId,
    and CanCategoryBeHidden() results that can differ depending on whether
    a template is currently attached - so the empirical detach-and-retest
    in _category_template_controls_visibility has something real to detect."""

    def __init__(self, view_template_id, can_hide_map, can_hide_map_detached=None, hidden_map=None):
        self.ViewTemplateId = view_template_id
        self._can_hide_attached = dict(can_hide_map)
        self._can_hide_detached = dict(can_hide_map_detached if can_hide_map_detached is not None else can_hide_map)
        self._hidden = dict(hidden_map or {})
        self.set_calls = []

    def GetCategoryHidden(self, cid):
        return self._hidden.get(cid.IntegerValue, False)

    def CanCategoryBeHidden(self, cid):
        table = self._can_hide_detached if self.ViewTemplateId == FakeElementId.InvalidElementId else self._can_hide_attached
        return table.get(cid.IntegerValue, True)

    def SetCategoryHidden(self, cid, value):
        self._hidden[cid.IntegerValue] = value
        self.set_calls.append(cid.IntegerValue)


@pytest.fixture
def fake_revit_db(monkeypatch):
    """_hide_annotation_categories imports CategoryType/BuiltInCategory/
    ElementId/SubTransaction from Autodesk.Revit.DB - not present outside
    Revit, so tests register a minimal fake module the same way
    test_revit_batch_executor.py does for its own Revit API dependencies."""
    fake_db = types.ModuleType("Autodesk.Revit.DB")
    fake_db.CategoryType = FakeCategoryType
    fake_db.BuiltInCategory = FakeBuiltInCategory
    fake_db.ElementId = FakeElementId
    fake_db.SubTransaction = FakeSubTransaction
    monkeypatch.setitem(sys.modules, "Autodesk.Revit.DB", fake_db)


def test_stage1_factorial_complete():
    names = [v["name"] for v in generate_stage1_variants()]
    assert names[:2] == ["attached_element_overrides_only", "detached_preserve_original_display"]
    assert {"detached_none", "detached_A", "detached_S", "detached_F", "detached_AS", "detached_AF", "detached_SF", "detached_ASF"}.issubset(names)


def test_stage1_reduced_when_flat_colors_unsupported():
    names = [v["name"] for v in generate_stage1_variants(flat_colors_supported=False)]
    assert "detached_F" not in names
    assert "detached_ASF" not in names
    assert "detached_AS" in names


def test_stage2_has_single_additions_and_leave_one_outs():
    variants = generate_stage2_variants(("detach_template", "hide_annotation_categories", "smooth_edges_off", "display_style_flat_colors"), ("category_halftone_neutralized", "shadows_off"))
    names = {v["name"] for v in variants}
    assert "baseline_plus_category_halftone_neutralized" in names
    assert "baseline_plus_shadows_off" in names
    assert "full_minus_category_halftone_neutralized" in names
    assert "full_minus_shadows_off" in names
    assert "full_suppression_reference" in names


def test_backward_elimination_bookkeeping():
    reduced, loo = backward_elimination_round(("a", "b", "c"), ("b",))
    assert reduced["mutations"] == ("a", "c")
    assert {v["name"] for v in loo} == {"reduced_minus_a", "reduced_minus_c"}


def test_mutation_status_serializes_all_required_fields():
    rec = mutation_status_record("smooth_edges_off", requested=True, status=STATUS_APPLIED, original=True, effective=False, applied=True)
    assert rec["mutation_id"] == "smooth_edges_off"
    assert rec["api_property_or_method"]
    json.dumps(rec)


def test_dimensions_comparison_invalidation():
    a = {"bounds_source": "active", "accepted_width_px": 10, "actual_width_px": 10, "actual_height_px": 5, "accepted_pixels_per_model_foot": 2.0}
    assert dimensions_comparable(a, dict(a))
    b = dict(a, actual_height_px=6)
    assert not dimensions_comparable(a, b)


def test_pixel_data_hashing_uses_decoded_rgb_order():
    rows = [[(1, 2, 3), (4, 5, 6)], [(7, 8, 9)]]
    assert pixel_data_sha256(rows) == pixel_data_sha256(rows)
    assert pixel_data_sha256(rows) != pixel_data_sha256([[(1, 2, 3)]])


def test_classification_separates_fidelity_and_semantic():
    smooth = mutation_status_record("smooth_edges_off", requested=True, status=STATUS_APPLIED)
    phase = mutation_status_record("phase_filter_neutralized", requested=True, status=STATUS_APPLIED)
    failed = mutation_status_record("display_style_flat_colors", requested=True, status=STATUS_FAILED)
    assert classify_mutation_evidence(smooth, fidelity_failed_without=True) == "required_for_color_fidelity"
    assert classify_mutation_evidence(phase) == "semantic_diagnostics_not_for_default"
    assert classify_mutation_evidence(failed) == "blocked_or_unsupported"


def test_resolution_runs_default_to_paper_space_150():
    assert resolution_runs() == [{"policy": "paper_space_dpi", "target_dpi": 150.0, "fixed_pixel_width": 1600, "max_pixel_dimension": None}]


def test_json_and_csv_serialization(tmp_path):
    payload = {"mutation_inventory": MUTATION_CATALOG, "variants": [{"variant": "x", "requested_mutations": ["smooth_edges_off"], "mutations": {"smooth_edges_off": mutation_status_record("smooth_edges_off", True, STATUS_APPLIED)}, "image_analysis": {"unexpected_rgb_values": {}}, "visible_set_analysis": {}, "rollback_status": "PASS", "eligible_for_recommended_minimum": True}]}
    jp = tmp_path / "probe.json"
    cp = tmp_path / "matrix.csv"
    jp.write_text(json.dumps(payload), encoding="utf-8")
    with cp.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["variant", "mutations_requested"])
        writer.writeheader(); writer.writerow({"variant": "x", "mutations_requested": "smooth_edges_off"})
    assert json.loads(jp.read_text())["variants"][0]["variant"] == "x"
    assert "smooth_edges_off" in cp.read_text()


def test_resolution_contract_fallback_does_not_require_dunder_file(monkeypatch):
    source = Path("tests/dynamo/probe_stage_a_minimum_id_mutations.py").read_text(encoding="utf-8")
    prefix = source.split("PROBE_NAME", 1)[0]
    namespace = {"__builtins__": builtins.__dict__, "__name__": "dynamo_string_probe"}
    exec(compile(prefix, "<string>", "exec"), namespace)
    assert "__file__" not in namespace
    assert namespace["_add_repo_root_to_path"](str(Path.cwd()), None) == str(Path.cwd())
    assert namespace["round_half_up_positive"](1.5) == 2


def test_explicit_bad_repo_root_is_rejected(tmp_path):
    bad_root = tmp_path / "not_repo"
    bad_root.mkdir()
    try:
        _add_repo_root_to_path(str(bad_root), None)
    except RuntimeError as ex:
        assert "IN[8] repository root" in str(ex)
    else:
        raise AssertionError("bad explicit repo root should fail")


def test_assignment_collection_uses_current_viewraster_signature():
    source = Path("tests/dynamo/probe_stage_a_minimum_id_mutations.py").read_text(encoding="utf-8")
    assert "from vop_interwoven.core.math_utils import Bounds2D" in source
    assert "ViewRaster(" in source
    assert "width=10" in source
    assert "height=10" in source
    assert "cell_size=1.0" in source
    assert 'tile_size=getattr(cfg, "tile_size", 16)' in source
    assert "cfg=cfg" in source
    assert "ViewRaster(10, 10, 1.0" not in source


def test_typing_shim_supports_annotation_imports(monkeypatch):
    import sys
    monkeypatch.delitem(sys.modules, "typing", raising=False)
    real_import = builtins.__import__

    def block_typing(name, *args, **kwargs):
        if name == "typing":
            raise ImportError("simulate Dynamo without typing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", block_typing)
    assert _ensure_typing_module() is True
    typing_mod = sys.modules["typing"]
    assert typing_mod.Tuple[str, ...] is typing_mod.Tuple
    assert typing_mod.Optional[int] is typing_mod.Optional


def test_diagnostic_variants_use_canonical_mutation_ids():
    variants = {v["name"]: v for v in generate_stage2_variants(("detach_template",))}
    assert "visibility_off_filters_disabled" in variants["diagnostic_disable_visibility_off_filters"]["mutations"]
    assert "phase_filter_neutralized" in variants["diagnostic_neutral_phase_filter"]["mutations"]
    assert "visibility_off_filters_disabled" in variants["diagnostic_recollect_after_filter_change"]["mutations"]
    assert "phase_filter_neutralized" in variants["diagnostic_recollect_after_phase_change"]["mutations"]


def test_fixed_width_resolution_applies_cap():
    report = __import__("tests.dynamo.probe_stage_a_minimum_id_mutations", fromlist=["build_resolution_report"]).build_resolution_report(
        "fixed_pixel_width", 10, 100, 100, None, "resolved_model_bounds", 1600, max_pixel_dimension=1000
    )
    assert report["max_pixel_dimension"] == 1000
    assert report["capped"] is True
    assert report["accepted_width_px"] <= 1000
    assert report["predicted_height_px"] <= 1000


def test_compare_tiff_pixels_reports_counts_and_bbox(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    mod = __import__("tests.dynamo.probe_stage_a_minimum_id_mutations", fromlist=["compare_tiff_pixels"])
    a = tmp_path / "a.tiff"; b = tmp_path / "b.tiff"
    ia = Image.new("RGB", (2, 2), (255, 255, 255))
    ib = Image.new("RGB", (2, 2), (255, 255, 255))
    ia.putpixel((1, 1), (1, 2, 3))
    ia.save(a); ib.save(b)
    result = mod.compare_tiff_pixels(str(a), str(b))
    assert result["pixel_difference_count"] == 1
    assert result["changed_pixel_bounding_rectangle"] == [1, 1, 1, 1]


def test_no_template_non_hideable_categories_do_not_block(fake_revit_db):
    """The elevation minimum-ID-mutation probe found ViewTemplateId == -1 with
    17 categories reporting CanCategoryBeHidden() == False; that must not be
    classified BLOCKED_BY_TEMPLATE when no template is attached."""
    categories = [FakeCategory(1, "Tags"), FakeCategory(2, "Text Notes"), FakeCategory(3, "Dimensions")]
    doc = FakeDoc(categories)
    view = FakeCategoryView(FakeElementId.InvalidElementId, {1: True, 2: True, 3: False})
    result = {"mutations": {}}
    _hide_annotation_categories(doc, view, result)
    rec = result["mutations"]["hide_annotation_categories"]
    assert rec["status"] != STATUS_TEMPLATE
    assert rec["status"] == STATUS_APPLIED
    assert rec["template_controlled"] is False
    summary = result["annotation_category_summary"]
    assert summary["template_attached"] is False
    assert summary["template_blocked"] == 0
    assert summary["non_hideable"] == 1


def test_hideable_categories_are_still_mutated_and_attested(fake_revit_db):
    categories = [FakeCategory(10, "Tags"), FakeCategory(11, "Levels")]
    doc = FakeDoc(categories)
    view = FakeCategoryView(FakeElementId.InvalidElementId, {10: True, 11: True})
    result = {"mutations": {}}
    _hide_annotation_categories(doc, view, result)
    assert set(view.set_calls) == {10, 11}
    assert view.GetCategoryHidden(FakeElementId(10)) is True
    assert view.GetCategoryHidden(FakeElementId(11)) is True
    rec = result["mutations"]["hide_annotation_categories"]
    assert rec["status"] == STATUS_APPLIED
    assert rec["applied"] is True


def test_non_hideable_categories_remain_visible_in_diagnostics_without_failing(fake_revit_db):
    categories = [FakeCategory(1, "Tags"), FakeCategory(20, "Elevation Marks")]
    doc = FakeDoc(categories)
    view = FakeCategoryView(FakeElementId.InvalidElementId, {1: True, 20: False})
    result = {"mutations": {}}
    _hide_annotation_categories(doc, view, result)
    trace_by_id = {t["category_id"]: t for t in result["annotation_category_trace"]}
    assert trace_by_id[20]["can_hide"] is False
    assert 20 not in view.set_calls
    rec = result["mutations"]["hide_annotation_categories"]
    assert rec["status"] not in (STATUS_FAILED, STATUS_TEMPLATE)


def test_attached_template_with_non_hideable_category_is_blocked_by_template(fake_revit_db):
    """Affirmative, category-level evidence: the template is attached AND
    detaching it empirically makes category 31 hideable, so BLOCKED_BY_TEMPLATE
    is warranted."""
    categories = [FakeCategory(30, "Tags"), FakeCategory(31, "Locked Annotation")]
    doc = FakeDoc(categories)
    view = FakeCategoryView(
        FakeElementId(500),
        can_hide_map={30: True, 31: False},
        can_hide_map_detached={30: True, 31: True},
    )
    result = {"mutations": {}}
    _hide_annotation_categories(doc, view, result)
    rec = result["mutations"]["hide_annotation_categories"]
    assert rec["status"] == STATUS_TEMPLATE
    assert rec["template_controlled"] is True
    trace_by_id = {t["category_id"]: t for t in result["annotation_category_trace"]}
    assert trace_by_id[31]["template_controlled"] is True
    assert view.set_calls == [30]
    summary = result["annotation_category_summary"]
    assert summary["template_attached"] is True
    assert summary["template_blocked"] == 1
    # The empirical detach-and-retest must leave the template reattached.
    assert view.ViewTemplateId == FakeElementId(500)


def test_attached_template_does_not_block_intrinsically_non_hideable_category(fake_revit_db):
    """A template being attached is not, by itself, evidence that it controls
    a given category: category 51 stays non-hideable even after the template
    is (empirically) detached, so it must not be reported BLOCKED_BY_TEMPLATE
    or fail the mutation."""
    categories = [FakeCategory(50, "Tags"), FakeCategory(51, "Sheets")]
    doc = FakeDoc(categories)
    view = FakeCategoryView(
        FakeElementId(500),
        can_hide_map={50: True, 51: False},
        can_hide_map_detached={50: True, 51: False},
    )
    result = {"mutations": {}}
    _hide_annotation_categories(doc, view, result)
    rec = result["mutations"]["hide_annotation_categories"]
    assert rec["status"] != STATUS_TEMPLATE
    assert rec["status"] == STATUS_APPLIED
    trace_by_id = {t["category_id"]: t for t in result["annotation_category_trace"]}
    assert trace_by_id[51]["template_controlled"] is False
    assert view.ViewTemplateId == FakeElementId(500)
    summary = result["annotation_category_summary"]
    assert summary["template_attached"] is True
    assert summary["non_hideable"] == 1
    assert summary["template_blocked"] == 0


def test_already_hidden_categories_still_report_already_matched(fake_revit_db):
    categories = [FakeCategory(40, "Tags")]
    doc = FakeDoc(categories)
    view = FakeCategoryView(FakeElementId.InvalidElementId, {40: True}, hidden_map={40: True})
    result = {"mutations": {}}
    _hide_annotation_categories(doc, view, result)
    rec = result["mutations"]["hide_annotation_categories"]
    assert rec["status"] == STATUS_ALREADY


def test_palette_step_uses_productions_global_threshold_not_the_element_count():
    """Production pins the step to the configured global threshold.

    Sizing the lattice to the view's own count gives step 8 where production
    uses 6, so the probe's ID rasters would not be the ones production
    produces -- the one thing a minimum-ID-mutation search must not get wrong.
    """
    from vop_interwoven.color_id_buffer import choose_step
    from vop_interwoven.config import Config
    threshold = int(Config().color_id_buffer_global_assignment_threshold)
    expected = choose_step(threshold)
    for count in (1, 50, 500, 5000, threshold):
        _palette_colors, step = _palette(count)
        assert step == expected, count


def test_palette_step_falls_back_to_the_count_above_the_threshold():
    from vop_interwoven.color_id_buffer import choose_step
    from vop_interwoven.config import Config
    threshold = int(Config().color_id_buffer_global_assignment_threshold)
    _palette_colors, step = _palette(threshold + 10000)
    assert step == choose_step(threshold + 10000)
