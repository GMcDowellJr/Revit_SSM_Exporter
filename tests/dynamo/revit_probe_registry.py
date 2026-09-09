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


# Runtime kwargs stage_a_minimum_id_mutations.run_probe() actually accepts
# (excluding raw_view/output_dir, which the adapter always injects itself).
_MINIMUM_ID_MUTATIONS_RUNTIME_SETTINGS = frozenset((
    "max_elements", "selection", "resolution_policy", "target_dpi",
    "fixed_pixel_width", "max_pixel_dimension", "repo_root",
))
# Campaign-facing setting name -> probe-facing kwarg name.
_MINIMUM_ID_MUTATIONS_ALIASES = {"dpi": "target_dpi", "pixel_size": "fixed_pixel_width"}
# Analysis/provenance-only metadata a campaign job may carry in `settings`;
# it is retained in the manifest's `requested_settings` for the analyzer, but
# must never reach run_probe().
_MINIMUM_ID_MUTATIONS_ANALYSIS_ONLY = frozenset(("comparison_reference",))


def _minimum_id_mutations_variant_names(module):
    """The probe's real, current supported variant/selection vocabulary,
    computed the same way _run_native() does, without touching Revit."""
    baseline_mutations = ("detach_template", "hide_annotation_categories", "smooth_edges_off", "display_style_flat_colors")
    variants = module.generate_stage1_variants(flat_colors_supported=True) + module.generate_stage2_variants(baseline_mutations)
    return [item["name"] for item in variants]


def _resolve_minimum_id_mutations_settings(module, settings, output_directory, variant=None):
    """Explicit adapter boundary for stage_a_minimum_id_mutations:

    * campaign ``dpi``          -> run_probe(target_dpi=...)
    * campaign job ``variant``  -> run_probe(selection=...)
    * ``comparison_reference``  -> analysis-only; stripped, never forwarded

    Rejects unknown settings, unsupported aliases, conflicting dpi/target_dpi,
    and unknown variants/selections - all without starting a transaction or
    exporting a TIFF, so this same function is reused as validate_settings for
    validation-only mode.
    """
    if not output_directory:
        raise ValueError("output_directory is required")
    settings = dict(settings)
    for alias, canonical in _MINIMUM_ID_MUTATIONS_ALIASES.items():
        if alias not in settings:
            continue
        aliased_value = settings.pop(alias)
        if canonical in settings and settings[canonical] != aliased_value:
            raise ValueError("conflicting {0} and {1} settings for stage_a_minimum_id_mutations".format(alias, canonical))
        settings.setdefault(canonical, aliased_value)
    for key in _MINIMUM_ID_MUTATIONS_ANALYSIS_ONLY:
        settings.pop(key, None)
    selection = settings.pop("selection", None)
    if selection is None:
        selection = variant
    if selection is not None:
        settings["selection"] = selection
    unknown = sorted(set(settings) - _MINIMUM_ID_MUTATIONS_RUNTIME_SETTINGS)
    if unknown:
        raise ValueError("Unknown settings for stage_a_minimum_id_mutations: {0}".format(unknown))
    if settings.get("selection") is not None:
        from tests.dynamo.stage_a_probe_contract import select_named
        select_named(settings["selection"], _minimum_id_mutations_variant_names(module), "minimum-ID variant(s)")
    return settings


def _minimum_id_mutations_adapter(module_name):
    def invoke(view, settings, output_directory, variant=None):
        module = __import__(module_name, fromlist=["run_probe"])
        arguments = _resolve_minimum_id_mutations_settings(module, settings, output_directory, variant)
        arguments["raw_view"] = view
        arguments["output_dir"] = output_directory
        return module.run_probe(**arguments)

    def validate_settings(settings, output_directory, variant=None):
        module = __import__(module_name, fromlist=["run_probe"])
        _resolve_minimum_id_mutations_settings(module, settings, output_directory, variant)

    invoke.validate_settings = validate_settings
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
            if any(isinstance(value, bool) or not isinstance(value, int) for value in integer_ids):
                raise ValueError("element_ids must contain integers")
            from Autodesk.Revit.DB import ElementId
            for integer_id in integer_ids:
                try:
                    element_id = ElementId(integer_id)
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


# Runtime kwargs stage_a_external_sources.run_probe() actually accepts
# (excluding raw_view/output_dir/raw_links/raw_dwgs, which the adapter always
# supplies itself - raw_links/raw_dwgs are resolved from campaign-facing
# settings below, never forwarded as JSON).
_EXTERNAL_SOURCES_RUNTIME_SETTINGS = frozenset((
    "selection", "resolution_policy", "target_dpi", "fixed_pixel_width", "max_pixel_dimension",
))
# Campaign-facing setting name -> probe-facing kwarg name, same alias
# stage_a_minimum_id_mutations already accepts for the same campaign-authored
# `dpi` convention used throughout examples/stage_a_campaign.json.
_EXTERNAL_SOURCES_ALIASES = {"dpi": "target_dpi"}


