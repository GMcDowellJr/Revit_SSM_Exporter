"""
Focused Stage A minimum-ID-mutation probe for Revit 2025 / Dynamo 3.3 CPython3.

Dynamo inputs:
    IN[0] = target model-capable view
    IN[1] = output directory
    IN[2] = maximum host elements to color; null/0 means all eligible
    IN[3] = variant selection; default "all"
    IN[4] = resolution policy: "paper_space_dpi" (default), "fixed_pixel_width", or "both"
    IN[5] = target DPI for paper_space_dpi (default 150)
    IN[6] = fixed pixel width diagnostic control (default 1600)
    IN[7] = optional maximum pixel dimension cap
    IN[8] = optional repository root containing vop_interwoven and tests/dynamo

This probe intentionally does not modify production Stage A code.  It reuses the
same paper-space resolution contract as the revised Stage A probes and runs each
variant in its own TransactionGroup that is rolled back in finally.
"""
from __future__ import print_function

import csv
import hashlib
import json
import math
import os
import sys
import time
import traceback

import re

_RESOLUTION_CONTRACT = None



_PROBE_CONTRACT = None


def _probe_contract():
    """Import the shared contract after the repository path has been bootstrapped."""
    global _PROBE_CONTRACT
    if _PROBE_CONTRACT is None:
        try:
            import tests.dynamo.stage_a_probe_contract as contract
        except ImportError:
            import stage_a_probe_contract as contract
        _PROBE_CONTRACT = contract
    return _PROBE_CONTRACT


def _ensure_typing_module():
    """Install a tiny typing fallback for stripped Dynamo Python runtimes.

    Some Dynamo Python environments used for pasted nodes do not expose the
    stdlib ``typing`` module, while the production collection policy imports
    names only for annotations.  The probe must not edit production code, so it
    supplies enough annotation placeholders before importing vop_interwoven.
    """
    try:
        import typing  # noqa: F401
        return False
    except Exception:
        pass
    try:
        import types
        module = types.ModuleType("typing")

        class _TypingAlias(object):
            def __getitem__(self, _item):
                return self
            def __call__(self, *args, **kwargs):
                return self

        alias = _TypingAlias()
        for name in ("Any", "Callable", "Dict", "Iterable", "List", "Optional", "Set", "Tuple", "TypeVar"):
            setattr(module, name, alias)
        sys.modules["typing"] = module
        return True
    except Exception:
        return False

def _candidate_roots(explicit_repo_root=None, output_dir=None):
    """Yield possible repository roots, preferring explicit Dynamo IN[8]."""
    seen = set()

    def emit(path):
        if not path:
            return
        try:
            path = os.path.abspath(os.path.expanduser(str(path)))
        except Exception:
            return
        if os.path.isfile(path):
            path = os.path.dirname(path)
        if path in seen:
            return
        seen.add(path)
        yield path

    anchors = [explicit_repo_root]
    for env_name in ("REVIT_SSM_EXPORTER_ROOT", "VOP_REPO_ROOT"):
        try:
            anchors.append(os.environ.get(env_name))
        except Exception:
            pass
    anchors.append(output_dir)
    try:
        anchors.append(os.getcwd())
    except Exception:
        pass
    try:
        anchors.append(os.path.dirname(os.path.abspath(__file__)))
    except Exception:
        # Expected in Dynamo pasted-node execution.
        pass
    home = os.path.expanduser("~")
    anchors.extend([
        os.path.join(home, "Documents", "Revit_SSM_Exporter"),
        os.path.join(home, "Documents", "GitHub", "Revit_SSM_Exporter"),
        os.path.join(home, "source", "repos", "Revit_SSM_Exporter"),
        os.path.join(home, "Revit_SSM_Exporter"),
        "/workspace/Revit_SSM_Exporter",
    ])
    for anchor in anchors:
        for root in emit(anchor):
            yield root
            cur = root
            for _ in range(8):
                parent = os.path.dirname(cur)
                if parent == cur:
                    break
                cur = parent
                for out in emit(cur):
                    yield out


def _add_repo_root_to_path(repo_root=None, output_dir=None):
    if repo_root:
        explicit = os.path.abspath(os.path.expanduser(str(repo_root)))
        has_pkg = os.path.isdir(os.path.join(explicit, "vop_interwoven"))
        has_contract = os.path.isfile(os.path.join(explicit, "tests", "dynamo", "resolution_contract.py"))
        if not (has_pkg or has_contract):
            raise RuntimeError("IN[8] repository root does not contain vop_interwoven or tests/dynamo/resolution_contract.py: {0}".format(repo_root))
        for candidate in (explicit, os.path.join(explicit, "tests", "dynamo")):
            if os.path.isdir(candidate) and candidate not in sys.path:
                sys.path.insert(0, candidate)
        return explicit
    checked = []
    for root in _candidate_roots(None, output_dir):
        checked.append(root)
        has_pkg = os.path.isdir(os.path.join(root, "vop_interwoven"))
        has_contract = os.path.isfile(os.path.join(root, "tests", "dynamo", "resolution_contract.py"))
        if has_pkg or has_contract:
            for candidate in (root, os.path.join(root, "tests", "dynamo")):
                if os.path.isdir(candidate) and candidate not in sys.path:
                    sys.path.insert(0, candidate)
            return root
    return None


def _load_resolution_contract(repo_root=None, output_dir=None):
    global _RESOLUTION_CONTRACT
    if _RESOLUTION_CONTRACT is not None:
        return _RESOLUTION_CONTRACT
    _add_repo_root_to_path(repo_root, output_dir)
    try:
        import tests.dynamo.resolution_contract as contract
    except Exception:
        import resolution_contract as contract  # type: ignore
    _RESOLUTION_CONTRACT = contract
    return contract


def round_half_up_positive(value):
    return _load_resolution_contract().round_half_up_positive(value)


def calculate_paper_space_resolution(*args, **kwargs):
    return _load_resolution_contract().calculate_paper_space_resolution(*args, **kwargs)


def apply_resolution_cap(*args, **kwargs):
    return _load_resolution_contract().apply_resolution_cap(*args, **kwargs)


def choose_resolution_bounds(*args, **kwargs):
    return _load_resolution_contract().choose_resolution_bounds(*args, **kwargs)


def resolution_report_for_accepted_width(*args, **kwargs):
    return _load_resolution_contract().resolution_report_for_accepted_width(*args, **kwargs)

PROBE_NAME = "stage_a_minimum_id_mutations"
PROBE_VERSION = "2026-08-05.1"
DEFAULT_RESOLUTION_POLICY = "paper_space_dpi"
DEFAULT_TARGET_DPI = 150
DEFAULT_FIXED_PIXEL_WIDTH = 1600
DEFAULT_MAX_PIXEL_DIMENSION = None

STATUS_APPLIED = "APPLIED"
STATUS_ALREADY = "ALREADY_MATCHED"
STATUS_TEMPLATE = "BLOCKED_BY_TEMPLATE"
STATUS_UNSUPPORTED = "UNSUPPORTED"
STATUS_FAILED = "FAILED"
STATUS_NOT_REQUESTED = "NOT_REQUESTED"

FACTORIAL_MUTATIONS = ("hide_annotation_categories", "smooth_edges_off", "display_style_flat_colors")
REMAINING_FULL_SUPPRESSION_MUTATIONS = (
    "visible_filter_graphics_neutralized",
    "category_halftone_neutralized",
    "phase_graphics_neutralized",
    "phase_filter_neutralized",
    "visibility_off_filters_disabled",
    "shadows_off",
    "ambient_occlusion_off",
    "sketchy_lines_off",
    "depth_cueing_off",
    "background_neutralized",
    "silhouettes_removed",
)
SEMANTIC_DIAGNOSTICS = (
    "diagnostic_disable_visibility_off_filters",
    "diagnostic_neutral_phase_filter",
    "diagnostic_recollect_after_filter_change",
    "diagnostic_recollect_after_phase_change",
)

