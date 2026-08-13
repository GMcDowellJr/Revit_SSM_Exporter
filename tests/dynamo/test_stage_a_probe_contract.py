import importlib
import inspect
import json
import sys
import types

import pytest

from tests.dynamo.stage_a_probe_contract import (
    execution_envelope,
    select_named,
    validate_execution_envelope,
)


PROBE_MODULES = (
    "probe_stage_a_external_sources",
    "probe_stage_a_graphics_semantics",
    "probe_stage_a_image_alignment",
    "probe_stage_a_minimum_id_mutations",
    "probe_stage_a_model_linework",
    "probe_stage_a_transaction_group_export",
)


@pytest.mark.parametrize("module_name", PROBE_MODULES)
def test_probe_import_is_inert(module_name):
    module = importlib.import_module("tests.dynamo." + module_name)
    assert "OUT" not in vars(module)
    assert callable(getattr(module, "dynamo_main"))


@pytest.mark.parametrize("module_name", PROBE_MODULES)
def test_probe_has_no_eager_shared_contract_import(module_name):
    module = importlib.import_module("tests.dynamo." + module_name)
    source = inspect.getsource(module)
    prefix = source.split("def _probe_contract", 1)[0] if "def _probe_contract" in source else source
    assert "from tests.dynamo.stage_a_probe_contract import" not in prefix


def test_common_selection_defaults_and_preserves_requested_order():
    assert select_named(None, ("a", "b")) == ["a", "b"]
    assert select_named("all", ("a", "b")) == ["a", "b"]
    assert select_named(["b", "a", "b"], ("a", "b")) == ["b", "a"]


def test_unknown_selection_is_rejected():
    with pytest.raises(ValueError, match="Unknown probe cases"):
        select_named("missing", ("known",), "probe cases")


def test_probe_specific_selectors_reject_unknown_and_keep_defaults():
    external = importlib.import_module("tests.dynamo.probe_stage_a_external_sources")
    alignment = importlib.import_module("tests.dynamo.probe_stage_a_image_alignment")
    minimum = importlib.import_module("tests.dynamo.probe_stage_a_minimum_id_mutations")
    linework = importlib.import_module("tests.dynamo.probe_stage_a_model_linework")
    stage1 = minimum.generate_stage1_variants()
    stage2 = minimum.generate_stage2_variants(("detach_template",))
    assert len(minimum._select_variants("all", stage1, stage2)) == len(stage1 + stage2)
    assert linework._select_modes(None) == list(linework.MODES)
    assert external.select_variants(None) == list(external.VARIANTS)
    runs = alignment.select_resolution_runs("dpi_150", "paper_space_dpi", 150, 1600, None)
    assert [alignment._resolution_suffix(item) for item in runs] == ["dpi_150"]
    with pytest.raises(ValueError):
        minimum._select_variants("not_a_variant", stage1, stage2)
    with pytest.raises(ValueError):
        linework._select_modes("not_a_mode")
    with pytest.raises(ValueError):
        external.select_variants("not_a_variant")
    with pytest.raises(ValueError):
        alignment.select_resolution_runs("not_a_case", "paper_space_dpi", 150, 1600, None)


def test_attached_as_has_only_requested_attached_template_mutations():
    minimum = importlib.import_module("tests.dynamo.probe_stage_a_minimum_id_mutations")
    variants = {variant["name"]: variant for variant in minimum.generate_stage1_variants()}
    assert variants["attached_AS"]["mutations"] == (
        "hide_annotation_categories",
        "smooth_edges_off",
    )
    assert "detach_template" not in variants["attached_AS"]["mutations"]
    assert variants["detached_AS"]["mutations"] == (
        "detach_template",
        "hide_annotation_categories",
        "smooth_edges_off",
    )



def test_execution_envelope_is_json_compatible_and_analysis_neutral():
    value = execution_envelope(
        "probe", {"case": "a"}, {"id": 7}, {"raw": True}, ["raw.tiff"],
        "succeeded", "restored", "2026-01-01T00:00:00Z",
    )
    assert validate_execution_envelope(value)
    json.dumps(value)
    assert value["execution_status"] == "completed"
    for forbidden in (
        "image_fidelity_status",
        "alignment_status",
        "semantic_preservation_status",
        "campaign_acceptance",
    ):
        assert forbidden not in value


def test_execution_envelope_rejects_analysis_status():
    value = execution_envelope(
        "probe", {}, {}, {}, [], "unknown", "unknown", "start",
    )
    value["alignment_status"] = "pass"
    with pytest.raises(ValueError, match="Image-analysis fields"):
        validate_execution_envelope(value)


def test_external_sources_ignores_skipped_variants_for_cleanup(monkeypatch, tmp_path):
    module = importlib.import_module("tests.dynamo.probe_stage_a_external_sources")
    monkeypatch.setattr(module, "_ensure_repo_import_path", lambda _path: None)
    monkeypatch.setattr(module, "_run_native", lambda *args: {
        "conclusion": "INCONCLUSIVE",
        "variants": [
            {"variant": "host_reference_coloring", "transaction_group": {"rollback_succeeded": True},
             "state": {"differences_after_rollback": []}, "exceptions": []},
            {"variant": "dwg_importinstance_coloring", "skipped": True, "reason": "no DWG"},
        ],
        "paths": {},
    })
    result = module.run_probe(object(), str(tmp_path))
    assert result["execution_status"] == "completed"
    assert result["rollback_status"] == "succeeded"
    assert result["state_restoration_status"] == "restored"


