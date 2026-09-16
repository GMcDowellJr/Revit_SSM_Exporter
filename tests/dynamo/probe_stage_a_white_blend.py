"""
Stage A 0.67-white-blend probe (experiments B1-B3) for Revit 2025 / Dynamo 3.3.

Extraction only. B1 is a strictly read-only interrogation of the view, its
underlay, its phase filter, and each candidate element's overrides, level,
design option, and phase. B3 re-exports the same view with one suspected
mechanism neutralized at a time, inside a TransactionGroup that is always
rolled back. B2 is pure pixel work and lives in
``tools/analyze_stage_a_probe.py --export-metrics``; nothing here inspects a
pixel or decides what the blend is.

Why the read-only pass matters before any re-export: production's paint step
(``color_id_buffer._build_flat_color_ogs``) already calls ``SetHalftone(False)``
and ``SetSurfaceTransparency(0)`` on every painted element, and element
overrides outrank category, filter, and template overrides in Revit's
precedence. So whatever produces a constant-alpha composite over white is
either applied after those overrides resolve, or reaches the element by a path
element overrides do not cover. B1 records which of those paths are configured
on this view at all, so B3 only neutralizes mechanisms the document actually
uses.

Nearly every Revit member this probe touches for underlay and halftone is
UNCONFIRMED against a real run, so they are reached by reflection and reported
as present/absent with whatever they returned, rather than assumed. See
``unconfirmed_api_assumptions`` in the emitted report.

Dynamo inputs:
    IN[0] = target Revit view (SITE PLAN AT LEVEL 4 / 587278 for the B-block)
    IN[1] = output directory
    IN[2] = case selection: "all" or a subset of
            b1_query, b3_baseline, b3_underlay_off, b3_halftone_cleared
                                                          (default "all")
    IN[3] = candidate element ids (list/comma string), or None to use every
            painted element in CANDIDATE_CATEGORY_NAMES  (default None)
    IN[4] = export DPI for the B3 exports                  (default 150.0)
    IN[5] = requested pixel size for the B3 exports, or None for native
                                                          (default None)
    IN[6] = max painted elements, 0/None for no limit      (default None)
    IN[7] = repository root containing vop_interwoven      (default: autodetect)
"""
from __future__ import absolute_import

import json
import os
import time

# The drift probe already owns the repo bootstrap, the production paint step,
# the export defaults, and the export-record shape the analyzer consumes.
# Importing it here is safe by the Stage A probe contract (importing a probe
# module opens no transaction, reads no Dynamo IN, and writes no artifact) and
# keeps both probes emitting byte-identical `exports` records.
try:
    from tests.dynamo.probe_stage_a_drift_onset import (  # noqa: F401
        _add_repo_root_to_path, _collect_host_elements, _diff_state, _ensure_repo_import_path,
        _exception_record, _export_tiff, _force_close_dynamo_transaction, _get_current_document,
        _image_dimensions, _mutate, _paint, _record_export, _safe_int_id, _safe_name,
        _sha256_file, _unwrap_dynamo, native_pixel_size,
    )
    import tests.dynamo.probe_stage_a_drift_onset as _drift
except ImportError:  # pasted Dynamo node: the sibling sits next to this file
    from probe_stage_a_drift_onset import (  # type: ignore # noqa: F401
        _add_repo_root_to_path, _collect_host_elements, _diff_state, _ensure_repo_import_path,
        _exception_record, _export_tiff, _force_close_dynamo_transaction, _get_current_document,
        _image_dimensions, _mutate, _paint, _record_export, _safe_int_id, _safe_name,
        _sha256_file, _unwrap_dynamo, native_pixel_size,
    )
    import probe_stage_a_drift_onset as _drift  # type: ignore


PROBE_NAME = "stage_a_white_blend"
PROBE_VERSION = 1

CASES = ("b1_query", "b3_baseline", "b3_underlay_off", "b3_halftone_cleared")

# The categories the 2026-09-15 run found rendering pastel in SITE PLAN AT
# LEVEL 2-6. Used only to pick candidates when no explicit id list is given;
# it is not a claim about which categories can be affected.
CANDIDATE_CATEGORY_NAMES = ("Walls", "Generic Models", "Roofs", "Doors", "Windows")

# Members this probe looks for but cannot assume exist. Each is reported as
# present/absent with its value, never treated as a fact about the API.
UNDERLAY_VIEW_MEMBERS = ("GetUnderlayBaseLevel", "GetUnderlayTopLevel",
                         "GetUnderlayOrientation", "SetUnderlayRange",
                         "SetUnderlayOrientation")
UNDERLAY_PARAMETER_NAMES = ("VIEW_UNDERLAY_ID", "VIEW_UNDERLAY_BOTTOM_ID",
                            "VIEW_UNDERLAY_TOP_ID")
HALFTONE_DOC_MEMBERS = ("GetHalftoneBrightness", "HalftoneBrightness",
                        "GetUnderlayBrightness", "UnderlayBrightness")
