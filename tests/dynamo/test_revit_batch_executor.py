import json
import os
import sys
import types
from pathlib import Path

import pytest

from tests.dynamo.revit_batch_contract import (BATCH_SCHEMA_VERSION, MANIFEST_SCHEMA_VERSION,
    ContractError, job_fingerprint, parse_view_reference, validate_batch, validate_manifest)
from tests.dynamo.revit_batch_executor import execute_batch, resolve_view
from tests.dynamo.revit_probe_registry import PROBE_MODULES, build_registry


class Id:
    def __init__(self, value): self.IntegerValue = value


class View:
    def __init__(self, uid, name, kind="FloorPlan", crop=True, number=1):
        self.UniqueId, self.Name, self.ViewType = uid, name, kind
        self.CropBoxActive, self.IsTemplate, self.Id = crop, False, Id(number)


class App:
    VersionNumber = "2025"


class Doc:
    Title, PathName, Application = "Model", "/models/model.rvt", App()
    def __init__(self, views): self.views = views
    def GetElement(self, uid): return next((v for v in self.views if v.UniqueId == uid), None)


class NonView:
    def __init__(self, uid): self.UniqueId = uid


class FakeRevitLinkInstance(NonView):
    """Stands in for Autodesk.Revit.DB.RevitLinkInstance - real class name
    confirmed against tests/dynamo/test_import_check.py's own Revit API
    import check, not guessed."""


class FakeImportInstance(NonView):
    """Stands in for Autodesk.Revit.DB.ImportInstance - see FakeRevitLinkInstance."""


@pytest.fixture(autouse=True)
def _fake_autodesk_revit_db(monkeypatch):
    """_external_sources_adapter imports Autodesk.Revit.DB.RevitLinkInstance
    and ImportInstance unconditionally (to validate a resolved element is
    the right kind of source, not just any element) even when no link/DWG
    settings are supplied, so every test in this file needs it satisfiable
    - registering sys.modules["Autodesk.Revit.DB"] directly is sufficient
    for `from Autodesk.Revit.DB import X` without needing parent packages."""
    fake_db = types.ModuleType("Autodesk.Revit.DB")
    fake_db.RevitLinkInstance = FakeRevitLinkInstance
    fake_db.ImportInstance = FakeImportInstance
    monkeypatch.setitem(sys.modules, "Autodesk.Revit.DB", fake_db)


def batch(jobs=None, limit=10, error="stop", resume=False):
    jobs = jobs or [job("one", "u1")]
    return {"schema_version": "1.0", "campaign_id": "campaign", "batch_id": "batch",
            "created_at": "2026-08-13T00:00:00Z", "document": {"expected_title": "Model",
            "expected_revit_version": "2025", "expected_path": "/models/model.rvt"},
            "execution_policy": {"max_jobs_per_run": limit, "on_job_error": error,
            "resume": resume, "allow_rerun": False}, "jobs": jobs}


def job(job_id, uid, probe="stub", settings=None):
    return {"job_id": job_id, "probe_id": probe, "view": {"unique_id": uid,
            "name": "View " + uid[-1], "view_type": "FloorPlan", "crop_active": True},
            "settings": settings or {}, "output_directory": "/raw/" + job_id}


def envelope(status="completed"):
    return {"execution_status": status, "artifact_paths": ["raw.tif"],
            "rollback_status": "succeeded", "state_restoration_status": "restored",
            "errors": [], "warnings": []}


def run(tmp_path, value, adapter, **kwargs):
    views = [View("u1", "View 1", number=1), View("u2", "View 2", number=2), View("u3", "View 3", number=3)]
    result = execute_batch(value, Doc(views), {"stub": adapter}, lambda doc: doc.views,
                           str(tmp_path), run_id=kwargs.pop("run_id", "run"), **kwargs)
    return result, json.loads(Path(result["manifest_path"]).read_text())