MUTATION_CATALOG = {
    "detach_template": {
        "api": "view.ViewTemplateId = ElementId.InvalidElementId",
        "requested": "InvalidElementId",
        "expected_fidelity_effect": "Prerequisite: unlock template-controlled display/VG properties; detachment alone should not be counted as a pixel-fidelity mutation.",
        "possible_visibility_semantic_effect": "May expose the view instance's evaluated settings if the template does not transfer perfectly; attestation snapshots before/after detect drift.",
        "restore": "TransactionGroup.RollBack plus explicit captured-state comparison.",
        "classification": "prerequisite",
    },
    "hide_annotation_categories": {
        "api": "view.SetCategoryHidden(category.Id, True) for CategoryType.Annotation plus OST_DetailComponents/OST_Lines",
        "requested": "hidden=True",
        "expected_fidelity_effect": "Removes annotation/view-only foreground that can create unassigned colors but is mainly a model-only scope mutation.",
        "possible_visibility_semantic_effect": "Intentional exclusion of non-model/view-only categories; should not alter model-category visibility.",
        "restore": "TransactionGroup.RollBack.",
        "classification": "model_only_scope",
    },
    "smooth_edges_off": {
        "api": "view.GetViewDisplayModel(); dm.SmoothEdges=False; view.SetViewDisplayModel(dm)",
        "requested": "False",
        "expected_fidelity_effect": "Prevents anti-aliased edge blends that generate off-palette pixels.",
        "possible_visibility_semantic_effect": "No intended model visible-set change; pixel occupancy along silhouettes may change by anti-alias removal.",
        "restore": "TransactionGroup.RollBack.",
        "classification": "fidelity",
    },
    "display_style_flat_colors": {
        "api": "view.DisplayStyle = DisplayStyle.FlatColors",
        "requested": "FlatColors",
        "expected_fidelity_effect": "Removes lighting/material tinting from element override colors.",
        "possible_visibility_semantic_effect": "Should preserve visible elements, but display-style support varies by view type.",
        "restore": "TransactionGroup.RollBack.",
        "classification": "fidelity",
    },
    "visible_filter_graphics_neutralized": {
        "api": "view.SetFilterOverrides(fid, OverrideGraphicSettings()) while preserving GetFilterVisibility(fid)",
        "requested": "blank OverrideGraphicSettings for enabled visible filters",
        "expected_fidelity_effect": "Removes filter color/halftone/transparency overrides without turning filters on/off.",
        "possible_visibility_semantic_effect": "Should preserve filter visibility booleans; rule-based inclusion remains but graphic appearance changes.",
        "restore": "TransactionGroup.RollBack.",
        "classification": "fidelity_candidate",
    },
    "category_halftone_neutralized": {
        "api": "view.GetCategoryOverrides(cat); ogs.SetHalftone(False); view.SetCategoryOverrides(cat, ogs)",
        "requested": "Halftone=False",
        "expected_fidelity_effect": "Prevents category halftone lightening of assigned element colors.",
        "possible_visibility_semantic_effect": "No category visibility change intended.",
        "restore": "TransactionGroup.RollBack.",
        "classification": "fidelity_candidate",
    },
    "phase_graphics_neutralized": {
        "api": "PhaseFilter phase-status presentations remain; phase graphic overrides are inspected, not changed unless API permits safe graphic-only reset.",
        "requested": "graphic-only neutralization when supported",
        "expected_fidelity_effect": "Would remove phase tint/halftone while preserving phase visibility rules.",
        "possible_visibility_semantic_effect": "Any API path that changes phase filter/status visibility is semantic and excluded from default.",
        "restore": "TransactionGroup.RollBack.",
        "classification": "cannot_isolate_if_api_missing",
    },
    "phase_filter_neutralized": {
        "api": "VIEW_PHASE_FILTER parameter set to neutral PhaseFilter from color_id_buffer.get_or_create_neutral_phase_filter",
        "requested": "neutral phase filter id",
        "expected_fidelity_effect": "Can make phase-hidden or phase-overridden elements render with neutral graphics.",
        "possible_visibility_semantic_effect": "Semantic unless evidence proves unchanged visible model set; default recommendation must preserve original phase semantics.",
        "restore": "TransactionGroup.RollBack; created neutral filter is temporary inside the group.",
        "classification": "semantic_diagnostic",
    },
    "visibility_off_filters_disabled": {
        "api": "view.SetIsFilterEnabled(fid, False) for filters with GetFilterVisibility(fid)==False",
        "requested": "enabled=False",
        "expected_fidelity_effect": "May reveal assigned colors hidden by visibility-off filters but that is not color fidelity.",
        "possible_visibility_semantic_effect": "Changes original filter visibility semantics; diagnostic only.",
        "restore": "TransactionGroup.RollBack.",
        "classification": "semantic_diagnostic",
    },
    "shadows_off": {"api": "ViewDisplayModel.ShowShadows=False", "requested": "False", "expected_fidelity_effect": "Removes shadow tinting if property exists.", "possible_visibility_semantic_effect": "No intended visible-set change.", "restore": "TransactionGroup.RollBack.", "classification": "fidelity_candidate"},
    "ambient_occlusion_off": {"api": "ViewDisplayModel.AmbientOcclusion=False", "requested": "False", "expected_fidelity_effect": "Removes AO tinting if property exists.", "possible_visibility_semantic_effect": "No intended visible-set change.", "restore": "TransactionGroup.RollBack.", "classification": "fidelity_candidate"},
    "sketchy_lines_off": {"api": "ViewDisplayModel.SketchyLines=False", "requested": "False", "expected_fidelity_effect": "Removes sketchy stroke perturbation if property exists.", "possible_visibility_semantic_effect": "No intended visible-set change.", "restore": "TransactionGroup.RollBack.", "classification": "fidelity_candidate"},
    "depth_cueing_off": {"api": "ViewDisplayModel.DepthCueing=False", "requested": "False", "expected_fidelity_effect": "Removes depth fade if property exists.", "possible_visibility_semantic_effect": "No intended visible-set change.", "restore": "TransactionGroup.RollBack.", "classification": "fidelity_candidate"},
    "background_neutralized": {"api": "GraphicDisplayOptions background-related properties when exposed by API", "requested": "neutral/none", "expected_fidelity_effect": "Stabilizes background classification rather than element colors.", "possible_visibility_semantic_effect": "No intended visible-set change.", "restore": "TransactionGroup.RollBack.", "classification": "fidelity_candidate"},
    "silhouettes_removed": {"api": "Silhouette display setting when exposed by API", "requested": "none", "expected_fidelity_effect": "Removes silhouette edge colors if property exists.", "possible_visibility_semantic_effect": "No intended visible-set change; linework occupancy can change.", "restore": "TransactionGroup.RollBack.", "classification": "fidelity_candidate"},
}



def _safe_name(value):
    text = str(value or "view")
    return re.sub(r"[^A-Za-z0-9_. -]+", "_", text).strip().replace(" ", "_") or "view"


def _candidate_repo_roots(output_dir=None, repo_root=None):
    return list(_candidate_roots(repo_root, output_dir))


def _ensure_repo_import_path(output_dir=None, repo_root=None):
    _ensure_typing_module()
    try:
        import vop_interwoven  # noqa: F401
        return None
    except Exception:
        pass
    root = _add_repo_root_to_path(repo_root, output_dir)
    try:
        import vop_interwoven  # noqa: F401
        return root
    except Exception:
        if repo_root:
            raise RuntimeError("IN[8] repository root was added to sys.path but vop_interwoven could not be imported: {0}".format(repo_root))
        raise RuntimeError("Could not locate Revit_SSM_Exporter repo root for vop_interwoven imports. Provide IN[8] as the repository root containing vop_interwoven.")


def _current_resolution_bounds(view):
    bounds, source = _active_crop_bounds(view)
    if bounds is not None:
        return bounds, source
    try:
        doc = getattr(view, "Document", None)
        from vop_interwoven.config import Config
        from vop_interwoven.revit.view_basis import make_view_basis, resolve_view_bounds
        cfg = Config()
        if not hasattr(cfg, "cell_size_paper_in") or cfg.cell_size_paper_in is None:
            cfg.cell_size_paper_in = 0.125
        scale = _positive_float(getattr(view, "Scale", None), "view_scale")
        basis = make_view_basis(view)
        cell_size_ft = (float(cfg.cell_size_paper_in) * scale) / 12.0
        resolved = resolve_view_bounds(view, policy={"doc": doc, "basis": basis, "cfg": cfg, "buffer_ft": float(getattr(cfg, "bounds_buffer_ft", 0.0) or 0.0), "cell_size_ft": cell_size_ft, "max_W": getattr(cfg, "max_grid_cells_width", None), "max_H": getattr(cfg, "max_grid_cells_height", None)})
        model_bounds = resolved.get("model_bounds_uv") or resolved.get("bounds_uv")
        if model_bounds is not None:
            return _bounds_tuple(model_bounds), "resolved_model_bounds" if resolved.get("model_bounds_uv") is not None else "canvas_bounds"
    except Exception:
        pass
    return None, "INCONCLUSIVE"

