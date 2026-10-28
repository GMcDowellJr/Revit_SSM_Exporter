"""Per-view change fingerprints for hash-and-skip, phase 1 (shadow).

docs/DESIGN_CHANGE_SKIP.md is the design; the evidence is
tests/dynamo/PROBE_CHANGE_DETECTION.md (Q1b). Phase 1 RECORDS and changes no
capture: every view's fingerprint is written to ``view_fingerprints_<d>.json``
in the run directory, and tools/validate_change_skip.py scores the verdict
``skip_verdict`` would give against the captures themselves.

A view's fingerprint (S-1) is a SHA-1 over component SHA-1s. Each component
hashes sorted ``id:VersionGuid`` pairs:

  view        the view itself
  template    its view template (none: an empty component)
  filters     the filters applied to the view and to its template
  members     view_membership_ids (the capture's own read)
  types       the types its members use (GetTypeId)
  references  every ElementId-valued parameter of the view and its template

A document graphics fingerprint (S-2) hashes every category and
subcategory's line weights, colour, line pattern and material, and the
VersionGuid of every LinePatternElement, FillPatternElement, Material and
GraphicsStyle. Any read that fails makes the fingerprint ``None``: unknown is
never "unchanged" (S-5). No geometry is read (S-4): VersionGuid, parameters
and ids only.

Pure parts are importable without Revit; the Revit parts import it inside
the functions.
"""

import hashlib
import json
import os
import time

SCHEMA = "vop.view_fingerprints.v1"
# S-9: raised whenever a component or a read changes. A baseline from another
# basis is not comparable, and the view is captured.
BASIS_VERSION = 1
COMPONENTS = ("view", "template", "filters", "members", "types", "references")
MISSING_GUID = "-"
# Capture statuses a baseline view may have to be skipped against (S-6.2).
BASELINE_STATUSES = ("success",)
# The skip rules in the order they are tested; the verdict names the first
# that fails (docs/DESIGN_CHANGE_SKIP.md, S-6).
RULES = ("no_baseline_fingerprint", "baseline_capture_not_success",
         "config_hash_differs", "exporter_major_differs", "revit_build_differs",
         "open_mode_differs", "basis_version_differs", "graphics_differs",
         "fingerprint_unknown", "shows_links_or_imports", "fingerprint_differs")


# ======================================================================
# PURE
# ======================================================================

