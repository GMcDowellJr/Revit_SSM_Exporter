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


def _transaction_adapter(doc, module_name):
    def resolve_elements(settings):
        arguments = dict(settings)
        integer_ids = arguments.pop("element_ids", [])
        unique_ids = arguments.pop("element_unique_ids", [])
        if "raw_elements" in arguments:
            raise ValueError("raw_elements cannot be supplied by JSON; use element_ids or element_unique_ids")
        if not isinstance(integer_ids, list) or not isinstance(unique_ids, list):
            raise ValueError("element_ids and element_unique_ids must be arrays")
        elements = []
        for unique_id in unique_ids:
            element = doc.GetElement(str(unique_id))
            if element is None:
                raise ValueError("No element has UniqueId {0}".format(unique_id))
            elements.append(element)
        if integer_ids:
            from Autodesk.Revit.DB import ElementId
            for integer_id in integer_ids:
                if isinstance(integer_id, bool):
                    raise ValueError("element_ids must contain integers")
                try:
                    element_id = ElementId(int(integer_id))
                except (TypeError, ValueError, OverflowError):
                    raise ValueError("Invalid element id: {0!r}".format(integer_id))
                element = doc.GetElement(element_id)
                if element is None:
                    raise ValueError("No element has ElementId {0}".format(integer_id))
                elements.append(element)
        if not elements:
            raise ValueError("stage_a_transaction_group_export requires element_ids or element_unique_ids")
        return elements, arguments

    def invoke(view, settings, output_directory):
        module = __import__(module_name, fromlist=["run_probe"])
        elements, arguments = resolve_elements(settings)
        return module.run_probe(raw_view=view, raw_elements=elements,
                                output_dir=output_directory, **arguments)
    invoke.validate_settings = lambda settings, output_directory: resolve_elements(settings)
    return invoke


def build_registry(doc=None):
    registry = {probe_id: _adapter(module) for probe_id, module in PROBE_MODULES.items()
                if probe_id != "stage_a_transaction_group_export"}
    transaction_module = PROBE_MODULES["stage_a_transaction_group_export"]
    registry["stage_a_transaction_group_export"] = (_transaction_adapter(doc, transaction_module)
                                                       if doc is not None else _adapter(transaction_module))
    return registry