def _parse_resolution_policy(value):
    text = str(value or DEFAULT_RESOLUTION_POLICY).strip().lower()
    if text not in ("fixed_pixel_width", "paper_space_dpi", "both"):
        raise ValueError("Unsupported resolution_policy '{0}'".format(value))
    return text


def _positive_float(value, name):
    if value is None or value == "":
        raise ValueError("{0} is required".format(name))
    number = float(value)
    if number <= 0:
        raise ValueError("{0} must be positive".format(name))
    return number


def _parse_dpi_values(value):
    if value is None or value == "":
        return [float(DEFAULT_TARGET_DPI)]
    raw = value if isinstance(value, (list, tuple)) else str(value).replace(";", ",").split(",")
    values = [_positive_float(item, "target_dpi") for item in raw if item is not None and str(item).strip()]
    return values or [float(DEFAULT_TARGET_DPI)]


def _parse_optional_cap(value):
    if value is None or value == "":
        return None
    return int(round_half_up_positive(_positive_float(value, "max_pixel_dimension")))


def resolution_runs(policy=DEFAULT_RESOLUTION_POLICY, dpi_value=DEFAULT_TARGET_DPI, fixed_width=DEFAULT_FIXED_PIXEL_WIDTH, max_dimension=DEFAULT_MAX_PIXEL_DIMENSION):
    policy = _parse_resolution_policy(policy)
    fixed = int(round_half_up_positive(_positive_float(fixed_width if fixed_width is not None else DEFAULT_FIXED_PIXEL_WIDTH, "fixed_pixel_width")))
    cap = _parse_optional_cap(max_dimension)
    runs = []
    if policy in ("paper_space_dpi", "both"):
        for dpi in _parse_dpi_values(dpi_value):
            runs.append({"policy": "paper_space_dpi", "target_dpi": float(dpi), "fixed_pixel_width": fixed, "max_pixel_dimension": cap})
    if policy in ("fixed_pixel_width", "both"):
        runs.append({"policy": "fixed_pixel_width", "target_dpi": None, "fixed_pixel_width": fixed, "max_pixel_dimension": cap})
    return runs


def resolution_suffix(run):
    if run.get("policy") == "fixed_pixel_width":
        return "fixed_{0}".format(int(run.get("fixed_pixel_width")))
    dpi = float(run.get("target_dpi"))
    return "dpi_{0}".format(int(dpi) if abs(dpi - int(dpi)) < 1e-9 else str(dpi).replace(".", "p"))


def build_resolution_report(policy, model_width_ft, model_height_ft, view_scale, target_dpi, bounds_source, fixed_pixel_width, max_pixel_dimension=None):
    if policy == "fixed_pixel_width":
        model_width_ft = _positive_float(model_width_ft, "model_width_ft")
        model_height_ft = _positive_float(model_height_ft, "model_height_ft")
        fixed = int(round_half_up_positive(_positive_float(fixed_pixel_width, "fixed_pixel_width")))
        ppf = float(fixed) / model_width_ft
        report = {"policy": "fixed_pixel_width", "target_dpi": None, "view_scale": int(view_scale) if view_scale else None, "bounds_source": str(bounds_source), "model_width_ft": float(model_width_ft), "model_height_ft": float(model_height_ft), "paper_width_in": None, "requested_width_px": fixed, "accepted_width_px": fixed, "predicted_height_px": round_half_up_positive(model_height_ft * ppf), "target_pixels_per_model_foot": ppf, "accepted_pixels_per_model_foot": ppf, "effective_dpi": (ppf * float(view_scale) / 12.0) if view_scale else None, "target_model_inches_per_pixel": 12.0 / ppf, "actual_model_inches_per_pixel": 12.0 / ppf, "max_pixel_dimension": None, "capped": False}
        return apply_resolution_cap(report, max_pixel_dimension)
    return apply_resolution_cap(calculate_paper_space_resolution(model_width_ft, model_height_ft, view_scale, target_dpi, bounds_source), max_pixel_dimension)


def make_variant(name, mutations, diagnostic=False, recollect=False, reference=False):
    return {"name": name, "mutations": tuple(mutations), "diagnostic": bool(diagnostic), "recollect": bool(recollect), "reference": bool(reference)}


def generate_stage1_variants(flat_colors_supported=True):
    variants = [
        make_variant("attached_element_overrides_only", ()),
        make_variant("detached_preserve_original_display", ("detach_template",)),
        make_variant("attached_AS", ("hide_annotation_categories", "smooth_edges_off")),
    ]
    combos = [
        ("detached_none", ()), ("detached_A", ("hide_annotation_categories",)),
        ("detached_S", ("smooth_edges_off",)), ("detached_F", ("display_style_flat_colors",)),
        ("detached_AS", ("hide_annotation_categories", "smooth_edges_off")),
        ("detached_AF", ("hide_annotation_categories", "display_style_flat_colors")),
        ("detached_SF", ("smooth_edges_off", "display_style_flat_colors")),
        ("detached_ASF", ("hide_annotation_categories", "smooth_edges_off", "display_style_flat_colors")),
    ]
    for name, muts in combos:
        if not flat_colors_supported and "display_style_flat_colors" in muts:
            continue
        variants.append(make_variant(name, ("detach_template",) + muts))
    return variants


def generate_stage2_variants(baseline_mutations, remaining=REMAINING_FULL_SUPPRESSION_MUTATIONS):
    baseline = tuple(baseline_mutations)
    full = baseline + tuple(m for m in remaining if m not in baseline)
    variants = [make_variant("full_suppression_reference", full, reference=True)]
    for m in remaining:
        if m not in baseline:
            variants.append(make_variant("baseline_plus_" + m, baseline + (m,)))
    for m in remaining:
        if m in full:
            variants.append(make_variant("full_minus_" + m, tuple(x for x in full if x != m), reference=True))
    diagnostic_mutations = {
        "diagnostic_disable_visibility_off_filters": "visibility_off_filters_disabled",
        "diagnostic_neutral_phase_filter": "phase_filter_neutralized",
        "diagnostic_recollect_after_filter_change": "visibility_off_filters_disabled",
        "diagnostic_recollect_after_phase_change": "phase_filter_neutralized",
    }
    variants.extend(make_variant(name, baseline + (diagnostic_mutations[name],), diagnostic=True, recollect=("recollect" in name)) for name in SEMANTIC_DIAGNOSTICS)
    return variants


def backward_elimination_round(current_mutations, individually_redundant):
    reduced = tuple(m for m in current_mutations if m not in set(individually_redundant))
    leave_one_out = [make_variant("reduced_minus_" + m, tuple(x for x in reduced if x != m)) for m in reduced]
    return make_variant("reduced_candidate", reduced), leave_one_out


def mutation_status_record(mutation_id, requested=False, status=STATUS_NOT_REQUESTED, original=None, requested_value=None, effective=None, template_controlled=False, applied=False, message=None):
    rec = {
        "mutation_id": mutation_id,
        "api_property_or_method": MUTATION_CATALOG.get(mutation_id, {}).get("api"),
        "original_value": original,
        "requested_value": requested_value if requested_value is not None else MUTATION_CATALOG.get(mutation_id, {}).get("requested"),
        "requested": bool(requested),
        "status": status,
        "applied": bool(applied),
        "effective_value_after_application": effective,
        "template_controlled": bool(template_controlled),
        "expected_fidelity_effect": MUTATION_CATALOG.get(mutation_id, {}).get("expected_fidelity_effect"),
        "possible_visibility_semantic_effect": MUTATION_CATALOG.get(mutation_id, {}).get("possible_visibility_semantic_effect"),
        "restore_mechanism": MUTATION_CATALOG.get(mutation_id, {}).get("restore"),
        "classification": MUTATION_CATALOG.get(mutation_id, {}).get("classification"),
    }
    if message:
        rec["message"] = message
    json.dumps(rec)
    return rec


def all_mutation_statuses(requested, observed=None):
    observed = observed or {}
    statuses = {}
    for mid in MUTATION_CATALOG:
        statuses[mid] = observed.get(mid) or mutation_status_record(mid, requested=(mid in requested), status=STATUS_NOT_REQUESTED if mid not in requested else STATUS_FAILED)
    return statuses


def dimensions_comparable(a, b):
    keys = ("bounds_source", "accepted_width_px", "actual_width_px", "actual_height_px", "accepted_pixels_per_model_foot")
    return all(a.get(k) == b.get(k) for k in keys)


