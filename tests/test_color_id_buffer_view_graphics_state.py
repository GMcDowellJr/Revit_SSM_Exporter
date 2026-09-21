"""The per-view graphics-state record written into the Stage A sidecar.

Two properties are worth more than the field list, and both are asserted over
the whole record rather than field by field:

  1. **Three-valued, structurally.** Every leaf is exactly one of
     ``{"state": "value", ...}``, ``{"state": "not_applicable", "reason": ...}``
     or ``{"state": "unavailable", "reason": ...}``. ``test_every_leaf_is_
     three_valued`` walks the record and proves it for every field at once, so
     a field added later cannot quietly ship as a bare bool.
     ``test_a_failed_read_is_unavailable_never_false`` is the one that matters:
     a read that RAISES must never come back as ``False`` -- that coercion is
     what made ``applied_smooth_edges``'s "unchanged" unreadable, per CLAUDE.md.
  2. **Additive only.** ``test_sidecar_gains_exactly_two_top_level_keys``
     compares the written sidecar's top-level keys against the frozen
     pre-change set, so an "additive" change that renamed or dropped an
     existing key fails here rather than downstream in the decoder.

``test_record_is_captured_before_the_view_template_is_detached`` is the
ordering pin. Stage A detaches the view template early on; a record captured
after that point would report "no view template applied" for every templated
view in the project and look perfectly healthy doing it.
"""
import json
import types

import vop_interwoven.color_id_buffer as color_id_buffer
import vop_interwoven.revit.collection as revit_collection
from vop_interwoven.config import Config
from vop_interwoven.core.math_utils import Bounds2D
from vop_interwoven.revit.view_basis import ViewBasis

from tests.stage_a_capture_fakes import (
    INVALID_ELEMENT_ID,
    FakeBuiltInParameter,
    FakeCategory,
    FakeDiag,
    FakeDoc,
    FakeElement,
    FakeElementOnPhaseStatus,
    FakeParameter,
    FakePhaseFilter,
    FakeRevitLinkGraphicsSettings,
    FakeRevitLinkInstance,
    FakeView,
    FakeViewPlan,
    install_fake_revit_db,
)

_PLAN_BASIS = ViewBasis(origin=(0, 0, 0), right=(1, 0, 0), up=(0, 1, 0), forward=(0, 0, -1))

# The sidecar's top-level keys as of the commit before this record existed,
# read off that revision's state_out literal. Frozen deliberately: "additive"
# is a claim about this exact set.
SIDECAR_KEYS_BEFORE = frozenset([
    "view_id", "resolution", "bounds_xy", "model_crop_offset_uv",
    "color_assignment_map", "paint_failures", "paint_failed_element_ids",
    "link_category_color_map", "near_face_w_map", "applied_display_style",
    "applied_smooth_edges", "smooth_edges_read_error", "applied_show_shadows",
    "colorable_category_source", "uncolorable_link_categories",
    "failed_link_categories", "categories_hidden", "filter_state",
    "phase_filter_state", "category_halftone_state", "palette_step",
    "tiff_path", "view_template_detached", "orig_view_template_id",
])

REQUIRED_RECORD_FIELDS = (
    "view_phase", "phase_filter", "phase_status_presentation",
    "category_overrides", "view_filters", "underlay", "view_template_id",
    "detail_level", "display_style", "link_instances",
)

VALID_STATES = ("value", "not_applicable", "unavailable")


def _raster():
    return types.SimpleNamespace(
        W=64, H=48, cell_size_ft=1.0,
        bounds_xy=Bounds2D(0.0, 0.0, 64.0, 48.0),
        model_clip_bounds=None,
        view_basis=_PLAN_BASIS,
    )


