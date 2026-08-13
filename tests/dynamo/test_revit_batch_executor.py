import json
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