LEVEL_PARAMETER_NAMES = ("LEVEL_PARAM", "FAMILY_LEVEL_PARAM", "SCHEDULE_LEVEL_PARAM",
                         "WALL_BASE_CONSTRAINT", "ROOF_BASE_LEVEL_PARAM",
                         "FAMILY_BASE_LEVEL_PARAM", "INSTANCE_REFERENCE_LEVEL_PARAM")


DEFAULT_EXPORT_DPI = 150.0


def select_cases(selection):
    if selection is None or (isinstance(selection, str) and selection.strip().lower() == "all"):
        return list(CASES)
    requested = ([part.strip() for part in selection.replace(";", ",").split(",") if part.strip()]
                 if isinstance(selection, str) else [str(part).strip() for part in selection])
    unknown = [name for name in requested if name not in CASES]
    if unknown:
        raise ValueError("Unknown case(s): {0}. Supported values: {1}".format(unknown, list(CASES)))
    out = []
    for name in requested:
        if name not in out:
            out.append(name)
    return out


def parse_element_ids(value):
    """Normalize IN[3] into a de-duplicated list of integer element ids."""
    if value is None:
        return None
    if isinstance(value, str):
        parts = [p.strip() for p in value.replace(";", ",").split(",") if p.strip()]
    else:
        parts = list(value)
    out = []
    for part in parts:
        number = int(_safe_int_id(part) if not isinstance(part, int) else part)
        if number not in out:
            out.append(number)
    return out or None


def _reflect(obj, names):
    """Report which of ``names`` exist on ``obj`` and what reading them gives.

    Used wherever the brief marks an API member UNCONFIRMED: an absent member
    is recorded as absent, a member that raises is recorded with the error, and
    nothing is inferred from either.
    """
    found = {}
    for name in names:
        if not hasattr(obj, name):
            found[name] = {"present": False}
            continue
        try:
            value = getattr(obj, name)
            if callable(value):
                try:
                    result = value()
                    found[name] = {"present": True, "callable": True,
                                   "value": _stringify(result)}
                except Exception as ex:
                    found[name] = {"present": True, "callable": True,
                                   "call_error": "{0}: {1}".format(type(ex).__name__, ex)}
            else:
                found[name] = {"present": True, "callable": False, "value": _stringify(value)}
        except Exception as ex:
            found[name] = {"present": True, "read_error": "{0}: {1}".format(type(ex).__name__, ex)}
    return found


def _element_id_int(value):
    """Read an ElementId-like object's integer, or None if it is not one.

    Deliberately has no ``int(value)`` fallback: this decides *whether* a
    value is an id, so anything merely int-convertible (a string, an enum)
    must not qualify. ``Value`` first, each access in its own try, for the
    same Revit 2025 reason as _safe_int_id.
    """
    for attr in ("Value", "IntegerValue"):
        try:
            inner = getattr(value, attr, None)
        except Exception:
            continue
        if inner is None:
            continue
        try:
            return int(inner)
        except (TypeError, ValueError):
            continue
    return None


def _stringify(value):
    if value is None:
        return None
    if isinstance(value, (bool, int, float)):
        return value
    # A second `hasattr(value, "IntegerValue")` here would re-invoke the
    # deprecated getter that _element_id_int already handled, and on a
    # 64-bit-range id that getter can raise -- failing B1 while stringifying
    # a large underlay id despite the safe read having just succeeded.
    as_id = _element_id_int(value)
    if as_id is not None:
        return as_id
    return str(value)


def _parameter_report(element, names):
    from Autodesk.Revit.DB import BuiltInParameter
    out = {}
    for name in names:
        bip = getattr(BuiltInParameter, name, None)
        if bip is None:
            out[name] = {"builtin_parameter_present": False}
            continue
        try:
            param = element.get_Parameter(bip)
        except Exception as ex:
            out[name] = {"builtin_parameter_present": True,
                         "error": "{0}: {1}".format(type(ex).__name__, ex)}
            continue
        if param is None:
            out[name] = {"builtin_parameter_present": True, "on_element": False}
            continue
        record = {"builtin_parameter_present": True, "on_element": True}
        try:
            record["element_id"] = _safe_int_id(param.AsElementId())
        except Exception:
            pass
        try:
            record["as_string"] = param.AsValueString() or param.AsString()
        except Exception:
            pass
        out[name] = record
    return out


def _ogs_report(ogs):
    """The override fields that could plausibly produce a composite."""
    if ogs is None:
        return None
    out = {}
    for name in ("Halftone", "Transparency", "SurfaceTransparency",
                 "ProjectionLineColor", "CutLineColor",
                 "SurfaceForegroundPatternId", "SurfaceForegroundPatternVisible",
                 "CutForegroundPatternId", "IsValidObject"):
        if not hasattr(ogs, name):
            continue
        try:
            value = getattr(ogs, name)
            if name.endswith("Color"):
                out[name] = (None if value is None or not getattr(value, "IsValid", True)
                             else [int(value.Red), int(value.Green), int(value.Blue)])
            else:
                out[name] = _stringify(value)
        except Exception as ex:
            out[name] = "ERROR {0}: {1}".format(type(ex).__name__, ex)
    return out


# --------------------------------------------------------------------------
# B1: read-only interrogation
# --------------------------------------------------------------------------