def _resolve_element_references(doc, unique_ids, integer_ids, label):
    """Resolve a campaign-facing (unique_id-or-integer) element reference list
    to real Revit elements. Shared shape with _transaction_adapter's
    resolve_elements: UniqueId is preferred, ElementId is a supported
    fallback, and everything is validated before any element is touched."""
    if not isinstance(unique_ids, list) or not isinstance(integer_ids, list):
        raise ValueError("{0}_unique_ids and {0}_ids must be arrays".format(label))
    elements = []
    for unique_id in unique_ids:
        element = doc.GetElement(str(unique_id))
        if element is None:
            raise ValueError("No element has UniqueId {0}".format(unique_id))
        elements.append(element)
    if integer_ids:
        if any(isinstance(value, bool) or not isinstance(value, int) for value in integer_ids):
            raise ValueError("{0}_ids must contain integers".format(label))
        from Autodesk.Revit.DB import ElementId
        for integer_id in integer_ids:
            try:
                element_id = ElementId(integer_id)
            except (TypeError, ValueError, OverflowError):
                raise ValueError("Invalid element id: {0!r}".format(integer_id))
            element = doc.GetElement(element_id)
            if element is None:
                raise ValueError("No element has ElementId {0}".format(integer_id))
            elements.append(element)
    return elements


def _external_sources_adapter(doc, module_name):
    """Explicit adapter boundary for stage_a_external_sources.

    run_probe()'s raw_links/raw_dwgs parameters require real Revit elements
    (RevitLinkInstance / ImportInstance); the probe never discovers them on
    its own - a job with neither supplied reports "No discovered candidates
    for required source type(s)" on every variant rather than failing loudly.
    Campaign JSON can only carry element identity, never a live element, so
    this adapter resolves campaign-facing ``link_instance_unique_ids`` /
    ``link_instance_ids`` and ``dwg_import_unique_ids`` / ``dwg_import_ids``
    into ``raw_links`` / ``raw_dwgs`` before dispatch - mirroring
    _transaction_adapter's element resolution for stage_a_transaction_group_export.
    Campaign ``dpi`` maps to ``run_probe(target_dpi=...)``, the same alias
    stage_a_minimum_id_mutations already accepts. Rejects unknown settings
    and a conflicting dpi/target_dpi pair without starting a transaction or
    exporting a TIFF, so this same function is reused as validate_settings.
    """
    def resolve(settings):
        arguments = dict(settings)
        raw_links = _resolve_element_references(
            doc, arguments.pop("link_instance_unique_ids", []) or [],
            arguments.pop("link_instance_ids", []) or [], "link_instance")
        raw_dwgs = _resolve_element_references(
            doc, arguments.pop("dwg_import_unique_ids", []) or [],
            arguments.pop("dwg_import_ids", []) or [], "dwg_import")
        for alias, canonical in _EXTERNAL_SOURCES_ALIASES.items():
            if alias not in arguments:
                continue
            aliased_value = arguments.pop(alias)
            if canonical in arguments and arguments[canonical] != aliased_value:
                raise ValueError("conflicting {0} and {1} settings for stage_a_external_sources".format(alias, canonical))
            arguments.setdefault(canonical, aliased_value)
        unknown = sorted(set(arguments) - _EXTERNAL_SOURCES_RUNTIME_SETTINGS)
        if unknown:
            raise ValueError("Unknown settings for stage_a_external_sources: {0}".format(unknown))
        return raw_links, raw_dwgs, arguments

    def invoke(view, settings, output_directory):
        module = __import__(module_name, fromlist=["run_probe"])
        raw_links, raw_dwgs, arguments = resolve(settings)
        return module.run_probe(raw_view=view, output_dir=output_directory,
                                raw_links=raw_links, raw_dwgs=raw_dwgs, **arguments)

    def validate_settings(settings, output_directory):
        resolve(settings)

    invoke.validate_settings = validate_settings
    return invoke


def build_registry(doc=None):
    specialized = {"stage_a_transaction_group_export", "stage_a_minimum_id_mutations", "stage_a_external_sources"}
    registry = {probe_id: _adapter(module) for probe_id, module in PROBE_MODULES.items()
                if probe_id not in specialized}
    registry["stage_a_minimum_id_mutations"] = _minimum_id_mutations_adapter(PROBE_MODULES["stage_a_minimum_id_mutations"])
    transaction_module = PROBE_MODULES["stage_a_transaction_group_export"]
    registry["stage_a_transaction_group_export"] = (_transaction_adapter(doc, transaction_module)
                                                       if doc is not None else _adapter(transaction_module))
    external_sources_module = PROBE_MODULES["stage_a_external_sources"]
    registry["stage_a_external_sources"] = (_external_sources_adapter(doc, external_sources_module)
                                              if doc is not None else _adapter(external_sources_module))
    return registry
