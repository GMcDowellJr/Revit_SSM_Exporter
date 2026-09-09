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
# Campaign-facing setting name -> probe-facing kwarg name, shared by every
# specialized adapter below: none of these probes accept `dpi` directly
# (only `target_dpi`), but `dpi` is the campaign-wide convention used
# throughout examples/stage_a_campaign.json, matching the alias
# stage_a_minimum_id_mutations already accepted before any of this file's
# other adapters existed.
_DPI_ALIAS = {"dpi": "target_dpi"}


def _apply_dpi_alias(arguments, probe_id):
    for alias, canonical in _DPI_ALIAS.items():
        if alias not in arguments:
            continue
        aliased_value = arguments.pop(alias)
        if canonical in arguments and arguments[canonical] != aliased_value:
            raise ValueError("conflicting {0} and {1} settings for {2}".format(alias, canonical, probe_id))
        arguments.setdefault(canonical, aliased_value)


def _default_if_absent_or_null(arguments, key, default):
    """Pop ``key``, substituting ``default`` only when it was absent or an
    explicit JSON ``null`` - never for another falsey value (``""``, ``0``,
    ``False``). A blanket ``value or default`` would silently turn a
    malformed non-array setting into a valid empty one, masking exactly the
    configuration error validation exists to catch."""
    value = arguments.pop(key, default)
    return default if value is None else value


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
    stage_a_minimum_id_mutations already accepts. Rejects unknown settings,
    a conflicting dpi/target_dpi pair, an unresolvable/malformed reference
    list, an unknown ``selection``, and an invalid resolution combination -
    all without starting a transaction or exporting a TIFF, so this same
    function is reused as validate_settings. Resolution/selection values are
    validated by calling the probe module's own parsers (rather than a
    second, driftable copy of its accepted values), matching exactly what a
    real invocation would accept or reject.
    """
    def resolve(settings):
        module = __import__(module_name, fromlist=["run_probe"])
        arguments = dict(settings)
        raw_links = _resolve_element_references(
            doc, _default_if_absent_or_null(arguments, "link_instance_unique_ids", []),
            _default_if_absent_or_null(arguments, "link_instance_ids", []), "link_instance")
        raw_dwgs = _resolve_element_references(
            doc, _default_if_absent_or_null(arguments, "dwg_import_unique_ids", []),
            _default_if_absent_or_null(arguments, "dwg_import_ids", []), "dwg_import")
        _apply_dpi_alias(arguments, "stage_a_external_sources")
        unknown = sorted(set(arguments) - _EXTERNAL_SOURCES_RUNTIME_SETTINGS)
        if unknown:
            raise ValueError("Unknown settings for stage_a_external_sources: {0}".format(unknown))
        if "selection" in arguments:
            module.select_variants(arguments["selection"])
        module._resolution_runs(  # noqa: SLF001 - reuse the probe's own parser, not a driftable copy
            arguments.get("resolution_policy", module.DEFAULT_RESOLUTION_POLICY),
            arguments.get("target_dpi", module.DEFAULT_TARGET_DPI),
            arguments.get("fixed_pixel_width", module.DEFAULT_FIXED_PIXEL_WIDTH),
            arguments.get("max_pixel_dimension", module.DEFAULT_MAX_PIXEL_DIMENSION))
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


# stage_a_image_alignment and stage_a_model_linework, like
# stage_a_external_sources above, only accept target_dpi - not dpi - and
# previously went through the generic passthrough adapter with no aliasing
# at all, so every campaign job using the campaign-wide `dpi` convention
# (examples/stage_a_campaign.json uses it throughout Stages 2-4 and 6-7)
# would fail at execution time with "run_probe() got an unexpected keyword
# argument 'dpi'" the same way the stage_a_external_sources jobs did before
# this file's shared _apply_dpi_alias() was added.
_IMAGE_ALIGNMENT_RUNTIME_SETTINGS = frozenset((
    "mode", "create_markers", "resolution_policy", "target_dpi", "fixed_pixel_width",
    "max_pixel_dimension", "resolution_cases", "repetition_count",
))


def _image_alignment_adapter(module_name):
    """Explicit adapter boundary for stage_a_image_alignment: campaign `dpi`
    maps to run_probe(target_dpi=...) as above. `mode` and resolution values
    are validated through the probe's own SUPPORTED_MODES/select_resolution_runs
    (not a second, driftable copy of accepted values) without starting a
    transaction or exporting a TIFF, so this same function backs
    validate_settings."""
    def resolve(settings):
        module = __import__(module_name, fromlist=["run_probe"])
        arguments = dict(settings)
        _apply_dpi_alias(arguments, "stage_a_image_alignment")
        unknown = sorted(set(arguments) - _IMAGE_ALIGNMENT_RUNTIME_SETTINGS)
        if unknown:
            raise ValueError("Unknown settings for stage_a_image_alignment: {0}".format(unknown))
        if "mode" in arguments and arguments["mode"] not in module.SUPPORTED_MODES:
            raise ValueError("Unsupported export mode {0!r}. Expected one of {1}".format(arguments["mode"], module.SUPPORTED_MODES))
        module.select_resolution_runs(
            arguments.get("resolution_cases", "all"),
            arguments.get("resolution_policy", module.DEFAULT_RESOLUTION_POLICY),
            arguments.get("target_dpi", module.DEFAULT_TARGET_DPI),
            arguments.get("fixed_pixel_width", module.DEFAULT_FIXED_PIXEL_WIDTH),
            arguments.get("max_pixel_dimension", module.DEFAULT_MAX_PIXEL_DIMENSION))
        return arguments

    def invoke(view, settings, output_directory):
        module = __import__(module_name, fromlist=["run_probe"])
        arguments = resolve(settings)
        return module.run_probe(raw_view=view, output_dir=output_directory, **arguments)

    def validate_settings(settings, output_directory):
        resolve(settings)

    invoke.validate_settings = validate_settings
    return invoke


_MODEL_LINEWORK_RUNTIME_SETTINGS = frozenset((
    "selection", "resolution_policy", "target_dpi", "fixed_pixel_width", "max_pixel_dimension",
))


def _model_linework_adapter(module_name):
    """Explicit adapter boundary for stage_a_model_linework: campaign `dpi`
    maps to run_probe(target_dpi=...) as above. `selection` and resolution
    values are validated through the probe's own _select_modes/_resolution_runs
    (reused rather than duplicated, same as the other specialized adapters
    above) without starting a transaction or exporting a TIFF, so this same
    function backs validate_settings.

    NOTE: run_probe() only accepts `selection` (one of MODES, e.g.
    "hidden_line_white_fill_black_lines") to choose a display/fill/line
    configuration - there is no display_style/fills/lines/bounds kwarg.
    examples/stage_a_campaign.json's Stage 3/6 jobs previously authored
    settings in that older shape ({"display_style": "HiddenLine", "fills":
    "white", "lines": "black", "bounds": "..."}); "HiddenLine display + white
    fills + black lines" is an exact match for the `hidden_line_white_fill_black_lines`
    mode's own documented description (see PROBE_STAGE_A_MODEL_LINEWORK.md),
    so those 8 jobs were updated to `"selection": "hidden_line_white_fill_black_lines"`.
    `bounds` ("id_raster" / "accepted_id_raster") was dropped rather than
    mapped: neither string, nor a `bounds` setting at all, appears anywhere
    in this probe's implementation or in the repository's git history back
    to the campaign file's original commit - it never corresponded to a real
    capability.
    """
    def resolve(settings):
        module = __import__(module_name, fromlist=["run_probe"])
        arguments = dict(settings)
        _apply_dpi_alias(arguments, "stage_a_model_linework")
        unknown = sorted(set(arguments) - _MODEL_LINEWORK_RUNTIME_SETTINGS)
        if unknown:
            raise ValueError("Unknown settings for stage_a_model_linework: {0}".format(unknown))
        if "selection" in arguments:
            module._select_modes(arguments["selection"])  # noqa: SLF001 - reuse the probe's own parser, not a driftable copy
        module._resolution_runs(  # noqa: SLF001 - reuse the probe's own parser, not a driftable copy
            arguments.get("resolution_policy", module.DEFAULT_RESOLUTION_POLICY),
            arguments.get("target_dpi", module.DEFAULT_TARGET_DPI),
            arguments.get("fixed_pixel_width", module.DEFAULT_FIXED_PIXEL_WIDTH),
            arguments.get("max_pixel_dimension", module.DEFAULT_MAX_PIXEL_DIMENSION))
        return arguments

    def invoke(view, settings, output_directory):
        module = __import__(module_name, fromlist=["run_probe"])
        arguments = resolve(settings)
        return module.run_probe(raw_view=view, output_dir=output_directory, **arguments)

    def validate_settings(settings, output_directory):
        resolve(settings)

    invoke.validate_settings = validate_settings
    return invoke


def build_registry(doc=None):
    specialized = {"stage_a_transaction_group_export", "stage_a_minimum_id_mutations", "stage_a_external_sources",
                   "stage_a_image_alignment", "stage_a_model_linework"}
    registry = {probe_id: _adapter(module) for probe_id, module in PROBE_MODULES.items()
                if probe_id not in specialized}
    registry["stage_a_minimum_id_mutations"] = _minimum_id_mutations_adapter(PROBE_MODULES["stage_a_minimum_id_mutations"])
    registry["stage_a_image_alignment"] = _image_alignment_adapter(PROBE_MODULES["stage_a_image_alignment"])
    registry["stage_a_model_linework"] = _model_linework_adapter(PROBE_MODULES["stage_a_model_linework"])
    transaction_module = PROBE_MODULES["stage_a_transaction_group_export"]
    registry["stage_a_transaction_group_export"] = (_transaction_adapter(doc, transaction_module)
                                                       if doc is not None else _adapter(transaction_module))
    external_sources_module = PROBE_MODULES["stage_a_external_sources"]
    registry["stage_a_external_sources"] = (_external_sources_adapter(doc, external_sources_module)
                                              if doc is not None else _adapter(external_sources_module))
    return registry
