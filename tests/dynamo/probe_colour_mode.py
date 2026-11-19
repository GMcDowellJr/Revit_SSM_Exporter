"""Colour-mode probe for Revit 2025 / Dynamo 3.x CPython3: cost and attribution.

docs/notes/export_floor_20261118T1002.md, Part B. Measures, on views that
already have a colour-ID capture (the BASELINE, arm 1, not re-run here), what
three cheaper colour modes cost and what they lose:

  as_is     no colour overrides; annotation hidden, same export settings.
  category  every model category's (and subcategory's) Object Styles line
            colour set ONCE for the document, in a transaction inside an
            outer TransactionGroup that is rolled back at the end of the
            probe. No per-view colour write. Per view, every view-level
            setting that could take precedence is recorded (``precedence``).
  type      one ParameterFilterElement per family type of the view's
            baseline elements, each with a line-colour override. Each
            Create, AddFilter and SetFilterOverrides call is timed (the
            September baseline was ~130 ms per filter).

Each arm, per view, runs in its own TransactionGroup, ROLLED BACK: detach
the view template (as the production model pass does, so the view's own
V/G can be written), hide the annotation categories and the view-owned
elements (production's annotation membership), apply the arm's colours,
commit, export with production ``_export_tiff`` at the BASELINE's pixel size
and fit direction, roll back. Timed with production's lap convention: setup,
commit, export, restore.

Inputs:
    IN[0] = output root (required). Writes <root>/probe_colour_mode_<clock>/:
            one TIFF per view and arm, and probe_colour_mode_<clock>.json.
    IN[1] = view ids (about 12: plans, RCPs, sections, elevations; one
            casework-heavy, one with diagonal walls)
    IN[2] = the BASELINE run directory: the capture run holding those views'
            colour-ID captures. Its model sidecars give the element ids
            labelled here (near_face_w_map.host) and the export size.
    IN[3] = options (Dictionary or JSON): repo_root; arms ("all" or a comma
            list of ARMS); palette_step (default 24)
Output: OUT = the record's path. Then, offline:
    python tools/probe_colour_mode_attribution.py <probe dir> <baseline run dir>

THE PROBE NEVER SAVES THE DOCUMENT and leaves nothing behind. Every write is
inside a TransactionGroup that is rolled back (``rolled_back_group``, which
raises if the rollback does not report RolledBack). The Object Styles state
of every category and subcategory is read before the probe and after the
outer rollback. If they differ, the record says ``object_styles_restored:
false``, lists the differences, and the probe RAISES after writing it.
tests/test_probe_colour_mode.py pins the rollback helper, the comparison, and
that no Save call exists in this file.
"""
from __future__ import print_function

import glob
import json
import os
import re
import sys
import time
import traceback

PROBE_NAME = "colour_mode"
PROBE_VERSION = "2026-11-19.1"
SCHEMA = "vop.probe.colour_mode.v1"
ARMS = ("as_is", "category", "type")
DEFAULT_PALETTE_STEP = 24
REQUIRED_PRODUCTION = {"vop_interwoven.color_id_buffer": ("_export_tiff", "stage_a_palette")}


# ======================================================================
# PURE (tests/test_probe_colour_mode.py)
# ======================================================================

def select_arms(selection):
    if selection is None or str(selection).strip().lower() in ("", "all"):
        return list(ARMS)
    names = [p.strip() for p in str(selection).replace(";", ",").split(",") if p.strip()]
    unknown = [n for n in names if n not in ARMS]
    if unknown:
        raise ValueError("unknown arm(s) {0}; known: {1}".format(unknown, ARMS))
    return [a for a in ARMS if a in names]


def colour_rgb(color):
    """A Revit Color as [r, g, b], or None when it is not valid (unset)."""
    if color is None or not getattr(color, "IsValid", False):
        return None
    return [int(color.Red), int(color.Green), int(color.Blue)]


def read_object_styles(categories):
    """PURE over category-like objects: ``{id: {"name", "rgb", "parent"}}``
    for every category and subcategory. The state the probe must restore."""
    out = {}

    def _one(cat, parent):
        cid = int(cat.Id.IntegerValue)
        out[str(cid)] = {"name": getattr(cat, "Name", None),
                         "rgb": colour_rgb(getattr(cat, "LineColor", None)),
                         "parent": parent}
        for sub in (getattr(cat, "SubCategories", None) or ()):
            _one(sub, cid)
    for cat in categories:
        _one(cat, None)
    return out


