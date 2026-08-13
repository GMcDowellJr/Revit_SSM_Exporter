"""Fixed probe registry. Configuration can never select an import path."""
from __future__ import absolute_import


PROBE_MODULES = {
    "stage_a_external_sources": "tests.dynamo.probe_stage_a_external_sources",
    "stage_a_graphics_semantics": "tests.dynamo.probe_stage_a_graphics_semantics",
    "stage_a_image_alignment": "tests.dynamo.probe_stage_a_image_alignment",
    "stage_a_minimum_id_mutations": "tests.dynamo.probe_stage_a_minimum_id_mutations",
    "stage_a_model_linework": "tests.dynamo.probe_stage_a_model_linework",
    "stage_a_transaction_group_export": "tests.dynamo.probe_stage_a_transaction_group_export",
}


def _adapter(module_name):
    def invoke(view, settings, output_directory):
        # Import names are fixed above; no configured path reaches importlib.
        module = __import__(module_name, fromlist=["run_probe"])
        arguments = dict(settings)
        arguments["raw_view"] = view
        arguments["output_dir"] = output_directory
        return module.run_probe(**arguments)
    return invoke


def build_registry():
    return {probe_id: _adapter(module) for probe_id, module in PROBE_MODULES.items()}