def _underlay_report(doc, view):
    """Everything this install will tell us about the view's underlay."""
    report = {"view_members": _reflect(view, UNDERLAY_VIEW_MEMBERS),
              "parameters": _parameter_report(view, UNDERLAY_PARAMETER_NAMES),
              "document_members": _reflect(doc, HALFTONE_DOC_MEMBERS),
              "base_level_id": None, "top_level_id": None,
              "underlay_configured": None, "level_ids_in_range": []}
    base = report["view_members"].get("GetUnderlayBaseLevel", {}).get("value")
    top = report["view_members"].get("GetUnderlayTopLevel", {}).get("value")
    if base is None:
        base = report["parameters"].get("VIEW_UNDERLAY_BOTTOM_ID", {}).get("element_id")
    if base is None:
        base = report["parameters"].get("VIEW_UNDERLAY_ID", {}).get("element_id")
    if top is None:
        top = report["parameters"].get("VIEW_UNDERLAY_TOP_ID", {}).get("element_id")
    report["base_level_id"] = base
    report["top_level_id"] = top
    # Revit spells "no underlay" as the invalid ElementId (-1).
    configured = [v for v in (base, top) if isinstance(v, int) and v > 0]
    report["underlay_configured"] = bool(configured) if (base is not None or top is not None) else None

    if report["underlay_configured"]:
        try:
            from Autodesk.Revit.DB import FilteredElementCollector, Level
            levels = []
            for level in FilteredElementCollector(doc).OfClass(Level):
                levels.append({"id": _safe_int_id(level.Id),
                               "name": getattr(level, "Name", None),
                               "elevation": float(getattr(level, "Elevation", 0.0))})
            levels.sort(key=lambda item: item["elevation"])
            ids = [item["id"] for item in levels]
            lo = ids.index(base) if base in ids else None
            hi = ids.index(top) if top in ids else (len(ids) - 1 if lo is not None else None)
            if lo is not None and hi is not None:
                report["level_ids_in_range"] = ids[min(lo, hi):max(lo, hi) + 1]
            report["levels"] = levels
        except Exception as ex:
            report["level_range_error"] = "{0}: {1}".format(type(ex).__name__, ex)
    return report


def _phase_report(doc, view):
    """The view's phase filter and how it presents each phase status."""
    from Autodesk.Revit.DB import BuiltInParameter
    report = {"view_phase_id": None, "phase_filter_id": None,
              "phase_filter_name": None, "phase_status_presentation": {},
              "phase_graphic_overrides": {}}
    try:
        param = view.get_Parameter(BuiltInParameter.VIEW_PHASE)
        report["view_phase_id"] = _safe_int_id(param.AsElementId()) if param else None
    except Exception as ex:
        report["view_phase_error"] = "{0}: {1}".format(type(ex).__name__, ex)
    try:
        param = view.get_Parameter(BuiltInParameter.VIEW_PHASE_FILTER)
        filter_id = _safe_int_id(param.AsElementId()) if param else None
        report["phase_filter_id"] = filter_id
        if filter_id and filter_id > 0:
            phase_filter = doc.GetElement(param.AsElementId())
            report["phase_filter_name"] = getattr(phase_filter, "Name", None)
            from Autodesk.Revit.DB import ElementOnPhaseStatus
            for status_name in ("New", "Existing", "Demolished", "Temporary", "None"):
                status = getattr(ElementOnPhaseStatus, status_name, None)
                if status is None:
                    continue
                try:
                    presentation = phase_filter.GetPhaseStatusPresentation(status)
                    report["phase_status_presentation"][status_name] = str(presentation)
                except Exception as ex:
                    report["phase_status_presentation"][status_name] = \
                        "ERROR {0}: {1}".format(type(ex).__name__, ex)
                try:
                    ogs = view.GetPhaseFilterOverrides(status) \
                        if hasattr(view, "GetPhaseFilterOverrides") else None
                    if ogs is not None:
                        report["phase_graphic_overrides"][status_name] = _ogs_report(ogs)
                except Exception as ex:
                    report["phase_graphic_overrides"][status_name] = \
                        "ERROR {0}: {1}".format(type(ex).__name__, ex)
    except Exception as ex:
        report["phase_filter_error"] = "{0}: {1}".format(type(ex).__name__, ex)
    return report


def _candidate_element_ids(doc, view, painted_ids, explicit_ids):
    """Which elements B1 interrogates, and why each was chosen."""
    if explicit_ids:
        return [eid for eid in painted_ids if _safe_int_id(eid) in set(explicit_ids)], "explicit_ids"
    wanted = set(CANDIDATE_CATEGORY_NAMES)
    chosen = []
    for eid in painted_ids:
        element = doc.GetElement(eid)
        category = getattr(element, "Category", None)
        if category is not None and getattr(category, "Name", None) in wanted:
            chosen.append(eid)
    return chosen, "candidate_categories"