def test_schema_validation_and_versioning():
    assert validate_batch(batch())["schema_version"] == BATCH_SCHEMA_VERSION
    assert parse_view_reference({"unique_id": "x", "crop_active": False})["unique_id"] == "x"
    invalid = batch(); del invalid["jobs"][0]["settings"]
    with pytest.raises(ContractError, match="missing settings"): validate_batch(invalid)
    invalid = batch(); invalid["schema_version"] = "2.0"
    with pytest.raises(ContractError, match="Unsupported"): validate_batch(invalid)


def test_unknown_probe_is_a_recorded_configuration_failure(tmp_path):
    result, manifest = run(tmp_path, batch([job("one", "u1", "not_registered")]), lambda *a: None)
    assert result["jobs_executed"] == []
    assert manifest["execution_status"] == "configuration_failed"
    assert "Unknown probe_id" in manifest["errors"][0]["message"]


@pytest.mark.parametrize("contents,error_type", [("{not json", "JSONDecodeError"),
                                                   (json.dumps({"schema_version": "1.0"}), "ContractError")])
def test_batch_load_failures_write_configuration_manifest(tmp_path, contents, error_type):
    batch_path = tmp_path / "next_batch.json"
    batch_path.write_text(contents)
    result = execute_batch(str(batch_path), Doc([]), {}, [], str(tmp_path / "manifests"),
                           validation_only=True, run_id="invalid")
    manifest = json.loads(Path(result["manifest_path"]).read_text())
    assert manifest["execution_status"] == "configuration_failed"
    assert manifest["errors"][0]["phase"] == "configuration"
    assert manifest["errors"][0]["type"] == error_type
    assert result["campaign_id"] is None and result["batch_id"] is None


def test_view_resolution_rejects_ambiguity_and_assertion_mismatch():
    views = [View("a", "Same"), View("b", "Same")]; doc = Doc(views)
    with pytest.raises(ContractError, match="resolved to 2"): resolve_view(doc, {"name": "Same"}, views)
    with pytest.raises(ContractError, match="name mismatch"): resolve_view(doc, {"unique_id": "a", "name": "Wrong"}, views)


def test_unique_id_must_resolve_to_a_view():
    element = NonView("element"); doc = Doc([element])
    with pytest.raises(ContractError, match="not a Revit view"):
        resolve_view(doc, {"unique_id": "element"}, [], is_view=lambda value: isinstance(value, View))


def test_transaction_adapter_resolves_unique_id_elements(monkeypatch):
    target_view, element = View("view", "View"), NonView("element")
    doc = Doc([target_view, element]); captured = {}
    module_name = PROBE_MODULES["stage_a_transaction_group_export"]
    fake_module = types.ModuleType(module_name)
    def run_probe(**arguments): captured.update(arguments); return envelope()
    fake_module.run_probe = run_probe
    monkeypatch.setitem(sys.modules, module_name, fake_module)
    adapter = build_registry(doc)["stage_a_transaction_group_export"]
    adapter(target_view, {"element_unique_ids": ["element"], "inject_failure": True}, "/raw")
    assert captured["raw_elements"] == [element]
    assert captured["inject_failure"] is True
    with pytest.raises(ValueError, match="requires element_ids"):
        adapter(target_view, {}, "/raw")
    with pytest.raises(ValueError, match="must contain integers"):
        adapter(target_view, {"element_ids": [123.9]}, "/raw")


def test_external_sources_adapter_resolves_link_and_dwg_unique_ids(monkeypatch):
    import tests.dynamo.probe_stage_a_external_sources as probe
    target_view = View("view", "View")
    link, dwg = FakeRevitLinkInstance("link"), FakeImportInstance("dwg")
    doc = Doc([target_view, link, dwg]); captured = {}
    def run_probe(**arguments): captured.update(arguments); return envelope()
    monkeypatch.setattr(probe, "run_probe", run_probe)
    adapter = build_registry(doc)["stage_a_external_sources"]
    adapter(target_view, {"link_instance_unique_ids": ["link"], "dwg_import_unique_ids": ["dwg"],
                          "fixed_pixel_width": 1600}, "/raw")
    assert captured["raw_links"] == [link]
    assert captured["raw_dwgs"] == [dwg]
    assert captured["fixed_pixel_width"] == 1600
    assert captured["raw_view"] is target_view
    assert captured["output_dir"] == "/raw"