def pixel_data_sha256(rgb_rows):
    h = hashlib.sha256()
    for row in rgb_rows:
        for r, g, b in row:
            h.update(bytes((int(r) & 255, int(g) & 255, int(b) & 255)))
    return h.hexdigest()


def classify_mutation_evidence(status_record, fidelity_failed_without=False, annotation_contamination_without=False, semantic_change=False):
    if status_record.get("status") in (STATUS_FAILED, STATUS_TEMPLATE, STATUS_UNSUPPORTED):
        return "blocked_or_unsupported"
    mid = status_record.get("mutation_id")
    cls = MUTATION_CATALOG.get(mid, {}).get("classification")
    if semantic_change or cls == "semantic_diagnostic":
        return "semantic_diagnostics_not_for_default"
    if annotation_contamination_without or cls == "model_only_scope":
        return "required_for_model_only_scope"
    if fidelity_failed_without or cls in ("fidelity", "fidelity_candidate"):
        return "required_for_color_fidelity"
    if cls == "prerequisite":
        return "prerequisite_only"
    return "unnecessary_in_tested_view"


def _safe_int_id(value):
    try:
        return int(value.IntegerValue)
    except Exception:
        try:
            return int(value)
        except Exception:
            return None


def _safe_enum(value):
    return None if value is None else str(value)


def _element_id(value):
    from Autodesk.Revit.DB import ElementId
    if hasattr(value, "IntegerValue"):
        return value
    return ElementId(int(value))


def _bounds_tuple(b):
    if b is None:
        return None
    if isinstance(b, dict):
        return (float(b["min_u"]), float(b["min_v"]), float(b["max_u"]), float(b["max_v"]))
    try:
        return (float(b[0]), float(b[1]), float(b[2]), float(b[3]))
    except Exception:
        return (float(b.Min.X), float(b.Min.Y), float(b.Max.X), float(b.Max.Y))


def _bounds_dict(b):
    t = _bounds_tuple(b)
    if t is None:
        return None
    return {"min_u": t[0], "min_v": t[1], "max_u": t[2], "max_v": t[3], "width": t[2] - t[0], "height": t[3] - t[1]}


def _active_crop_bounds(view):
    try:
        if bool(getattr(view, "CropBoxActive", False)) and getattr(view, "CropBox", None) is not None:
            b = view.CropBox
            return (float(b.Min.X), float(b.Min.Y), float(b.Max.X), float(b.Max.Y)), "active_model_crop"
    except Exception:
        pass
    return None, None


def _collect_assignment_set(doc, view, max_count):
    _ensure_typing_module()
    from vop_interwoven.config import Config
    from vop_interwoven.core.math_utils import Bounds2D
    from vop_interwoven.core.raster import ViewRaster
    from vop_interwoven.revit.collection import collect_view_elements, expand_host_link_import_model_elements
    from vop_interwoven.color_id_buffer import resolve_all
    cfg = Config()
    raster = ViewRaster(
        width=10,
        height=10,
        cell_size=1.0,
        bounds=Bounds2D(0.0, 0.0, 1.0, 1.0),
        tile_size=getattr(cfg, "tile_size", 16),
        cfg=cfg,
    )
    elements = collect_view_elements(doc, view, raster, diag=None, cfg=cfg)
    expanded = expand_host_link_import_model_elements(doc, view, elements, cfg, diag=None, elem_cache=None)
    host = []
    seen = set()
    for item in expanded:
        if item.get("source_type") != "HOST":
            continue
        elem = item.get("element")
        eid = getattr(elem, "Id", None)
        if eid is None or eid.IntegerValue in seen:
            continue
        seen.add(eid.IntegerValue)
        host.append(elem)
    resolved = resolve_all(doc, host)
    resolved = sorted(resolved, key=lambda eid: eid.IntegerValue)
    if max_count and int(max_count) > 0:
        resolved = resolved[:int(max_count)]
    return resolved, {"initial_collect_view_elements": len(elements), "expanded_host_candidates": len(host), "assigned_host_elements": len(resolved)}


def _palette(n):
    from vop_interwoven.color_id_buffer import build_palette, choose_step
    step = choose_step(max(n, 1))
    return build_palette(n, step=step), step


def _build_flat_color_ogs(doc, rgb):
    from Autodesk.Revit.DB import Color
    from vop_interwoven.color_id_buffer import _build_flat_color_ogs as prod_build, _get_solid_pattern_id
    solid = _get_solid_pattern_id(doc)
    if solid is None:
        raise RuntimeError("No solid drafting fill pattern found")
    return prod_build(solid, Color(int(rgb[0]), int(rgb[1]), int(rgb[2])))


def _snapshot(doc, view):
    state = {"doc_is_modified": bool(getattr(doc, "IsModified", False)), "view_template_id": _safe_int_id(getattr(view, "ViewTemplateId", None)), "display_style": _safe_enum(getattr(view, "DisplayStyle", None)), "filters": {}, "phase_filter_id": None, "smooth_edges": None, "display_model": {}, "category_hidden": {}}
    try:
        dm = view.GetViewDisplayModel()
        try:
            for attr in ("SmoothEdges", "ShowShadows", "AmbientOcclusion", "SketchyLines", "DepthCueing"):
                try:
                    state["display_model"][attr] = bool(getattr(dm, attr))
                except Exception:
                    state["display_model"][attr] = "UNSUPPORTED"
            state["smooth_edges"] = state["display_model"].get("SmoothEdges")
        finally:
            try: dm.Dispose()
            except Exception: pass
    except Exception as ex:
        state["display_model_error"] = str(ex)
    try:
        from Autodesk.Revit.DB import BuiltInParameter
        p = view.get_Parameter(BuiltInParameter.VIEW_PHASE_FILTER)
        state["phase_filter_id"] = _safe_int_id(p.AsElementId()) if p is not None else None
        state["phase_filter_read_only"] = bool(p.IsReadOnly) if p is not None else None
    except Exception as ex:
        state["phase_filter_error"] = str(ex)
    try:
        for fid in view.GetFilters():
            state["filters"][str(fid.IntegerValue)] = _filter_record(view, fid)
    except Exception as ex:
        state["filters_error"] = str(ex)
    return state


def _filter_record(view, fid):
    rec = {"filter_id": _safe_int_id(fid), "filter_name": None, "original_visibility": None, "effective_visibility": None, "original_graphic_override_summary": None, "effective_graphic_override_summary": None, "enabled": None}
    try:
        elem = view.Document.GetElement(fid)
        rec["filter_name"] = getattr(elem, "Name", None)
    except Exception:
        pass
    try: rec["enabled"] = bool(view.GetIsFilterEnabled(fid))
    except Exception: pass
    try:
        vis = bool(view.GetFilterVisibility(fid)); rec["original_visibility"] = vis; rec["effective_visibility"] = vis
    except Exception: pass
    try:
        rec["original_graphic_override_summary"] = _ogs_summary(view.GetFilterOverrides(fid))
        rec["effective_graphic_override_summary"] = rec["original_graphic_override_summary"]
    except Exception as ex:
        rec["override_error"] = str(ex)
    return rec


def _ogs_summary(ogs):
    if ogs is None:
        return None
    fields = {}
    for name in ("Halftone", "Transparency", "SurfaceTransparency", "ProjectionLineColor", "CutLineColor", "SurfaceForegroundPatternId", "CutForegroundPatternId"):
        try:
            v = getattr(ogs, name)
            fields[name] = _safe_int_id(v) if name.endswith("Id") else str(v)
        except Exception:
            pass
    return fields


def _set_status(result, mid, status, original=None, effective=None, template=False, applied=False, message=None):
    result["mutations"][mid] = mutation_status_record(mid, True, status, original, None, effective, template, applied, message)


def _detach_template(view, result):
    from Autodesk.Revit.DB import ElementId
    orig = getattr(view, "ViewTemplateId", None)
    if orig is None or orig == ElementId.InvalidElementId:
        _set_status(result, "detach_template", STATUS_ALREADY, _safe_int_id(orig), _safe_int_id(orig), False, False)
        return
    view.ViewTemplateId = ElementId.InvalidElementId
    eff = getattr(view, "ViewTemplateId", None)
    status = STATUS_APPLIED if eff == ElementId.InvalidElementId else STATUS_FAILED
    _set_status(result, "detach_template", status, _safe_int_id(orig), _safe_int_id(eff), False, status == STATUS_APPLIED)