def _element_report(doc, view, element_id, underlay):
    element = doc.GetElement(element_id)
    record = {"element_id": _safe_int_id(element_id), "category": None, "level_id": None,
              "level_name": None, "in_underlay_range": None,
              "element_overrides": None, "category_overrides": None,
              "category_hidden": None, "design_option_id": None, "design_option_name": None,
              "phase_created_id": None, "phase_demolished_id": None,
              "level_parameters": {}}
    try:
        category = getattr(element, "Category", None)
        record["category"] = getattr(category, "Name", None)
        cat_id = getattr(category, "Id", None)
        if cat_id is not None:
            try:
                record["category_overrides"] = _ogs_report(view.GetCategoryOverrides(cat_id))
            except Exception as ex:
                record["category_overrides"] = "ERROR {0}: {1}".format(type(ex).__name__, ex)
            try:
                record["category_hidden"] = bool(view.GetCategoryHidden(cat_id))
            except Exception:
                pass
    except Exception as ex:
        record["category_error"] = "{0}: {1}".format(type(ex).__name__, ex)
    try:
        record["element_overrides"] = _ogs_report(view.GetElementOverrides(element_id))
    except Exception as ex:
        record["element_overrides"] = "ERROR {0}: {1}".format(type(ex).__name__, ex)
    try:
        level_id = _safe_int_id(getattr(element, "LevelId", None))
        record["level_parameters"] = _parameter_report(element, LEVEL_PARAMETER_NAMES)
        if level_id is None or level_id <= 0:
            for detail in record["level_parameters"].values():
                candidate = detail.get("element_id") if isinstance(detail, dict) else None
                if candidate and candidate > 0:
                    level_id = candidate
                    break
        record["level_id"] = level_id
        if level_id and level_id > 0:
            from Autodesk.Revit.DB import ElementId
            level = doc.GetElement(ElementId(int(level_id)))
            record["level_name"] = getattr(level, "Name", None)
            in_range = underlay.get("level_ids_in_range") or []
            record["in_underlay_range"] = (level_id in in_range) if in_range else None
    except Exception as ex:
        record["level_error"] = "{0}: {1}".format(type(ex).__name__, ex)
    try:
        option = getattr(element, "DesignOption", None)
        record["design_option_id"] = _safe_int_id(getattr(option, "Id", None))
        record["design_option_name"] = getattr(option, "Name", None)
    except Exception as ex:
        record["design_option_error"] = "{0}: {1}".format(type(ex).__name__, ex)
    record.update({k: v for k, v in _phase_parameters(element).items()})
    return record


def _phase_parameters(element):
    from Autodesk.Revit.DB import BuiltInParameter
    out = {"phase_created_id": None, "phase_demolished_id": None}
    for key, name in (("phase_created_id", "PHASE_CREATED"),
                      ("phase_demolished_id", "PHASE_DEMOLISHED")):
        bip = getattr(BuiltInParameter, name, None)
        if bip is None:
            continue
        try:
            param = element.get_Parameter(bip)
            if param is not None:
                out[key] = _safe_int_id(param.AsElementId())
        except Exception:
            continue
    return out


def _summarize_b1(underlay, elements):
    """What B1 found configured -- not what it means."""
    with_element_halftone = [
        rec["element_id"] for rec in elements
        if isinstance(rec.get("element_overrides"), dict)
        and rec["element_overrides"].get("Halftone") is True]
    with_category_halftone = [
        rec["element_id"] for rec in elements
        if isinstance(rec.get("category_overrides"), dict)
        and rec["category_overrides"].get("Halftone") is True]
    with_transparency = [
        rec["element_id"] for rec in elements
        if isinstance(rec.get("element_overrides"), dict)
        and (rec["element_overrides"].get("SurfaceTransparency") or 0)]
    in_underlay = [rec["element_id"] for rec in elements if rec.get("in_underlay_range")]
    return {
        "candidate_count": len(elements),
        "underlay_configured": underlay.get("underlay_configured"),
        "elements_on_an_underlay_level": in_underlay,
        "elements_with_element_halftone": with_element_halftone,
        "elements_with_category_halftone": with_category_halftone,
        "elements_with_surface_transparency": with_transparency,
        "distinct_levels": sorted({rec.get("level_name") for rec in elements
                                   if rec.get("level_name")}),
        "distinct_design_options": sorted({rec.get("design_option_name") for rec in elements
                                           if rec.get("design_option_name")}),
        # B3 gating, stated as what B1 observed rather than as a diagnosis.
        "underlay_off_is_warranted": bool(underlay.get("underlay_configured")),
        "halftone_clear_is_warranted": bool(with_element_halftone or with_category_halftone),
    }


# --------------------------------------------------------------------------
# B3: neutralize one mechanism at a time and re-export
# --------------------------------------------------------------------------

def _clear_underlay(view):
    from Autodesk.Revit.DB import ElementId
    if hasattr(view, "SetUnderlayRange"):
        view.SetUnderlayRange(ElementId.InvalidElementId, ElementId.InvalidElementId)
        return "SetUnderlayRange"
    from Autodesk.Revit.DB import BuiltInParameter
    for name in UNDERLAY_PARAMETER_NAMES:
        bip = getattr(BuiltInParameter, name, None)
        if bip is None:
            continue
        param = view.get_Parameter(bip)
        if param is not None and not param.IsReadOnly:
            param.Set(ElementId.InvalidElementId)
            return name
    raise RuntimeError(
        "No writable underlay API on this install: neither ViewPlan.SetUnderlayRange nor "
        "a settable {0} parameter".format("/".join(UNDERLAY_PARAMETER_NAMES)))


