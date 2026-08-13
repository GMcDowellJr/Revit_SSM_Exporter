"""
Stage A TransactionGroup export probe for Revit 2025 / Dynamo 3.3 CPython3.

Dynamo inputs:
    IN[0] = target Revit view
    IN[1] = one test element or a list of test elements
    IN[2] = output directory
    IN[3] = inject failure after TIFF export: bool, default False

This probe intentionally does not import vop_interwoven production modules. It is
standalone so it can be pasted directly into a Dynamo CPython3 node.
"""

import importlib.util
import json
import os
import re
import sys
import time
import traceback


PROBE_NAME = "stage_a_transaction_group_export"
PROBE_VERSION = 1
REQUESTED_PIXEL_SIZE = 1600
_PROBE_CONTRACT = None


def _probe_contract(output_dir=None):
    """Locate and import the shared contract without assuming a package path."""
    global _PROBE_CONTRACT
    if _PROBE_CONTRACT is not None:
        return _PROBE_CONTRACT
    roots = [output_dir, os.getcwd(), os.environ.get("REVIT_SSM_EXPORTER_ROOT"), os.environ.get("VOP_REPO_ROOT")]
    for seed in roots:
        if not seed:
            continue
        current = os.path.abspath(os.path.expanduser(str(seed)))
        for _ in range(8):
            if os.path.isfile(os.path.join(current, "tests", "dynamo", "stage_a_probe_contract.py")):
                for candidate in (current, os.path.join(current, "tests", "dynamo")):
                    if candidate not in sys.path:
                        sys.path.insert(0, candidate)
                break
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
    try:
        import tests.dynamo.stage_a_probe_contract as contract
    except ImportError:
        import stage_a_probe_contract as contract
    _PROBE_CONTRACT = contract
    return contract


def _now_ms():
    return int(round(time.time() * 1000.0))


def _safe_int_id(value):
    if value is None:
        return None
    try:
        return int(value.Value)
    except Exception:
        try:
            return int(value.IntegerValue)
        except Exception:
            return None


def _safe_enum(value):
    if value is None:
        return None
    try:
        return str(value)
    except Exception:
        try:
            return int(value)
        except Exception:
            return "unavailable"


def _safe_string(value):
    if value is None:
        return None
    try:
        return str(value)
    except Exception:
        return "unavailable"


def _safe_color(color):
    if color is None:
        return None
    try:
        if bool(color.IsValid):
            return [int(color.Red), int(color.Green), int(color.Blue)]
        return "invalid"
    except Exception:
        return "unavailable"


def _safe_xyz(xyz):
    if xyz is None:
        return None
    try:
        return [float(xyz.X), float(xyz.Y), float(xyz.Z)]
    except Exception:
        return "unavailable"


def _safe_transform(transform):
    if transform is None:
        return None
    return {
        "origin": _safe_xyz(getattr(transform, "Origin", None)),
        "basis_x": _safe_xyz(getattr(transform, "BasisX", None)),
        "basis_y": _safe_xyz(getattr(transform, "BasisY", None)),
        "basis_z": _safe_xyz(getattr(transform, "BasisZ", None)),
    }


def _safe_name(name):
    text = _safe_string(name) or "view"
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("._")
    return text[:80] or "view"


def _exception_record(stage, ex):
    return {
        "stage": stage,
        "type": type(ex).__name__,
        "message": str(ex),
        "traceback": traceback.format_exc(),
    }


def _unwrap_dynamo(obj):
    if obj is None:
        return None
    for attr in ("InternalElement", "InternalGeometry"):
        try:
            inner = getattr(obj, attr, None)
            if inner is not None:
                return inner
        except Exception:
            pass
    return obj


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _ensure_revit_api_reference():
    import clr
    clr.AddReference("RevitAPI")


def _get_current_document():
    import clr
    clr.AddReference("RevitServices")
    from RevitServices.Persistence import DocumentManager
    return DocumentManager.Instance.CurrentDBDocument


def _force_close_dynamo_transaction():
    import clr
    clr.AddReference("RevitServices")
    from RevitServices.Transactions import TransactionManager
    TransactionManager.Instance.ForceCloseTransaction()



def _document_label(doc):
    if doc is None:
        return "<none>"
    parts = []
    for attr in ("Title", "PathName"):
        try:
            value = getattr(doc, attr, None)
            if value:
                parts.append("{0}={1}".format(attr, value))
        except Exception:
            pass
    try:
        parts.append("hash={0}".format(doc.GetHashCode()))
    except Exception:
        pass
    return ", ".join(parts) if parts else "<unavailable document label>"


