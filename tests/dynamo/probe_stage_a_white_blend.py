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
        _exception_record, _force_close_dynamo_transaction, _get_current_document,
        _mutate, _safe_int_id, _safe_name, _set_view_crop, _unwrap_dynamo,
        build_capture_config, plan_capture_geometry, production_capture,
    )
    import tests.dynamo.probe_stage_a_drift_onset as _drift
except ImportError:  # pasted Dynamo node: the sibling sits next to this file
    from probe_stage_a_drift_onset import (  # type: ignore # noqa: F401
        _exception_record, _force_close_dynamo_transaction, _get_current_document,
        _mutate, _safe_int_id, _safe_name, _set_view_crop, _unwrap_dynamo,
        build_capture_config, plan_capture_geometry, production_capture,
    )
    import probe_stage_a_drift_onset as _drift  # type: ignore


PROBE_NAME = "stage_a_white_blend"
PROBE_VERSION = 1

# See probe_stage_a_drift_onset.source_record. This probe takes its captures
# through that module's production_capture, so BOTH have to be current: on
# 2026-09-16 this module was up to date and the one it calls was not, and
# nothing in the output said so.
MODULE_FILE = os.path.abspath(__file__) if "__file__" in globals() else None
MODULE_MTIME_AT_IMPORT = _drift._file_mtime(MODULE_FILE)


def source_records():
    import sys
    return [_drift.source_record(sys.modules[__name__]),
            _drift.source_record(_drift)]


CASES = ("b1_query", "b3_baseline", "b3_underlay_off", "b3_halftone_cleared")
# Variants that only run when B1 found the mechanism present in the AUTHORED
# view. B1's result lives in a local of _run_native and does not survive
# across invocations, so a selection that omits b1_query turns each of these
# into a skip -- and a b3_baseline in the same selection is enough for the run
# to be reported completed, so resume never retries the captures that were
# quietly dropped. That is how a campaign can run to green having never taken
# the one export its leading hypothesis rests on.
B1_GATED_CASES = ("b3_underlay_off", "b3_halftone_cleared")

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
    gated = [name for name in out if name in B1_GATED_CASES]
    if gated and "b1_query" not in out:
        raise ValueError(
            "{0} require(s) b1_query in the same selection: each is applied only where B1 "
            "found the mechanism present in the authored view, and B1 is read before any "
            "mutation within a single run. Without it these cases are skipped, not run. "
            "Requested: {1}".format(", ".join(gated), out))
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
        # Retrieved ONCE, inside the handler. hasattr() invokes the getter and
        # only swallows AttributeError, so a .NET property that exists but
        # throws for this view or document would propagate out and abort the
        # whole B1 query -- the opposite of this function's promise. Absence
        # is AttributeError specifically; anything else is a read_error on a
        # member that is present.
        try:
            value = getattr(obj, name)
        except AttributeError:
            found[name] = {"present": False}
            continue
        except Exception as ex:
            found[name] = {"present": True,
                           "read_error": "{0}: {1}".format(type(ex).__name__, ex)}
            continue
        if callable(value):
            try:
                result = value()
            except Exception as ex:
                found[name] = {"present": True, "callable": True,
                               "call_error": "{0}: {1}".format(type(ex).__name__, ex)}
                continue
            found[name] = {"present": True, "callable": True, "value": _stringify(result)}
            continue
        try:
            found[name] = {"present": True, "callable": False, "value": _stringify(value)}
        except Exception as ex:
            found[name] = {"present": True, "callable": False,
                           "read_error": "{0}: {1}".format(type(ex).__name__, ex)}
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
        except Exception as ex:
            # This id is the only fallback _underlay_report has when the
            # reflected GetUnderlay* members are absent. Swallowing the failure
            # reports the underlay as "not configured" on a view that has one,
            # and b3_underlay_off then skips itself for a reason that is not
            # true -- the experiment lost to a silent except.
            record["element_id_error"] = "{0}: {1}".format(type(ex).__name__, ex)
        try:
            record["as_string"] = param.AsValueString() or param.AsString()
        except Exception as ex:
            record["as_string_error"] = "{0}: {1}".format(type(ex).__name__, ex)
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
        # Retrieved ONCE, inside the handler. hasattr() invokes the getter and
        # swallows only AttributeError, so a property that exists and throws
        # propagated out and the caller discarded the whole override report --
        # taking a readable Halftone=True on another property with it, which
        # is the value the B3 halftone gate turns on. Same defect as _reflect
        # and _stringify, in the third place it appears.
        try:
            value = getattr(ogs, name)
        except AttributeError:
            continue
        except Exception as ex:
            out[name] = "ERROR {0}: {1}".format(type(ex).__name__, ex)
            continue
        try:
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
    # A parameter that exists and threw is not evidence of "no underlay". If
    # the only fallback id could not be read, the answer is unknown -- and
    # unknown has to warrant the experiment, the same asymmetry the halftone
    # gate uses: a redundant export costs disk, a wrongly skipped one costs
    # the answer.
    report["read_errors"] = sorted(
        name for name, record in (report.get("parameters") or {}).items()
        if isinstance(record, dict) and ("element_id_error" in record or "error" in record))
    report["underlay_configured"] = (
        bool(configured) if (base is not None or top is not None)
        else (None if report["read_errors"] else False))

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