def _furnished_world(view_cls=FakeViewPlan):
    """A view with every readable setting actually set to something."""
    walls = FakeCategory("Walls", 10)
    elements = [FakeElement(1001, walls), FakeElement(1002, walls)]
    link = FakeRevitLinkInstance(9001, "Site.rvt")
    doc = FakeDoc(elements=elements, link_instances=[link])

    phase = doc.register(FakeElement(300, None, name="New Construction"))
    template = doc.register(FakeElement(777, None, name="Arch Plan Template"))
    level = doc.register(FakeElement(555, None, name="Level 1"))
    view_filter = doc.register(FakeElement(880, None, name="Existing Walls Filter"))

    phase_filter = FakePhaseFilter("Show Complete", 4100)
    phase_filter.SetPhaseStatusPresentation(
        FakeElementOnPhaseStatus.Demolished, "Overridden")
    doc.phase_filters.append(phase_filter)
    doc.register(phase_filter)

    view = view_cls(42)
    view.params[FakeBuiltInParameter.VIEW_PHASE] = FakeParameter(phase.Id)
    view.params[FakeBuiltInParameter.VIEW_PHASE_FILTER] = FakeParameter(phase_filter.Id)
    view.ViewTemplateId = template.Id
    view.DetailLevel = "Coarse"
    view.DisplayStyle = "Hidden Line"
    view.filters = [view_filter.Id]
    view.filter_enabled[880] = True
    view.filter_visibility[880] = False
    view.GetFilterOverrides(view_filter.Id).SetHalftone(True)
    view.GetCategoryOverrides(walls.Id).SetHalftone(True)
    view.category_hidden[10] = True
    view.link_overrides[9001] = FakeRevitLinkGraphicsSettings(True)
    if isinstance(view, FakeViewPlan):
        view.underlay_base = level.Id
    return doc, view, elements


def _capture(doc, view, elements, diag=None):
    with install_fake_revit_db():
        return color_id_buffer._capture_view_graphics_state(
            doc, view, elements, diag=diag or FakeDiag(), view_id=42)


def _walk_leaves(node, path="record"):
    """Yield ``(path, leaf)`` for every three-valued envelope in the record.

    An envelope is any dict carrying a "state" key; its own "value" is walked
    too, so nested envelopes (a category's halftone, a filter's visibility)
    are reached rather than stopping at the section that contains them.
    """
    if isinstance(node, dict):
        if "state" in node:
            yield path, node
            if node.get("state") == "value":
                for sub in _walk_leaves(node.get("value"), path + ".value"):
                    yield sub
            return
        for key, sub_node in node.items():
            for sub in _walk_leaves(sub_node, "{0}.{1}".format(path, key)):
                yield sub
    elif isinstance(node, list):
        for idx, item in enumerate(node):
            for sub in _walk_leaves(item, "{0}[{1}]".format(path, idx)):
                yield sub


# --- shape ------------------------------------------------------------------


def test_record_carries_every_required_field():
    doc, view, elements = _furnished_world()
    record = _capture(doc, view, elements)

    assert record["schema"] == color_id_buffer.VIEW_GRAPHICS_STATE_SCHEMA
    for field in REQUIRED_RECORD_FIELDS:
        assert field in record, field


def test_every_leaf_is_three_valued():
    doc, view, elements = _furnished_world()
    record = _capture(doc, view, elements)

    leaves = list(_walk_leaves(record))
    # Reachability control: a walker that found nothing would pass the loop
    # below vacuously.
    assert len(leaves) > len(REQUIRED_RECORD_FIELDS)
    for path, leaf in leaves:
        assert leaf["state"] in VALID_STATES, path
        if leaf["state"] == "value":
            assert "value" in leaf, path
            assert "reason" not in leaf, path
        else:
            assert isinstance(leaf["reason"], str) and leaf["reason"], path
            assert "value" not in leaf, path