def test_contract_bootstrap_does_not_depend_on_production_import(monkeypatch, tmp_path):
    module = importlib.import_module("tests.dynamo.probe_stage_a_external_sources")
    checkout = tmp_path / "checkout"
    contract_dir = checkout / "tests" / "dynamo"
    contract_dir.mkdir(parents=True)
    (contract_dir / "stage_a_probe_contract.py").write_text("# contract marker\n")
    monkeypatch.setitem(sys.modules, "vop_interwoven", types.ModuleType("vop_interwoven"))
    monkeypatch.setattr(module, "_candidate_repo_roots", lambda _output: [str(checkout)])
    monkeypatch.setattr(sys, "path", [path for path in sys.path if path not in (str(checkout), str(contract_dir))])

    resolved = module._ensure_contract_import_path(str(tmp_path / "output"))

    assert resolved == str(checkout)
    assert str(checkout) in sys.path
    assert str(contract_dir) in sys.path


def test_external_sources_all_skipped_is_inconclusive(monkeypatch, tmp_path):
    module = importlib.import_module("tests.dynamo.probe_stage_a_external_sources")
    monkeypatch.setattr(module, "_ensure_contract_import_path", lambda _path: None)
    monkeypatch.setattr(module, "_run_native", lambda *args: {
        "conclusion": "INCONCLUSIVE",
        "variants": [{"variant": "dwg_importinstance_coloring", "skipped": True, "reason": "no DWG"}],
        "paths": {},
    })
    result = module.run_probe(object(), str(tmp_path), selection="dwg_importinstance_coloring")
    assert result["execution_status"] == "inconclusive"
    assert result["rollback_status"] == "not_started"
    assert result["state_restoration_status"] == "not_checked"


def test_unwired_alignment_repetition_uses_default(monkeypatch):
    module = importlib.import_module("tests.dynamo.probe_stage_a_image_alignment")
    captured = {}

    def fake_run_probe(*args):
        captured["repetition_count"] = args[-1]
        return {}

    monkeypatch.setattr(module, "run_probe", fake_run_probe)
    module.dynamo_main([object(), "out", "all", True, "paper_space_dpi", 150, 1600, None, "all", None])
    assert captured["repetition_count"] == 2


def test_single_alignment_export_has_no_equality_claim():
    module = importlib.import_module("tests.dynamo.probe_stage_a_image_alignment")
    assert module._sequential_export_equality([{"sha256": "one", "actual_width": 1, "actual_height": 1}]) is None
    assert module._sequential_export_equality([]) is None
    assert module._sequential_export_equality([
        {"sha256": "same", "actual_width": 1, "actual_height": 2},
        {"sha256": "same", "actual_width": 1, "actual_height": 2},
    ]) is True


@pytest.mark.parametrize("module_name,native_result", (
    ("probe_stage_a_graphics_semantics", {"variants": [], "paths": {}, "conclusion": "INCONCLUSIVE", "resolution_diagnostics": [{"reason": "no bounds"}]}),
    ("probe_stage_a_model_linework", {"modes": [], "reference_runs": [], "paths": {}, "conclusion": "INCONCLUSIVE", "resolution_diagnostics": [{"reason": "no bounds"}]}),
))
def test_resolution_only_skip_is_inconclusive(monkeypatch, tmp_path, module_name, native_result):
    module = importlib.import_module("tests.dynamo." + module_name)
    monkeypatch.setattr(module, "_ensure_contract_import_path", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "_run_native", lambda *args: native_result)
    result = module.run_probe(object(), str(tmp_path))
    assert result["execution_status"] == "inconclusive"
    assert result["rollback_status"] == "not_started"
    assert result["state_restoration_status"] == "not_checked"


def test_graphics_and_transaction_adapters_return_envelopes(monkeypatch, tmp_path):
    graphics = importlib.import_module("tests.dynamo.probe_stage_a_graphics_semantics")
    monkeypatch.setattr(graphics, "_ensure_repo_import_path", lambda _path: None)
    monkeypatch.setattr(graphics, "_run_native", lambda *args: {
        "variants": [{"transaction_group": {"rollback_succeeded": True},
                      "state": {"differences_after_rollback": []}, "exceptions": []}],
        "paths": {}, "conclusion": "PASS_PARTIAL",
    })
    graphics_result = graphics.dynamo_main([object(), str(tmp_path)])
    assert validate_execution_envelope(graphics_result)

    transaction = importlib.import_module("tests.dynamo.probe_stage_a_transaction_group_export")
    monkeypatch.setattr(transaction, "_run_native", lambda *args: {
        "transaction_group": {"rollback_succeeded": True},
        "state": {"captured_state_equal_after_rollback": True},
        "export": {}, "exceptions": [], "result": {"conclusion": "PASS"},
    })
    transaction_result = transaction.dynamo_main([object(), [], str(tmp_path)])
    assert validate_execution_envelope(transaction_result)