def _candidate_element_ids(doc, view, explicit_ids):
    """Which elements B1 interrogates, and why each was chosen.

    A pure read of the untouched view: B1 runs before any capture, so there is
    no painted set to draw from and nothing here may mutate the document.
    """
    from Autodesk.Revit.DB import FilteredElementCollector
    collector = FilteredElementCollector(doc, view.Id).WhereElementIsNotElementType()
    if explicit_ids:
        wanted_ids = set(explicit_ids)
        chosen = [element.Id for element in collector
                  if _safe_int_id(getattr(element, "Id", None)) in wanted_ids]
        return chosen, "explicit_ids"
    wanted = set(CANDIDATE_CATEGORY_NAMES)
    chosen = []
    for element in collector:
        category = getattr(element, "Category", None)
        if category is not None and getattr(category, "Name", None) in wanted:
            chosen.append(element.Id)
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
        # None means unreadable, which warrants the variant; only an explicit
        # False (nothing configured, nothing failed) skips it.
        "underlay_off_is_warranted": underlay.get("underlay_configured") is not False,
        "underlay_read_errors": list(underlay.get("read_errors") or []),
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


def production_cleared_category_halftone(diagnostics, captures_taken):
    """True only when a capture actually ran AND its halftone step did not error.

    export_color_id_buffer_view clears category halftone before painting and
    sets Halftone(False) on every painted element, so a baseline capture is
    normally already halftone-free and a `halftone_cleared` variant would
    reproduce it exactly. But that step is guarded and only warns on failure,
    so a capture CAN reach export with category halftone still set -- and then
    the variant is not redundant at all. Production's own diagnostics are the
    only way to tell the two apart.

    ``captures_taken`` is why this takes two arguments. With a selection like
    ``b1_query,b3_halftone_cleared`` no capture has run when this is consulted,
    so the diagnostics are empty -- and reading that emptiness as "no error, so
    production cleared it" would skip the only export the user asked for, on
    evidence that does not exist. Absence of a capture is unknown, not success,
    and unknown runs the variant: a redundant export costs disk, a wrongly
    skipped one costs the answer.

    ``diagnostics`` is a Diagnostics.to_dict() payload. When a capture ran but
    its diagnostics cannot be read, production's normal behaviour is assumed
    rather than guessed against.
    """
    if not captures_taken:
        return False
    events = (diagnostics or {}).get("events")
    if not isinstance(events, list):
        return True
    return not any((event or {}).get("callsite") == "category_halftone"
                   for event in events if isinstance(event, dict))


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


