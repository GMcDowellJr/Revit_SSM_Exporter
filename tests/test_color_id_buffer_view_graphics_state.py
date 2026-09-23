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
  2. **Additive only.** ``test_sidecar_top_level_keys_are_additive_only``
     compares the written sidecar's top-level keys against the frozen
     pre-change set, so an "additive" change that renamed or dropped an
     existing key fails here rather than downstream in the decoder. Its
     name used to carry a count ("exactly_two") that was stale by the
     time a third key landed -- the same way a line-number citation rots,
     so the count now lives only in the assertion.

``test_record_is_captured_before_the_view_template_is_detached`` is the
ordering pin. Stage A detaches the view template early on; a record captured
after that point would report "no view template applied" for every templated
view in the project and look perfectly healthy doing it.
"""
import copy
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
    """A view with every readable setting actually set to something.

    Walls are VISIBLE and collected; Casework is hidden in the view and
    contributes no elements. That pairing is deliberate and production-shaped:
    collect_view_elements() is view-scoped, so Revit drops a hidden category's
    elements before the capture ever sees them. An earlier version of this
    fixture marked Walls hidden while still passing Wall elements -- a state
    production cannot reach, and one that let the record's hidden-category
    blindness pass unnoticed.
    """
    walls = FakeCategory("Walls", 10)
    casework = FakeCategory("Casework", 11)
    elements = [FakeElement(1001, walls), FakeElement(1002, walls)]
    link = FakeRevitLinkInstance(9001, "Site.rvt")
    doc = FakeDoc(elements=elements, link_instances=[link],
                  categories=[walls, casework])

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
    view.category_hidden[10] = False
    view.category_hidden[11] = True
    view.link_overrides[9001] = FakeRevitLinkGraphicsSettings(True)
    if isinstance(view, FakeViewPlan):
        view.underlay_base = level.Id
        view.underlay_orientation = "LookingUp"
    return doc, view, elements


def _capture(doc, view, elements, diag=None):
    with install_fake_revit_db():
        return color_id_buffer._capture_view_graphics_state(
            doc, view, elements, diag=diag or FakeDiag(), view_id=42)


# Keys whose values are legitimately bare: schema/provenance metadata and
# counts, not graphics-state readings. Everything else in a field position
# must be an envelope. Explicit, so adding a bare field means adding it here
# in the same change and saying why -- rather than it slipping past.
METADATA_KEYS = frozenset([
    "schema", "captured_at", "basis", "source", "id",
    "elements_with_unreadable_category", "categories_unreadable_in_document_scan",
])


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


def _bare_primitives(node, path="record"):
    """Yield ``(path, value)`` for every primitive sitting where an envelope belongs.

    ``_walk_leaves`` alone cannot enforce the three-valued guarantee: it only
    yields dicts carrying "state", so a field that regressed from an envelope
    to a bare ``False`` is simply not yielded and
    ``test_every_leaf_is_three_valued`` stays green on the envelopes that
    remain. Reported by review on PR #208, and reproduced before fixing --
    regressing a category's "halftone" to ``False`` left that test passing.

    An envelope's own ``value`` is DATA, not a field position -- detail_level's
    value is legitimately the string "Coarse" -- so a primitive payload stops
    the walk. Only a dict or list payload can hold further field positions.
    """
    if isinstance(node, dict):
        if "state" in node:
            payload = node.get("value")
            if isinstance(payload, (dict, list)):
                for sub in _bare_primitives(payload, path + ".value"):
                    yield sub
            return
        for key, sub_node in node.items():
            if key in METADATA_KEYS:
                continue
            for sub in _bare_primitives(sub_node, "{0}.{1}".format(path, key)):
                yield sub
    elif isinstance(node, list):
        for idx, item in enumerate(node):
            for sub in _bare_primitives(item, "{0}[{1}]".format(path, idx)):
                yield sub
    else:
        yield path, node


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

    # The other half, and the one that makes the guarantee real: nothing sits
    # in a field position as a bare primitive. Checking the envelopes alone
    # cannot fail on a field that stopped being an envelope.
    assert list(_bare_primitives(record)) == []


def test_the_walker_reports_a_bare_primitive_where_an_envelope_belongs():
    """The walker's own test -- harness behaviour nothing else observes.

    Without it, ``_bare_primitives`` returning nothing for any input would
    satisfy the assertion above forever.
    """
    doc, view, elements = _furnished_world()
    record = _capture(doc, view, elements)

    regressed = copy.deepcopy(record)
    regressed["category_overrides"]["value"]["categories"]["10"]["halftone"] = False
    assert [path for path, _ in _bare_primitives(regressed)] == [
        "record.category_overrides.value.categories.10.halftone"]

    # A primitive nested inside an envelope's payload is data, not a field
    # position, and must NOT be reported -- otherwise every real value trips it.
    assert list(_bare_primitives({"x": {"state": "value", "value": "Coarse"}})) == []


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
    assert walls["hidden"]["value"] is False
    assert walls["source"] == "collected_element"
    assert walls["name"]["value"] == "Walls"
    applied_filter = record["view_filters"]["value"][0]
    assert applied_filter["enabled"]["value"] is True
    assert applied_filter["visible"]["value"] is False
    assert applied_filter["halftone"]["value"] is True
    assert record["underlay"]["value"]["base_level"]["value"]["id"] == 555
    assert record["underlay"]["value"]["orientation"]["value"] == "LookingUp"
    assert record["view_template_id"]["value"]["id"] == 777
    assert record["detail_level"]["value"] == "Coarse"
    assert record["display_style"]["value"] == "Hidden Line"
    link = record["link_instances"]["value"][0]
    assert link["id"] == 9001
    assert link["link_override_halftone"]["value"] is True


# --- what the record must be able to explain --------------------------------


def test_a_category_hidden_in_the_view_is_recorded_though_nothing_collected_it():
    """The case the record exists for, and the one the element set cannot see.

    collect_view_elements() is view-scoped, so Revit drops a hidden category's
    elements before the capture is handed the list. Derive "categories
    present" from that list alone and the hidden category is simply absent --
    no "hidden": true anywhere to explain the absence. Reported by review on
    PR #208.
    """
    doc, view, elements = _furnished_world()
    # Production-shaped: Casework is hidden, so NOTHING of it is collected.
    assert all(e.Category.Id.IntegerValue != 11 for e in elements)

    record = _capture(doc, view, elements)
    categories = record["category_overrides"]["value"]["categories"]

    assert "11" in categories, "a hidden category must still be recorded"
    casework = categories["11"]
    assert casework["hidden"]["value"] is True
    assert casework["source"] == "hidden_in_view"
    assert casework["name"]["value"] == "Casework"


def test_each_category_names_the_source_that_put_it_in_the_record():
    """Without this a reader cannot tell "visible and collected" from
    "hidden, nothing collected" -- the two the union of sources merges."""
    doc, view, elements = _furnished_world()
    # Walls are BOTH collected and (now) hidden: the overlap case.
    view.category_hidden[10] = True

    record = _capture(doc, view, elements)
    categories = record["category_overrides"]["value"]["categories"]

    assert categories["10"]["source"] == "collected_element+hidden_in_view"
    assert categories["11"]["source"] == "hidden_in_view"


def test_a_failed_document_category_scan_is_unavailable_not_an_empty_scan():
    doc, view, elements = _furnished_world()

    class _HostileSettings(object):
        @property
        def Categories(self):
            raise RuntimeError("settings unavailable")

    doc.Settings = _HostileSettings()
    record = _capture(doc, view, elements)

    section = record["category_overrides"]["value"]
    assert section["document_categories_scanned"]["state"] == "unavailable"
    assert "RuntimeError" in section["document_categories_scanned"]["reason"]
    # The collected-element source still works -- one failure does not take
    # the section down.
    assert "10" in section["categories"]


def test_underlay_orientation_distinguishes_two_otherwise_identical_views():
    """LookingUp and LookingDown draw different graphics from the same base
    and top levels, so a record without orientation reports them identically.
    Reported by review on PR #208; GetUnderlayOrientation is confirmed present
    on the target install by the repo's Revit 2025 probe."""
    doc_down, view_down, elements_down = _furnished_world()
    view_down.underlay_orientation = "LookingDown"
    doc_up, view_up, elements_up = _furnished_world()
    view_up.underlay_orientation = "LookingUp"

    down = _capture(doc_down, view_down, elements_down)["underlay"]["value"]
    up = _capture(doc_up, view_up, elements_up)["underlay"]["value"]

    # Same levels ...
    assert down["base_level"] == up["base_level"]
    assert down["top_level"] == up["top_level"]
    # ... and the records still differ, which is the whole point.
    assert down["orientation"]["value"] == "LookingDown"
    assert up["orientation"]["value"] == "LookingUp"
    assert down != up