def _apply_smooth_edges(view, result):
    try:
        dm = view.GetViewDisplayModel()
        try:
            orig = bool(dm.SmoothEdges)
            dm.SmoothEdges = False
            view.SetViewDisplayModel(dm)
        finally:
            try: dm.Dispose()
            except Exception: pass
        dm2 = view.GetViewDisplayModel()
        try: eff = bool(dm2.SmoothEdges)
        finally:
            try: dm2.Dispose()
            except Exception: pass
        _set_status(result, "smooth_edges_off", STATUS_ALREADY if orig is False and eff is False else (STATUS_APPLIED if eff is False else STATUS_FAILED), orig, eff, False, eff is False and orig is not False)
    except Exception as ex:
        _set_status(result, "smooth_edges_off", STATUS_UNSUPPORTED, None, None, False, False, str(ex))


def _apply_flat_colors(view, result):
    try:
        from Autodesk.Revit.DB import DisplayStyle
        flat = getattr(DisplayStyle, "FlatColors", None)
        if flat is None:
            _set_status(result, "display_style_flat_colors", STATUS_UNSUPPORTED, _safe_enum(getattr(view, "DisplayStyle", None)), None, False, False, "DisplayStyle.FlatColors unavailable")
            return
        orig = getattr(view, "DisplayStyle", None)
        view.DisplayStyle = flat
        eff = getattr(view, "DisplayStyle", None)
        _set_status(result, "display_style_flat_colors", STATUS_ALREADY if orig == flat and eff == flat else (STATUS_APPLIED if eff == flat else STATUS_FAILED), _safe_enum(orig), _safe_enum(eff), False, eff == flat and orig != flat)
    except Exception as ex:
        _set_status(result, "display_style_flat_colors", STATUS_FAILED, None, _safe_enum(getattr(view, "DisplayStyle", None)), False, False, str(ex))


def _hide_annotation_categories(doc, view, result):
    from Autodesk.Revit.DB import CategoryType, BuiltInCategory
    view_only = set(int(getattr(BuiltInCategory, n)) for n in ("OST_DetailComponents", "OST_Lines") if getattr(BuiltInCategory, n, None) is not None)
    trace = []
    applied = 0
    blocked = 0
    errors = 0
    for cat in doc.Settings.Categories:
        try:
            cid = cat.Id
            if cat.CategoryType != CategoryType.Annotation and cid.IntegerValue not in view_only:
                continue
            rec = {"category_id": cid.IntegerValue, "name": cat.Name, "category_type": _safe_enum(cat.CategoryType), "was_hidden": None, "effective_hidden": None, "can_hide": None}
            try: rec["was_hidden"] = bool(view.GetCategoryHidden(cid))
            except Exception as ex: rec["get_hidden_error"] = str(ex)
            try: rec["can_hide"] = bool(view.CanCategoryBeHidden(cid))
            except Exception as ex: rec["can_hide_error"] = str(ex); rec["can_hide"] = False
            if rec["can_hide"]:
                view.SetCategoryHidden(cid, True)
                rec["effective_hidden"] = bool(view.GetCategoryHidden(cid))
                applied += 1 if rec["was_hidden"] is not True and rec["effective_hidden"] is True else 0
            else:
                blocked += 1
            trace.append(rec)
        except Exception as ex:
            errors += 1
            trace.append({"error": str(ex)})
    result["annotation_category_trace"] = trace
    if blocked or errors:
        status = STATUS_TEMPLATE if blocked else STATUS_FAILED
    else:
        status = STATUS_APPLIED if applied else (STATUS_ALREADY if trace else STATUS_UNSUPPORTED)
    _set_status(result, "hide_annotation_categories", status, "see annotation_category_trace", "hidden categories={0}, blocked={1}, errors={2}".format(applied, blocked, errors), blocked > 0, applied > 0 and not blocked and not errors)


def _neutralize_visible_filter_graphics(view, result):
    from Autodesk.Revit.DB import OverrideGraphicSettings
    before, after, changed = [], [], 0
    try:
        for fid in view.GetFilters():
            rec = _filter_record(view, fid)
            before.append(dict(rec))
            if rec.get("enabled") and rec.get("original_visibility"):
                view.SetFilterOverrides(fid, OverrideGraphicSettings())
                changed += 1
            after_rec = _filter_record(view, fid)
            after_rec["original_visibility"] = rec.get("original_visibility")
            after_rec["original_graphic_override_summary"] = rec.get("original_graphic_override_summary")
            after.append(after_rec)
        result["filter_records"] = after
        visibility_changed = any(b.get("original_visibility") != a.get("effective_visibility") for b, a in zip(before, after))
        _set_status(result, "visible_filter_graphics_neutralized", STATUS_APPLIED if changed else STATUS_ALREADY, before, after, False, changed > 0, "filter visibility changed" if visibility_changed else None)
    except Exception as ex:
        _set_status(result, "visible_filter_graphics_neutralized", STATUS_FAILED, before, after, False, False, str(ex))


def _disable_visibility_off_filters(view, result):
    from Autodesk.Revit.DB import ElementId
    before, after, changed = [], [], 0
    try:
        for fid in view.GetFilters():
            rec = _filter_record(view, fid); before.append(dict(rec))
            if rec.get("enabled") and rec.get("original_visibility") is False:
                view.SetIsFilterEnabled(ElementId(int(fid.IntegerValue)), False); changed += 1
            after.append(_filter_record(view, fid))
        result["filter_records"] = after
        _set_status(result, "visibility_off_filters_disabled", STATUS_APPLIED if changed else STATUS_ALREADY, before, after, False, changed > 0)
    except Exception as ex:
        _set_status(result, "visibility_off_filters_disabled", STATUS_FAILED, before, after, False, False, str(ex))


def _neutral_phase_filter(view, result):
    try:
        from Autodesk.Revit.DB import BuiltInParameter
        from vop_interwoven.color_id_buffer import get_or_create_neutral_phase_filter
        p = view.get_Parameter(BuiltInParameter.VIEW_PHASE_FILTER)
        orig = _safe_int_id(p.AsElementId()) if p is not None else None
        if p is None or p.IsReadOnly:
            _set_status(result, "phase_filter_neutralized", STATUS_TEMPLATE if p is not None else STATUS_UNSUPPORTED, orig, orig, bool(p is not None and p.IsReadOnly), False, "VIEW_PHASE_FILTER missing or read-only")
            return
        pf, created = get_or_create_neutral_phase_filter(view.Document)
        p.Set(pf.Id)
        eff = _safe_int_id(p.AsElementId())
        result["phase_filter_created"] = bool(created)
        _set_status(result, "phase_filter_neutralized", STATUS_ALREADY if orig == eff else (STATUS_APPLIED if eff == pf.Id.IntegerValue else STATUS_FAILED), orig, eff, False, orig != eff)
    except Exception as ex:
        _set_status(result, "phase_filter_neutralized", STATUS_FAILED, None, None, False, False, str(ex))


def _neutralize_category_halftone(doc, view, assigned_ids, result):
    from Autodesk.Revit.DB import ElementId
    cats, changed = set(), 0
    before_after = []
    for eid in assigned_ids:
        elem = doc.GetElement(eid)
        cat = getattr(elem, "Category", None) if elem is not None else None
        if cat is not None and getattr(cat, "Id", None) is not None:
            cats.add(cat.Id.IntegerValue)
    for cid in sorted(cats):
        try:
            ogs = view.GetCategoryOverrides(ElementId(int(cid)))
            orig = bool(ogs.Halftone)
            ogs.SetHalftone(False)
            view.SetCategoryOverrides(ElementId(int(cid)), ogs)
            eff = bool(view.GetCategoryOverrides(ElementId(int(cid))).Halftone)
            if orig and not eff:
                changed += 1
            before_after.append({"category_id": cid, "original_halftone": orig, "effective_halftone": eff})
        except Exception as ex:
            before_after.append({"category_id": cid, "error": str(ex)})
    result["category_halftone_records"] = before_after
    failed = any("error" in r for r in before_after)
    _set_status(result, "category_halftone_neutralized", STATUS_FAILED if failed else (STATUS_APPLIED if changed else STATUS_ALREADY), "see category_halftone_records", "see category_halftone_records", False, changed > 0)