def test_a_furnished_view_resolves_every_field_to_a_real_value():
    """Control for the failure cases below: with everything present, nothing
    is "unavailable". Without this, a record that failed every read would
    still satisfy the three-valued property."""
    doc, view, elements = _furnished_world()
    record = _capture(doc, view, elements)

    for field in REQUIRED_RECORD_FIELDS:
        assert record[field]["state"] == "value", (field, record[field])

    assert record["view_phase"]["value"]["name"]["value"] == "New Construction"
    assert record["phase_filter"]["value"]["id"] == 4100
    presentation = record["phase_status_presentation"]["value"]
    assert set(presentation) == {"New", "Existing", "Demolished", "Temporary"}
    assert presentation["Demolished"]["value"] == "Overridden"
    walls = record["category_overrides"]["value"]["categories"]["10"]
    assert walls["halftone"]["value"] is True
    assert walls["hidden"]["value"] is True
    applied_filter = record["view_filters"]["value"][0]
    assert applied_filter["enabled"]["value"] is True
    assert applied_filter["visible"]["value"] is False
    assert applied_filter["halftone"]["value"] is True
    assert record["underlay"]["value"]["base_level"]["value"]["id"] == 555
    assert record["view_template_id"]["value"]["id"] == 777
    assert record["detail_level"]["value"] == "Coarse"
    assert record["display_style"]["value"] == "Hidden Line"
    link = record["link_instances"]["value"][0]
    assert link["id"] == 9001
    assert link["link_override_halftone"]["value"] is True


# --- the three values -------------------------------------------------------


def test_underlay_is_not_applicable_on_a_non_plan_view():
    doc, view, elements = _furnished_world(view_cls=FakeView)
    record = _capture(doc, view, elements)

    assert record["underlay"]["state"] == "not_applicable"
    assert "plan-view" in record["underlay"]["reason"]
    # Per-link underlay is a different question with its own answer.
    link = record["link_instances"]["value"][0]
    assert link["underlay"]["state"] == "not_applicable"


def test_a_view_without_a_template_is_not_applicable_not_unavailable():
    doc, view, elements = _furnished_world()
    view.ViewTemplateId = INVALID_ELEMENT_ID
    record = _capture(doc, view, elements)

    assert record["view_template_id"]["state"] == "not_applicable"
    assert "no view template" in record["view_template_id"]["reason"]


def test_a_failed_read_is_unavailable_never_false():
    """The property this whole envelope exists for.

    ``GetCategoryOverrides`` raising must not land in the record as
    ``halftone: False`` -- indistinguishable from a category that genuinely
    is not halftoned.
    """
    doc, view, elements = _furnished_world()

    def _boom(_cat_id):
        raise RuntimeError("category override read failed")

    view.GetCategoryOverrides = _boom
    diag = FakeDiag()
    record = _capture(doc, view, elements, diag=diag)

    halftone = record["category_overrides"]["value"]["categories"]["10"]["halftone"]
    assert halftone["state"] == "unavailable"
    assert "RuntimeError" in halftone["reason"]
    assert "value" not in halftone
    # The sibling read on the same category still succeeded -- one failure
    # does not take the section down with it.
    assert record["category_overrides"]["value"]["categories"]["10"]["hidden"]["state"] == "value"
    assert any(w.get("callsite") == "graphics_state_category_halftone"
               for w in diag.warnings)


def test_a_host_without_getlinkoverrides_is_not_applicable_not_false():
    doc, view, elements = _furnished_world()
    # Remove the member from the host surface entirely -- setting it to None
    # would be a DIFFERENT case (a member that exists and misbehaves), and
    # production is right to call that one "unavailable" rather than
    # "not_applicable".
    original = FakeView.GetLinkOverrides
    del FakeView.GetLinkOverrides
    try:
        record = _capture(doc, view, elements)
    finally:
        FakeView.GetLinkOverrides = original

    link_halftone = record["link_instances"]["value"][0]["link_override_halftone"]
    assert link_halftone["state"] == "not_applicable"
    assert "GetLinkOverrides" in link_halftone["reason"]


def test_a_section_that_raises_outright_is_unavailable_with_the_reason():
    doc, view, elements = _furnished_world()

    def _boom():
        raise RuntimeError("filters unavailable")

    view.GetFilters = _boom
    record = _capture(doc, view, elements)

    assert record["view_filters"]["state"] == "unavailable"
    assert "RuntimeError: filters unavailable" in record["view_filters"]["reason"]


def test_elements_with_an_unreadable_category_are_counted_not_dropped_silently():
    doc, view, elements = _furnished_world()

    class _Hostile(object):
        Id = None

        @property
        def Category(self):
            raise RuntimeError("stale element")

    diag = FakeDiag()
    record = _capture(doc, view, list(elements) + [_Hostile()], diag=diag)

    section = record["category_overrides"]["value"]
    assert section["elements_with_unreadable_category"] == 1
    assert any(w.get("callsite") == "graphics_state_category_overrides"
               for w in diag.warnings)