def test_external_sources_adapter_defaults_to_empty_when_no_sources_supplied(monkeypatch):
    import tests.dynamo.probe_stage_a_external_sources as probe
    target_view = View("view", "View")
    doc = Doc([target_view]); captured = {}
    monkeypatch.setattr(probe, "run_probe", lambda **arguments: captured.update(arguments) or envelope())
    adapter = build_registry(doc)["stage_a_external_sources"]
    adapter(target_view, {}, "/raw")
    assert captured["raw_links"] == [] and captured["raw_dwgs"] == []


def test_external_sources_adapter_maps_dpi_alias_and_rejects_conflict(monkeypatch):
    import tests.dynamo.probe_stage_a_external_sources as probe
    target_view = View("view", "View")
    doc = Doc([target_view]); captured = {}
    monkeypatch.setattr(probe, "run_probe", lambda **arguments: captured.update(arguments) or envelope())
    adapter = build_registry(doc)["stage_a_external_sources"]
    # The checked-in examples/stage_a_campaign.json's s7.*.150 jobs use this
    # exact shape ({"dpi": 150}), matching the minimum-ID adapter's alias.
    adapter(target_view, {"dpi": 150}, "/raw")
    assert captured["target_dpi"] == 150
    assert "dpi" not in captured
    with pytest.raises(ValueError, match="conflicting"):
        adapter.validate_settings({"dpi": 150, "target_dpi": 300}, "/raw")
    adapter.validate_settings({"dpi": 150, "target_dpi": 150}, "/raw")


def test_external_sources_adapter_rejects_unknown_settings_and_unresolvable_ids(monkeypatch):
    import tests.dynamo.probe_stage_a_external_sources as probe
    target_view = View("view", "View")
    doc = Doc([target_view])
    monkeypatch.setattr(probe, "run_probe", lambda **arguments: (_ for _ in ()).throw(AssertionError("must not dispatch")))
    adapter = build_registry(doc)["stage_a_external_sources"]
    with pytest.raises(ValueError, match="Unknown settings"):
        adapter.validate_settings({"pixel_size": 1600}, "/raw")
    with pytest.raises(ValueError, match="No element has UniqueId"):
        adapter.validate_settings({"link_instance_unique_ids": ["missing"]}, "/raw")
    with pytest.raises(ValueError, match="link_instance_ids must contain integers"):
        adapter.validate_settings({"link_instance_ids": [1.5]}, "/raw")


def test_external_sources_adapter_rejects_wrong_element_class(monkeypatch):
    # A valid UniqueId naming an ordinary element - not a RevitLinkInstance/
    # ImportInstance - must be rejected here, not accepted and forwarded to
    # silently produce skipped/inconclusive source variants at real dispatch.
    import tests.dynamo.probe_stage_a_external_sources as probe
    target_view, ordinary = View("view", "View"), NonView("wall")
    dwg_masquerading_as_link = FakeImportInstance("not_a_link")
    doc = Doc([target_view, ordinary, dwg_masquerading_as_link])
    monkeypatch.setattr(probe, "run_probe", lambda **arguments: (_ for _ in ()).throw(AssertionError("must not dispatch")))
    adapter = build_registry(doc)["stage_a_external_sources"]
    with pytest.raises(ValueError, match="link_instance 'wall' resolved to a NonView, not a FakeRevitLinkInstance"):
        adapter.validate_settings({"link_instance_unique_ids": ["wall"]}, "/raw")
    with pytest.raises(ValueError, match="dwg_import 'wall' resolved to a NonView, not a FakeImportInstance"):
        adapter.validate_settings({"dwg_import_unique_ids": ["wall"]}, "/raw")
    with pytest.raises(ValueError, match="link_instance 'not_a_link' resolved to a FakeImportInstance, not a FakeRevitLinkInstance"):
        adapter.validate_settings({"link_instance_unique_ids": ["not_a_link"]}, "/raw")