def object_styles_diff(before, after):
    """PURE. Every category whose recorded state differs, or that is in one
    snapshot only. Empty means restored."""
    diffs = []
    for key in sorted(set(before) | set(after)):
        if before.get(key) != after.get(key):
            diffs.append({"category_id": key, "before": before.get(key),
                          "after": after.get(key)})
    return diffs


def label_colours(labels, palette):
    """PURE. ``{label: [r, g, b]}``, one distinct palette colour per label,
    in sorted label order. Refuses a palette shorter than the labels."""
    labels = sorted(set(int(x) for x in labels))
    if len(palette) < len(labels):
        raise ValueError("palette has {0} colours for {1} labels".format(
            len(palette), len(labels)))
    return dict((lab, [int(c) for c in palette[i]]) for i, lab in enumerate(labels))


def type_filter_plan(element_labels):
    """PURE. ``[(type_id, category_id)]``: one filter per type, from
    ``{elem: {"category", "type"}}``. A type seen under two categories is
    refused for the second (a filter is one category's)."""
    seen = {}
    for _elem, lab in sorted(element_labels.items()):
        tid, cid = (lab or {}).get("type"), (lab or {}).get("category")
        if tid is None or cid is None or tid <= 0:
            continue
        seen.setdefault(int(tid), int(cid))
    return sorted(seen.items())


def baseline_host_ids(sidecar):
    return sorted(int(k) for k, e in ((sidecar.get("near_face_w_map") or {})
                                      .get("host") or {}).items() if isinstance(e, dict))


class rolled_back_group(object):
    """``with rolled_back_group(doc, name, api) as g:`` -- a TransactionGroup
    that is ALWAYS rolled back on exit, and raises when the rollback does not
    report RolledBack. ``api``: an object with ``TransactionGroup`` and
    ``TransactionStatus`` (the Revit DB namespace, or a fake)."""

    def __init__(self, doc, name, api):
        self.group = api.TransactionGroup(doc, name)
        self.status = api.TransactionStatus
        self.rollback_ms = None

    def __enter__(self):
        if self.group.Start() != self.status.Started:
            raise RuntimeError("TransactionGroup did not start")
        return self

    def __exit__(self, exc_type, exc, tb):
        t0 = time.time()
        status = self.group.RollBack()
        self.rollback_ms = round((time.time() - t0) * 1000.0, 3)
        if status != self.status.RolledBack:
            raise RuntimeError("TransactionGroup did not roll back ({0})".format(status))
        return False


def apply_object_styles(doc, model_cats, colours, api):
    """Set every model category's Object Styles line colour, and its
    subcategories' to the same, in ONE committed transaction. The caller
    holds the TransactionGroup whose rollback undoes it. Returns the laps."""
    laps = Laps()
    tx = api.Transaction(doc, "VOP probe Object Styles colours")
    tx.Start()
    for cat in model_cats:
        rgb = colours[cat.Id.IntegerValue]
        colour = api.Color(rgb[0], rgb[1], rgb[2])
        cat.LineColor = colour
        for sub in cat.SubCategories:
            sub.LineColor = colour
    laps.lap("object_styles_setup")
    if tx.Commit() != api.TransactionStatus.Committed:
        raise RuntimeError("the Object Styles transaction did not commit")
    laps.lap("object_styles_commit")
    return laps.ms


class Laps(object):
    """Production's lap convention: each lap closes where the next begins."""

    def __init__(self):
        self.at = time.time()
        self.ms = {}

    def lap(self, name):
        now = time.time()
        self.ms[name] = round(self.ms.get(name, 0.0) + (now - self.at) * 1000.0, 3)
        self.at = now


# ======================================================================
# REVIT
# ======================================================================

def _ensure_repo(repo_root):
    import importlib
    candidates = [repo_root, os.environ.get("REVIT_SSM_EXPORTER_ROOT"),
                  os.path.join(os.path.expanduser("~"), "Documents", "Revit_SSM_Exporter")]
    for name in [k for k in list(sys.modules)
                 if k == "vop_interwoven" or k.startswith("vop_interwoven.")]:
        del sys.modules[name]
    importlib.invalidate_caches()
    root = next((c for c in candidates if c and os.path.isdir(
        os.path.join(c, "vop_interwoven"))), None)
    if root is None:
        raise RuntimeError("vop_interwoven not found; set repo_root")
    if root in sys.path:
        sys.path.remove(root)
    sys.path.insert(0, root)
    for name, symbols in REQUIRED_PRODUCTION.items():
        module = importlib.import_module(name)
        missing = [s for s in symbols if not hasattr(module, s)]
        if missing:
            raise RuntimeError("the vop_interwoven loaded lacks {0}".format(missing))
    return root