class _NoExportCasesRequested(Exception):
    """Only ``b1_query`` was selected. B1 is read-only, so there is nothing to
    paint, normalize, export, or roll back -- not an error."""


def _halftone_already_neutralized(normalization):
    """True when this probe's own normalization already cleared halftone.

    Production clears element halftone in its paint step and category halftone
    in its capture setup. When the normalization applied here reproduces that,
    a `halftone_cleared` variant would differ from the baseline in nothing at
    all, and exporting it would spend a full-resolution TIFF to re-measure the
    baseline.
    """
    record = (normalization or {}).get("mutations", {}).get("category_halftone_neutralized")
    return (record or {}).get("status") in ("APPLIED", "ALREADY_MATCHED")


def _set_underlay(view, base_id, top_id):
    """Put the underlay range back exactly as B1 found it.

    Each B3 variant has to be the *only* difference from the baseline export.
    Every mutation here commits into the enclosing TransactionGroup, so
    without an explicit restore the underlay stays off for whatever variant
    runs next and its TIFF carries two changes at once -- which is precisely
    what makes a one-mechanism-at-a-time experiment unattributable.
    """
    from Autodesk.Revit.DB import ElementId
    base = ElementId(int(base_id)) if base_id and int(base_id) > 0 else ElementId.InvalidElementId
    top = ElementId(int(top_id)) if top_id and int(top_id) > 0 else ElementId.InvalidElementId
    if hasattr(view, "SetUnderlayRange"):
        view.SetUnderlayRange(base, top)
        return "SetUnderlayRange"
    from Autodesk.Revit.DB import BuiltInParameter
    for name, value in (("VIEW_UNDERLAY_BOTTOM_ID", base), ("VIEW_UNDERLAY_TOP_ID", top),
                        ("VIEW_UNDERLAY_ID", base)):
        bip = getattr(BuiltInParameter, name, None)
        if bip is None:
            continue
        param = view.get_Parameter(bip)
        if param is not None and not param.IsReadOnly:
            param.Set(value)
    return "parameters"


def _category_override_state(view, categories):
    """Snapshot category overrides so a B3 variant can be undone exactly."""
    state = []
    for cat_id, cat_name in categories:
        try:
            state.append((cat_id, cat_name, view.GetCategoryOverrides(cat_id)))
        except Exception:
            continue
    return state


def _restore_category_overrides(view, state):
    restored = []
    for cat_id, cat_name, ogs in state:
        if ogs is None:
            continue
        try:
            view.SetCategoryOverrides(cat_id, ogs)
            restored.append(cat_name)
        except Exception:
            continue
    return restored


def _clear_halftone(doc, view, element_ids, categories):
    """Force Halftone off at the element AND category level.

    Production's paint step already sets element halftone off, so this is only
    meaningful for the category/template layer -- ``already_off_at_element``
    records how many elements were already clear, so a null result here is not
    mistaken for the mechanism having been neutralized.
    """
    from Autodesk.Revit.DB import OverrideGraphicSettings
    already_off = 0
    for eid in element_ids:
        try:
            ogs = view.GetElementOverrides(eid)
        except Exception:
            continue
        if ogs is None:
            continue
        if getattr(ogs, "Halftone", False) is False:
            already_off += 1
        ogs.SetHalftone(False)
        ogs.SetSurfaceTransparency(0)
        view.SetElementOverrides(eid, ogs)
    cleared_categories = []
    for cat_id, cat_name in categories:
        try:
            ogs = view.GetCategoryOverrides(cat_id) or OverrideGraphicSettings()
            ogs.SetHalftone(False)
            ogs.SetSurfaceTransparency(0)
            view.SetCategoryOverrides(cat_id, ogs)
            cleared_categories.append(cat_name)
        except Exception:
            continue
    return {"already_off_at_element": already_off,
            "elements_touched": len(element_ids),
            "categories_cleared": cleared_categories}


# --------------------------------------------------------------------------
# Native driver
# --------------------------------------------------------------------------