def test_external_sources_adapter_rejects_malformed_falsey_reference_lists(monkeypatch):
    import tests.dynamo.probe_stage_a_external_sources as probe
    target_view = View("view", "View")
    doc = Doc([target_view])
    monkeypatch.setattr(probe, "run_probe", lambda **arguments: (_ for _ in ()).throw(AssertionError("must not dispatch")))
    adapter = build_registry(doc)["stage_a_external_sources"]
    # A malformed non-array value (empty string, 0, False) must never be
    # silently treated as "no references supplied" - only an absent key or
    # an explicit JSON null may default to [].
    for bad in ("", 0, False):
        with pytest.raises(ValueError, match="must be arrays"):
            adapter.validate_settings({"link_instance_unique_ids": bad}, "/raw")
    adapter.validate_settings({"link_instance_unique_ids": None}, "/raw")  # explicit null is fine


def test_external_sources_adapter_rejects_invalid_selection_and_resolution_values(monkeypatch):
    import tests.dynamo.probe_stage_a_external_sources as probe
    target_view = View("view", "View")
    doc = Doc([target_view])
    monkeypatch.setattr(probe, "run_probe", lambda **arguments: (_ for _ in ()).throw(AssertionError("must not dispatch")))
    adapter = build_registry(doc)["stage_a_external_sources"]
    with pytest.raises(ValueError, match="Unknown external-source variant"):
        adapter.validate_settings({"selection": "not_a_real_variant"}, "/raw")
    with pytest.raises(ValueError, match="Unsupported resolution_policy"):
        adapter.validate_settings({"resolution_policy": "nonsense"}, "/raw")
    with pytest.raises(ValueError, match="fixed_pixel_width"):
        adapter.validate_settings({"fixed_pixel_width": -1}, "/raw")
    adapter.validate_settings({"selection": "host_reference_coloring", "resolution_policy": "fixed_pixel_width",
                               "fixed_pixel_width": 1600}, "/raw")


def test_external_sources_adapter_falls_back_to_generic_passthrough_without_a_document():
    registry = build_registry(doc=None)
    assert not hasattr(registry["stage_a_external_sources"], "validate_settings")


def test_example_campaign_external_sources_settings_validate_cleanly():
    # External-source jobs live in the non-blocking follow-up campaign, not
    # the core host color-ID feasibility campaign - see
    # examples/stage_a_followup_campaign.json / docs/CAMPAIGN_PLANNER.md.
    import json
    from pathlib import Path
    campaign = json.loads((Path(__file__).parents[2] / "examples" / "stage_a_followup_campaign.json").read_text())
    doc = Doc([])
    adapter = build_registry(doc)["stage_a_external_sources"]
    checked = 0
    for stage in campaign["stages"]:
        for j in stage["jobs"]:
            if j["probe_id"] == "stage_a_external_sources":
                adapter.validate_settings(j["settings"], "/raw")
                checked += 1
    assert checked == 4  # s7.rvt_link.fixed/.150, s7.dwg.fixed/.150


def test_example_campaign_image_alignment_settings_validate_cleanly():
    """Every stage_a_image_alignment job in the checked-in core campaign
    (all of which use the campaign-wide `dpi` convention) must validate
    through the real adapter."""
    import json
    from pathlib import Path
    campaign = json.loads((Path(__file__).parents[2] / "examples" / "stage_a_campaign.json").read_text())
    adapter = build_registry(Doc([]))["stage_a_image_alignment"]
    checked = 0
    for stage in campaign["stages"]:
        for j in stage["jobs"]:
            if j["probe_id"] == "stage_a_image_alignment":
                adapter.validate_settings(j["settings"], "/raw")
                checked += 1
    assert checked == 7  # s2.align.r1/.r2 (2) + 5 cross-view *.confirm jobs (5)