def _display_model_bool(view, result, mutation_id, attr):
    try:
        dm = view.GetViewDisplayModel()
        try:
            orig = bool(getattr(dm, attr))
            setattr(dm, attr, False)
            view.SetViewDisplayModel(dm)
        finally:
            try: dm.Dispose()
            except Exception: pass
        dm2 = view.GetViewDisplayModel()
        try: eff = bool(getattr(dm2, attr))
        finally:
            try: dm2.Dispose()
            except Exception: pass
        _set_status(result, mutation_id, STATUS_ALREADY if orig is False and eff is False else (STATUS_APPLIED if eff is False else STATUS_FAILED), orig, eff, False, eff is False and orig is not False)
    except Exception as ex:
        _set_status(result, mutation_id, STATUS_UNSUPPORTED, None, None, False, False, str(ex))


def _unsupported_inventory_only(result, mutation_id, message):
    _set_status(result, mutation_id, STATUS_UNSUPPORTED, None, None, False, False, message)


def _apply_mutation(doc, view, assigned_ids, result, mutation_id):
    if mutation_id == "detach_template": _detach_template(view, result)
    elif mutation_id == "hide_annotation_categories": _hide_annotation_categories(doc, view, result)
    elif mutation_id == "smooth_edges_off": _apply_smooth_edges(view, result)
    elif mutation_id == "display_style_flat_colors": _apply_flat_colors(view, result)
    elif mutation_id == "visible_filter_graphics_neutralized": _neutralize_visible_filter_graphics(view, result)
    elif mutation_id == "visibility_off_filters_disabled" or mutation_id == "disable_visibility_off_filters": _disable_visibility_off_filters(view, result)
    elif mutation_id == "phase_filter_neutralized" or mutation_id == "neutral_phase_filter": _neutral_phase_filter(view, result)
    elif mutation_id == "category_halftone_neutralized": _neutralize_category_halftone(doc, view, assigned_ids, result)
    elif mutation_id == "shadows_off": _display_model_bool(view, result, mutation_id, "ShowShadows")
    elif mutation_id == "ambient_occlusion_off": _display_model_bool(view, result, mutation_id, "AmbientOcclusion")
    elif mutation_id == "sketchy_lines_off": _display_model_bool(view, result, mutation_id, "SketchyLines")
    elif mutation_id == "depth_cueing_off": _display_model_bool(view, result, mutation_id, "DepthCueing")
    elif mutation_id in ("phase_graphics_neutralized", "background_neutralized", "silhouettes_removed"):
        _unsupported_inventory_only(result, mutation_id, "No safe graphic-only API mutation is implemented by this probe; inventory and runtime state are reported for isolation.")
    elif mutation_id.startswith("diagnostic_recollect"):
        _unsupported_inventory_only(result, mutation_id, "Handled as diagnostic metadata; recollection counts are captured separately.")
    else:
        _set_status(result, mutation_id, STATUS_UNSUPPORTED, None, None, False, False, "Unknown mutation")


def _paint(doc, view, assigned_ids):
    palette, step = _palette(len(assigned_ids))
    assigned, failures = {}, []
    for idx, eid in enumerate(assigned_ids):
        rgb = palette[idx]
        assigned[str(eid.IntegerValue)] = {"rgb": list(rgb), "element_id": eid.IntegerValue}
        try:
            view.SetElementOverrides(eid, _build_flat_color_ogs(doc, rgb))
        except Exception as ex:
            failures.append({"element_id": eid.IntegerValue, "type": type(ex).__name__, "message": str(ex)})
    return assigned, failures, step


def _set_pixel_size(opts, requested):
    candidate = max(1, int(requested))
    while True:
        try:
            opts.PixelSize = candidate
            return candidate
        except Exception:
            if candidate <= 16:
                raise
            candidate = max(16, candidate // 2)


def _export_tiff(doc, view, path, requested_pixel_size):
    from Autodesk.Revit.DB import ImageExportOptions, ExportRange, ZoomFitType, FitDirectionType, ElementId, ImageFileType
    import System.Collections.Generic as SCG
    out_dir = os.path.dirname(path)
    if not os.path.isdir(out_dir): os.makedirs(out_dir)
    before = set(os.listdir(out_dir))
    ids = SCG.List[ElementId](); ids.Add(view.Id)
    opts = ImageExportOptions(); opts.ExportRange = ExportRange.SetOfViews; opts.SetViewsAndSheets(ids)
    opts.ZoomType = ZoomFitType.FitToPage; opts.FitDirection = FitDirectionType.Horizontal
    accepted = _set_pixel_size(opts, requested_pixel_size)
    opts.FilePath = os.path.join(out_dir, "_vop_minimum_id_mutations_tmp")
    tiff = getattr(ImageFileType, "TIFF", getattr(ImageFileType, "TIF", None))
    if tiff is None:
        raise RuntimeError("Revit ImageFileType does not expose TIFF/TIF")
    opts.HLRandWFViewsFileType = tiff; opts.ShadowViewsFileType = tiff
    doc.ExportImage(opts)
    candidates = [f for f in (set(os.listdir(out_dir)) - before) if f.lower().endswith((".tif", ".tiff"))]
    if not candidates:
        raise RuntimeError("ExportImage produced no new TIFF in {0}".format(out_dir))
    candidates.sort(key=lambda n: os.path.getmtime(os.path.join(out_dir, n)), reverse=True)
    created = os.path.join(out_dir, candidates[0])
    if os.path.exists(path): os.remove(path)
    os.rename(created, path)
    return path, accepted


def analyze_tiff(path, assigned, baseline_pixels=None, full_pixels=None):
    result = {"path": path, "actual_dimensions": None, "file_sha256": None, "pixel_data_sha256": None, "background_pixel_count": None, "exact_expected_palette_pixel_count": None, "off_palette_foreground_pixels": None, "off_palette_foreground_percent": None, "unexpected_rgb_values": {}, "black_violations": None, "near_white_violations": None, "assigned_colors_detected": 0, "missing_assigned_colors": [], "visible_pixel_count_by_assigned_element": {}, "assigned_elements_with_zero_visible_pixels": None, "pixel_difference_from_faithful_baseline": None, "pixel_difference_from_full_suppression": None, "changed_pixel_bounding_rectangle": None, "analysis_status": "not_run"}
    if not os.path.exists(path):
        result["analysis_status"] = "missing_tiff"
        return result
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    result["file_sha256"] = h.hexdigest()
    try:
        from PIL import Image
        img = Image.open(path).convert("RGB")
        w, hgt = img.size
        result["actual_dimensions"] = [int(w), int(hgt)]
        pixels = list(img.getdata())
        ph = hashlib.sha256(); expected_to_elem = {}
        for key, val in assigned.items():
            expected_to_elem[tuple(val["rgb"])] = key
            result["visible_pixel_count_by_assigned_element"][key] = 0
        expected_colors = set(expected_to_elem)
        bg = (255, 255, 255)
        bg_count = expected_count = off_count = black = near_white = 0
        unexpected = {}
        for rgb in pixels:
            ph.update(bytes(rgb))
            if rgb == bg:
                bg_count += 1; continue
            if rgb in expected_colors:
                expected_count += 1
                result["visible_pixel_count_by_assigned_element"][expected_to_elem[rgb]] += 1
            else:
                off_count += 1; unexpected[rgb] = unexpected.get(rgb, 0) + 1
            if sum(rgb) < 24: black += 1
            if rgb != bg and all(c >= 245 for c in rgb): near_white += 1
        result["pixel_data_sha256"] = ph.hexdigest()
        result["background_pixel_count"] = bg_count
        result["exact_expected_palette_pixel_count"] = expected_count
        result["off_palette_foreground_pixels"] = off_count
        denom = max(1, len(pixels) - bg_count)
        result["off_palette_foreground_percent"] = 100.0 * off_count / denom
        result["unexpected_rgb_values"] = {"{0},{1},{2}".format(*k): v for k, v in sorted(unexpected.items(), key=lambda kv: kv[1], reverse=True)[:256]}
        result["black_violations"] = black
        result["near_white_violations"] = near_white
        result["assigned_colors_detected"] = sum(1 for v in result["visible_pixel_count_by_assigned_element"].values() if v)
        result["missing_assigned_colors"] = [k for k, v in result["visible_pixel_count_by_assigned_element"].items() if not v]
        result["assigned_elements_with_zero_visible_pixels"] = len(result["missing_assigned_colors"])
        result["analysis_status"] = "complete"
    except Exception as ex:
        result["analysis_status"] = "external_analysis_required"
        result["analysis_error"] = str(ex)
    return result



def compare_tiff_pixels(path, reference_path):
    if not path or not reference_path or path == reference_path:
        return {"pixel_difference_count": 0, "changed_pixel_bounding_rectangle": None}
    try:
        from PIL import Image
        a = Image.open(path).convert("RGB")
        b = Image.open(reference_path).convert("RGB")
        if a.size != b.size:
            return {"pixel_difference_count": None, "changed_pixel_bounding_rectangle": None, "reason": "dimension mismatch", "dimensions": [list(a.size), list(b.size)]}
        w, h = a.size
        ap = list(a.getdata()); bp = list(b.getdata())
        count = 0; min_x = min_y = None; max_x = max_y = None
        for idx, (pa, pb) in enumerate(zip(ap, bp)):
            if pa == pb:
                continue
            count += 1; x = idx % w; y = idx // w
            min_x = x if min_x is None or x < min_x else min_x
            min_y = y if min_y is None or y < min_y else min_y
            max_x = x if max_x is None or x > max_x else max_x
            max_y = y if max_y is None or y > max_y else max_y
        return {"pixel_difference_count": int(count), "changed_pixel_bounding_rectangle": ([int(min_x), int(min_y), int(max_x), int(max_y)] if count else None)}
    except Exception as ex:
        return {"pixel_difference_count": None, "changed_pixel_bounding_rectangle": None, "reason": str(ex)}

def _write_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)