def _same_document(left, right):
    if left is None or right is None:
        return False
    if left is right:
        return True
    for a, b in ((left, right), (right, left)):
        try:
            if bool(a.Equals(b)):
                return True
        except Exception:
            pass
    try:
        return int(left.GetHashCode()) == int(right.GetHashCode())
    except Exception:
        return False


def _validate_inputs(doc, raw_view, raw_elements, output_dir):
    _ensure_revit_api_reference()
    from Autodesk.Revit.DB import Element, ElementId, ElementType, View
    view = _unwrap_dynamo(raw_view)
    if view is None or not isinstance(view, View):
        raise ValueError("IN[0] must be a Revit DB View or Dynamo-wrapped Revit view; active view is not substituted")
    if bool(getattr(view, "IsTemplate", False)):
        raise ValueError("Target view is a view template and cannot be exported")
    view_doc = getattr(view, "Document", None)
    if not _same_document(view_doc, doc):
        raise ValueError("Target view belongs to a different document. Current document: {0}; target view document: {1}".format(_document_label(doc), _document_label(view_doc)))
    try:
        if not view.CanBePrinted:
            raise ValueError("Target view reports CanBePrinted=False and is treated as non-exportable")
    except Exception as ex:
        raise ValueError("Could not verify target view exportability: {0}".format(ex))

    elements = []
    seen = set()
    for raw in _as_list(raw_elements):
        elem = _unwrap_dynamo(raw)
        if elem is None or not isinstance(elem, Element):
            raise ValueError("IN[1] contains a missing or non-Revit element")
        if isinstance(elem, ElementType):
            raise ValueError("IN[1] contains an element type; provide model element instances")
        elem_doc = getattr(elem, "Document", None)
        if not _same_document(elem_doc, doc):
            raise ValueError("IN[1] contains an element from a different document. Current document: {0}; element {1} document: {2}".format(_document_label(doc), _safe_int_id(getattr(elem, "Id", None)), _document_label(elem_doc)))
        eid = _safe_int_id(elem.Id)
        invalid_eid = _safe_int_id(ElementId.InvalidElementId)
        if eid is None or eid == invalid_eid:
            raise ValueError("IN[1] contains an element with an invalid ElementId")
        if eid not in seen:
            elements.append(elem)
            seen.add(eid)
    if not elements:
        raise ValueError("IN[1] must contain at least one test element")
    if not output_dir:
        raise ValueError("IN[2] must be a writable output directory")
    return view, elements, os.path.abspath(str(output_dir))


def _solid_drafting_fill_pattern_id(doc):
    _ensure_revit_api_reference()
    from Autodesk.Revit.DB import FilteredElementCollector, FillPatternElement, FillPatternTarget
    for fp in FilteredElementCollector(doc).OfClass(FillPatternElement):
        try:
            pat = fp.GetFillPattern()
            if pat.IsSolidFill and pat.Target == FillPatternTarget.Drafting:
                return fp.Id
        except Exception:
            continue
    return None


def _build_flat_color_ogs(solid_pattern_id, color):
    _ensure_revit_api_reference()
    from Autodesk.Revit.DB import OverrideGraphicSettings
    ogs = OverrideGraphicSettings()
    calls = [
        ("SetSurfaceForegroundPatternId", solid_pattern_id),
        ("SetSurfaceForegroundPatternColor", color),
        ("SetSurfaceForegroundPatternVisible", True),
        ("SetCutForegroundPatternId", solid_pattern_id),
        ("SetCutForegroundPatternColor", color),
        ("SetCutForegroundPatternVisible", True),
        ("SetProjectionLineColor", color),
        ("SetCutLineColor", color),
        ("SetSurfaceTransparency", 0),
        ("SetHalftone", False),
    ]
    diagnostics = []
    for name, arg in calls:
        try:
            getattr(ogs, name)(arg)
        except Exception as ex:
            diagnostics.append({"setter": name, "message": str(ex)})
    return ogs, diagnostics