def _clear_halftone(doc, view, element_ids, categories, diag=None):
    """Force Halftone off at the element AND category level.

    Production's paint step already sets element halftone off, so this is only
    meaningful for the category/template layer -- ``already_off_at_element``
    records how many elements were already clear, so a null result here is not
    mistaken for the mechanism having been neutralized.

    A category or element that refuses its override is NOT skipped quietly.
    This variant exists to answer "does the blend survive with halftone off",
    and it only answers that if halftone is actually off everywhere it was
    asked to be; exporting a capture labelled ``halftone_cleared`` with
    halftone still active on some category would make the comparison say the
    opposite of what it appears to. Failures are recorded in Diagnostics and
    returned, and the caller marks the variant inconclusive rather than
    publishing it -- the repository's no-silent-failure rule, applied to an
    experiment rather than to the pipeline.
    """
    already_off = 0
    failures = []

    def _record(kind, identity, error):
        failures.append({"kind": kind, "identity": identity,
                         "type": type(error).__name__, "message": str(error)})
        if diag is not None:
            try:
                diag.error(phase="white_blend_probe", callsite="clear_halftone",
                           message="Could not clear halftone on {0} {1}: {2}".format(
                               kind, identity, error),
                           exc=error)
            except Exception:
                pass

    for eid in element_ids:
        try:
            ogs = view.GetElementOverrides(eid)
            if ogs is None:
                continue
            if getattr(ogs, "Halftone", False) is False:
                already_off += 1
            ogs.SetHalftone(False)
            ogs.SetSurfaceTransparency(0)
            view.SetElementOverrides(eid, ogs)
        except Exception as ex:
            _record("element", _safe_int_id(eid), ex)
    cleared_categories = []
    for cat_id, cat_name in categories:
        try:
            ogs = view.GetCategoryOverrides(cat_id)
            if ogs is None:
                # Imported here rather than at the top of the function: this is
                # the only path that needs Revit, and keeping it off the others
                # lets the clearing logic be exercised outside Dynamo.
                from Autodesk.Revit.DB import OverrideGraphicSettings
                ogs = OverrideGraphicSettings()
            ogs.SetHalftone(False)
            ogs.SetSurfaceTransparency(0)
            view.SetCategoryOverrides(cat_id, ogs)
            cleared_categories.append(cat_name)
        except Exception as ex:
            _record("category", cat_name, ex)
    return {"already_off_at_element": already_off,
            "elements_touched": len(element_ids),
            "categories_requested": [name for _cat_id, name in categories],
            "categories_cleared": cleared_categories,
            "failures": failures,
            "fully_cleared": not failures}


# --------------------------------------------------------------------------
# Native driver
# --------------------------------------------------------------------------