def _run_native(raw_view, output_dir, selection="all", element_ids=None,
                export_dpi=DEFAULT_EXPORT_DPI, pixel_size=None, max_elements=None,
                repo_root=None):
    report = {
        "probe": {"name": PROBE_NAME, "version": PROBE_VERSION,
                  "target": "Revit 2025 / Dynamo 3.3 CPython3"},
        "inputs": {}, "view": {}, "paint": {}, "cases": {},
        "exports": [], "color_assignment_map": {},
        "transaction_group": {"started": False, "rollback_attempted": False,
                              "rollback_succeeded": False},
        "state": {"before": {}, "after": {}, "restored": None, "differences": []},
        "unconfirmed_api_assumptions": [
            "ViewPlan.GetUnderlayBaseLevel / GetUnderlayTopLevel / GetUnderlayOrientation exist",
            "ViewPlan.SetUnderlayRange(InvalidElementId, InvalidElementId) disables the underlay",
            "BuiltInParameter.VIEW_UNDERLAY_ID / _BOTTOM_ID / _TOP_ID are the pre-2018 fallback",
            "A document-level halftone/underlay brightness is reachable from the API at all",
            "View.GetPhaseFilterOverrides exists and returns the phase graphic override",
            "Underlay graphics compose over the element's resolved override color rather than replacing it",
        ],
        "exceptions": [], "timings_ms": {},
    }
    doc = _get_current_document()
    group = None
    group_started = False
    try:
        _ensure_repo_import_path(output_dir, repo_root)
        view = _unwrap_dynamo(raw_view)
        if view is None:
            raise ValueError("IN[0] did not resolve to a Revit view")
        cases = select_cases(selection)
        explicit_ids = parse_element_ids(element_ids)
        out_base = os.path.abspath(os.path.expanduser(str(output_dir)))
        out_dir = os.path.join(out_base, "white_blend_probe")
        if not os.path.isdir(out_dir):
            os.makedirs(out_dir)

        view_scale = float(getattr(view, "Scale", 1) or 1)
        export_dpi = float(export_dpi)
        bounds_xy, bounds_source = _drift._resolution_bounds(view)
        native_w, native_h = native_pixel_size(bounds_xy, export_dpi, view_scale)
        requested_px = int(pixel_size) if pixel_size else int(round(native_w))
        base = "{0}.{1}".format(_safe_name(getattr(view, "Name", "view")), _safe_int_id(view.Id))

        report["inputs"] = {"output_directory": out_base, "cases": cases,
                            "explicit_element_ids": explicit_ids, "export_dpi": export_dpi,
                            "requested_pixel_size": requested_px,
                            "max_elements": max_elements}
        report["view"] = {"id": _safe_int_id(view.Id), "name": getattr(view, "Name", None),
                          "scale": view_scale, "bounds_xy": list(bounds_xy),
                          "bounds_source": bounds_source,
                          "native_width_px": native_w, "native_height_px": native_h,
                          "view_type": str(getattr(view, "ViewType", None)),
                          "view_template_id": _safe_int_id(getattr(view, "ViewTemplateId", None))}

        _force_close_dynamo_transaction()
        report["state"]["before"] = _drift._snapshot(doc, view)

        painted_ids, collect_stats = _collect_host_elements(doc, view, max_elements)
        candidates, candidate_source = _candidate_element_ids(
            doc, view, painted_ids, explicit_ids)

        # B1 runs FIRST, on the untouched document, and outside the
        # TransactionGroup entirely. It has to report the view's *authored*
        # state: the paint step writes an OverrideGraphicSettings with
        # Halftone off and SurfaceTransparency 0 onto every painted element,
        # so a B1 run after painting would read its own overrides back and
        # report "no element halftone anywhere" for every document, making the
        # halftone gate structurally incapable of ever firing.
        b1 = None
        if "b1_query" in cases:
            started = time.time()
            try:
                underlay = _underlay_report(doc, view)
                elements = [_element_report(doc, view, eid, underlay) for eid in candidates]
                b1 = {"candidate_source": candidate_source,
                      "read_before_any_mutation": True,
                      "underlay": underlay,
                      "phase": _phase_report(doc, view),
                      "elements": elements,
                      "summary": _summarize_b1(underlay, elements)}
                report["cases"]["b1_query"] = b1
            except Exception as ex:
                report["exceptions"].append(_exception_record("b1_query", ex))
                report["cases"]["b1_query"] = {"failed": True, "type": type(ex).__name__,
                                               "message": str(ex)}
            report["timings_ms"]["b1_query"] = int(round((time.time() - started) * 1000.0))

        export_cases = [case for case in cases if case != "b1_query"]
        if not export_cases:
            raise _NoExportCasesRequested()

        from Autodesk.Revit.DB import TransactionGroup, TransactionStatus
        group = TransactionGroup(doc, "VOP Stage A White Blend Probe")
        group_started = group.Start() == TransactionStatus.Started
        report["transaction_group"]["started"] = group_started
        if not group_started:
            raise RuntimeError("TransactionGroup.Start did not start")

        paint_result = {}

        def do_paint():
            color_map, failures, step = _paint(doc, view, painted_ids)
            paint_result.update({"color_map": color_map, "failures": failures, "step": step})
        _mutate(doc, "paint", do_paint)
        report["paint"] = {"collection": collect_stats,
                           "palette_step": paint_result.get("step"),
                           "paint_failures": paint_result.get("failures", [])}
        report["color_assignment_map"] = paint_result.get("color_map", {})

        # The B3 exports must start from production's export state, or the
        # baseline is not the capture the 0.67 blend was observed in.
        normalization = {}
        _mutate(doc, "normalize_view_state",
                lambda: normalization.update(
                    _drift._normalize_view_state(doc, view, painted_ids)))
        shortfall = _drift.normalization_shortfall(normalization.get("mutations"))
        report["view_state_normalization"] = {
            "requested": list(_drift.PRODUCTION_SUPPRESSION_MUTATIONS),
            "mutations": normalization.get("mutations", {}),
            "not_in_production_state": shortfall,
            "matches_production_capture_state": not shortfall,
        }
        if shortfall:
            report.setdefault("warnings", []).append({
                "stage": "normalize_view_state",
                "message": "B3 exports are NOT in production's export state; {0} did not "
                           "apply.".format(", ".join(shortfall)),
            })

        # Production crops the view to these bounds before exporting. Without
        # it, FitToPage fits whatever the view shows and the B3 baseline is
        # not the capture the 0.67 blend was observed in.
        cropped = {}
        _mutate(doc, "apply_production_crop",
                lambda: cropped.__setitem__(
                    "bounds", _drift.apply_production_crop(view, bounds_xy)))
        crop_bounds = cropped.get("bounds")
        report["view"]["crop_applied"] = crop_bounds is not None
        report["view"]["crop_bounds_xy"] = list(crop_bounds) if crop_bounds else None
        if crop_bounds is None:
            report.setdefault("warnings", []).append({
                "stage": "apply_production_crop",
                "message": "This view has no CropBox, so the B3 exports fall back to "
                           "FitToPage's auto-computed extent and bounds_xy is null.",
            })

        def export(label, extra=None):
            return _record_export(doc, view, out_dir, base, "b3", label, requested_px,
                                  crop_bounds, export_dpi, view_scale, extra=extra)

        for case in export_cases:
            started = time.time()
            try:
                if case == "b3_baseline":
                    record = export("baseline")
                    report["exports"].append(record)
                    report["cases"][case] = {"tiff_path": record["tiff_path"]}
                elif case == "b3_underlay_off":
                    warranted = bool(b1 and b1["summary"].get("underlay_off_is_warranted"))
                    if b1 is None:
                        report["cases"][case] = {
                            "skipped": "b1_query was not run, so nothing established that this "
                                       "view uses an underlay"}
                    elif not warranted:
                        report["cases"][case] = {
                            "skipped": "B1 found no underlay configured on this view",
                            "underlay": b1["underlay"].get("underlay_configured")}
                    else:
                        original = (b1["underlay"].get("base_level_id"),
                                    b1["underlay"].get("top_level_id"))
                        used = {}
                        _mutate(doc, "underlay_off",
                                lambda: used.setdefault("api", _clear_underlay(view)))
                        try:
                            record = export("underlay_off",
                                            extra={"mechanism": "underlay_off"})
                            report["exports"].append(record)
                            report["cases"][case] = {"tiff_path": record["tiff_path"],
                                                     "api_used": used.get("api")}
                        finally:
                            # Restore before the next variant runs, so its TIFF
                            # differs from the baseline in one mechanism only.
                            _mutate(doc, "underlay_restore",
                                    lambda: used.setdefault(
                                        "restore_api", _set_underlay(view, *original)))
                            report["cases"][case] = dict(
                                report["cases"].get(case) or {},
                                restored_underlay=list(original),
                                restore_api=used.get("restore_api"))
                elif case == "b3_halftone_cleared":
                    warranted = bool(b1 and b1["summary"].get("halftone_clear_is_warranted"))
                    if b1 is None:
                        report["cases"][case] = {
                            "skipped": "b1_query was not run, so nothing established that "
                                       "halftone is set anywhere on this view"}
                    elif not warranted:
                        report["cases"][case] = {
                            "skipped": "B1 found Halftone set on no candidate element or "
                                       "category; production's paint step already clears it "
                                       "per element",
                            "elements_with_element_halftone": b1["summary"]["elements_with_element_halftone"],
                            "elements_with_category_halftone": b1["summary"]["elements_with_category_halftone"]}
                    elif _halftone_already_neutralized(normalization):
                        # Production's own normalization clears category
                        # halftone and its paint step clears element halftone,
                        # so re-clearing them here would produce a TIFF
                        # identical to the baseline. That identity is the
                        # answer to B-H1, established without an export:
                        # halftone cannot be what survives into a production
                        # capture.
                        report["cases"][case] = {
                            "skipped": "redundant: the production normalization this probe "
                                       "applies already neutralized category halftone, and "
                                       "the paint step already cleared element halftone, so "
                                       "this variant would re-export the baseline unchanged",
                            "category_halftone_neutralized": (
                                normalization.get("mutations", {})
                                .get("category_halftone_neutralized")),
                            "authored_state_had_halftone": {
                                "elements": b1["summary"]["elements_with_element_halftone"],
                                "categories": b1["summary"]["elements_with_category_halftone"]},
                        }
                    else:
                        categories = _candidate_categories(doc, view, candidates)
                        before = _category_override_state(view, categories)
                        detail = {}
                        _mutate(doc, "halftone_cleared",
                                lambda: detail.update(
                                    _clear_halftone(doc, view, candidates, categories)))
                        try:
                            record = export("halftone_cleared",
                                            extra={"mechanism": "halftone_cleared"})
                            report["exports"].append(record)
                            report["cases"][case] = dict(detail, tiff_path=record["tiff_path"])
                        finally:
                            restored = {}
                            _mutate(doc, "halftone_restore",
                                    lambda: restored.setdefault(
                                        "categories",
                                        _restore_category_overrides(view, before)))
                            report["cases"][case] = dict(
                                report["cases"].get(case) or {},
                                restored_categories=restored.get("categories"))
            except Exception as ex:
                report["exceptions"].append(_exception_record(case, ex))
                report["cases"][case] = {"failed": True, "type": type(ex).__name__,
                                         "message": str(ex)}
            report["timings_ms"][case] = int(round((time.time() - started) * 1000.0))
    except _NoExportCasesRequested:
        # b1_query alone: read-only, so no TransactionGroup was ever started
        # and there is nothing to restore.
        report["b1_only_run"] = True
    except Exception as ex:
        report["exceptions"].append(_exception_record("setup_or_outer", ex))
    finally:
        if group_started and group is not None:
            report["transaction_group"]["rollback_attempted"] = True
            try:
                from Autodesk.Revit.DB import TransactionStatus
                status = group.RollBack()
                report["transaction_group"]["rollback_status"] = str(status)
                report["transaction_group"]["rollback_succeeded"] = \
                    status == TransactionStatus.RolledBack
            except Exception as ex:
                report["exceptions"].append(_exception_record("transaction_group_rollback", ex))
        try:
            view = _unwrap_dynamo(raw_view)
            if view is not None and report["state"]["before"]:
                report["state"]["after"] = _drift._snapshot(doc, view)
                diffs = _diff_state(report["state"]["before"], report["state"]["after"])
                report["state"]["differences"] = diffs
                report["state"]["restored"] = len(diffs) == 0
        except Exception as ex:
            report["exceptions"].append(_exception_record("post_rollback_state_capture", ex))
        try:
            out_base = os.path.abspath(os.path.expanduser(str(output_dir)))
            out_dir = os.path.join(out_base, "white_blend_probe")
            if not os.path.isdir(out_dir):
                os.makedirs(out_dir)
            json_path = os.path.join(out_dir, "{0}.{1}.white_blend.json".format(
                _safe_name(report["view"].get("name") or "view"), report["view"].get("id")))
            with open(json_path, "w") as handle:
                json.dump(report, handle, indent=2, sort_keys=True)
            report["json_report_path"] = json_path
        except Exception as ex:
            report["exceptions"].append(_exception_record("json_report_write", ex))
    return report