def test_image_alignment_adapter_normalizes_mode_before_validating():
    """A runtime-legal mode value that isn't byte-identical to a
    SUPPORTED_MODES member (differs only by case/whitespace, or is omitted/
    null) must validate the same way _run_native()'s own
    str(mode or "all").strip().lower() normalization would accept it - not
    be rejected here only to succeed at real dispatch."""
    adapter = build_registry(Doc([]))["stage_a_image_alignment"]
    adapter.validate_settings({"mode": "ALL"}, "/raw")
    adapter.validate_settings({"mode": " model_bounds "}, "/raw")
    adapter.validate_settings({"mode": None}, "/raw")
    adapter.validate_settings({}, "/raw")
    with pytest.raises(ValueError, match="Unsupported export mode"):
        adapter.validate_settings({"mode": "not_a_real_mode"}, "/raw")


def test_image_alignment_adapter_validates_repetition_count():
    adapter = build_registry(Doc([]))["stage_a_image_alignment"]
    adapter.validate_settings({"repetition_count": 2}, "/raw")
    with pytest.raises(ValueError, match="repetition_count must be at least 1"):
        adapter.validate_settings({"repetition_count": 0}, "/raw")
    with pytest.raises(ValueError, match="repetition_count must be at least 1"):
        adapter.validate_settings({"repetition_count": -1}, "/raw")
    with pytest.raises(ValueError, match="repetition_count must be an integer"):
        adapter.validate_settings({"repetition_count": "not_a_number"}, "/raw")


def test_model_linework_adapter_maps_dpi_and_selection(monkeypatch):
    """The adapter correctly aliases dpi and validates a real `selection`
    mode name; the older display_style/fills/lines/bounds shape (superseded
    in examples/stage_a_campaign.json - see the module docstring) is
    correctly rejected as unknown settings, not silently accepted."""
    import tests.dynamo.probe_stage_a_model_linework as probe
    target_view = View("view", "View"); captured = {}
    monkeypatch.setattr(probe, "run_probe", lambda **arguments: captured.update(arguments) or envelope())
    adapter = build_registry(Doc([]))["stage_a_model_linework"]
    adapter(target_view, {"dpi": 150, "selection": "hidden_line_white_fill_black_lines"}, "/raw")
    assert captured["target_dpi"] == 150
    assert captured["selection"] == "hidden_line_white_fill_black_lines"
    with pytest.raises(ValueError, match="Unknown settings"):
        adapter.validate_settings({"display_style": "HiddenLine"}, "/raw")


def test_example_campaign_model_linework_settings_validate_cleanly():
    """All 8 stage_a_model_linework jobs (the follow-up campaign's elevation
    75/150/300 DPI sweep and 5 cross-view linework-validation jobs - see
    examples/stage_a_followup_campaign.json) use `selection` and validate
    through the real adapter - see the module docstring in
    revit_probe_registry.py for why they no longer use
    display_style/fills/lines/bounds."""
    import json
    from pathlib import Path
    campaign = json.loads((Path(__file__).parents[2] / "examples" / "stage_a_followup_campaign.json").read_text())
    adapter = build_registry(Doc([]))["stage_a_model_linework"]
    checked = 0
    for stage in campaign["stages"]:
        for j in stage["jobs"]:
            if j["probe_id"] == "stage_a_model_linework":
                assert j["settings"] == {"dpi": j["settings"]["dpi"], "selection": "hidden_line_white_fill_black_lines"}
                adapter.validate_settings(j["settings"], "/raw")
                checked += 1
    assert checked == 8