def _run_native(raw_view, output_dir, selection="all", element_ids=None,
                export_dpi=DEFAULT_EXPORT_DPI, pixel_size=None, max_elements=None,
                repo_root=None):
    report = {
        "probe": {"name": PROBE_NAME, "version": PROBE_VERSION, "source": source_records(),
                  "target": "Revit 2025 / Dynamo 3.3 CPython3"},
        "inputs": {}, "view": {}, "geometry": {}, "cases": {},
        "exports": [], "capture_directory": None,
        "capture_path": "pipeline.init_view_raster + revit.collection.collect_view_elements "
                        "+ color_id_buffer.export_color_id_buffer_view",
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
        "diagnostics": None, "exceptions": [], "warnings": [], "timings_ms": {},
    }
    doc = _get_current_document()
    group = None
    group_started = False
    diag = None
    try:
        _drift._ensure_repo_import_path(output_dir, repo_root)
        from vop_interwoven.core.diagnostics import Diagnostics
        diag = Diagnostics()
        view = _unwrap_dynamo(raw_view)
        if view is None:
            raise ValueError("IN[0] did not resolve to a Revit view")
        cases = select_cases(selection)
        explicit_ids = parse_element_ids(element_ids)
        out_base = os.path.abspath(os.path.expanduser(str(output_dir)))
        run = _drift.run_token(out_base)
        probe_dir = os.path.join(out_base, "white_blend_probe")
        staging_dir = os.path.join(probe_dir, "_staging")
        capture_dir = os.path.join(probe_dir, "captures")
        for path in (probe_dir, staging_dir, capture_dir):
            if not os.path.isdir(path):
                os.makedirs(path)
        report["capture_directory"] = capture_dir

        cfg = build_capture_config(staging_dir, export_dpi=float(export_dpi))
        geometry = plan_capture_geometry(doc, view, cfg, diag=diag)
        report["geometry"] = dict(geometry, bounds_xy=list(geometry["bounds_xy"]))
        report["inputs"] = {"output_directory": out_base, "cases": cases,
                            "explicit_element_ids": explicit_ids,
                            "export_dpi": float(export_dpi),
                            "requested_pixel_size": pixel_size,
                            "max_elements": max_elements}
        report["view"] = {"id": _safe_int_id(view.Id), "name": getattr(view, "Name", None),
                          "scale": geometry["view_scale"],
                          "bounds_xy": list(geometry["bounds_xy"]),
                          "bounds_source": geometry["bounds_source"],
                          "native_pixel_size": geometry["native_pixel_size"],
                          "view_type": str(getattr(view, "ViewType", None)),
                          "view_template_id": _safe_int_id(getattr(view, "ViewTemplateId", None))}
        if pixel_size:
            cfg = build_capture_config(
                staging_dir,
                export_dpi=_drift.dpi_for_pixel_width(pixel_size, geometry["paper_width_in"]))
        if max_elements:
            report["warnings"].append({
                "stage": "inputs",
                "message": "max_elements is ignored: captures are produced by "
                           "export_color_id_buffer_view, which resolves its own element set.",
            })

        _force_close_dynamo_transaction()
        report["state"]["before"] = _drift._snapshot(doc, view)

        # B1 runs FIRST, on the untouched document, outside the
        # TransactionGroup. It has to report the view's *authored* state:
        # production's paint step writes an OverrideGraphicSettings with
        # Halftone off and SurfaceTransparency 0 onto every painted element,
        # so a B1 pass after any capture would read those overrides back and
        # report "no element halftone anywhere" for every document, leaving
        # the halftone gate structurally unable to fire.
        b1 = None
        candidates = []
        if "b1_query" in cases:
            started = time.time()
            try:
                candidates, candidate_source = _candidate_element_ids(doc, view, explicit_ids)
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

        # Pin the crop before the first capture and leave it pinned for every
        # B3 variant. production_capture re-runs init_view_raster each time, and
        # a view whose underlay carries elements beyond the ordinary model
        # changes extents when that underlay is switched off -- so underlay_off
        # would be measured at different bounds, dimensions and density from
        # the baseline, and a difference in edge or pastel metrics could no
        # longer be attributed to the underlay alone. D2 pins for the same
        # reason when it hides categories.
        pinned = {}
        _mutate(doc, "pin_capture_geometry",
                lambda: pinned.__setitem__(
                    "bounds", _set_view_crop(view, geometry["bounds_xy"])))
        report["view"]["capture_geometry_pinned"] = pinned.get("bounds") is not None
        report["view"]["pinned_bounds_xy"] = (list(pinned["bounds"])
                                              if pinned.get("bounds") else None)
        if pinned.get("bounds") is None:
            report["warnings"].append({
                "stage": "pin_capture_geometry",
                "message": "This view has no CropBox, so each B3 capture re-derives its "
                           "own bounds. A variant that changes what is visible may then "
                           "differ from the baseline in extent as well as in mechanism, "
                           "and its metrics are not attributable to the mechanism alone.",
            })

        captures_taken = []
        capture_geometry = []

        def capture(label, extra=None):
            record = production_capture(doc, view, cfg, "b3", label, capture_dir,
                                        diag=diag, run=run)
            captures_taken.append(label)
            capture_geometry.append((label, record.get("bounds_xy"),
                                     record.get("dimensions_px")))
            if extra:
                record.update(extra)
            return record

        for case in export_cases:
            started = time.time()
            try:
                if case == "b3_baseline":
                    record = capture("baseline")
                    report["exports"].append(record)
                    report["cases"][case] = {"tiff_path": record["tiff_path"],
                                             "sidecar_path": record["sidecar_path"]}
                elif case == "b3_underlay_off":
                    if b1 is None:
                        report["cases"][case] = {
                            "skipped": "b1_query was not run, so nothing established that "
                                       "this view uses an underlay"}
                    elif not b1["summary"].get("underlay_off_is_warranted"):
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
                            record = capture("underlay_off",
                                             extra={"mechanism": "underlay_off"})
                            report["exports"].append(record)
                            report["cases"][case] = {"tiff_path": record["tiff_path"],
                                                     "sidecar_path": record["sidecar_path"],
                                                     "api_used": used.get("api")}
                        finally:
                            # Restore before the next variant, so its capture
                            # differs from the baseline in one mechanism only.
                            _mutate(doc, "underlay_restore",
                                    lambda: used.setdefault(
                                        "restore_api", _set_underlay(view, *original)))
                            report["cases"][case] = dict(
                                report["cases"].get(case) or {},
                                restored_underlay=list(original),
                                restore_api=used.get("restore_api"))
                elif case == "b3_halftone_cleared":
                    if b1 is None:
                        report["cases"][case] = {
                            "skipped": "b1_query was not run, so nothing established that "
                                       "halftone is set anywhere on this view"}
                    elif not b1["summary"].get("halftone_clear_is_warranted"):
                        report["cases"][case] = {
                            "skipped": "B1 found Halftone set on no candidate element or "
                                       "category in the authored view",
                            "elements_with_element_halftone":
                                b1["summary"]["elements_with_element_halftone"],
                            "elements_with_category_halftone":
                                b1["summary"]["elements_with_category_halftone"]}
                    elif production_cleared_category_halftone(
                            diag.to_dict() if diag is not None else None,
                            bool(captures_taken)):
                        # Production clears category halftone before painting
                        # and sets Halftone(False) on every painted element, so
                        # the baseline capture is already halftone-free and this
                        # variant would reproduce it exactly. That identity is
                        # the answer to B-H1, without spending a
                        # full-resolution TIFF on it.
                        report["cases"][case] = {
                            "skipped": "redundant: export_color_id_buffer_view already "
                                       "clears category halftone before painting and sets "
                                       "Halftone(False) on every painted element, and its "
                                       "diagnostics record no failure doing so, so the "
                                       "baseline capture is already halftone-free",
                            "authored_state_had_halftone": {
                                "elements": b1["summary"]["elements_with_element_halftone"],
                                "categories": b1["summary"]["elements_with_category_halftone"]},
                            "evidence": "read the baseline capture's pastel element count: "
                                        "still non-zero with halftone already off means "
                                        "halftone is not the mechanism",
                            "captures_examined": list(captures_taken),
                        }
                    else:
                        # Either production's halftone step errored on this
                        # view, or no capture has run yet to say either way
                        # (a selection without b3_baseline). Both are reasons
                        # to run the variant rather than skip it.
                        categories = _candidate_categories(doc, view, candidates)
                        before = _category_override_state(view, categories)
                        detail = {}
                        _mutate(doc, "halftone_cleared",
                                lambda: detail.update(
                                    _clear_halftone(doc, view, candidates, categories,
                                                    diag=diag)))
                        try:
                            record = capture("halftone_cleared",
                                             extra={"mechanism": "halftone_cleared"})
                            report["exports"].append(record)
                            if not detail.get("fully_cleared", True):
                                # The capture exists, but it is not the
                                # experiment it is named after.
                                report["warnings"].append({
                                    "stage": "b3_halftone_cleared",
                                    "message": "halftone could not be cleared on {0} "
                                               "target(s); this capture does not establish "
                                               "that halftone was neutralized and must not "
                                               "be read as the halftone-off "
                                               "condition".format(len(detail["failures"])),
                                })
                            report["cases"][case] = dict(detail,
                                                         tiff_path=record["tiff_path"],
                                                         sidecar_path=record["sidecar_path"],
                                                         variant_status=(
                                                             "completed"
                                                             if detail.get("fully_cleared", True)
                                                             else "inconclusive"),
                                                         ran_because=(
                                                             "production's own category-halftone "
                                                             "step reported a failure for this view"
                                                             if captures_taken else
                                                             "no capture had run yet to establish "
                                                             "that production cleared halftone; "
                                                             "unknown is not redundant"),
                                                         captures_examined=list(captures_taken))
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
        # Every B3 capture's realized bounds and dimensions, so a variant that
        # drifted from the baseline despite the pin is visible in the report
        # rather than having to be inferred from the metrics.
        report["capture_geometry"] = [
            {"label": label, "bounds_xy": bounds, "dimensions_px": dimensions}
            for label, bounds, dimensions in capture_geometry]
        baseline = next((item for item in capture_geometry if item[0] == "baseline"), None)
        if baseline is not None:
            drifted = [item[0] for item in capture_geometry
                       if item[1] != baseline[1] or item[2] != baseline[2]]
            report["capture_geometry_matches_baseline"] = not drifted
            if drifted:
                report["warnings"].append({
                    "stage": "capture_geometry",
                    "message": "B3 variant(s) {0} were captured at different bounds or "
                               "dimensions from the baseline, so a difference in their "
                               "metrics is not attributable to the mechanism "
                               "alone".format(", ".join(drifted)),
                })
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
                diffs = _drift._diff_state(report["state"]["before"], report["state"]["after"])
                report["state"]["differences"] = diffs
                report["state"]["restored"] = len(diffs) == 0
        except Exception as ex:
            report["exceptions"].append(_exception_record("post_rollback_state_capture", ex))
        if diag is not None:
            try:
                report["diagnostics"] = diag.to_dict()
            except Exception as ex:
                report["exceptions"].append(_exception_record("diagnostics_serialize", ex))
        try:
            out_base = os.path.abspath(os.path.expanduser(str(output_dir)))
            probe_dir = os.path.join(out_base, "white_blend_probe")
            if not os.path.isdir(probe_dir):
                os.makedirs(probe_dir)
            json_path = os.path.join(probe_dir, "{0}.white_blend.json".format(
                _drift._stem(_drift.run_token(out_base), report["view"].get("id"),
                             _safe_name(report["view"].get("name") or "view"))))
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
    # The sidecar is listed beside its TIFF, not left out of the inventory. An
    # artifact consumer that copies only artifact_paths would otherwise take
    # the images and leave behind the palette, resolution and bounds that
    # analyze_stage_a_probe.py reads them with -- a capture set that cannot be
    # analysed. The drift probe already inventories both.
    artifacts = []
    for rec in native.get("exports", []):
        artifacts.extend(path for path in (rec.get("tiff_path"), rec.get("sidecar_path"))
                         if path)
    if native.get("json_report_path"):
        artifacts.append(native["json_report_path"])
    errors = list(native.get("exceptions", []))
    ran_something = bool(native.get("exports")) or "b1_query" in native.get("cases", {})
    # Shared with the drift probe. A read-only run never opens a
    # TransactionGroup, so an absent rollback is the correct outcome for it,
    # not a failed one -- and a case that recorded itself inconclusive (a
    # halftone variant whose categories refused to clear) has to reach the
    # envelope, or the executor banks the run as a success and resume never
    # retries the experiment that did not happen.
    status = _drift.envelope_status(native, rollback_ok, restored, ran_something,
                                    read_only=read_only)
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