def _candidate_categories(doc, view, element_ids):
    seen, out = set(), []
    for eid in element_ids:
        element = doc.GetElement(eid)
        category = getattr(element, "Category", None)
        cat_id = getattr(category, "Id", None)
        key = _safe_int_id(cat_id)
        if cat_id is None or key in seen:
            continue
        seen.add(key)
        out.append((cat_id, getattr(category, "Name", None)))
    return out


def run_probe(raw_view, output_dir, selection="all", element_ids=None,
              export_dpi=DEFAULT_EXPORT_DPI, pixel_size=None, max_elements=None,
              repo_root=None):
    contract = _drift._probe_contract(output_dir, repo_root)
    started_at = contract.utc_now_iso()
    native = _run_native(raw_view, output_dir, selection, element_ids, export_dpi,
                         pixel_size, max_elements, repo_root)
    read_only = bool(native.get("b1_only_run"))
    rollback_ok = bool(native["transaction_group"].get("rollback_succeeded"))
    restored = native["state"].get("restored")
    artifacts = [rec["tiff_path"] for rec in native.get("exports", []) if rec.get("tiff_path")]
    if native.get("json_report_path"):
        artifacts.append(native["json_report_path"])
    errors = list(native.get("exceptions", []))
    ran_something = bool(native.get("exports")) or "b1_query" in native.get("cases", {})
    # A read-only run never opens a TransactionGroup, so an absent rollback is
    # the correct outcome for it, not a failed one.
    if errors or not (rollback_ok or read_only):
        status = "failed"
    elif not ran_something:
        status = "inconclusive"
    else:
        status = "completed"
    if read_only:
        rollback_status, restoration_status = "not_started", "not_checked"
    else:
        rollback_status = ("succeeded" if rollback_ok
                           else ("failed" if native["transaction_group"].get("rollback_attempted")
                                 else "not_started"))
        restoration_status = ("restored" if restored
                              else ("not_restored" if restored is False else "not_checked"))
    return contract.execution_envelope(
        PROBE_NAME,
        {"selection": selection, "element_ids": element_ids, "export_dpi": export_dpi,
         "pixel_size": pixel_size, "max_elements": max_elements},
        contract.view_identity(raw_view), native, artifacts,
        rollback_status, restoration_status,
        started_at, execution_status=status, errors=errors,
        warnings=list(native.get("warnings", [])))


def dynamo_main(inputs):
    def at(index, default=None):
        value = inputs[index] if len(inputs) > index else None
        return default if value is None else value
    return run_probe(
        inputs[0], inputs[1],
        selection=at(2, "all"),
        element_ids=at(3),
        export_dpi=float(at(4, DEFAULT_EXPORT_DPI)),
        pixel_size=at(5),
        max_elements=at(6),
        repo_root=at(7),
    )


if "IN" in globals():
    try:
        OUT = dynamo_main(IN)  # noqa: F821  (Dynamo injects IN)
    except Exception as fatal:
        OUT = {"probe_id": PROBE_NAME, "execution_status": "failed",
               "fatal_exception": _exception_record("dynamo_entrypoint", fatal)}
