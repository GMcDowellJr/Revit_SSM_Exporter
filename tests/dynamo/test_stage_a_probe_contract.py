import importlib
import json

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