def _read_ogs_property(ogs, getter_name, normalizer):
    try:
        getter = getattr(ogs, getter_name)
    except Exception:
        return {"value": None, "diagnostic": "unavailable: missing getter"}
    try:
        value = getter() if callable(getter) else getter
        return {"value": normalizer(value), "diagnostic": None}
    except Exception as ex:
        return {"value": "unavailable", "diagnostic": "{0}: {1}".format(type(ex).__name__, ex)}


def _snapshot_ogs(ogs):
    spec = {
        "projection_line_color": ("ProjectionLineColor", _safe_color),
        "projection_line_pattern_id": ("ProjectionLinePatternId", _safe_int_id),
        "projection_line_weight": ("ProjectionLineWeight", lambda v: int(v) if v is not None else None),
        "cut_line_color": ("CutLineColor", _safe_color),
        "cut_line_pattern_id": ("CutLinePatternId", _safe_int_id),
        "cut_line_weight": ("CutLineWeight", lambda v: int(v) if v is not None else None),
        "surface_foreground_pattern_id": ("SurfaceForegroundPatternId", _safe_int_id),
        "surface_foreground_pattern_color": ("SurfaceForegroundPatternColor", _safe_color),
        "surface_background_pattern_id": ("SurfaceBackgroundPatternId", _safe_int_id),
        "surface_background_pattern_color": ("SurfaceBackgroundPatternColor", _safe_color),
        "cut_foreground_pattern_id": ("CutForegroundPatternId", _safe_int_id),
        "cut_foreground_pattern_color": ("CutForegroundPatternColor", _safe_color),
        "cut_background_pattern_id": ("CutBackgroundPatternId", _safe_int_id),
        "cut_background_pattern_color": ("CutBackgroundPatternColor", _safe_color),
        "surface_transparency": (("Transparency", "SurfaceTransparency"), lambda v: int(v) if v is not None else None),
        "halftone": ("Halftone", lambda v: bool(v) if v is not None else None),
        "detail_level": ("DetailLevel", _safe_enum),
    }
    snap = {}
    diagnostics = []
    for key, (props, norm) in spec.items():
        # Revit exposes OGS values as properties in modern APIs; older shims may expose getters.
        prop_names = props if isinstance(props, tuple) else (props,)
        prop_diagnostics = []
        found = False
        for prop in prop_names:
            try:
                value = getattr(ogs, prop)
                snap[key] = norm(value)
                found = True
                break
            except Exception:
                got = _read_ogs_property(ogs, "Get" + prop, norm)
                if got["diagnostic"] is None:
                    snap[key] = got["value"]
                    found = True
                    break
                prop_diagnostics.append("{0}: {1}".format(prop, got["diagnostic"]))
        if not found:
            snap[key] = "unavailable"
            diagnostics.append({"property": key, "diagnostic": "; ".join(prop_diagnostics)})
    if diagnostics:
        snap["diagnostics"] = diagnostics
    return snap