def _baseline(run_dir, view_id):
    pattern = re.compile(r"_{0}\.json$".format(view_id))
    for sub in ("color_id", "color_id_buffer"):
        hits = [p for p in glob.glob(os.path.join(run_dir, sub, "*_{0}.json".format(view_id)))
                if pattern.search(os.path.basename(p))]
        if len(hits) == 1:
            with open(hits[0]) as handle:
                return hits[0], json.load(handle)
    raise IOError("no single model sidecar for view {0} under {1}".format(view_id, run_dir))


def _element_labels(doc, ids):
    from Autodesk.Revit.DB import ElementId
    out = {}
    for i in ids:
        elem = doc.GetElement(ElementId(int(i)))
        if elem is None:
            out[str(i)] = {"category": None, "type": None}
            continue
        cat = elem.Category
        tid = elem.GetTypeId()
        out[str(i)] = {"category": None if cat is None else int(cat.Id.IntegerValue),
                       "type": (None if tid is None or tid.IntegerValue <= 0
                                else int(tid.IntegerValue))}
    return out


def _line_ogs(rgb):
    from Autodesk.Revit.DB import Color, OverrideGraphicSettings
    c = Color(int(rgb[0]), int(rgb[1]), int(rgb[2]))
    ogs = OverrideGraphicSettings()
    ogs.SetProjectionLineColor(c)
    ogs.SetCutLineColor(c)
    return ogs


def _precedence(doc, view, element_ids):
    """Every view-level setting that could win over the Object Styles colour."""
    from Autodesk.Revit.DB import BuiltInParameter, CategoryType, ElementId
    rec = {"template_id": None, "template_controls_model_vg": None,
           "vg_category_line_colour": [], "filters_with_line_colour": [],
           "element_overrides_with_line_colour": 0}
    tid = view.ViewTemplateId
    if tid is not None and tid != ElementId.InvalidElementId:
        rec["template_id"] = tid.IntegerValue
        template = doc.GetElement(tid)
        free = set(p.IntegerValue for p in template.GetNonControlledTemplateParameterIds())
        rec["template_controls_model_vg"] = (
            int(BuiltInParameter.VIS_GRAPHICS_MODEL) not in free)
    for cat in doc.Settings.Categories:
        if cat.CategoryType != CategoryType.Model:
            continue
        ogs = view.GetCategoryOverrides(cat.Id)
        if colour_rgb(ogs.ProjectionLineColor) or colour_rgb(ogs.CutLineColor):
            rec["vg_category_line_colour"].append(cat.Id.IntegerValue)
    for fid in view.GetFilters():
        ogs = view.GetFilterOverrides(fid)
        if colour_rgb(ogs.ProjectionLineColor) or colour_rgb(ogs.CutLineColor):
            rec["filters_with_line_colour"].append(
                {"id": fid.IntegerValue, "enabled": bool(view.GetIsFilterEnabled(fid)),
                 "visible": bool(view.GetFilterVisibility(fid))})
    for i in element_ids:
        ogs = view.GetElementOverrides(ElementId(int(i)))
        if colour_rgb(ogs.ProjectionLineColor) or colour_rgb(ogs.CutLineColor):
            rec["element_overrides_with_line_colour"] += 1
    return rec


def _prepare_view(doc, view):
    """Detach the template, hide annotation categories and view-owned
    elements. Inside the caller's transaction. Returns what it did."""
    import System.Collections.Generic as SCG
    from Autodesk.Revit.DB import CategoryType, ElementId, FilteredElementCollector
    done = {"template_detached": False, "categories_hidden": 0, "elements_hidden": 0}
    if view.ViewTemplateId != ElementId.InvalidElementId:
        view.ViewTemplateId = ElementId.InvalidElementId
        done["template_detached"] = True
    for cat in doc.Settings.Categories:
        if cat.CategoryType == CategoryType.Annotation and view.CanCategoryBeHidden(cat.Id):
            view.SetCategoryHidden(cat.Id, True)
            done["categories_hidden"] += 1
    owned = SCG.List[ElementId]()
    for e in FilteredElementCollector(doc, view.Id).OwnedByView(view.Id):
        if e.CanBeHidden(view):
            owned.Add(e.Id)
    if owned.Count:
        view.HideElements(owned)
        done["elements_hidden"] = owned.Count
    return done


