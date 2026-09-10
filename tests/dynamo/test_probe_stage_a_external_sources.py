"""Pure-Python coverage for probe_stage_a_external_sources.py helpers that
don't require Revit. The probe module itself imports cleanly without Revit
(Revit API imports are deferred inside function bodies), but most of its
logic can only run inside Revit/Dynamo - this covers the one exception:
_win_long_path(), the MAX_PATH workaround _export_tiff() relies on.
"""
import os
import sys
import types

import pytest

import tests.dynamo.probe_stage_a_external_sources as probe
import vop_interwoven.color_id_buffer as color_id_buffer


def test_win_long_path_prefixes_on_windows(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(os.path, "abspath", lambda p: p)
    assert probe._win_long_path(r"C:\short\path.tiff") == r"\\?\C:\short\path.tiff"


def test_win_long_path_is_noop_on_posix(monkeypatch):
    monkeypatch.setattr(os, "name", "posix")
    assert probe._win_long_path("/already/short") == "/already/short"


def test_win_long_path_does_not_double_prefix(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    already = r"\\?\C:\already\prefixed.tiff"
    monkeypatch.setattr(os.path, "abspath", lambda p: p)
    assert probe._win_long_path(already) == already


def test_win_long_path_leaves_unc_paths_alone(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    unc = r"\\server\share\file.tiff"
    monkeypatch.setattr(os.path, "abspath", lambda p: p)
    assert probe._win_long_path(unc) == unc


def test_win_long_path_normalizes_the_reported_failure_case_under_the_limit(monkeypatch):
    """The exact destination path from the reported bug (286 chars, over the
    260-char MAX_PATH) must come back under a Win32 API's practical limit
    once prefixed - the \\?\\ prefix itself raises the limit to ~32,767."""
    monkeypatch.setattr(os, "name", "nt")
    dest = (r"C:\Users\gmcdowell\Documents\Revit_SSM_Exporter\campaign\raw\\"
            r"stage-a-initial.07_external_sources.rvt_link.stage_a_external_sources."
            r"capability.fixed_1600.1.8af2c3804b3e\external_sources_probe\\"
            r"__Plan_RVTLink_19293485.dpi_150.linked_per_element_linkelementid_coloring."
            r"external_sources.tiff")
    assert len(dest) > 260
    monkeypatch.setattr(os.path, "abspath", lambda p: p)
    prefixed = probe._win_long_path(dest)
    assert prefixed == "\\\\?\\" + dest


# --- _apply_assignments(): one record per discovered item, no duplication ---
#
# A prior revision kept a second, parallel bookkeeping list that re-recorded
# every failed assignment a second time; e.g. 21 discovered LINK items that
# all failed the LinkElementId override would produce 42 total records
# instead of 21. These tests lock the corrected one-record-per-item contract
# and the explicit APPLIED/UNSUPPORTED/FAILED/NOT_TESTED capability status
# introduced alongside the fix (see LINK_OVERRIDE_* in
# probe_stage_a_external_sources.py).

def _link_item(i):
    return {
        "assignment_key": "LINK:100:{0}".format(i),
        "source_type": "LINK",
        "link_instance_id": 100,
        "linked_element_id": i,
        "link_instance_api_id": object(),
        "linked_element_api_id": object(),
        "host_element_id": None,
        "import_instance_id": None,
        "category": "Walls",
        "label": "LINK {0}".format(i),
    }


def _host_item(i):
    return {
        "assignment_key": "HOST:None:{0}".format(i),
        "source_type": "HOST",
        "host_element_id": i,
        "host_api_id": object(),
        "category": "Walls",
        "label": "HOST {0}".format(i),
    }


class _FakeDoc(object):
    pass


class _FakeView(object):
    def SetElementOverrides(self, *args, **kwargs):
        pass


@pytest.fixture
def stub_paint(monkeypatch):
    """Stub the Revit-only pieces of _apply_assignments (color OGS
    construction and whole-link hide fallback) so the function's own
    per-item bookkeeping can be exercised without a live Revit session."""
    monkeypatch.setattr(probe, "_ogs", lambda doc, rgb: object())
    monkeypatch.setattr(probe, "_palette", lambda n: ([(1, 2, 3)] * n, 1))
    monkeypatch.setattr(probe, "_hide_element_ids",
                        lambda doc, view, ids: sorted({int(x) for x in ids if x is not None}))


def test_apply_assignments_one_record_per_item_when_override_unsupported(monkeypatch, stub_paint):
    # _try_color_link_element_detailed() never returns a clean (False, None) -
    # every failure comes back as (False, <exception>) - so "unsupported" is
    # only reachable through an exception whose type the classifier maps to
    # LINK_OVERRIDE_UNSUPPORTED (see _classify_link_override_exception).
    monkeypatch.setattr(color_id_buffer, "_try_color_link_element_detailed",
                        lambda *a, **k: (False, AttributeError("SetElementOverrides has no LinkElementId overload")))
    items = [_link_item(i) for i in range(21)]
    result = probe._apply_assignments(_FakeDoc(), _FakeView(), "linked_per_element_linkelementid_coloring", items)
    assert len(result["assignments"]) == 21
    assert all(a["link_override_status"] == "UNSUPPORTED" for a in result["assignments"])
    assert all(not a["paint_success"] for a in result["assignments"])
    assert all("AttributeError" in a["paint_failure"] for a in result["assignments"])
    assert result["hidden_link_fallback_ids"] == [100]


def test_apply_assignments_one_record_per_item_when_override_supported(monkeypatch, stub_paint):
    monkeypatch.setattr(color_id_buffer, "_try_color_link_element_detailed", lambda *a, **k: (True, None))
    items = [_link_item(i) for i in range(21)]
    result = probe._apply_assignments(_FakeDoc(), _FakeView(), "linked_per_element_linkelementid_coloring", items)
    assert len(result["assignments"]) == 21
    assert all(a["link_override_status"] == "APPLIED" for a in result["assignments"])
    assert all(a["paint_success"] for a in result["assignments"])
    assert result["hidden_link_fallback_ids"] == []


def test_apply_assignments_unexpected_exception_is_failed_not_unsupported(monkeypatch, stub_paint):
    # A genuine bug (e.g. an invalid link/element reference) must surface as
    # FAILED, with the real exception preserved - never silently reclassified
    # as an expected, non-failing UNSUPPORTED capability finding.
    monkeypatch.setattr(color_id_buffer, "_try_color_link_element_detailed",
                        lambda *a, **k: (False, RuntimeError("boom")))
    result = probe._apply_assignments(_FakeDoc(), _FakeView(), "linked_per_element_linkelementid_coloring", [_link_item(0)])
    assert len(result["assignments"]) == 1
    assert result["assignments"][0]["link_override_status"] == "FAILED"
    assert "boom" in result["assignments"][0]["paint_failure"]


def test_apply_assignments_forced_variant_never_attempts_override(monkeypatch, stub_paint):
    def _fail_if_called(*args, **kwargs):
        raise AssertionError("forced-failure variant must not attempt LinkElementId override")
    monkeypatch.setattr(color_id_buffer, "_try_color_link_element_detailed", _fail_if_called)
    items = [_link_item(i) for i in range(21)]
    result = probe._apply_assignments(_FakeDoc(), _FakeView(), "forced_linked_override_failure_hide_instance_fallback", items)
    assert len(result["assignments"]) == 21
    assert all(a["link_override_status"] == "NOT_TESTED" for a in result["assignments"])
    assert result["hidden_link_fallback_ids"] == [100]


# --- _classify_link_override_exception(): UNSUPPORTED vs FAILED heuristic ---

def test_classify_link_override_exception_environment_gap_is_unsupported():
    assert probe._classify_link_override_exception(AttributeError("no such method")) == "UNSUPPORTED"
    assert probe._classify_link_override_exception(TypeError("bad overload")) == "UNSUPPORTED"
    assert probe._classify_link_override_exception(NotImplementedError("nope")) == "UNSUPPORTED"


def test_classify_link_override_exception_real_bug_is_failed():
    assert probe._classify_link_override_exception(RuntimeError("bad state")) == "FAILED"
    assert probe._classify_link_override_exception(ValueError("invalid element id")) == "FAILED"


# --- _try_color_link_element / _try_color_link_element_detailed wiring ---

def test_try_color_link_element_wrapper_delegates_to_detailed(monkeypatch):
    monkeypatch.setattr(color_id_buffer, "_try_color_link_element_detailed",
                        lambda *a, **k: (True, None))
    assert color_id_buffer._try_color_link_element(None, None, None, None) is True


def test_try_color_link_element_wrapper_discards_exception_on_failure(monkeypatch):
    monkeypatch.setattr(color_id_buffer, "_try_color_link_element_detailed",
                        lambda *a, **k: (False, RuntimeError("boom")))
    assert color_id_buffer._try_color_link_element(None, None, None, None) is False


def test_apply_assignments_host_items_unaffected_by_link_capability_field(stub_paint):
    result = probe._apply_assignments(_FakeDoc(), _FakeView(), "host_reference_coloring", [_host_item(0)])
    assert len(result["assignments"]) == 1
    assert result["assignments"][0]["paint_success"] is True
    assert result["assignments"][0]["link_override_status"] is None


# --- _link_override_capability(): per-variant capability aggregation ---

def test_link_override_capability_aggregation():
    cap = probe._link_override_capability
    assert cap([]) == "not_applicable"
    assert cap([{"link_override_status": "APPLIED"}]) == "supported"
    assert cap([{"link_override_status": "APPLIED"}, {"link_override_status": "UNSUPPORTED"}]) == "unsupported"
    assert cap([{"link_override_status": "FAILED"}, {"link_override_status": "APPLIED"}]) == "failed_unexpectedly"
    assert cap([{"link_override_status": "NOT_TESTED"}]) == "not_tested"


# --- _source_evidence_status(): explicit UNSUPPORTED capability, not a generic FAIL ---

def _analysis(counts):
    return {"pillow_available": True, "expected_color_pixel_counts": counts}


def test_evidence_status_unsupported_link_override_is_explicit_and_not_failing():
    assignments = [{"assignment_key": "k{0}".format(i), "source_type": "LINK",
                    "link_override_status": "UNSUPPORTED"} for i in range(3)]
    status = probe._source_evidence_status(
        "linked_per_element_linkelementid_coloring", assignments, _analysis({}), [])
    assert status["has_required_evidence"] is True
    assert status["capability_status"] == "UNSUPPORTED"


def test_evidence_status_unexpected_link_override_exception_is_not_evidence():
    assignments = [{"assignment_key": "k0", "source_type": "LINK", "link_override_status": "FAILED"}]
    status = probe._source_evidence_status(
        "linked_per_element_linkelementid_coloring", assignments, _analysis({}), [])
    assert status["has_required_evidence"] is False
    assert status["capability_status"] == "FAILED_UNEXPECTEDLY"


def test_evidence_status_supported_link_override_requires_rendered_color():
    assignments = [{"assignment_key": "k0", "source_type": "LINK", "link_override_status": "APPLIED"}]
    no_render = probe._source_evidence_status(
        "linked_per_element_linkelementid_coloring", assignments, _analysis({"k0": 0}), [])
    assert no_render["has_required_evidence"] is False
    rendered = probe._source_evidence_status(
        "linked_per_element_linkelementid_coloring", assignments, _analysis({"k0": 5}), [])
    assert rendered["has_required_evidence"] is True
    assert rendered["capability_status"] == "SUPPORTED"


# --- _source_family_conclusion(): requested-case scoping, not HOST+LINK+DWG always ---

def _variant(name, conclusion, skipped=False):
    return {"variant": name, "conclusion": conclusion, "skipped": skipped}


def test_family_conclusion_rvt_link_only_job_not_penalized_for_missing_host_or_dwg():
    selected = ["linked_per_element_linkelementid_coloring", "forced_linked_override_failure_hide_instance_fallback"]
    variants = [_variant("linked_per_element_linkelementid_coloring", "UNSUPPORTED"),
                _variant("forced_linked_override_failure_hide_instance_fallback", "PASS")]
    family_status, conclusion = probe._source_family_conclusion(variants, selected)
    assert family_status["HOST"]["requested"] is False
    assert family_status["DWG"]["requested"] is False
    assert family_status["LINK"]["requested"] is True
    assert family_status["LINK"]["passed"] is True
    assert conclusion == "PASS"


def test_family_conclusion_dwg_only_job_not_penalized_for_missing_link_or_host():
    selected = ["dwg_importinstance_coloring"]
    variants = [_variant("dwg_importinstance_coloring", "PASS")]
    family_status, conclusion = probe._source_family_conclusion(variants, selected)
    assert family_status["HOST"]["requested"] is False
    assert family_status["LINK"]["requested"] is False
    assert family_status["DWG"]["requested"] is True
    assert conclusion == "PASS"


def test_family_conclusion_still_requires_every_selected_family_when_all_requested():
    selected = list(probe.VARIANTS)
    variants = [_variant("host_reference_coloring", "PASS"),
                _variant("linked_per_element_linkelementid_coloring", "UNSUPPORTED"),
                _variant("forced_linked_override_failure_hide_instance_fallback", "PASS"),
                _variant("dwg_importinstance_coloring", "INCONCLUSIVE")]
    family_status, conclusion = probe._source_family_conclusion(variants, selected)
    assert family_status["DWG"]["passed"] is False
    assert conclusion == "INCONCLUSIVE"


def test_family_conclusion_fails_on_any_variant_failure_regardless_of_scope():
    selected = ["dwg_importinstance_coloring"]
    variants = [_variant("dwg_importinstance_coloring", "FAIL")]
    _, conclusion = probe._source_family_conclusion(variants, selected)
    assert conclusion == "FAIL"


def test_family_conclusion_whole_link_fallback_passes_independently_of_per_element_override():
    # A job that only requests the forced whole-link-suppression fallback
    # (not the per-element override variant) must be able to PASS on its own.
    selected = ["forced_linked_override_failure_hide_instance_fallback"]
    variants = [_variant("forced_linked_override_failure_hide_instance_fallback", "PASS")]
    family_status, conclusion = probe._source_family_conclusion(variants, selected)
    assert family_status["LINK"]["passed"] is True
    assert conclusion == "PASS"


def test_family_conclusion_host_only_selection_unaffected_by_redesign():
    selected = ["host_reference_coloring"]
    variants = [_variant("host_reference_coloring", "PASS")]
    family_status, conclusion = probe._source_family_conclusion(variants, selected)
    assert family_status["HOST"]["passed"] is True
    assert family_status["LINK"]["requested"] is False
    assert family_status["DWG"]["requested"] is False
    assert conclusion == "PASS"


def test_family_conclusion_counts_distinct_variants_across_resolution_runs():
    # A job requesting more than one resolution case (resolution_policy="both",
    # or several target_dpi values) makes _run_native() append one report
    # entry per (resolution case x selected variant) - the same variant name
    # appears more than once. Coverage must key off distinct variant names,
    # not the raw entry count, while still requiring every emitted entry to
    # have passed.
    selected = ["dwg_importinstance_coloring"]
    variants = [
        _variant("dwg_importinstance_coloring", "PASS"),
        _variant("dwg_importinstance_coloring", "PASS"),
    ]
    family_status, conclusion = probe._source_family_conclusion(variants, selected)
    assert family_status["DWG"]["ran"] is True
    assert family_status["DWG"]["passed"] is True
    assert conclusion == "PASS"


def test_family_conclusion_one_bad_resolution_run_fails_the_family():
    selected = ["dwg_importinstance_coloring"]
    variants = [
        _variant("dwg_importinstance_coloring", "PASS"),
        _variant("dwg_importinstance_coloring", "INCONCLUSIVE"),
    ]
    family_status, conclusion = probe._source_family_conclusion(variants, selected)
    assert family_status["DWG"]["passed"] is False
    assert conclusion == "INCONCLUSIVE"


# --- _dwg_eligibility_diagnostics(): explicit ViewSpecific exclusion reason ---

class _FakeElementId(object):
    def __init__(self, value):
        self.IntegerValue = value


class _FakeImportInstance(object):
    def __init__(self, id_value, view_specific, category_name=None):
        self.Id = _FakeElementId(id_value)
        self.ViewSpecific = view_specific
        self.Category = types.SimpleNamespace(Name=category_name) if category_name else None


class _FakeElementsResult(object):
    def __init__(self, items):
        self._items = items

    def ToElements(self):
        return self._items


class _FakeFilteredElementCollector(object):
    def __init__(self, doc, view_id=None):
        self._doc = doc

    def OfClass(self, cls):
        return _FakeElementsResult(self._doc.import_instances)


@pytest.fixture
def fake_dwg_revit_db(monkeypatch):
    fake_db = types.ModuleType("Autodesk.Revit.DB")
    fake_db.FilteredElementCollector = _FakeFilteredElementCollector
    fake_db.ImportInstance = _FakeImportInstance
    monkeypatch.setitem(sys.modules, "Autodesk.Revit.DB", fake_db)


def test_dwg_eligibility_reports_explicit_view_specific_exclusion(fake_dwg_revit_db):
    view_specific = _FakeImportInstance(501, True, "Imports in Families")
    doc = types.SimpleNamespace(import_instances=[view_specific])
    view = types.SimpleNamespace(Id="view-1")
    diagnostics = probe._dwg_eligibility_diagnostics(doc, view, [view_specific], discovered_dwg_ids=set())
    assert len(diagnostics) == 1
    entry = diagnostics[0]
    assert entry["import_instance_id"] == 501
    assert entry["view_specific"] is True
    assert entry["eligible_under_current_discovery_policy"] is False
    assert entry["exclusion_reason"] == "EXCLUDED_VIEW_SPECIFIC_IMPORT"
    assert entry["supplied"] is True


def test_dwg_eligibility_reports_eligible_source_with_no_exclusion_reason(fake_dwg_revit_db):
    inst = _FakeImportInstance(77, False, "Imports in Families")
    doc = types.SimpleNamespace(import_instances=[inst])
    view = types.SimpleNamespace(Id="view-1")
    diagnostics = probe._dwg_eligibility_diagnostics(doc, view, [], discovered_dwg_ids={77})
    assert diagnostics[0]["eligible_under_current_discovery_policy"] is True
    assert diagnostics[0]["exclusion_reason"] is None


def test_dwg_eligibility_reports_supplied_element_missing_from_view_collector(fake_dwg_revit_db):
    missing = _FakeImportInstance(9, False)
    doc = types.SimpleNamespace(import_instances=[])
    view = types.SimpleNamespace(Id="view-1")
    diagnostics = probe._dwg_eligibility_diagnostics(doc, view, [missing], discovered_dwg_ids=set())
    assert diagnostics[0]["in_view_import_instance_collector"] is False
    assert diagnostics[0]["exclusion_reason"] == "NOT_FOUND_IN_VIEW_IMPORT_INSTANCE_COLLECTOR"


# --- _skip_reason(): a supplied-but-excluded DWG never reads as a bare "DWG = 0" ---

def test_skip_reason_names_the_dwg_exclusion_explicitly():
    discovery = {"dwg_eligibility_diagnostics": [
        {"import_instance_id": 501, "exclusion_reason": "EXCLUDED_VIEW_SPECIFIC_IMPORT"}]}
    reason = probe._skip_reason("dwg_importinstance_coloring", [object()], discovery)
    assert "EXCLUDED_VIEW_SPECIFIC_IMPORT" in reason


def test_skip_reason_generic_when_nothing_supplied():
    reason = probe._skip_reason("host_reference_coloring", [], {})
    assert reason == "No discovered candidates for required source type(s)"