# --- placement in the capture lifecycle -------------------------------------


def _cfg(tmp_path):
    cfg = Config()
    cfg.include_linked_rvt = False
    cfg.debug_dump_path = str(tmp_path)
    return cfg


def _export(doc, view, elements, cfg, diag, raster):
    with install_fake_revit_db():
        return color_id_buffer.export_color_id_buffer_view(
            doc, view, elements=elements, cfg=cfg, diag=diag,
            raster=raster, elem_cache=None,
        )


def test_record_is_captured_before_the_view_template_is_detached(tmp_path):
    doc, view, elements = _furnished_world()
    result = _export(doc, view, elements, _cfg(tmp_path), FakeDiag(), _raster())

    record = result["metadata"]["view_graphics_state"]
    # Stage A detaches the template during the capture ...
    assert result["metadata"]["view_template_detached"] is True
    # ... but the record still reports the template the view really carries.
    # Captured after the detach this would read "not_applicable".
    assert record["view_template_id"]["state"] == "value"
    assert record["view_template_id"]["value"]["id"] == 777


def test_record_reports_the_views_own_phase_filter_not_the_neutral_one(tmp_path):
    """With the swap on, the sidecar's phase_filter_state describes the
    capture's phase state; the graphics-state record must still describe the
    view's."""
    doc, view, elements = _furnished_world()
    cfg = _cfg(tmp_path)
    cfg.color_id_neutral_phase_swap = True

    def _collect(doc_, view_, raster_, diag=None, cfg=None):
        return list(elements)

    original = revit_collection.collect_view_elements
    revit_collection.collect_view_elements = _collect
    try:
        result = _export(doc, view, elements, cfg, FakeDiag(), _raster())
    finally:
        revit_collection.collect_view_elements = original

    assert result["metadata"]["phase_filter_state"]["swapped"] is True
    record = result["metadata"]["view_graphics_state"]
    assert record["phase_filter"]["value"]["name"]["value"] == "Show Complete"


def test_record_survives_a_total_capture_failure_as_unavailable(tmp_path, monkeypatch):
    doc, view, elements = _furnished_world()

    def _boom(*args, **kwargs):
        raise RuntimeError("record capture exploded")

    monkeypatch.setattr(color_id_buffer, "_capture_view_graphics_state", _boom)
    diag = FakeDiag()
    result = _export(doc, view, elements, _cfg(tmp_path), diag, _raster())

    record = result["metadata"]["view_graphics_state"]
    # Present and three-valued, not missing -- an absent key is
    # indistinguishable from a capture written before this section existed.
    assert record["state"] == "unavailable"
    assert "RuntimeError" in record["reason"]
    assert record["schema"] == color_id_buffer.VIEW_GRAPHICS_STATE_SCHEMA
    assert result["success"] is True
    assert any(w.get("callsite") == "capture_view_graphics_state"
               for w in diag.warnings)


# --- additive-only ----------------------------------------------------------


def test_sidecar_gains_exactly_two_top_level_keys(tmp_path):
    doc, view, elements = _furnished_world()
    result = _export(doc, view, elements, _cfg(tmp_path), FakeDiag(), _raster())

    keys = set(result["metadata"])
    assert SIDECAR_KEYS_BEFORE <= keys, SIDECAR_KEYS_BEFORE - keys
    assert keys - SIDECAR_KEYS_BEFORE == {
        "view_graphics_state", "phase_swap_element_set_audit"}


def test_record_round_trips_through_the_written_sidecar_file(tmp_path):
    doc, view, elements = _furnished_world()
    result = _export(doc, view, elements, _cfg(tmp_path), FakeDiag(), _raster())

    with open(result["sidecar_path"]) as f:
        on_disk = json.load(f)

    assert on_disk["view_graphics_state"] == result["metadata"]["view_graphics_state"]
    assert on_disk["phase_swap_element_set_audit"] == (
        result["metadata"]["phase_swap_element_set_audit"])