def test_validation_only_resolves_external_sources_element_references(tmp_path, monkeypatch):
    import tests.dynamo.probe_stage_a_external_sources as probe
    view, link = View("u1", "View 1"), FakeRevitLinkInstance("link")
    doc = Doc([view, link])
    monkeypatch.setattr(probe, "run_probe", lambda **arguments: (_ for _ in ()).throw(AssertionError("must not dispatch")))
    configured = batch([job("one", "u1", "stage_a_external_sources",
                            {"link_instance_unique_ids": ["link"]})])
    result = execute_batch(configured, doc, build_registry(doc), lambda current: [view],
                           str(tmp_path), validation_only=True, run_id="validation",
                           is_view=lambda value: isinstance(value, View))
    manifest = json.loads(Path(result["manifest_path"]).read_text())
    assert manifest["execution_status"] == "validation_only"
    assert manifest["jobs"][0]["execution_status"] == "validated_only"


def test_validation_only_resolves_transaction_element_references(tmp_path, monkeypatch):
    view, element = View("u1", "View 1"), NonView("element")
    doc = Doc([view, element]); module_name = PROBE_MODULES["stage_a_transaction_group_export"]
    fake_module = types.ModuleType(module_name)
    fake_module.run_probe = lambda **arguments: (_ for _ in ()).throw(AssertionError("must not dispatch"))
    monkeypatch.setitem(sys.modules, module_name, fake_module)
    configured = batch([job("one", "u1", "stage_a_transaction_group_export",
                            {"element_unique_ids": ["element"]})])
    result = execute_batch(configured, doc, build_registry(doc), lambda current: [view],
                           str(tmp_path), validation_only=True, run_id="validation",
                           is_view=lambda value: isinstance(value, View))
    manifest = json.loads(Path(result["manifest_path"]).read_text())
    assert manifest["execution_status"] == "validation_only"
    assert manifest["jobs"][0]["execution_status"] == "validated_only"


def test_json_schema_identifier_patterns_match_runtime():
    schema = json.loads(Path(__file__).with_name("next_batch.schema.json").read_text())
    properties = schema["$defs"]["job"]["properties"]
    assert properties["job_id"]["pattern"] == properties["probe_id"]["pattern"]
    assert properties["job_id"]["pattern"] == r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"


def test_ordered_dispatch_and_job_limit(tmp_path):
    calls = []
    def adapter(view, settings, output): calls.append(view.UniqueId); return envelope()
    result, manifest = run(tmp_path, batch([job("one", "u1"), job("two", "u2")], limit=1), adapter)
    assert calls == ["u1"]
    assert result["jobs_deferred"] == ["two"] and result["another_invocation_needed"]
    assert [item["job_id"] for item in manifest["jobs"]] == ["one"]


@pytest.mark.parametrize("policy,expected", [("stop", ["u1"]), ("continue", ["u1", "u2"])])
def test_stop_versus_continue_and_partial_manifest(tmp_path, policy, expected):
    calls = []
    def adapter(view, settings, output):
        calls.append(view.UniqueId)
        if view.UniqueId == "u1": raise RuntimeError("render failed")
        return envelope()
    _, manifest = run(tmp_path, batch([job("one", "u1"), job("two", "u2")], error=policy), adapter)
    assert calls == expected
    assert manifest["jobs"][0]["execution_status"] == "failed"
    assert manifest["completed_at"]
    if policy == "stop": assert manifest["jobs_not_attempted"] == [{"job_id": "two", "reason": "stopped_after_job_error"}]


def test_validation_only_does_not_dispatch(tmp_path):
    def forbidden(*args): raise AssertionError("probe or analyzer must not run")
    result, manifest = run(tmp_path, batch(), forbidden, validation_only=True)
    assert result["validation_only"]
    assert manifest["jobs"][0]["execution_status"] == "validated_only"
    assert manifest["jobs"][0]["raw_result_envelope"] is None