def _apply_type_filters(doc, view, plan, colours, tag):
    import System.Collections.Generic as SCG
    from Autodesk.Revit.DB import (BuiltInParameter, ElementId, ElementParameterFilter,
                                   ParameterFilterElement, ParameterFilterRuleFactory,
                                   ParameterFilterUtilities)
    filterable = set(c.IntegerValue for c in ParameterFilterUtilities.GetAllFilterableCategories())
    calls = {"create_ms": [], "add_filter_ms": [], "set_overrides_ms": [], "skipped": []}
    for type_id, cat_id in plan:
        if cat_id not in filterable:
            calls["skipped"].append({"type": type_id, "category": cat_id,
                                     "reason": "category not filterable"})
            continue
        cats = SCG.List[ElementId]()
        cats.Add(ElementId(cat_id))
        rule = ParameterFilterRuleFactory.CreateEqualsRule(
            ElementId(BuiltInParameter.ELEM_TYPE_PARAM), ElementId(type_id))
        t = time.time()
        pfe = ParameterFilterElement.Create(doc, "vop_probe_{0}_{1}".format(tag, type_id),
                                            cats, ElementParameterFilter(rule))
        calls["create_ms"].append(round((time.time() - t) * 1000.0, 3))
        t = time.time()
        view.AddFilter(pfe.Id)
        calls["add_filter_ms"].append(round((time.time() - t) * 1000.0, 3))
        t = time.time()
        view.SetFilterOverrides(pfe.Id, _line_ogs(colours[type_id]))
        calls["set_overrides_ms"].append(round((time.time() - t) * 1000.0, 3))
    calls["filters"] = len(calls["add_filter_ms"])
    return calls


def _run_arm(doc, view, arm, ctx, api):
    """One arm on one view, inside its own rolled-back group."""
    from vop_interwoven import color_id_buffer as cib
    rec = {"arm": arm}
    laps = Laps()
    tiff = os.path.join(ctx["probe_dir"], "{0}_{1}.tiff".format(ctx["view_id"], arm))
    group = rolled_back_group(doc, "VOP probe colour mode {0}".format(arm), api)
    with group:
        tx = api.Transaction(doc, "VOP probe colour mode {0} setup".format(arm))
        tx.Start()
        try:
            rec["prepared"] = _prepare_view(doc, view)
            if arm == "category":
                rec["precedence"] = _precedence(doc, view, ctx["element_ids"])
            if arm == "type":
                rec["filter_calls"] = _apply_type_filters(
                    doc, view, ctx["type_plan"], ctx["type_colours"], ctx["view_id"])
                rec["label_colour"] = dict((str(k), v) for k, v in ctx["type_colours"].items())
            laps.lap("setup")
            if tx.Commit() != api.TransactionStatus.Committed:
                raise RuntimeError("the setup transaction did not commit")
            laps.lap("commit")
        except Exception:
            if tx.HasStarted() and not tx.HasEnded():
                tx.RollBack()
            raise
        attempts = []
        cib._export_tiff(doc, view, tiff, ctx["pixel_size"], view_id=ctx["view_id"],
                         fit_direction=ctx["fit_direction"], timing_out=attempts)
        laps.lap("export")
        rec["export_attempts"] = attempts
    laps.ms["restore"] = group.rollback_ms
    if arm == "category":
        rec["label_colour"] = dict((str(k), v) for k, v in ctx["category_colours"].items())
    rec["timings_ms"] = laps.ms
    rec["tiff_path"] = tiff
    return rec