def test_a_host_without_getunderlayorientation_is_not_applicable_not_missing():
    doc, view, elements = _furnished_world()
    original = FakeViewPlan.GetUnderlayOrientation
    del FakeViewPlan.GetUnderlayOrientation
    try:
        record = _capture(doc, view, elements)
    finally:
        FakeViewPlan.GetUnderlayOrientation = original

    orientation = record["underlay"]["value"]["orientation"]
    assert orientation["state"] == "not_applicable"
    assert "GetUnderlayOrientation" in orientation["reason"]


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


def test_sidecar_top_level_keys_are_additive_only(tmp_path):
    doc, view, elements = _furnished_world()
    result = _export(doc, view, elements, _cfg(tmp_path), FakeDiag(), _raster())

    keys = set(result["metadata"])
    assert SIDECAR_KEYS_BEFORE <= keys, SIDECAR_KEYS_BEFORE - keys
    # Each name here is a reviewed addition. The frozen set below it is never
    # edited: an "additive" change that renamed or dropped a pre-change key
    # still fails on the subset assertion above.
    assert keys - SIDECAR_KEYS_BEFORE == {
        "view_graphics_state",
        "phase_swap_element_set_audit",
        # Stage A step 1.
        "dwg_imports_omitted",
        # Stage A step 2: the frame-derived export geometry, including the
        # requested-vs-achieved dpi/px/fpp triple and decision A's
        # whole-pixel registration offset.
        "export_frame",
        # Round 3 of the annotation-pass variant probe: whether this capture
        # left OST_Lines visible (probe-only switch; False in production).
        "model_lines_visible",
    }


def test_the_frozen_key_set_is_not_vacuous(tmp_path):
    """Control for the test above. If the export ever stopped writing a
    sidecar, or the fixture stopped reaching the record, both of its
    assertions would hold over an empty key set and report additive-only
    forever. This repo has shipped a check that passed by not running."""
    doc, view, elements = _furnished_world()
    result = _export(doc, view, elements, _cfg(tmp_path), FakeDiag(), _raster())
    keys = set(result["metadata"])
    assert len(SIDECAR_KEYS_BEFORE) >= 10
    assert len(keys) > len(SIDECAR_KEYS_BEFORE)


def test_record_round_trips_through_the_written_sidecar_file(tmp_path):
    doc, view, elements = _furnished_world()
    result = _export(doc, view, elements, _cfg(tmp_path), FakeDiag(), _raster())

    with open(result["sidecar_path"]) as f:
        on_disk = json.load(f)

    assert on_disk["view_graphics_state"] == result["metadata"]["view_graphics_state"]
    assert on_disk["phase_swap_element_set_audit"] == (
        result["metadata"]["phase_swap_element_set_audit"])