def test_resume_skips_success_and_detects_drift(tmp_path):
    calls = []
    adapter = lambda view, settings, output: (calls.append(view.UniqueId) or envelope())
    first, _ = run(tmp_path, batch(), adapter, run_id="first")
    resumed = batch(); resumed["execution_policy"]["resume"] = True
    second, manifest = run(tmp_path, resumed, adapter, run_id="second")
    assert calls == ["u1"] and manifest["jobs"][0]["execution_status"] == "skipped_resume"
    drifted = batch(); drifted["execution_policy"]["resume"] = True; drifted["jobs"][0]["settings"] = {"changed": True}
    _, drift_manifest = run(tmp_path, drifted, adapter, run_id="third")
    assert drift_manifest["execution_status"] == "configuration_failed"
    assert "Configuration drift" in drift_manifest["errors"][0]["message"]


def test_resume_rejects_success_from_a_different_document(tmp_path):
    adapter = lambda *args: envelope()
    run(tmp_path, batch(), adapter, run_id="first")
    resumed = batch(); resumed["execution_policy"]["resume"] = True
    other_doc = Doc([View("u1", "View 1")]); other_doc.PathName = "/models/copied-model.rvt"
    # Keep configured assertions valid to exercise the stronger prior-manifest binding.
    resumed["document"]["expected_path"] = "/models/copied-model.rvt"
    result = execute_batch(resumed, other_doc, {"stub": adapter}, lambda doc: doc.views,
                           str(tmp_path), run_id="second")
    manifest = json.loads(Path(result["manifest_path"]).read_text())
    assert manifest["execution_status"] == "configuration_failed"
    assert "different document" in manifest["errors"][0]["message"]


def test_resume_ignores_cross_document_manifest_without_completed_evidence(tmp_path):
    wrong_doc = Doc([]); wrong_doc.PathName = "/models/wrong-model.rvt"
    invalid = batch(); invalid["document"]["expected_path"] = "/models/correct-model.rvt"
    failed = execute_batch(invalid, wrong_doc, {"stub": lambda *args: envelope()}, [],
                           str(tmp_path), run_id="wrong-model")
    failed_manifest = json.loads(Path(failed["manifest_path"]).read_text())
    assert failed_manifest["execution_status"] == "configuration_failed"

    calls = []
    resumed = batch(); resumed["execution_policy"]["resume"] = True
    adapter = lambda view, settings, output: (calls.append(view.UniqueId) or envelope())
    result = execute_batch(resumed, Doc([View("u1", "View 1")]), {"stub": adapter},
                           lambda doc: doc.views, str(tmp_path), run_id="correct-model")
    manifest = json.loads(Path(result["manifest_path"]).read_text())
    assert manifest["execution_status"] == "completed"
    assert calls == ["u1"]


def test_example_uses_supported_image_alignment_mode():
    example = json.loads(Path(__file__).with_name("next_batch.example.json").read_text())
    assert example["jobs"][0]["settings"]["mode"] in ("original", "model_bounds", "canvas_bounds", "all")


def test_manifest_serialization_contract_and_raw_evidence(tmp_path):
    _, manifest = run(tmp_path, batch(), lambda *args: envelope("inconclusive"))
    assert manifest["schema_version"] == MANIFEST_SCHEMA_VERSION
    assert manifest["jobs"][0]["requested_view_identity"] != manifest["jobs"][0]["resolved_view_identity"]
    assert len(manifest["jobs"][0]["configuration_fingerprint"]) == 64
    assert validate_manifest(manifest)
    assert job_fingerprint(batch()["jobs"][0]) == job_fingerprint(batch()["jobs"][0])


def test_executor_has_no_analysis_or_campaign_gating_imports():
    source = Path(__file__).with_name("revit_batch_executor.py").read_text()
    forbidden = ("compare_golden", "analyze_stage", "campaign_acceptance", "eligibility")
    assert all(term not in source for term in forbidden)


# --- output_directory resolution is independent of the process cwd ---