def _write_csv(path, variants):
    fields = ["variant", "mutations_requested", "mutations_verified", "off_palette_foreground_pixels", "unexpected_color_count", "assignment_set_changed", "resolved_visible_element_set_changed", "pixel_difference_from_baseline", "pixel_difference_from_full", "rollback_status", "eligible_for_recommended_minimum"]
    with open(path, "w") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for v in variants:
            muts = v.get("mutations", {})
            verified = [mid for mid, rec in muts.items() if rec.get("requested") and rec.get("status") in (STATUS_APPLIED, STATUS_ALREADY)]
            img = v.get("image_analysis", {})
            writer.writerow({
                "variant": v.get("variant"),
                "mutations_requested": ";".join(v.get("requested_mutations", [])),
                "mutations_verified": ";".join(verified),
                "off_palette_foreground_pixels": img.get("off_palette_foreground_pixels"),
                "unexpected_color_count": len(img.get("unexpected_rgb_values") or {}),
                "assignment_set_changed": v.get("visible_set_analysis", {}).get("assignment_set_changed"),
                "resolved_visible_element_set_changed": v.get("visible_set_analysis", {}).get("resolved_visible_element_set_changed"),
                "pixel_difference_from_baseline": img.get("pixel_difference_from_faithful_baseline"),
                "pixel_difference_from_full": img.get("pixel_difference_from_full_suppression"),
                "rollback_status": v.get("rollback_status"),
                "eligible_for_recommended_minimum": v.get("eligible_for_recommended_minimum"),
            })



def _variant_has_semantic_diagnostic_mutation(variant):
    for mid in variant.get("mutations", ()): 
        if MUTATION_CATALOG.get(mid, {}).get("classification") == "semantic_diagnostic":
            return True
    return False


def _collect_assigned_id_set(doc, view, max_count):
    ids, _counts = _collect_assignment_set(doc, view, max_count)
    return set(eid.IntegerValue for eid in ids)

def _run_variant(doc, view, out_dir, base, variant, assigned_ids, assigned, res_report, res_suffix):
    from Autodesk.Revit.DB import Transaction, TransactionGroup, TransactionStatus
    result = {"variant": variant["name"], "requested_mutations": list(variant["mutations"]), "diagnostic": variant.get("diagnostic"), "reference": variant.get("reference"), "recollect": variant.get("recollect"), "mutations": {}, "image_analysis": {}, "visible_set_analysis": {"assignment_set_changed": False, "resolved_visible_element_set_changed": "not_directly_exposed_by_revit_probe", "elements_gained": [], "elements_lost": [], "model_pixel_occupancy_changed": None, "annotation_pixel_occupancy_changed": None}, "state": {}, "transaction_group": {}, "eligible_for_recommended_minimum": False, "rollback_status": "FAIL", "exceptions": []}
    for mid in MUTATION_CATALOG:
        result["mutations"][mid] = mutation_status_record(mid, requested=False)
    group = None
    tx = None
    t0 = time.time()
    tiff_path = os.path.join(out_dir, base + "." + res_suffix + "." + variant["name"] + ".tiff")
    try:
        try:
            from RevitServices.Transactions import TransactionManager
            TransactionManager.Instance.ForceCloseTransaction()
            result["transaction_group"]["dynamo_transaction_force_closed"] = True
        except Exception as ex:
            result["transaction_group"]["dynamo_transaction_force_close_error"] = str(ex)
        result["state"]["before"] = _snapshot(doc, view)
        group = TransactionGroup(doc, "VOP minimum ID mutations: " + variant["name"])
        st = group.Start(); result["transaction_group"]["start_status"] = _safe_enum(st)
        if st != TransactionStatus.Started:
            raise RuntimeError("TransactionGroup.Start returned {0}".format(st))

        remaining = list(variant["mutations"])
        if "detach_template" in remaining:
            tx = Transaction(doc, "Apply detach_template for " + variant["name"]); tx.Start()
            _apply_mutation(doc, view, assigned_ids, result, "detach_template")
            tx.Commit(); tx = None
            result["transaction_group"]["detach_transaction_committed"] = True
            remaining = [m for m in remaining if m != "detach_template"]

        tx = Transaction(doc, "Apply " + variant["name"]); tx.Start()
        for mid in remaining:
            _apply_mutation(doc, view, assigned_ids, result, mid)
        if variant.get("recollect"):
            try:
                before_ids = set(eid.IntegerValue for eid in assigned_ids)
                after_ids = _collect_assigned_id_set(doc, view, None)
                result["visible_set_analysis"].update({
                    "assignment_set_changed": before_ids != after_ids,
                    "resolved_visible_element_set_changed": before_ids != after_ids,
                    "elements_gained": sorted(after_ids - before_ids),
                    "elements_lost": sorted(before_ids - after_ids),
                    "recollection_diagnostic": True,
                })
            except Exception as ex:
                result["visible_set_analysis"]["recollection_error"] = str(ex)
        paint_assigned, failures, step = _paint(doc, view, assigned_ids)
        result["paint_failures"] = failures; result["palette_step"] = step
        tx.Commit(); tx = None; result["transaction_group"]["child_transaction_committed"] = True
        requested_ok = all(result["mutations"].get(mid, {}).get("status") in (STATUS_APPLIED, STATUS_ALREADY) for mid in variant["mutations"])
        result["mutation_attestation_passed"] = bool(requested_ok)
        if requested_ok:
            path, accepted = _export_tiff(doc, view, tiff_path, res_report["accepted_width_px"])
            rr = resolution_report_for_accepted_width(res_report, accepted)
            result["resolution"] = dict(rr)
            result["image_analysis"] = analyze_tiff(path, assigned)
            if result["image_analysis"].get("actual_dimensions"):
                result["resolution"]["actual_width_px"] = result["image_analysis"]["actual_dimensions"][0]
                result["resolution"]["actual_height_px"] = result["image_analysis"]["actual_dimensions"][1]
        else:
            result["image_analysis"] = {"path": tiff_path, "analysis_status": "not_exported_failed_attestation"}
        result["eligible_for_recommended_minimum"] = bool(requested_ok and not variant.get("diagnostic") and not _variant_has_semantic_diagnostic_mutation(variant))
    except Exception as ex:
        result["exceptions"].append({"type": type(ex).__name__, "message": str(ex), "traceback": traceback.format_exc()})
        if tx is not None:
            try:
                tx.RollBack(); result["transaction_group"]["failed_child_transaction_rolled_back"] = True
            except Exception as rb_ex:
                result["transaction_group"]["failed_child_transaction_rollback_error"] = str(rb_ex)
            tx = None
    finally:
        try:
            if group is not None:
                rb = group.RollBack(); result["transaction_group"]["rollback_status"] = _safe_enum(rb); result["rollback_status"] = "PASS"
        except Exception as ex:
            result["rollback_status"] = "FAIL"; result["exceptions"].append({"type": type(ex).__name__, "message": str(ex)})
        try:
            result["state"]["after"] = _snapshot(doc, view)
            result["state"]["captured_state_equal_after_rollback"] = result["state"].get("before") == result["state"].get("after")
        except Exception as ex:
            result["exceptions"].append({"type": type(ex).__name__, "message": str(ex)})
    result["timing_seconds"] = time.time() - t0
    return result


def _select_variants(selection, stage1, stage2):
    variants = stage1 + stage2
    names = _probe_contract().select_named(selection, [variant["name"] for variant in variants], "minimum-ID variant(s)")
    by_name = dict((variant["name"], variant) for variant in variants)
    return [by_name[name] for name in names]