def _snapshot_state(doc, view, elements):
    state = {
        "document": {
            "title": _safe_string(getattr(doc, "Title", None)),
            "path": _safe_string(getattr(doc, "PathName", None)),
            "is_modified": bool(getattr(doc, "IsModified", False)),
            "is_read_only": bool(getattr(doc, "IsReadOnly", False)),
            "active_view_id": None,
        },
        "view": {},
        "display_model": {},
        "crop": {},
        "filters": {},
        "elements": {},
    }
    try:
        state["document"]["active_view_id"] = _safe_int_id(doc.ActiveView.Id)
    except Exception:
        state["document"]["active_view_id"] = "unavailable"

    state["view"] = {
        "id": _safe_int_id(view.Id),
        "unique_id": _safe_string(getattr(view, "UniqueId", None)),
        "name": _safe_string(getattr(view, "Name", None)),
        "view_type": _safe_enum(getattr(view, "ViewType", None)),
        "view_template_id": _safe_int_id(getattr(view, "ViewTemplateId", None)),
        "display_style": _safe_enum(getattr(view, "DisplayStyle", None)),
    }

    try:
        dm = view.GetViewDisplayModel()
        try:
            state["display_model"]["smooth_edges"] = bool(dm.SmoothEdges)
        finally:
            try:
                dm.Dispose()
            except Exception:
                pass
    except Exception as ex:
        state["display_model"]["smooth_edges"] = "unavailable: {0}".format(ex)

    try:
        crop = view.CropBox
    except Exception:
        crop = None
    state["crop"] = {
        "crop_box_active": bool(getattr(view, "CropBoxActive", False)),
        "crop_box_visible": bool(getattr(view, "CropBoxVisible", False)),
        "min": _safe_xyz(getattr(crop, "Min", None)),
        "max": _safe_xyz(getattr(crop, "Max", None)),
        "transform": _safe_transform(getattr(crop, "Transform", None)),
    }

    try:
        filter_ids = list(view.GetFilters())
    except Exception:
        filter_ids = []
    for fid in filter_ids:
        fid_key = str(_safe_int_id(fid))
        entry = {"filter_id": _safe_int_id(fid), "enabled": "unavailable", "visible": "unavailable"}
        try:
            entry["enabled"] = bool(view.GetIsFilterEnabled(fid))
        except Exception as ex:
            entry["enabled"] = "unavailable: {0}".format(ex)
        try:
            entry["visible"] = bool(view.GetFilterVisibility(fid))
        except Exception as ex:
            entry["visible"] = "unavailable: {0}".format(ex)
        state["filters"][fid_key] = entry

    for elem in elements:
        eid_int = _safe_int_id(elem.Id)
        cat = getattr(elem, "Category", None)
        entry = {
            "element_id": eid_int,
            "unique_id": _safe_string(getattr(elem, "UniqueId", None)),
            "category_id": _safe_int_id(getattr(cat, "Id", None)),
            "category_name": _safe_string(getattr(cat, "Name", None)),
            "hidden_in_view": "unavailable",
            "overrides": {},
        }
        try:
            entry["hidden_in_view"] = bool(elem.IsHidden(view))
        except Exception as ex:
            entry["hidden_in_view"] = "unavailable: {0}".format(ex)
        try:
            entry["overrides"] = _snapshot_ogs(view.GetElementOverrides(elem.Id))
        except Exception as ex:
            entry["overrides"] = {"unavailable": "{0}: {1}".format(type(ex).__name__, ex)}
        state["elements"][str(eid_int)] = entry
    return state


def _diff_values(before, after, path=""):
    diffs = []
    if isinstance(before, dict) and isinstance(after, dict):
        keys = sorted(set(before.keys()) | set(after.keys()))
        for key in keys:
            child = "{0}.{1}".format(path, key) if path else str(key)
            if key not in before:
                diffs.append({"path": child, "before": None, "after": after[key]})
            elif key not in after:
                diffs.append({"path": child, "before": before[key], "after": None})
            else:
                diffs.extend(_diff_values(before[key], after[key], child))
    elif isinstance(before, list) and isinstance(after, list):
        if before != after:
            diffs.append({"path": path, "before": before, "after": after})
    elif before != after:
        diffs.append({"path": path, "before": before, "after": after})
    return diffs