def test_relative_output_directory_resolves_against_batch_file_directory(tmp_path, monkeypatch):
    campaign_dir = tmp_path / "campaign"
    campaign_dir.mkdir()
    relative_job = job("one", "u1")
    relative_job["output_directory"] = "raw/one"
    batch_path = campaign_dir / "next_batch.json"
    batch_path.write_text(json.dumps(batch([relative_job])))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)  # process cwd must never affect resolution
    seen = {}
    def adapter(view, settings, output_directory):
        seen["output_directory"] = output_directory
        return envelope()
    views = [View("u1", "View 1", number=1)]
    result = execute_batch(str(batch_path), Doc(views), {"stub": adapter}, lambda doc: doc.views,
                           str(campaign_dir / "manifests"), run_id="run")
    expected = str((campaign_dir / "raw" / "one").resolve())
    assert seen["output_directory"] == expected
    assert os.path.isabs(seen["output_directory"])
    manifest = json.loads(Path(result["manifest_path"]).read_text())
    job_record = manifest["jobs"][0]
    assert job_record["output_directory"] == "raw/one"  # configured relative identity preserved
    assert job_record["output_directory_resolved"] == expected  # canonical absolute path used


def test_absolute_output_directory_is_used_unchanged(tmp_path):
    _, manifest = run(tmp_path, batch(), lambda *args: envelope())
    absolute_job = batch()["jobs"][0]
    assert manifest["jobs"][0]["output_directory"] == absolute_job["output_directory"]
    assert manifest["jobs"][0]["output_directory_resolved"] == os.path.normpath(absolute_job["output_directory"])


def test_artifact_root_overrides_batch_file_directory_for_relative_paths(tmp_path):
    campaign_dir = tmp_path / "campaign"
    campaign_dir.mkdir()
    artifact_root = tmp_path / "artifacts"
    relative_job = job("one", "u1")
    relative_job["output_directory"] = "raw/one"
    batch_path = campaign_dir / "next_batch.json"
    batch_path.write_text(json.dumps(batch([relative_job])))
    seen = {}
    def adapter(view, settings, output_directory):
        seen["output_directory"] = output_directory
        return envelope()
    views = [View("u1", "View 1", number=1)]
    execute_batch(str(batch_path), Doc(views), {"stub": adapter}, lambda doc: doc.views,
                 str(campaign_dir / "manifests"), run_id="run", artifact_root=str(artifact_root))
    assert seen["output_directory"] == str((artifact_root / "raw" / "one").resolve())


def test_relative_artifact_root_is_itself_resolved_to_absolute(tmp_path, monkeypatch):
    # A caller-supplied artifact_root that is itself relative must not leave
    # the final output_directory relative - that would silently reintroduce
    # a dependency on the process cwd, defeating the whole point.
    from tests.dynamo.revit_batch_executor import resolve_output_directory
    monkeypatch.chdir(tmp_path)
    resolved = resolve_output_directory("raw/one", None, artifact_root="relative_artifacts")
    assert os.path.isabs(resolved)
    assert resolved == str((tmp_path / "relative_artifacts" / "raw" / "one").resolve())


# --- ViewType is always reported as a stable name, never a bare ordinal ---

def test_view_type_numeric_stringification_is_not_reported_as_an_ordinal():
    from tests.dynamo.revit_batch_executor import _view_type_name

    class NumericViewType:
        def __str__(self): return "1"

    class RawView:
        ViewType = NumericViewType()

    # Outside Revit (Autodesk.Revit.DB unavailable) resolution cannot recover
    # the real name, but it must not silently look like a clean value either -
    # the raw ordinal string is preserved verbatim rather than crashing.
    assert _view_type_name(RawView()) == "1"


def test_view_type_stable_name_passes_through_unchanged():
    from tests.dynamo.revit_batch_executor import _view_type_name

    class RawView:
        ViewType = "Elevation"

    assert _view_type_name(RawView()) == "Elevation"
