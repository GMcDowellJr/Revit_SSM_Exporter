"""Adapter boundary and validation-only coverage for the probe registry.

Focused on stage_a_minimum_id_mutations, whose adapter must:
  * map campaign `dpi` -> run_probe(target_dpi=...)
  * map the campaign job's `variant` -> run_probe(selection=...)
  * strip `comparison_reference` (analysis-only) before it ever reaches run_probe()
  * reject unknown settings, unsupported aliases, conflicting dpi/target_dpi,
    and unknown variants/selections - all without starting a transaction or
    exporting a TIFF (i.e. usable as validate_settings in validation-only mode).
"""
import pytest

from tests.dynamo.revit_probe_registry import build_registry


@pytest.fixture
def adapter():
    return build_registry()["stage_a_minimum_id_mutations"]


def test_dpi_maps_to_target_dpi_and_variant_maps_to_selection(monkeypatch, adapter):
    import tests.dynamo.probe_stage_a_minimum_id_mutations as probe

    captured = {}

    def fake_run_probe(**kwargs):
        captured.update(kwargs)
        return {"execution_status": "completed"}

    monkeypatch.setattr(probe, "run_probe", fake_run_probe)
    adapter("view", {"dpi": 150, "comparison_reference": "detached_AS"}, "/out", variant="attached_AS")
    assert captured["target_dpi"] == 150
    assert captured["selection"] == "attached_AS"
    assert "dpi" not in captured
    assert "comparison_reference" not in captured
    assert captured["raw_view"] == "view"
    assert captured["output_dir"] == "/out"


def test_settings_selection_wins_over_job_variant_when_both_present(monkeypatch, adapter):
    import tests.dynamo.probe_stage_a_minimum_id_mutations as probe
    captured = {}
    monkeypatch.setattr(probe, "run_probe", lambda **kw: captured.update(kw) or {"execution_status": "completed"})
    adapter("view", {"selection": "detached_AS"}, "/out", variant="attached_AS")
    assert captured["selection"] == "detached_AS"


def test_unknown_settings_are_rejected_without_dispatch(monkeypatch, adapter):
    import tests.dynamo.probe_stage_a_minimum_id_mutations as probe
    monkeypatch.setattr(probe, "run_probe", lambda **kw: pytest.fail("run_probe must not be called"))
    with pytest.raises(ValueError, match="Unknown settings"):
        adapter.validate_settings({"dpi": 150, "not_a_real_setting": 1}, "/out")


def test_conflicting_dpi_and_target_dpi_are_rejected(adapter):
    with pytest.raises(ValueError, match="conflicting"):
        adapter.validate_settings({"dpi": 150, "target_dpi": 300}, "/out")


def test_matching_dpi_and_target_dpi_are_accepted(adapter):
    adapter.validate_settings({"dpi": 150, "target_dpi": 150}, "/out")


def test_unknown_variant_is_rejected(adapter):
    with pytest.raises(ValueError, match="Unknown minimum-ID variant"):
        adapter.validate_settings({"selection": "attached_element_overrides"}, "/out")


def test_correct_variant_name_is_accepted(adapter):
    adapter.validate_settings({}, "/out", variant="attached_element_overrides_only")


def test_missing_output_directory_is_rejected(adapter):
    with pytest.raises(ValueError, match="output_directory"):
        adapter.validate_settings({"dpi": 150}, None)


def test_unknown_probe_id_is_absent_from_registry():
    registry = build_registry()
    assert "not_a_real_probe" not in registry


def test_validation_only_never_starts_a_transaction_or_exports(monkeypatch, adapter):
    import tests.dynamo.probe_stage_a_minimum_id_mutations as probe

    def fail_run_probe(**kwargs):
        raise AssertionError("run_probe (transactions/export) must not run during validation")

    monkeypatch.setattr(probe, "run_probe", fail_run_probe)
    # A validation-only call with a valid, well-formed request must return
    # cleanly without ever reaching run_probe().
    adapter.validate_settings({"dpi": 150, "comparison_reference": "detached_AS"}, "/out", variant="attached_AS")