def _set_pixel_size_with_backoff(opts, requested):
    candidate = max(1, int(requested))
    floor = 16
    while True:
        try:
            opts.PixelSize = candidate
            return candidate
        except Exception:
            if candidate <= floor:
                raise
            candidate = max(floor, candidate // 2)


def _discover_and_rename_tiff(out_dir, before, final_path):
    after = set(os.listdir(out_dir))
    candidates = [f for f in (after - before) if f.lower().endswith((".tif", ".tiff"))]
    if not candidates:
        raise RuntimeError("ExportImage produced no new TIFF in {0}".format(out_dir))
    candidates.sort(key=lambda n: os.path.getmtime(os.path.join(out_dir, n)), reverse=True)
    created = os.path.join(out_dir, candidates[0])
    if os.path.exists(final_path):
        os.remove(final_path)
    os.rename(created, final_path)
    return final_path


def _export_tiff(doc, view, output_path, requested_pixel_size):
    _ensure_revit_api_reference()
    from Autodesk.Revit.DB import ImageExportOptions, ExportRange, ZoomFitType, FitDirectionType, ElementId, ImageFileType
    import System.Collections.Generic as SCG
    out_dir = os.path.dirname(output_path)
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    before = set(os.listdir(out_dir))
    ids = SCG.List[ElementId]()
    ids.Add(view.Id)
    opts = ImageExportOptions()
    opts.ExportRange = ExportRange.SetOfViews
    opts.SetViewsAndSheets(ids)
    opts.ZoomType = ZoomFitType.FitToPage
    opts.FitDirection = FitDirectionType.Horizontal
    accepted_pixel_size = _set_pixel_size_with_backoff(opts, requested_pixel_size)
    opts.FilePath = os.path.join(out_dir, "_vop_stage_a_tg_probe_tmp")
    tiff_type = getattr(ImageFileType, "TIFF", getattr(ImageFileType, "TIF", None))
    if tiff_type is None:
        raise RuntimeError("Revit ImageFileType does not expose TIFF/TIF")
    opts.HLRandWFViewsFileType = tiff_type
    opts.ShadowViewsFileType = tiff_type
    doc.ExportImage(opts)
    return _discover_and_rename_tiff(out_dir, before, output_path), accepted_pixel_size


def _try_image_inspection(path, expected_colors):
    result = {
        "actual_dimensions_px": None,
        "expected_color_pixel_counts": {},
        "missing_expected_colors": [],
        "temporary_colors_visible_in_export": "requires_manual_review",
        "diagnostic": None,
    }
    if importlib.util.find_spec("PIL") is None:
        result["diagnostic"] = "Pillow unavailable in this Dynamo Python environment"
        return result
    from PIL import Image
    try:
        img = Image.open(path)
        result["actual_dimensions_px"] = [int(img.size[0]), int(img.size[1])]
        rgb_img = img.convert("RGB")
        pixels = list(rgb_img.getdata())
        any_visible = False
        for elem_id, rgb in expected_colors.items():
            tup = tuple(int(v) for v in rgb)
            count = pixels.count(tup)
            result["expected_color_pixel_counts"][str(elem_id)] = int(count)
            if count <= 0:
                result["missing_expected_colors"].append(str(elem_id))
            any_visible = any_visible or count > 0
        result["temporary_colors_visible_in_export"] = "confirmed" if any_visible else "not_detected"
    except Exception as ex:
        result["diagnostic"] = "Image inspection failed: {0}".format(ex)
    return result


def _apply_temporary_changes(doc, view, elements):
    _ensure_revit_api_reference()
    from Autodesk.Revit.DB import Color, DisplayStyle
    diagnostics = []
    expected = {}
    solid_id = _solid_drafting_fill_pattern_id(doc)
    if solid_id is None:
        raise RuntimeError("No solid drafting fill pattern found")
    palette = [(255, 0, 0), (0, 192, 255), (0, 255, 0), (255, 0, 255), (255, 192, 0), (0, 0, 255)]
    for idx, elem in enumerate(elements):
        rgb = palette[idx % len(palette)]
        expected[str(_safe_int_id(elem.Id))] = list(rgb)
        ogs, ogs_diags = _build_flat_color_ogs(solid_id, Color(rgb[0], rgb[1], rgb[2]))
        diagnostics.extend(ogs_diags)
        view.SetElementOverrides(elem.Id, ogs)
    try:
        flat = getattr(DisplayStyle, "FlatColors", None)
        if flat is not None:
            view.DisplayStyle = flat
        else:
            diagnostics.append({"setting": "display_style", "message": "DisplayStyle.FlatColors unavailable"})
    except Exception as ex:
        diagnostics.append({"setting": "display_style", "message": str(ex)})
    try:
        dm = view.GetViewDisplayModel()
        try:
            dm.SmoothEdges = False
            view.SetViewDisplayModel(dm)
        finally:
            try:
                dm.Dispose()
            except Exception:
                pass
    except Exception as ex:
        diagnostics.append({"setting": "smooth_edges", "message": str(ex)})
    return expected, diagnostics


def _run_native(raw_view, raw_elements, output_dir, inject_failure=False):
    _ensure_revit_api_reference()
    from Autodesk.Revit.DB import Transaction, TransactionGroup, TransactionStatus
    report = {
        "probe": {"name": PROBE_NAME, "version": PROBE_VERSION, "target": "Revit 2025 / Dynamo 3.3 CPython3"},
        "inputs": {},
        "transaction_group": {"started": False, "child_transaction_committed": False, "child_transaction_commit_status": None, "export_attempted": False, "rollback_attempted": False, "rollback_succeeded": False, "no_child_transaction_open_at_export": None},
        "export": {"succeeded": False, "path": None, "requested_pixel_size": REQUESTED_PIXEL_SIZE, "accepted_pixel_size": None, "actual_dimensions_px": None, "file_size_bytes": None, "expected_element_colors": {}, "expected_color_pixel_counts": {}, "missing_expected_colors": [], "temporary_colors_visible_in_export": "requires_manual_review"},
        "failure_injection": {"requested": bool(inject_failure), "triggered": False},
        "state": {"before": {}, "after": {}, "captured_state_equal_after_rollback": False, "state_differences": []},
        "result": {"success": False, "conclusion": "FAIL", "reasons": []},
        "exceptions": [],
        "timings_ms": {},
    }
    doc = _get_current_document()
    view = None
    elements = []
    group = None
    group_started = False
    json_path = None
    try:
        view, elements, out_base = _validate_inputs(doc, raw_view, raw_elements, output_dir)
        probe_dir = os.path.join(out_base, "transaction_group_probe")
        if not os.path.isdir(probe_dir):
            os.makedirs(probe_dir)
        run_mode = "failure_injection" if bool(inject_failure) else "normal"
        base = "{0}_{1}.{2}.transaction_group_probe".format(_safe_name(view.Name), _safe_int_id(view.Id), run_mode)
        tiff_path = os.path.join(probe_dir, base + ".tiff")
        json_path = os.path.join(probe_dir, base + ".json")
        report["inputs"] = {
            "view_id": _safe_int_id(view.Id),
            "view_name": _safe_string(view.Name),
            "element_ids": [_safe_int_id(e.Id) for e in elements],
            "output_directory": out_base,
            "inject_failure_after_tiff_export": bool(inject_failure),
            "run_mode": run_mode,
        }
        _force_close_dynamo_transaction()
        report["state"]["before"] = _snapshot_state(doc, view, elements)

        group = TransactionGroup(doc, "VOP Stage A TransactionGroup Probe")
        status = group.Start()
        group_started = status == TransactionStatus.Started
        report["transaction_group"]["started"] = bool(group_started)
        if not group_started:
            raise RuntimeError("TransactionGroup.Start returned {0}".format(status))

        try:
            tx = Transaction(doc, "VOP Stage A Probe Temporary Graphics")
            tx_status = tx.Start()
            if tx_status != TransactionStatus.Started:
                raise RuntimeError("Child Transaction.Start returned {0}".format(tx_status))
            try:
                expected_colors, setter_diags = _apply_temporary_changes(doc, view, elements)
                report["export"]["expected_element_colors"] = expected_colors
                if setter_diags:
                    report["temporary_change_diagnostics"] = setter_diags
                commit_status = tx.Commit()
                report["transaction_group"]["child_transaction_commit_status"] = _safe_enum(commit_status)
                report["transaction_group"]["child_transaction_committed"] = commit_status == TransactionStatus.Committed
                if commit_status != TransactionStatus.Committed:
                    raise RuntimeError("Child Transaction.Commit returned {0}; export skipped".format(commit_status))
            except Exception:
                try:
                    tx.RollBack()
                except Exception as rb_ex:
                    report["exceptions"].append(_exception_record("child_transaction_rollback", rb_ex))
                raise

            report["transaction_group"]["no_child_transaction_open_at_export"] = not bool(doc.IsModifiable)
            report["transaction_group"]["export_attempted"] = True
            t0 = _now_ms()
            exported, accepted = _export_tiff(doc, view, tiff_path, REQUESTED_PIXEL_SIZE)
            report["timings_ms"]["export"] = _now_ms() - t0
            report["export"]["succeeded"] = True
            report["export"]["path"] = exported
            report["export"]["accepted_pixel_size"] = accepted
            if os.path.exists(exported):
                report["export"]["file_size_bytes"] = int(os.path.getsize(exported))
            inspection = _try_image_inspection(exported, report["export"]["expected_element_colors"])
            report["export"].update(inspection)

            if bool(inject_failure):
                report["failure_injection"]["triggered"] = True
                raise RuntimeError("Injected failure after TIFF export")
        except Exception as ex:
            if not (bool(inject_failure) and report["failure_injection"]["triggered"] and str(ex) == "Injected failure after TIFF export"):
                report["exceptions"].append(_exception_record("probe_body", ex))
            else:
                report["exceptions"].append({"stage": "failure_injection", "type": type(ex).__name__, "message": str(ex), "controlled": True})
    except Exception as ex:
        report["exceptions"].append(_exception_record("setup_or_outer", ex))
    finally:
        if group_started and group is not None:
            report["transaction_group"]["rollback_attempted"] = True
            try:
                rb_status = group.RollBack()
                report["transaction_group"]["rollback_status"] = _safe_enum(rb_status)
                report["transaction_group"]["rollback_succeeded"] = rb_status == TransactionStatus.RolledBack
            except Exception as ex:
                report["exceptions"].append(_exception_record("transaction_group_rollback", ex))
                report["transaction_group"]["rollback_succeeded"] = False
        if view is not None and elements:
            try:
                report["state"]["after"] = _snapshot_state(doc, view, elements)
                diffs = _diff_values(report["state"]["before"], report["state"]["after"])
                report["state"]["state_differences"] = diffs
                report["state"]["captured_state_equal_after_rollback"] = len(diffs) == 0
            except Exception as ex:
                report["exceptions"].append(_exception_record("post_rollback_state_capture", ex))

        unexpected = [e for e in report["exceptions"] if not e.get("controlled")]
        reasons = []
        if not report["transaction_group"]["started"]:
            reasons.append("TransactionGroup did not start")
        if not report["transaction_group"]["child_transaction_committed"]:
            reasons.append("Child transaction did not commit")
        if not report["export"]["succeeded"]:
            reasons.append("TIFF export did not succeed")
        if report["transaction_group"].get("export_attempted") and report["transaction_group"].get("no_child_transaction_open_at_export") is not True:
            reasons.append("Document was still modifiable or an open transaction was detected at export")
        if not report["transaction_group"]["rollback_succeeded"]:
            reasons.append("TransactionGroup rollback did not succeed")
        if not report["state"]["captured_state_equal_after_rollback"]:
            reasons.append("Captured before/after state differs after rollback")
        if unexpected:
            reasons.append("Unexpected exception(s) occurred")
        if report["export"].get("temporary_colors_visible_in_export") == "not_detected":
            reasons.append("Pillow inspection found zero pixels for every expected temporary color")
        if bool(inject_failure) and not report["failure_injection"]["triggered"]:
            reasons.append("Failure injection was requested but did not trigger")
        report["result"]["reasons"] = reasons
        if not reasons:
            report["result"]["success"] = True
            report["result"]["conclusion"] = "PASS"
        elif report["export"]["succeeded"] and report["transaction_group"]["rollback_succeeded"] and report["state"]["captured_state_equal_after_rollback"]:
            report["result"]["conclusion"] = "INCONCLUSIVE"
        else:
            report["result"]["conclusion"] = "FAIL"

        if json_path is None:
            try:
                fallback_dir = os.path.join(os.path.abspath(str(output_dir or os.getcwd())), "transaction_group_probe")
                if not os.path.isdir(fallback_dir):
                    os.makedirs(fallback_dir)
                json_path = os.path.join(fallback_dir, "stage_a_transaction_group_probe_error.json")
            except Exception:
                json_path = None
        if json_path is not None:
            try:
                with open(json_path, "w") as f:
                    json.dump(report, f, indent=2, sort_keys=True)
                report["json_report_path"] = json_path
            except Exception as ex:
                report["exceptions"].append(_exception_record("json_report_write", ex))
    return report


def run_probe(raw_view, raw_elements, output_dir, inject_failure=False):
    contract = _probe_contract(output_dir)
    started_at = contract.utc_now_iso()
    native = _run_native(raw_view, raw_elements, output_dir, inject_failure)
    transaction = native.get("transaction_group", {})
    state = native.get("state", {})
    rollback_ok = bool(transaction.get("rollback_succeeded"))
    restored = bool(state.get("captured_state_equal_after_rollback"))
    artifacts = [path for path in (native.get("export", {}).get("path"), native.get("json_report_path")) if path]
    errors = [item for item in native.get("exceptions", []) if not item.get("controlled")]
    return contract.execution_envelope(
        PROBE_NAME, {"inject_failure": bool(inject_failure)}, contract.view_identity(raw_view),
        native, artifacts, "succeeded" if rollback_ok else "failed",
        "restored" if restored else "not_restored", started_at,
        execution_status="failed" if errors or not rollback_ok or not restored else "completed",
        errors=errors,
        warnings=[item for item in native.get("exceptions", []) if item.get("controlled")])


def dynamo_main(inputs):
    inject = bool(inputs[3]) if len(inputs) > 3 and inputs[3] is not None else False
    return run_probe(inputs[0], inputs[1], inputs[2], inject)


if "IN" in globals():
    try:
        OUT = dynamo_main(IN)
    except Exception as fatal:
        OUT = {"conclusion": "FAIL", "success": False, "fatal_exception": _exception_record("dynamo_entrypoint", fatal)}