def _sha1(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def component_hash(ids, guids):
    """PURE. SHA-1 of the sorted ``id:VersionGuid`` pairs of ``ids``
    (``-`` for an id ``guids`` lacks); ``(sha1, missing count)``."""
    ids = sorted(set(int(i) for i in ids))
    missing = sum(1 for i in ids if guids.get(i) is None)
    return _sha1(",".join("{0}:{1}".format(i, guids.get(i) or MISSING_GUID)
                          for i in ids)), missing


def view_fingerprint(view_id, inputs, guids):
    """PURE. ``inputs``: ``{members, template_id, filter_ids, type_ids,
    reference_ids}`` (lists of ints; ``template_id`` -1 for none). Returns
    ``{"fingerprint", "components", "counts", "missing_guids"}``, with
    ``fingerprint`` None (and no components) when any input is None or the
    VersionGuid map is."""
    keys = ("members", "template_id", "filter_ids", "type_ids", "reference_ids")
    if inputs is None or guids is None or any(inputs.get(k) is None for k in keys):
        return {"fingerprint": None, "components": None, "counts": None,
                "missing_guids": None}
    tid = int(inputs["template_id"])
    parts = {
        "view": [int(view_id)],
        "template": [] if tid in (-1,) else [tid],
        "filters": inputs["filter_ids"],
        "members": inputs["members"],
        "types": inputs["type_ids"],
        "references": inputs["reference_ids"],
    }
    components, missing = {}, 0
    for name in COMPONENTS:
        components[name], miss = component_hash(parts[name], guids)
        missing += miss
    return {"fingerprint": _sha1("|".join("{0}={1}".format(n, components[n])
                                          for n in COMPONENTS)),
            "components": components,
            "counts": dict((n, len(set(parts[n]))) for n in COMPONENTS),
            "missing_guids": missing}


def graphics_fingerprint(category_rows, element_ids, guids):
    """PURE. ``category_rows``: one tuple of plain values per category and
    subcategory; ``element_ids``: the pattern, material and graphics-style
    elements. None when either input is None."""
    if category_rows is None or element_ids is None or guids is None:
        return None
    rows = sorted("|".join(str(v) for v in row) for row in category_rows)
    elements, _missing = component_hash(element_ids, guids)
    return _sha1("categories:" + "\n".join(rows) + "\nelements:" + elements)


def exporter_major(version):
    """PURE. ``"3.2.0+src.05e4"`` -> 3; None when unreadable."""
    try:
        return int(str(version).split(".")[0])
    except (TypeError, ValueError):
        return None


def run_facts(run_meta, fingerprints):
    """PURE. What the skip rules compare between two runs: from run_meta the
    capture's config hash, exporter MAJOR and Revit build; from the
    fingerprints file the open mode, basis version and graphics hash."""
    meta = run_meta or {}
    fp = fingerprints or {}
    revit = meta.get("revit_version") or {}
    if isinstance(revit, dict) and "value" in revit:
        revit = revit.get("value") or {}
    return {"config_hash": meta.get("config_hash"),
            "exporter_major": exporter_major(meta.get("exporter_version")),
            "revit_build": (revit or {}).get("version_build"),
            "open_mode": (fp.get("document") or {}).get("open_mode"),
            "basis_version": fp.get("basis_version"),
            "graphics": ((fp.get("document") or {}).get("graphics") or {}).get("sha1")}


def skip_verdict(base_facts, cur_facts, base_view, cur_view, base_status):
    """PURE. Would the current run skip this view against its baseline?

    ``*_facts``: run_facts() of the baseline and current runs; ``*_view``: the
    view's entry in each fingerprints file (None when absent);
    ``base_status``: the view's capture_status in the baseline run's
    run_meta. Returns ``{"would_skip": bool, "rule": <first failing rule> or
    None}``. An unknown on either side fails its rule: unknown never skips."""
    def _fail(rule):
        return {"would_skip": False, "rule": rule}
    if base_view is None:
        return _fail("no_baseline_fingerprint")
    if base_status not in BASELINE_STATUSES:
        return _fail("baseline_capture_not_success")
    for key, rule in (("config_hash", "config_hash_differs"),
                      ("exporter_major", "exporter_major_differs"),
                      ("revit_build", "revit_build_differs"),
                      ("open_mode", "open_mode_differs"),
                      ("basis_version", "basis_version_differs"),
                      ("graphics", "graphics_differs")):
        a, b = base_facts.get(key), cur_facts.get(key)
        if a is None or b is None or a != b:
            return _fail(rule)
    if cur_view is None or not base_view.get("fingerprint") or not cur_view.get("fingerprint"):
        return _fail("fingerprint_unknown")
    if base_view.get("shows_links_or_imports") is not False \
            or cur_view.get("shows_links_or_imports") is not False:
        return _fail("shows_links_or_imports")
    if base_view["fingerprint"] != cur_view["fingerprint"]:
        return _fail("fingerprint_differs")
    return {"would_skip": True, "rule": None}


def changed_components(base_view, cur_view):
    """PURE. The component names whose hashes differ (None when either side
    has none)."""
    a = (base_view or {}).get("components")
    b = (cur_view or {}).get("components")
    if not a or not b:
        return None
    return [n for n in COMPONENTS if a.get(n) != b.get(n)]


def resolve_type_name(read_name, enum_name):
    """PURE. A ViewType's name: the reader's when it is a name, else, when it
    is a number (Dynamo's Python engine hands enums over as numbers: run
    20261026T095313), ``enum_name(int)``; None when neither gives one."""
    text = None if read_name is None else str(read_name)
    if text and not text.lstrip("-").isdigit():
        return text
    if text is None:
        return None
    try:
        return enum_name(int(text)) or None
    except Exception:  # noqa: BLE001 -- an unnamed type is recorded as None
        return None


def summarize(views):
    """PURE. Counts over a fingerprints file's ``views``."""
    known = [v for v in views.values() if v.get("fingerprint")]
    return {"views": len(views), "fingerprinted": len(known),
            "unknown": len(views) - len(known),
            "shows_links_or_imports": sum(1 for v in views.values()
                                          if v.get("shows_links_or_imports"))}


# ======================================================================
# REVIT
# ======================================================================

def _eid(value):
    if value is None:
        return None
    for attr in ("Value", "IntegerValue"):
        inner = getattr(value, attr, None)
        if inner is not None:
            return int(inner)
    return None


def read_version_guids(doc):
    """``({element id: str(VersionGuid)}, stats)`` over every element and
    element type. A read that raises is counted; the first is kept."""
    from Autodesk.Revit.DB import FilteredElementCollector
    out, errors, first_error = {}, 0, None
    t0 = time.time()
    for collector in (FilteredElementCollector(doc).WhereElementIsNotElementType(),
                      FilteredElementCollector(doc).WhereElementIsElementType()):
        for elem in collector:
            try:
                out[_eid(elem.Id)] = str(elem.VersionGuid)
            except Exception as ex:  # noqa: BLE001 -- counted; first kept
                errors += 1
                if first_error is None:
                    first_error = "{0}: {1}".format(type(ex).__name__, ex)
    return out, {"elements": len(out), "errors": errors, "first_error": first_error,
                 "ms": round((time.time() - t0) * 1000.0, 1)}


def _category_row(cat):
    """One category's drawing settings as plain values; an unreadable field
    is ``?`` (stable while it stays unreadable)."""
    from Autodesk.Revit.DB import GraphicsStyleType

    def _try(fn):
        try:
            return fn()
        except Exception:  # noqa: BLE001 -- recorded as "?" in the row
            return "?"
    colour = _try(lambda: cat.LineColor)
    if colour is not None and not isinstance(colour, str):
        colour = _try(lambda: "{0},{1},{2}".format(colour.Red, colour.Green, colour.Blue))
    return (_eid(cat.Id), _try(lambda: str(cat.Name)),
            _try(lambda: cat.GetLineWeight(GraphicsStyleType.Projection)),
            _try(lambda: cat.GetLineWeight(GraphicsStyleType.Cut)),
            colour,
            _try(lambda: _eid(cat.GetLinePatternId(GraphicsStyleType.Projection))),
            _try(lambda: _eid(cat.GetLinePatternId(GraphicsStyleType.Cut))),
            _try(lambda: _eid(cat.Material.Id) if cat.Material is not None else None))


def read_graphics_state(doc, guids):
    """``(sha1 or None, record)``: the document graphics fingerprint (S-2)."""
    from Autodesk.Revit.DB import (FillPatternElement, FilteredElementCollector,
                                   GraphicsStyle, LinePatternElement, Material)
    t0 = time.time()
    rows, error = [], None
    try:
        for cat in doc.Settings.Categories:
            rows.append(_category_row(cat))
            for sub in cat.SubCategories:
                rows.append(_category_row(sub))
        element_ids = []
        for cls in (LinePatternElement, FillPatternElement, Material, GraphicsStyle):
            element_ids.extend(_eid(e.Id) for e in FilteredElementCollector(doc).OfClass(cls))
    except Exception as ex:  # noqa: BLE001 -- the fingerprint is None, the reason kept
        error = "{0}: {1}".format(type(ex).__name__, ex)
        rows, element_ids = None, None
    sha1 = graphics_fingerprint(rows, element_ids, guids)
    return sha1, {"sha1": sha1, "categories": None if rows is None else len(rows),
                  "elements": None if element_ids is None else len(element_ids),
                  "unreadable_fields": None if rows is None else sum(
                      1 for r in rows for v in r if v == "?"),
                  "error": error, "ms": round((time.time() - t0) * 1000.0, 1)}


def read_open_mode(doc):
    """``{"detached", "workshared", "closed_user_worksets"}`` as the document
    reports them; None for a value that cannot be read."""
    from Autodesk.Revit.DB import FilteredWorksetCollector, WorksetKind
    out = {}
    for key, fn in (("detached", lambda: bool(doc.IsDetached)),
                    ("workshared", lambda: bool(doc.IsWorkshared))):
        try:
            out[key] = fn()
        except Exception:  # noqa: BLE001 -- None: the open mode is unknown
            out[key] = None
    try:
        out["closed_user_worksets"] = (
            sum(1 for w in FilteredWorksetCollector(doc).OfKind(WorksetKind.UserWorkset)
                if not w.IsOpen) if out.get("workshared") else 0)
    except Exception:  # noqa: BLE001 -- None: the open mode is unknown
        out["closed_user_worksets"] = None
    return out


def view_type_name(view):
    """The view's ViewType name, numbers resolved through Enum.GetName."""
    import clr
    import System
    from Autodesk.Revit.DB import ViewType
    from .batch_capture import _view_type_name
    enum_t = clr.GetClrType(ViewType)
    return resolve_type_name(_view_type_name(view),
                             lambda number: System.Enum.GetName(enum_t, number))


def _filter_ids(view):
    """A view's filters; [] for a view that allows no graphic overrides."""
    if not bool(view.AreGraphicsOverridesAllowed()):
        return []
    return [_eid(i) for i in view.GetFilters()]


def _reference_ids(view):
    """Every ElementId-valued parameter's value on ``view`` (invalid ids out)."""
    from Autodesk.Revit.DB import StorageType
    out = []
    for p in view.Parameters:
        if p.StorageType == StorageType.ElementId:
            eid = _eid(p.AsElementId())
            if eid is not None and eid != -1:
                out.append(eid)
    return out


def read_view_inputs(doc, view):
    """``(inputs, shows_links_or_imports, unread)`` for one view. A failed
    read leaves its input None (the fingerprint is then unknown) and is
    named in ``unread``."""
    from Autodesk.Revit.DB import ElementId, ImportInstance, RevitLinkInstance
    from .stage_a_registered_capture import view_membership_ids
    inputs, unread = {}, {}
    try:
        inputs["members"] = view_membership_ids(doc, view)
    except Exception as ex:  # noqa: BLE001 -- named in unread
        inputs["members"], unread["members"] = None, "{0}: {1}".format(type(ex).__name__, ex)
    template = None
    try:
        inputs["template_id"] = _eid(view.ViewTemplateId)
        if inputs["template_id"] not in (None, -1):
            template = doc.GetElement(view.ViewTemplateId)
    except Exception as ex:  # noqa: BLE001 -- named in unread
        inputs["template_id"], unread["template_id"] = None, "{0}: {1}".format(
            type(ex).__name__, ex)
    try:
        ids = _filter_ids(view)
        if template is not None:
            ids += _filter_ids(template)
        inputs["filter_ids"] = ids
    except Exception as ex:  # noqa: BLE001 -- named in unread
        inputs["filter_ids"], unread["filter_ids"] = None, "{0}: {1}".format(
            type(ex).__name__, ex)
    try:
        ids = _reference_ids(view)
        if template is not None:
            ids += _reference_ids(template)
        inputs["reference_ids"] = ids
    except Exception as ex:  # noqa: BLE001 -- named in unread
        inputs["reference_ids"], unread["reference_ids"] = None, "{0}: {1}".format(
            type(ex).__name__, ex)
    links = None
    if inputs.get("members") is not None:
        try:
            types, links = set(), False
            for i in inputs["members"]:
                elem = doc.GetElement(ElementId(int(i)))
                if elem is None:
                    continue
                if isinstance(elem, (RevitLinkInstance, ImportInstance)):
                    links = True
                tid = _eid(elem.GetTypeId())
                if tid not in (None, -1):
                    types.add(tid)
            inputs["type_ids"] = sorted(types)
        except Exception as ex:  # noqa: BLE001 -- named in unread
            inputs["type_ids"], links = None, None
            unread["type_ids"] = "{0}: {1}".format(type(ex).__name__, ex)
    else:
        inputs["type_ids"] = None
    return inputs, links, unread


def fingerprint_views(doc, view_ids, mode, opened=None):
    """The fingerprints record for ``view_ids`` of ``doc``. Read-only: no
    transaction. ``mode``: "capture" (recorded beside a capture run) or
    "backfill" (computed later for an existing run)."""
    from Autodesk.Revit.DB import ElementId, View
    t0 = time.time()
    guids, guid_stats = read_version_guids(doc)
    graphics_sha1, graphics = read_graphics_state(doc, guids)
    record = {"schema": SCHEMA, "basis_version": BASIS_VERSION, "mode": mode,
              "computed_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "opened": opened,
              "document": {"title": getattr(doc, "Title", None),
                           "open_mode": read_open_mode(doc),
                           "version_guids": guid_stats, "graphics": graphics},
              "views": {}}
    for vid in view_ids:
        t1 = time.time()
        entry = {"view_id": int(vid)}
        view = doc.GetElement(ElementId(int(vid)))
        if not isinstance(view, View):
            entry.update(fingerprint=None, absent=True)
        else:
            try:
                entry["view_type"] = view_type_name(view)
            except Exception:  # noqa: BLE001 -- None: the type is unknown
                entry["view_type"] = None
            inputs, links, unread = read_view_inputs(doc, view)
            entry.update(view_fingerprint(vid, inputs, guids))
            entry["shows_links_or_imports"] = links
            if unread:
                entry["unread"] = unread
        entry["ms"] = round((time.time() - t1) * 1000.0, 1)
        record["views"][str(int(vid))] = entry
    record["summary"] = summarize(record["views"])
    record["ms"] = round((time.time() - t0) * 1000.0, 1)
    return record


def write_fingerprints(run_dir, record):
    """Write the record as the run's fingerprints file (atomically; written
    LAST, CLAUDE.md defect class 4) and stamp run_meta ``view_fingerprints``
    with its name and summary. Returns ``(path, stamp error or None)``."""
    from . import output_layout
    from .batch_capture import run_meta_of, stamp_run_meta
    meta, err = run_meta_of(run_dir)
    if meta is None:
        raise ValueError("no run_meta in {0}: {1}".format(run_dir, err))
    name = output_layout.fingerprints_name(output_layout.census_file_date(meta, run_dir))
    path = os.path.join(run_dir, name)
    record = dict(record, run_id=meta.get("run_id"), run_dir=str(run_dir))
    tmp = path + ".tmp"
    with open(tmp, "w") as handle:
        json.dump(record, handle, indent=1, sort_keys=True)
    os.replace(tmp, path)
    stamp = dict(record["summary"], file=name, mode=record["mode"],
                 basis_version=record["basis_version"], ms=record.get("ms"))
    return path, stamp_run_meta(run_dir, "view_fingerprints", stamp)


def fingerprint_existing_run(doc, run_dir, opened=None):
    """BACKFILL. Fingerprint the views an existing, finalized run captured,
    from ``doc`` (the same archive, opened as the run was), and write the
    run's fingerprints file. Refuses a document whose title is not the run's
    and a run that is not finalized."""
    from .batch_capture import run_meta_of, title_matches
    meta, err = run_meta_of(run_dir)
    if meta is None:
        raise ValueError("no run_meta in {0}: {1}".format(run_dir, err))
    if meta.get("finalized") is not True:
        raise ValueError("run {0} is not finalized".format(run_dir))
    recorded = meta.get("doc_title")
    if isinstance(recorded, dict):
        recorded = recorded.get("value")
    if not title_matches(recorded, getattr(doc, "Title", None)):
        raise ValueError("the open document is {0!r}, the run was captured from {1!r}".format(
            getattr(doc, "Title", None), recorded))
    view_ids = [int(v) for v in meta.get("views_requested") or []]
    record = fingerprint_views(doc, view_ids, "backfill", opened=opened)
    path, stamp_err = write_fingerprints(run_dir, record)
    return {"run_dirs": [run_dir], "fingerprints": path, "summary": record["summary"],
            "stamp_error": stamp_err}