def run_probe(inputs):
    inputs = list(inputs)
    at = (lambda i: inputs[i] if len(inputs) > i else None)
    if not at(0) or not at(2):
        raise ValueError("IN[0] output root and IN[2] baseline run directory are required")
    options = at(3) or {}
    if not isinstance(options, dict):
        options = (dict((str(k), options[k]) for k in options.Keys)
                   if hasattr(options, "Keys") else json.loads(str(options)))
    repo = _ensure_repo(options.get("repo_root"))
    arms = select_arms(options.get("arms"))
    view_ids = [int(str(v).strip()) for v in (at(1) if isinstance(at(1), (list, tuple))
                                              else str(at(1) or "").split(",")) if str(v).strip()]
    import Autodesk.Revit.DB as api
    from RevitServices.Persistence import DocumentManager
    from RevitServices.Transactions import TransactionManager
    from vop_interwoven.color_id_buffer import stage_a_palette
    doc = DocumentManager.Instance.CurrentDBDocument
    TransactionManager.Instance.ForceCloseTransaction()
    stamp = time.strftime("%Y%m%dT%H%M%S")
    probe_dir = os.path.join(os.path.abspath(str(at(0))), "probe_colour_mode_" + stamp)
    os.makedirs(probe_dir)
    step = int(options.get("palette_step") or DEFAULT_PALETTE_STEP)
    record = {"schema": SCHEMA, "probe": {"name": PROBE_NAME, "version": PROBE_VERSION},
              "started_at": stamp, "finalized": False, "repo_root": repo,
              "doc_title": getattr(doc, "Title", None), "baseline_run_dir": at(2),
              "arms": arms, "views": [], "exceptions": [],
              "note": "surface and cut fill patterns keep their own colours in the "
                      "as_is and category arms, and in type (line colour only): "
                      "their pixels read as off-palette"}
    styles_before = read_object_styles(doc.Settings.Categories)
    record["object_styles_categories"] = len(styles_before)
    model_cats = [c for c in doc.Settings.Categories if c.CategoryType == api.CategoryType.Model]
    palette, _step, _pal = stage_a_palette(len(model_cats) + 1, step, reserve_tick_colour=True)
    category_colours = label_colours([c.Id.IntegerValue for c in model_cats], palette)
    outer = rolled_back_group(doc, "VOP probe colour mode (Object Styles)", api)
    try:
        with outer:
            views = []
            for vid in view_ids:
                v = {"view_id": vid, "arms": {}}
                try:
                    path, sidecar = _baseline(at(2), vid)
                    frame = sidecar.get("frame") or sidecar.get("resolution") or {}
                    ids = baseline_host_ids(sidecar)
                    labels = _element_labels(doc, ids)
                    plan = type_filter_plan(dict((int(k), x) for k, x in labels.items()))
                    tpal, _s, _r = stage_a_palette(len(plan) + 1, step, reserve_tick_colour=True)
                    v.update(baseline_sidecar=path, element_labels=labels,
                             crop_write=frame.get("crop_write"))
                    ctx = {"probe_dir": probe_dir, "view_id": vid, "element_ids": ids,
                           "pixel_size": int(frame["pixel_size"]),
                           "fit_direction": frame.get("fit_direction") or "horizontal",
                           "type_plan": plan,
                           "type_colours": label_colours([t for t, _c in plan], tpal),
                           "category_colours": category_colours}
                    views.append((v, ctx))
                except Exception as ex:
                    v["error"] = "{0}: {1}".format(type(ex).__name__, ex)
                    record["exceptions"].append({"view_id": vid,
                                                 "traceback": traceback.format_exc()})
                record["views"].append(v)
            for arm in [a for a in arms if a != "category"]:
                for v, ctx in views:
                    _arm_into(doc, v, arm, ctx, api, record)
            if "category" in arms:
                record["object_styles_timings_ms"] = apply_object_styles(
                    doc, model_cats, category_colours, api)
                for v, ctx in views:
                    _arm_into(doc, v, "category", ctx, api, record)
    finally:
        styles_after = read_object_styles(doc.Settings.Categories)
        diffs = object_styles_diff(styles_before, styles_after)
        record["object_styles_restore_ms"] = outer.rollback_ms
        record["object_styles_restored"] = not diffs
        record["object_styles_differences"] = diffs[:200]
        record["document_modified_after"] = bool(getattr(doc, "IsModified", False))
        record["finished_at"] = time.strftime("%Y%m%dT%H%M%S")
        record["finalized"] = True
        json_path = os.path.join(probe_dir, "probe_colour_mode_{0}.json".format(stamp))
        # Written LAST, once every field is final (CLAUDE.md, defect class 4).
        with open(json_path, "w") as handle:
            json.dump(record, handle, indent=2, sort_keys=True, default=str)
    if diffs:
        raise RuntimeError("OBJECT STYLES NOT RESTORED: {0} categories differ; see {1}".format(
            len(diffs), json_path))
    return json_path


def _arm_into(doc, v, arm, ctx, api, record):
    view = doc.GetElement(api.ElementId(ctx["view_id"]))
    try:
        v["arms"][arm] = _run_arm(doc, view, arm, ctx, api)
    except Exception as ex:
        v["arms"][arm] = {"arm": arm, "error": "{0}: {1}".format(type(ex).__name__, ex)}
        record["exceptions"].append({"view_id": ctx["view_id"], "arm": arm,
                                     "traceback": traceback.format_exc()})


if "IN" in globals():
    try:
        OUT = run_probe(IN)  # noqa: F821
    except Exception as _fatal:
        OUT = {"json_path": None, "fatal": "{0}: {1}".format(type(_fatal).__name__, _fatal),
               "traceback": traceback.format_exc()}