def _run_native(raw_view, output_dir, max_elements=None, selection="all", resolution_policy=DEFAULT_RESOLUTION_POLICY, target_dpi=DEFAULT_TARGET_DPI, fixed_pixel_width=DEFAULT_FIXED_PIXEL_WIDTH, max_pixel_dimension=DEFAULT_MAX_PIXEL_DIMENSION, repo_root=None):
    view = getattr(raw_view, "InternalElement", raw_view)
    doc = getattr(view, "Document", None)
    if doc is None:
        raise RuntimeError("IN[0] must be a Revit view")
    out_dir = str(output_dir)
    if not os.path.isdir(out_dir): os.makedirs(out_dir)
    _ensure_repo_import_path(out_dir, repo_root)
    _load_resolution_contract(repo_root, out_dir)
    base = "{0}_{1}".format(_safe_name(getattr(view, "Name", "view")), _safe_int_id(view.Id))
    assigned_ids, counts = _collect_assignment_set(doc, view, max_elements)
    palette, step = _palette(len(assigned_ids))
    frozen_assignment = {str(eid.IntegerValue): {"rgb": list(palette[i]), "element_id": eid.IntegerValue} for i, eid in enumerate(assigned_ids)}
    chosen_bounds, bounds_source = _current_resolution_bounds(view)
    if chosen_bounds is None:
        raise ValueError("paper-space/fixed resolution requires original model crop or resolved model-only bounds; inactive crop without resolved bounds is INCONCLUSIVE")
    bd = _bounds_dict(chosen_bounds)
    report = {"probe": {"name": PROBE_NAME, "version": PROBE_VERSION, "target": "Revit 2025 / Dynamo 3.3 CPython3"}, "inputs": {"view_id": _safe_int_id(view.Id), "view_name": getattr(view, "Name", None), "output_directory": out_dir, "maximum_host_elements_to_color": max_elements, "variant_selection": selection, "resolution_policy": resolution_policy, "target_dpi": target_dpi, "fixed_pixel_width": fixed_pixel_width, "max_pixel_dimension": max_pixel_dimension, "repository_root": repo_root}, "mutation_inventory": MUTATION_CATALOG, "assignment_set": {"frozen": True, "palette_step": step, "counts": counts, "assigned": frozen_assignment}, "bounds": {"bounds_source": bounds_source, "bounds_uv": bd}, "variants": [], "comparison_contract": {"assignment_set_frozen": True, "recollect_primary_experiment": False, "same_bounds_required": True, "same_resolution_required": True}, "recommendation": {"recommended_minimum": {"mutations": [], "fidelity_status": "INCONCLUSIVE", "semantic_preservation_status": "INCONCLUSIVE", "resolution_status": "INCONCLUSIVE", "rollback_status": "FAIL"}, "required_for_color_fidelity": [], "required_for_model_only_scope": [], "prerequisite_only": [], "unnecessary_in_tested_view": [], "semantic_diagnostics_not_for_default": list(SEMANTIC_DIAGNOSTICS), "blocked_or_unsupported": [], "view_types_still_required": ["floor_plan_active_crop", "floor_plan_inactive_crop", "rcp", "section", "elevation", "detail_view"]}}
    stage1 = generate_stage1_variants(flat_colors_supported=True)
    baseline_mutations = ("detach_template", "hide_annotation_categories", "smooth_edges_off", "display_style_flat_colors")
    stage2 = generate_stage2_variants(baseline_mutations)
    for run_cfg in resolution_runs(resolution_policy, target_dpi, fixed_pixel_width, max_pixel_dimension):
        res = build_resolution_report(run_cfg["policy"], bd["width"], bd["height"], getattr(view, "Scale", None), run_cfg.get("target_dpi"), bounds_source, run_cfg.get("fixed_pixel_width"), run_cfg.get("max_pixel_dimension"))
        res_suffix = resolution_suffix(run_cfg)
        for variant in _select_variants(selection, stage1, stage2):
            vr = _run_variant(doc, view, out_dir, base, variant, assigned_ids, frozen_assignment, res, res_suffix)
            vr["resolution_run"] = run_cfg
            report["variants"].append(vr)

    baseline_hash = baseline_path = None
    full_hash = full_path = None
    for _v in report["variants"]:
        _ia = _v.get("image_analysis") or {}
        if _v.get("variant") in ("detached_ASF", "faithful_baseline", "reduced_candidate") and baseline_hash is None:
            baseline_hash = _ia.get("pixel_data_sha256"); baseline_path = _ia.get("path")
        if _v.get("variant") == "full_suppression_reference":
            full_hash = _ia.get("pixel_data_sha256"); full_path = _ia.get("path")
    for _v in report["variants"]:
        _ia = _v.get("image_analysis") or {}
        if baseline_hash and _ia.get("pixel_data_sha256"):
            cmp = compare_tiff_pixels(_ia.get("path"), baseline_path)
            _ia["pixel_difference_from_faithful_baseline"] = cmp.get("pixel_difference_count")
            _ia["changed_pixel_bounding_rectangle_from_faithful_baseline"] = cmp.get("changed_pixel_bounding_rectangle")
        if full_hash and _ia.get("pixel_data_sha256"):
            cmp = compare_tiff_pixels(_ia.get("path"), full_path)
            _ia["pixel_difference_from_full_suppression"] = cmp.get("pixel_difference_count")
            _ia["changed_pixel_bounding_rectangle_from_full_suppression"] = cmp.get("changed_pixel_bounding_rectangle")
    json_path = os.path.join(out_dir, base + "." + resolution_suffix(resolution_runs(resolution_policy, target_dpi, fixed_pixel_width, max_pixel_dimension)[0]) + ".minimum_id_mutations.json")
    csv_path = os.path.join(out_dir, base + "." + resolution_suffix(resolution_runs(resolution_policy, target_dpi, fixed_pixel_width, max_pixel_dimension)[0]) + ".mutation_matrix.csv")
    _write_json(json_path, report); _write_csv(csv_path, report["variants"])
    report["output_files"] = {"json": json_path, "csv": csv_path}
    return report


def run_probe(raw_view, output_dir, max_elements=None, selection="all",
              resolution_policy=DEFAULT_RESOLUTION_POLICY, target_dpi=DEFAULT_TARGET_DPI,
              fixed_pixel_width=DEFAULT_FIXED_PIXEL_WIDTH,
              max_pixel_dimension=DEFAULT_MAX_PIXEL_DIMENSION, repo_root=None):
    _add_repo_root_to_path(repo_root, output_dir)
    started_at = _probe_contract().utc_now_iso()
    native = _run_native(raw_view, output_dir, max_elements, selection, resolution_policy,
                         target_dpi, fixed_pixel_width, max_pixel_dimension, repo_root)
    variants = native.get("variants", [])
    rollback_ok = bool(variants) and all(item.get("rollback_status") == "PASS" for item in variants)
    restored = bool(variants) and all(item.get("state", {}).get("captured_state_equal_after_rollback") for item in variants)
    artifacts = list((native.get("output_files") or {}).values())
    errors = [error for item in variants for error in item.get("exceptions", [])]
    return _probe_contract().execution_envelope(
        PROBE_NAME, {"max_elements": max_elements, "selection": [item.get("variant") for item in variants],
                     "resolution_policy": resolution_policy, "target_dpi": target_dpi,
                     "fixed_pixel_width": fixed_pixel_width, "max_pixel_dimension": max_pixel_dimension},
        _probe_contract().view_identity(raw_view), native, artifacts, "succeeded" if rollback_ok else "failed",
        "restored" if restored else "not_restored", started_at,
        execution_status="failed" if errors or not rollback_ok or not restored else "completed", errors=errors)


def dynamo_main(inputs):
    return run_probe(inputs[0], inputs[1], inputs[2] if len(inputs) > 2 else None,
                     inputs[3] if len(inputs) > 3 else "all",
                     inputs[4] if len(inputs) > 4 else DEFAULT_RESOLUTION_POLICY,
                     inputs[5] if len(inputs) > 5 else DEFAULT_TARGET_DPI,
                     inputs[6] if len(inputs) > 6 else DEFAULT_FIXED_PIXEL_WIDTH,
                     inputs[7] if len(inputs) > 7 else DEFAULT_MAX_PIXEL_DIMENSION,
                     inputs[8] if len(inputs) > 8 else None)


if "IN" in globals():
    OUT = dynamo_main(IN)
