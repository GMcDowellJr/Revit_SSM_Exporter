"""Stage A capture-state probe for Revit 2025 / Dynamo 3.x CPython3.

Answers five questions about view state BEFORE any capture code changes:

  Q1  Does writing view.CropBox on a split elevation undo the split?
  Q2  Can the API see that split?
  Q3  Does a scope box (or a non-rectangular crop shape) override the crop
      box the capture writes? Discriminated against a control view.
  Q4  What do ShadowIntensity and a flat background change on an elevation?
  Q5  Can DisplayStyle be set with the template attached, and after detaching?

Dynamo inputs (every view id is an integer ElementId value):
    IN[0] = output directory (required). A subfolder
            capture_state_<timestamp>/ is created in it.
    IN[1] = Q1/Q2 split elevation view id          (default 6112047)
    IN[2] = Q3 test view id                         (default 11999340)
    IN[3] = Q3 SLAB PLAN view id                    (no default; Q3 records
            "not_provided" for it when empty)
    IN[4] = Q3 control view id: a view that HAS a scope box and registered
            fine in run 20261001T084840_1d0b0c1     (no default)
    IN[5] = Q4 elevation view id                    (default 2888380)
    IN[6] = Q5 view ids, a list                     (default [13663964, 11999340])
    IN[7] = export pixel width                      (default 2000)
    IN[8] = questions to run: "all" or a comma list of q1_q2,q3,q4,q5
            (default "all")

Output: OUT = the path of probe_capture_state_<timestamp>.json. The TIFFs sit
beside it, named <step>_<viewid>.tiff. Pixel analysis is OFFLINE:
    python tools/analyze_capture_state_probe.py <that folder>

THE PROBE CHANGES NOTHING PERSISTENTLY. Every write happens inside ONE outer
TransactionGroup that is rolled back; steps that must start from the
untouched view run inside a NESTED TransactionGroup that is rolled back
before the next step. After the outer rollback every probed view is read
again and compared with the read taken before the group started. A field
that changed is listed under ``verify.changed`` and the probe does not try to
fix it.

Every read and every write is recorded as
``{"state": "value" | "unavailable" | "raised", "value": ..., "error": ...}``
-- never absent. No bare ``except``, no ``except: pass``.

Standalone on purpose: it can be pasted into a Dynamo Python node and it
imports nothing from vop_interwoven (see PROBE_CAPTURE_STATE.md, "What is
reimplemented").
"""
from __future__ import print_function

import hashlib
import json
import os
import struct
import sys
import time
import traceback


PROBE_NAME = "capture_state"
PROBE_VERSION = "2026-10-02.1"
SCHEMA = "vop.probe.capture_state.v1"

DEFAULT_Q1_VIEW = 6112047
DEFAULT_Q3_TEST_VIEW = 11999340
DEFAULT_Q4_VIEW = 2888380
DEFAULT_Q5_VIEWS = (13663964, 11999340)
DEFAULT_PIXEL_WIDTH = 2000
QUESTIONS = ("q1_q2", "q3", "q4", "q5", "q1b", "q3b", "q5b", "q6")
ROUND1_QUESTIONS = ("q1_q2", "q3", "q4", "q5")
# The default since 2026-10-02.1: round 2 only. "round1" and "all" select
# the others.
ROUND2_QUESTIONS = ("q1b", "q3b", "q5b", "q6")

# Round 2 view defaults (IN[9] keys override them).
DEFAULT_Q1B_VIEWS = (6112047, 6207878)       # Plaza elevation; WEST - EAST SECTION
DEFAULT_Q3B_VIEWS = (11999340, 5823803)      # MOHAVE; slab plan as the control
DEFAULT_Q5B_VIEWS = (13663964, 11999340)     # dependent views
DEFAULT_Q6_VIEWS = (5823803, 9948, 11999340)
# Ids round 1 saw enter or leave Plaza's membership on the identity write.
Q1B_WATCH_IDS = (15846537, 15809670)
OPTION_KEYS = ("repo_root", "q1b_views", "q3b_views", "q5b_views", "q6_views",
               "q6_extra_views")

# Case-insensitive substrings of a parameter's definition name that put it in
# the per-view parameter dump.
PARAM_KEYWORDS = ("shadow", "background", "light", "sun", "display", "crop",
                  "scope", "far clip", "depth", "split")
# Read-back equality for a crop box, in feet.
CROP_TOLERANCE_FT = 1.0e-6
# Q1 S2's fallback crop: S0's box inset by this much on every edge.
Q1_INSET_FT = 1.0
# Q3's written crop: S0's box scaled about its centre by this factor.
Q3_SHRINK = 0.9
# Added / removed member ids reported per comparison.
MEMBER_DIFF_LIMIT = 50


# ======================================================================
# THREE-VALUED RECORDS
# ======================================================================

def rec_value(value):
    return {"state": "value", "value": value, "error": None}


def rec_unavailable(reason):
    return {"state": "unavailable", "value": None, "error": str(reason)}


def rec_raised(ex):
    return {"state": "raised", "value": None,
            "error": "{0}: {1}".format(type(ex).__name__, ex)}


def read(fn, *args):
    """``fn(*args)`` as a three-valued record."""
    try:
        return rec_value(fn(*args))
    except Exception as ex:
        return rec_raised(ex)


def read_attr(obj, name, conv=None):
    """``obj.name`` (converted) as a three-valued record. A missing attribute
    is "unavailable"; a getter that raises is "raised"."""
    if obj is None:
        return rec_unavailable("object is None")
    try:
        present = hasattr(obj, name)
    except Exception as ex:
        return rec_raised(ex)
    if not present:
        return rec_unavailable("no attribute {0}".format(name))
    try:
        value = getattr(obj, name)
        return rec_value(conv(value) if conv is not None else value)
    except Exception as ex:
        return rec_raised(ex)


def call_method(obj, name, args=(), conv=None):
    """``obj.name(*args)`` (converted) as a three-valued record."""
    if obj is None:
        return rec_unavailable("object is None")
    try:
        present = hasattr(obj, name)
    except Exception as ex:
        return rec_raised(ex)
    if not present:
        return rec_unavailable("no method {0}".format(name))
    try:
        value = getattr(obj, name)(*args)
        return rec_value(conv(value) if conv is not None else value)
    except Exception as ex:
        return rec_raised(ex)


def value_of(record, default=None):
    """The value of a "value" record, else ``default``."""
    if isinstance(record, dict) and record.get("state") == "value":
        return record.get("value")
    return default


def exception_record(stage, ex):
    return {"stage": stage, "type": type(ex).__name__, "message": str(ex),
            "traceback": traceback.format_exc()}


# ======================================================================
# PURE HELPERS (no Revit): JSON-safe values, ids, boxes, TIFF headers
# ======================================================================

def ids_fingerprint(ids):
    """``{"count", "sha1"}`` of a list of int ids, order-independent."""
    ordered = sorted(int(i) for i in ids)
    text = ",".join(str(i) for i in ordered)
    return {"count": len(ordered),
            "sha1": hashlib.sha1(text.encode("ascii")).hexdigest()}


def ids_diff(before, after, limit=MEMBER_DIFF_LIMIT):
    """Added / removed ids of ``after`` against ``before``, first ``limit``."""
    b, a = set(before), set(after)
    added, removed = sorted(a - b), sorted(b - a)
    return {"added_count": len(added), "removed_count": len(removed),
            "added": added[:limit], "removed": removed[:limit]}


def box_extent_ft(box):
    """``[width, height]`` of a box record ``{"min": xyz, "max": xyz}``, in
    the box's own (view-aligned) coordinates."""
    return [float(box["max"][0]) - float(box["min"][0]),
            float(box["max"][1]) - float(box["min"][1])]


def inset_box(box, inset_ft):
    """``box`` (record) inset by ``inset_ft`` on every X/Y edge; Z unchanged.
    None when the inset would leave no box."""
    mn, mx = [float(v) for v in box["min"]], [float(v) for v in box["max"]]
    if mx[0] - mn[0] <= 2.0 * inset_ft or mx[1] - mn[1] <= 2.0 * inset_ft:
        return None
    return {"min": [mn[0] + inset_ft, mn[1] + inset_ft, mn[2]],
            "max": [mx[0] - inset_ft, mx[1] - inset_ft, mx[2]]}


def scaled_box(box, factor):
    """``box`` (record) scaled about its X/Y centre by ``factor``; Z unchanged."""
    mn, mx = [float(v) for v in box["min"]], [float(v) for v in box["max"]]
    cx, cy = (mn[0] + mx[0]) / 2.0, (mn[1] + mx[1]) / 2.0
    hx, hy = (mx[0] - mn[0]) * factor / 2.0, (mx[1] - mn[1]) * factor / 2.0
    return {"min": [cx - hx, cy - hy, mn[2]], "max": [cx + hx, cy + hy, mx[2]]}


def _world(transform, point):
    o, bx, by, bz = (transform[k] for k in ("origin", "basis_x", "basis_y", "basis_z"))
    return [o[i] + bx[i] * point[0] + by[i] * point[1] + bz[i] * point[2]
            for i in range(3)]


def boxes_equal(written, read_back, tol=CROP_TOLERANCE_FT):
    """Compare two crop-box records. ``local`` compares Min/Max as written;
    ``world`` compares the corners through each box's own transform, so a
    box Revit re-expressed about another origin still reads as the same box.
    Either is None when its inputs are missing."""
    def _close(a, b):
        return all(abs(float(x) - float(y)) <= tol for x, y in zip(a, b))
    out = {"tolerance_ft": tol, "local": None, "world": None}
    if not written or not read_back:
        return out
    out["local"] = bool(_close(written["min"], read_back["min"])
                        and _close(written["max"], read_back["max"]))
    tw, tr = written.get("transform"), read_back.get("transform")
    if tw and tr:
        out["world"] = bool(_close(_world(tw, written["min"]), _world(tr, read_back["min"]))
                            and _close(_world(tw, written["max"]), _world(tr, read_back["max"])))
    return out


_TIFF_TAG_WIDTH = 256
_TIFF_TAG_HEIGHT = 257
_TIFF_SCALAR = {1: "B", 3: "H", 4: "I"}


def read_tiff_dimensions(path):
    """``(width, height)`` of a classic TIFF from its header (stdlib only).
    Raises on anything it cannot read exactly."""
    with open(path, "rb") as fh:
        header = fh.read(8)
        if len(header) < 8:
            raise ValueError("too short to hold a TIFF header")
        if header[0:2] == b"II":
            endian = "<"
        elif header[0:2] == b"MM":
            endian = ">"
        else:
            raise ValueError("no TIFF byte-order mark")
        magic, ifd = struct.unpack(endian + "HI", header[2:8])
        if magic != 42:
            raise ValueError("TIFF magic {0}, expected 42".format(magic))
        fh.seek(ifd)
        raw = fh.read(2)
        if len(raw) < 2:
            raise ValueError("truncated IFD count")
        (count,) = struct.unpack(endian + "H", raw)
        entries = fh.read(count * 12)
        if len(entries) < count * 12:
            raise ValueError("truncated IFD")
    dims = {}
    for i in range(count):
        tag, ftype, n = struct.unpack(endian + "HHI", entries[i * 12:i * 12 + 8])
        if tag not in (_TIFF_TAG_WIDTH, _TIFF_TAG_HEIGHT):
            continue
        fmt = _TIFF_SCALAR.get(ftype)
        if fmt is None or n != 1:
            raise ValueError("tag {0} has type {1} count {2}".format(tag, ftype, n))
        (dims[tag],) = struct.unpack(endian + fmt, entries[i * 12 + 8:i * 12 + 8 + struct.calcsize(fmt)])
    if _TIFF_TAG_WIDTH not in dims or _TIFF_TAG_HEIGHT not in dims:
        raise ValueError("no ImageWidth/ImageLength tag")
    return int(dims[_TIFF_TAG_WIDTH]), int(dims[_TIFF_TAG_HEIGHT])


def parse_view_id(value, default=None):
    """An input view id as int, a Dynamo-wrapped view's id, or ``default``."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    inner = getattr(value, "InternalElement", None)
    if inner is not None:
        value = inner
    eid = getattr(value, "Id", None)
    if eid is not None:
        found = element_id_int(eid)
        if found is not None:
            return found
    return int(str(value).strip())


def parse_view_ids(value, default):
    if value is None or (isinstance(value, str) and not value.strip()):
        return list(default)
    if isinstance(value, (list, tuple)):
        return [parse_view_id(v) for v in value if parse_view_id(v) is not None]
    if isinstance(value, str) and "," in value:
        return [int(v) for v in value.split(",") if v.strip()]
    return [parse_view_id(value)]


def select_questions(value):
    """None or "" -> round 2; "round1", "round2" or "all" -> that set; else a
    comma list of names, refused when one is unknown."""
    text = value.strip().lower() if isinstance(value, str) else None
    if value is None or text in ("", "round2"):
        return list(ROUND2_QUESTIONS)
    if text == "round1":
        return list(ROUND1_QUESTIONS)
    if text == "all":
        return list(QUESTIONS)
    parts = value if isinstance(value, (list, tuple)) else str(value).split(",")
    chosen = [str(p).strip().lower() for p in parts if str(p).strip()]
    unknown = [q for q in chosen if q not in QUESTIONS]
    if unknown:
        raise ValueError("unknown question(s) {0}; known: {1}".format(unknown, QUESTIONS))
    return [q for q in QUESTIONS if q in chosen]


# ======================================================================
# REVIT VALUE CONVERSIONS
# ======================================================================

def element_id_int(value):
    """An ElementId as int: the 64-bit ``Value`` first (Revit 2025 deprecates
    ``IntegerValue``, whose getter can raise outside int32). None when the
    object carries neither; raises, naming both failures, when each raised."""
    if value is None:
        return None
    errors = []
    for attr in ("Value", "IntegerValue"):
        try:
            inner = getattr(value, attr, None)
            if inner is not None:
                return int(inner)
        except Exception as ex:
            errors.append("{0}: {1}: {2}".format(attr, type(ex).__name__, ex))
    if errors:
        raise ValueError("ElementId unreadable ({0})".format("; ".join(errors)))
    return None


def xyz_list(p):
    return [float(p.X), float(p.Y), float(p.Z)]


def enum_text(v):
    """An enum value by NAME. Under Python.NET 3 ``str()`` of a Revit enum
    gives its number -- round 1 recorded DisplayStyle as "7" and commit
    statuses as "3" -- so the .NET ToString() is used when the value has
    one. Comparisons never rely on this being a name: they compare against
    the same conversion of the target member (flat_colors_target())."""
    if v is None:
        return None
    to_string = getattr(v, "ToString", None)
    return str(to_string()) if callable(to_string) else str(v)


def colour_list(c):
    if c is None:
        return None
    return [int(c.Red), int(c.Green), int(c.Blue)]


def jsonable(v):
    """A Revit value made JSON-safe, for reads whose return type varies."""
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if all(hasattr(v, a) for a in ("X", "Y", "Z")):
        return xyz_list(v)
    if type(v).__name__ == "ElementId":
        return element_id_int(v)
    return str(v)


def _ensure_revit():
    import clr
    clr.AddReference("RevitAPI")
    clr.AddReference("RevitServices")


def _doc():
    from RevitServices.Persistence import DocumentManager
    return DocumentManager.Instance.CurrentDBDocument


def _force_close_dynamo_transaction():
    from RevitServices.Transactions import TransactionManager
    TransactionManager.Instance.ForceCloseTransaction()


# ======================================================================
# COMMON READS (per view)
# ======================================================================

def transform_record(t):
    return {"origin": xyz_list(t.Origin), "basis_x": xyz_list(t.BasisX),
            "basis_y": xyz_list(t.BasisY), "basis_z": xyz_list(t.BasisZ)}


def crop_box_record(view):
    """The view's CropBox as ``{"min", "max", "transform"}``."""
    box = view.CropBox
    if box is None:
        raise ValueError("view.CropBox is None")
    return {"min": xyz_list(box.Min), "max": xyz_list(box.Max),
            "transform": transform_record(box.Transform)}


def scope_box_record(doc, view):
    from Autodesk.Revit.DB import BuiltInParameter
    p = view.get_Parameter(BuiltInParameter.VIEWER_VOLUME_OF_INTEREST_CROP)
    if p is None:
        return {"present": False, "value": rec_unavailable("parameter not on this view"),
                "read_only": rec_unavailable("parameter not on this view"),
                "is_set": None}
    value = read(lambda: element_id_int(p.AsElementId()))
    out = {"present": True, "value": value, "read_only": read_attr(p, "IsReadOnly", bool),
           "is_set": None, "scope_box_name": rec_unavailable("no scope box")}
    eid = value_of(value)
    if eid is not None:
        out["is_set"] = eid != -1
        if eid != -1:
            out["scope_box_name"] = read(lambda: str(doc.GetElement(p.AsElementId()).Name))
    return out


def view_basis(view):
    return {"origin": xyz_list(view.Origin), "right": xyz_list(view.RightDirection),
            "up": xyz_list(view.UpDirection)}


def _uv(basis, p):
    d = [p[i] - basis["origin"][i] for i in range(3)]
    return (sum(d[i] * basis["right"][i] for i in range(3)),
            sum(d[i] * basis["up"][i] for i in range(3)))


def _curve_points(curve, notes):
    """The curve's tessellation; its end points (with a note saying why) when
    Tessellate raises."""
    try:
        return [xyz_list(p) for p in curve.Tessellate()]
    except Exception as ex:
        notes.append("Tessellate raised {0}: {1}; end points used".format(
            type(ex).__name__, ex))
        return [xyz_list(curve.GetEndPoint(0)), xyz_list(curve.GetEndPoint(1))]


def curve_loop_record(loop, basis):
    """A CurveLoop's curve count and UV bounding extents in the view basis."""
    pts, count, notes = [], 0, []
    for curve in loop:
        count += 1
        pts.extend(_uv(basis, p) for p in _curve_points(curve, notes))
    out = {"curve_count": count, "uv_extents": None, "notes": notes[:5]}
    if pts:
        us, vs = [p[0] for p in pts], [p[1] for p in pts]
        out["uv_extents"] = [min(us), min(vs), max(us), max(vs)]
    return out


def shape_manager_record(view):
    m_rec = call_method(view, "GetCropRegionShapeManager")
    m = value_of(m_rec)
    out = {"manager": {"state": m_rec["state"], "error": m_rec["error"]}}
    if m is None:
        return out
    for name in ("Split", "NumberOfSplitRegions", "IsSplitHorizontally",
                 "IsSplitVertically", "CanHaveShape", "ShapeSet",
                 "CanHaveAnnotationCrop", "CanBeSplit"):
        out[name] = read_attr(m, name, jsonable)
    n = value_of(out["NumberOfSplitRegions"])
    regions = []
    if isinstance(n, int) and n > 0:
        for i in range(n):
            regions.append(dict(
                (meth, call_method(m, meth, (i,), jsonable))
                for meth in ("GetSplitRegionMinimum", "GetSplitRegionMaximum",
                             "GetSplitRegionOffset")))
    out["split_regions"] = regions
    basis = read(view_basis, view)
    out["view_basis"] = basis
    b = value_of(basis)
    if b is None:
        out["crop_shape"] = rec_unavailable("no view basis")
        out["annotation_crop_shape"] = rec_unavailable("no view basis")
        return out
    out["crop_shape"] = call_method(
        m, "GetCropShape", conv=lambda loops: {
            "loop_count": len(list(loops)),
            "loops": [curve_loop_record(loop, b) for loop in loops]})
    out["annotation_crop_shape"] = call_method(
        m, "GetAnnotationCropShape", conv=lambda loop: curve_loop_record(loop, b))
    return out


def member_ids(doc, view):
    from Autodesk.Revit.DB import FilteredElementCollector
    ids = []
    for eid in FilteredElementCollector(doc, view.Id).WhereElementIsNotElementType().ToElementIds():
        value = element_id_int(eid)
        if value is not None:
            ids.append(value)
    return sorted(ids)


def _parameter_value(p):
    st = str(p.StorageType)
    if st.endswith("Double"):
        return float(p.AsDouble())
    if st.endswith("Integer"):
        return int(p.AsInteger())
    if st.endswith("String"):
        return p.AsString()
    if st.endswith("ElementId"):
        return element_id_int(p.AsElementId())
    return None


def parameter_dump(view):
    """Every parameter whose definition name holds a PARAM_KEYWORDS term."""
    out = []
    for p in view.Parameters:
        name_rec = read(lambda: str(p.Definition.Name))
        name = value_of(name_rec)
        if name is None:
            out.append({"name": name_rec})
            continue
        if not any(k in name.lower() for k in PARAM_KEYWORDS):
            continue
        out.append({"name": name,
                    "storage_type": read(lambda: str(p.StorageType)),
                    "value": read(_parameter_value, p),
                    "value_string": call_method(p, "AsValueString"),
                    "read_only": read_attr(p, "IsReadOnly", bool),
                    "built_in": read_attr(p.Definition, "BuiltInParameter", enum_text)})
    out.sort(key=lambda e: str(e.get("name")))
    return out


def common_reads(doc, view):
    """``(record, member ids or None)``. The ids stay in memory (they can
    run to tens of thousands); the record holds their count and sha1."""
    record = {
        "view_id": element_id_int(view.Id),
        "name": read_attr(view, "Name", str),
        "view_type": read_attr(view, "ViewType", enum_text),
        "template_id": read(lambda: element_id_int(view.ViewTemplateId)),
        "is_template": read_attr(view, "IsTemplate", bool),
        "primary_view_id": call_method(view, "GetPrimaryViewId", conv=element_id_int),
        "crop_box": read(crop_box_record, view),
        "crop_box_active": read_attr(view, "CropBoxActive", bool),
        "crop_box_visible": read_attr(view, "CropBoxVisible", bool),
        "display_style": read_attr(view, "DisplayStyle", enum_text),
        "shadow_intensity": read_attr(view, "ShadowIntensity", jsonable),
        "sunlight_intensity": read_attr(view, "SunlightIntensity", jsonable),
        "scope_box": read(scope_box_record, doc, view),
        "crop_region_shape": read(shape_manager_record, view),
        "parameters": read(parameter_dump, view),
    }
    ids_rec = read(member_ids, doc, view)
    ids = value_of(ids_rec)
    record["members"] = (rec_value(ids_fingerprint(ids)) if ids is not None
                         else ids_rec)
    return record, ids


UNREADABLE = "unreadable"


def _snap(record):
    """A read's value for a state snapshot, or UNREADABLE."""
    if isinstance(record, dict) and record.get("state") == "value":
        return record.get("value")
    return UNREADABLE


def background_fingerprint(view):
    """The background's type and colours, each colour UNREADABLE on its own
    when its getter raises, so one unreadable colour does not hide the rest."""
    bg = view.GetBackground()
    if bg is None:
        return None
    out = {"type": _snap(read(lambda: str(bg.GetType().Name)))}
    for name in ("SkyColor", "HorizonColor", "GroundColor", "BackgroundColor"):
        out[name] = _snap(read_attr(bg, name, colour_list))
    return out


def state_snapshot(doc, view):
    """Everything a probe step can change on a view, as plain values. The
    ONE snapshot used for the pre-group baseline, for the restore check after
    every nested rollback, and for the verify after the outer rollback."""
    from Autodesk.Revit.DB import BuiltInParameter

    def _scope_box():
        p = view.get_Parameter(BuiltInParameter.VIEWER_VOLUME_OF_INTEREST_CROP)
        return None if p is None else element_id_int(p.AsElementId())

    def _shape_set():
        return bool(view.GetCropRegionShapeManager().ShapeSet)

    ids = value_of(read(member_ids, doc, view))
    return {
        "template_id": _snap(read(lambda: element_id_int(view.ViewTemplateId))),
        "crop_box": _snap(read(crop_box_record, view)),
        "crop_box_active": _snap(read_attr(view, "CropBoxActive", bool)),
        "scope_box": _snap(read(_scope_box)),
        "shape_set": _snap(read(_shape_set)),
        "display_style": _snap(read_attr(view, "DisplayStyle", enum_text)),
        "shadow_intensity": _snap(read_attr(view, "ShadowIntensity", jsonable)),
        "background": _snap(read(background_fingerprint, view)),
        "members_sha1": ids_fingerprint(ids)["sha1"] if ids is not None else UNREADABLE,
    }


def compare_fields(before, after):
    """PURE. Per field: ``equal``, ``changed``, ``unverifiable`` (readable on
    one side only) or ``unreadable_both``. Crop boxes compare to
    CROP_TOLERANCE_FT, not bit for bit."""
    out = {}
    for key in sorted(set(before) | set(after)):
        b, a = before.get(key, UNREADABLE), after.get(key, UNREADABLE)
        if b == UNREADABLE and a == UNREADABLE:
            verdict = "unreadable_both"
        elif b == UNREADABLE or a == UNREADABLE:
            verdict = "unverifiable"
        elif key == "crop_box":
            eq = boxes_equal(b, a)
            verdict = "equal" if eq["local"] and eq["world"] is not False else "changed"
        else:
            verdict = "equal" if b == a else "changed"
        out[key] = {"verdict": verdict, "before": b, "after": a}
    return out


def restore_verdict(baseline, after, required):
    """PURE. Whether ``after`` is the baseline state again.

    Not restored when any field changed, when any field is readable on one
    side only, or when a field in ``required`` -- the state the question
    itself writes -- cannot be read on either side, since then its restore
    cannot be shown. A field unreadable on both sides that the question does
    not write is recorded but does not refuse: Revit does not expose every
    field on every view type (a plan view has no settable background)."""
    fields = compare_fields(baseline, after)
    changed = [k for k, v in sorted(fields.items()) if v["verdict"] == "changed"]
    one_sided = [k for k, v in sorted(fields.items()) if v["verdict"] == "unverifiable"]
    required_unreadable = [k for k in required
                           if fields.get(k, {}).get("verdict", "unreadable_both")
                           == "unreadable_both"]
    return {"restored": not (changed or one_sided or required_unreadable),
            "changed": changed, "unverifiable": one_sided,
            "required": list(required), "required_unreadable": required_unreadable,
            "fields": fields}


def refusal_text(verdict):
    if verdict.get("error"):
        return "state could not be read after {0}: {1}".format(
            verdict.get("checked_after"), verdict["error"])
    return ("state not restored after {0}: changed {1}, readable on one side "
            "only {2}, written but unreadable {3}".format(
                verdict.get("checked_after"), verdict.get("changed"),
                verdict.get("unverifiable"), verdict.get("required_unreadable")))


# ======================================================================
# WRITES, NESTED GROUPS, EXPORT
# ======================================================================

class TransactionLeftOpen(Exception):
    """A transaction could not be closed. ``args`` = (name, write record).

    Every later read, export and write would run inside it, and the enclosing
    groups might not roll back, so the probe stops writing: nested_group sets
    ``ctx.halted`` and run_gated refuses everything after."""


def close_open_transaction(tx):
    """Roll ``tx`` back if it has not ended. ``still_open`` is True when it
    is still open afterwards OR when that cannot be read: an unknown state is
    not a closed one."""
    rec = {"has_ended_before": read(lambda: bool(tx.HasEnded())),
           "rollback_status": None, "has_ended_after": None, "still_open": None}
    if value_of(rec["has_ended_before"]) is True:
        rec["still_open"] = False
        return rec
    try:
        rec["rollback_status"] = enum_text(tx.RollBack())
    except Exception as ex:
        rec["rollback_status"] = "raised: {0}: {1}".format(type(ex).__name__, ex)
    rec["has_ended_after"] = read(lambda: bool(tx.HasEnded()))
    rec["still_open"] = value_of(rec["has_ended_after"]) is not True
    return rec


def _settle(tx, rec):
    """Close ``tx`` after a failed write or commit, recording how."""
    rec["transaction_close"] = close_open_transaction(tx)
    return rec


def _equal(attempted, read_back):
    return attempted == read_back


def tx_write(doc, name, fn, attempted, read_back, matches=None):
    """``fn()`` inside its own Transaction, as ONE record that obeys the
    probe's write invariant:

      ``attempted``     -- the value the write asked for;
      ``commit_status`` -- what Commit returned (None if it never ran);
      ``read_back``     -- a fresh read AFTER the transaction closed, as a
                           three-valued record (``read_back()`` is required:
                           a write without a read-back cannot be expressed);
      ``took_effect``   -- ``matches(attempted, read_back value)``. Computed
                           from the read-back, NEVER from the commit status: a
                           write that commits but does not take effect is a
                           finding (``took_effect: false``), not an error.
                           None only when the read-back itself is unreadable.

    ``state`` / ``value`` / ``error`` still say whether the call raised. A
    raise, a raising Commit and a Commit that does not return Committed
    (Pending, RolledBack, Error) are recorded as raised, and in each case the
    transaction is closed before the read-back -- rolled back if it has not
    ended. One that cannot be closed raises TransactionLeftOpen, with the
    record, after the read-back."""
    from Autodesk.Revit.DB import Transaction, TransactionStatus
    matches = matches or _equal
    rec = {"attempted": attempted, "commit_status": None}
    tx = Transaction(doc, "VOP capture-state probe: " + name)
    try:
        started = tx.Start()
    except Exception as ex:
        tx = None
        rec.update(rec_raised(ex))
        started = None
    if tx is not None and started != TransactionStatus.Started:
        rec.update({"state": "raised", "value": None,
                    "error": "Transaction.Start returned {0}".format(started)})
        tx = None
    if tx is not None:
        try:
            value = fn()
        except Exception as ex:
            rec.update(rec_raised(ex))
            _settle(tx, rec)
        else:
            try:
                status = tx.Commit()
            except Exception as ex:
                rec.update(rec_raised(ex))
                _settle(tx, rec)
            else:
                rec["commit_status"] = enum_text(status)
                if status != TransactionStatus.Committed:
                    rec.update({"state": "raised", "value": jsonable(value),
                                "error": "Transaction.Commit returned {0}".format(status)})
                    _settle(tx, rec)
                else:
                    rec.update(rec_value(value if isinstance(value, dict) else jsonable(value)))
    rec["read_back"] = read(read_back)
    if rec["read_back"]["state"] == "value":
        rec["took_effect"] = bool(matches(attempted, rec["read_back"]["value"]))
    else:
        rec["took_effect"] = None
    if (rec.get("transaction_close") or {}).get("still_open"):
        raise TransactionLeftOpen(name, rec)
    return rec


def transform_from_record(rec):
    """A Revit Transform rebuilt from a transform_record."""
    from Autodesk.Revit.DB import Transform, XYZ
    t = Transform(Transform.Identity)
    t.Origin = XYZ(*rec["origin"])
    t.BasisX = XYZ(*rec["basis_x"])
    t.BasisY = XYZ(*rec["basis_y"])
    t.BasisZ = XYZ(*rec["basis_z"])
    return t


def crop_boxes_match(attempted, read_back):
    """The same box, judged in WORLD coordinates when both carry a transform
    (a box Revit re-expresses about another origin is still the same box),
    else by Min/Max."""
    eq = boxes_equal(attempted, read_back)
    return eq["world"] if eq["world"] is not None else bool(eq["local"])


def write_crop_box(doc, view, box, name):
    """Write ``box`` (a record) as view.CropBox and read it back.

    With ``box["transform"]`` the box is written in THAT frame. Round 1 wrote
    S0's local Min/Max into whatever transform the view carried at the write;
    on MOHAVE (S0 rotated 180 degrees) clearing the scope box reset the
    transform to identity, so the same numbers cropped a mirrored, empty
    region. Without a transform, the view's current one is read first and
    used, and ``attempted`` records it either way."""
    from Autodesk.Revit.DB import BoundingBoxXYZ, XYZ
    given = box.get("transform")
    current = value_of(read(crop_box_record, view)) or {}
    attempted = {"min": list(box["min"]), "max": list(box["max"]),
                 "transform": given or current.get("transform"),
                 "transform_source": "given" if given else "current_at_write"}

    def _apply():
        new = BoundingBoxXYZ()
        new.Transform = (transform_from_record(given) if given
                         else view.CropBox.Transform)
        new.Min = XYZ(*box["min"])
        new.Max = XYZ(*box["max"])
        view.CropBox = new
        return True
    return tx_write(doc, name, _apply, attempted,
                    lambda: crop_box_record(view), crop_boxes_match)


def clear_scope_box(doc, view, name):
    """Set VIEWER_VOLUME_OF_INTEREST_CROP to InvalidElementId; attempted -1,
    read back from the parameter."""
    from Autodesk.Revit.DB import BuiltInParameter, ElementId

    def _param():
        p = view.get_Parameter(BuiltInParameter.VIEWER_VOLUME_OF_INTEREST_CROP)
        if p is None:
            raise ValueError("VIEWER_VOLUME_OF_INTEREST_CROP is not on this view")
        return p
    return tx_write(doc, name, lambda: bool(_param().Set(ElementId.InvalidElementId)),
                    -1, lambda: element_id_int(_param().AsElementId()))


def scope_box_cleared(write):
    """The clear APPLIED: it committed, Set returned True, and it took effect."""
    return (write.get("state") == "value" and write.get("value") is True
            and write.get("took_effect") is True)


def detach_template(doc, view, name):
    """ViewTemplateId = InvalidElementId; attempted -1, read back. Round 1:
    on a dependent view this commits and reads back unchanged."""
    from Autodesk.Revit.DB import ElementId
    return tx_write(doc, name,
                    lambda: setattr(view, "ViewTemplateId", ElementId.InvalidElementId) or True,
                    -1, lambda: element_id_int(view.ViewTemplateId))


WHITE_RGB = [255, 255, 255]
WHITE_GRADIENT = {"SkyColor": WHITE_RGB, "HorizonColor": WHITE_RGB, "GroundColor": WHITE_RGB}


def write_white_background(doc, view, name, apply):
    """SetBackground(white x3); took_effect when the three gradient colours
    read back white."""
    def _matches(attempted, read):
        return isinstance(read, dict) and all(read.get(k) == v for k, v in attempted.items())
    return tx_write(doc, name, apply, dict(WHITE_GRADIENT),
                    lambda: background_fingerprint(view), _matches)


def nested_group(ctx, name, body):
    """Run ``body()`` inside a TransactionGroup that is rolled back, so the
    next step starts from the state before it. Returns the group record."""
    from Autodesk.Revit.DB import TransactionGroup, TransactionStatus
    rec = {"name": name, "started": False, "rolled_back": False,
           "rollback_status": None, "body_error": None}
    group = TransactionGroup(ctx.doc, "VOP capture-state probe: " + name)
    try:
        rec["started"] = group.Start() == TransactionStatus.Started
    except Exception as ex:
        rec["body_error"] = exception_record(name + ":start", ex)
        ctx.exceptions.append(rec["body_error"])
        return rec
    if not rec["started"]:
        rec["body_error"] = {"stage": name + ":start", "message": "did not start"}
        return rec
    try:
        body()
    except TransactionLeftOpen as ex:
        rec["body_error"] = exception_record(name, ex)
        rec["body_error"]["write_record"] = ex.args[1] if len(ex.args) > 1 else None
        ctx.exceptions.append(rec["body_error"])
        ctx.halted = ("transaction {0!r} could not be closed; the probe stopped "
                      "writing".format(ex.args[0] if ex.args else name))
    except Exception as ex:
        rec["body_error"] = exception_record(name, ex)
        ctx.exceptions.append(rec["body_error"])
    finally:
        try:
            status = group.RollBack()
            rec["rollback_status"] = enum_text(status)
            rec["rolled_back"] = status == TransactionStatus.RolledBack
        except Exception as ex:
            rec["rollback_status"] = "raised: {0}".format(ex)
            ctx.exceptions.append(exception_record(name + ":rollback", ex))
    return rec


def restore_gate(ctx, view, baseline, required, checked_after):
    """Snapshot the view and judge it against the baseline."""
    snap = read(state_snapshot, ctx.doc, view)
    if snap["state"] != "value":
        verdict = {"restored": False, "error": snap["error"], "required": list(required)}
    else:
        verdict = restore_verdict(baseline, snap["value"], required)
    verdict["checked_after"] = checked_after
    return verdict


def gate_subjects(ctx, subjects, required, checked_after):
    """restore_gate over ``subjects`` -- ``[(view, baseline), ...]``. One
    subject gives its own verdict unchanged; several give
    ``{"restored": all, "views": {id: verdict}}`` (Q5b judges a dependent view
    and its primary together)."""
    if len(subjects) == 1:
        view, baseline = subjects[0]
        return restore_gate(ctx, view, baseline, required, checked_after)
    verdicts = dict((str(element_id_int(v.Id)), restore_gate(ctx, v, b, required, checked_after))
                    for v, b in subjects)
    failing = sorted(k for k, v in verdicts.items() if not v["restored"])
    out = {"restored": not failing, "views": verdicts, "checked_after": checked_after}
    if failing:
        out["error"] = "; ".join("view {0}: {1}".format(k, refusal_text(verdicts[k]))
                                 for k in failing)
    return out


def run_gated(ctx, subjects, required, out, plan, prefix):
    """Run ``plan`` -- ``(label, step names, body)`` in order -- each body in
    its own rolled-back nested group, judging every ``(view, baseline)`` in
    ``subjects`` against its baseline at the start and after every rollback.

    Once a check fails, every later step is recorded REFUSED rather than run:
    a step run from a state the previous one left behind is evidence about
    the wrong cause. The check at the start also catches an EARLIER question
    that left this view changed. Verdicts land in
    ``out["restore_checks"][label]``; the start's under ``question_start``."""
    checks = out.setdefault("restore_checks", {})

    def _refuse_halted(steps):
        for name in steps:
            out["steps"].append({"step": name, "refused": "probe_halted",
                                 "refused_reason": ctx.halted})

    if ctx.halted:
        for _label, steps, _body in plan:
            _refuse_halted(steps)
        out["refused"] = "probe halted before this question: {0}".format(ctx.halted)
        return None
    checks["question_start"] = gate_subjects(ctx, subjects, required, "question_start")
    blocked = None if checks["question_start"]["restored"] else checks["question_start"]
    for label, steps, body in plan:
        if ctx.halted:
            _refuse_halted(steps)
            continue
        if blocked is not None:
            for name in steps:
                out["steps"].append({"step": name, "refused": "state_not_restored",
                                     "refused_reason": refusal_text(blocked),
                                     "blocked_by": blocked["checked_after"]})
            continue
        group = nested_group(ctx, "{0} {1}".format(prefix, label), body)
        out["groups"].append(group)
        present = set(r.get("step") for r in out["steps"])
        for name in steps:
            if name not in present:
                out["steps"].append({"step": name, "refused": "raised",
                                     "exception": group.get("body_error")})
        if ctx.halted:
            continue
        checks[label] = gate_subjects(ctx, subjects, required, label)
        if not checks[label]["restored"]:
            blocked = checks[label]
    if checks["question_start"]["restored"] is False:
        out["refused"] = refusal_text(checks["question_start"])
    return blocked


def export_view(ctx, view, step):
    """One TIFF export with _export_tiff's option set (SetOfViews,
    FitToPage, horizontal fit, fixed pixel width, TIFF for both view kinds),
    renamed to ``<step>_<viewid>.tiff``, then its dimensions read back from
    the file header."""
    from Autodesk.Revit.DB import (ElementId, ExportRange, FitDirectionType,
                                   ImageExportOptions, ImageFileType, ZoomFitType)
    import System.Collections.Generic as SCG
    view_id = element_id_int(view.Id)
    file_name = "{0}_{1}.tiff".format(step, view_id)
    path = os.path.join(ctx.probe_dir, file_name)
    rec = {"step": step, "view_id": view_id, "file": file_name,
           "requested_pixel_size": ctx.pixel_width, "fit_direction": "horizontal",
           "zoom": "FitToPage", "state": None, "error": None,
           "document_modifiable_at_export": read_attr(ctx.doc, "IsModifiable", bool),
           "dims_px": rec_unavailable("export did not run")}
    t0 = time.time()
    try:
        before = set(os.listdir(ctx.probe_dir))
        ids = SCG.List[ElementId]()
        ids.Add(view.Id)
        opts = ImageExportOptions()
        opts.ExportRange = ExportRange.SetOfViews
        opts.SetViewsAndSheets(ids)
        opts.ZoomType = ZoomFitType.FitToPage
        opts.FitDirection = FitDirectionType.Horizontal
        opts.PixelSize = int(ctx.pixel_width)
        rec["accepted_pixel_size"] = int(opts.PixelSize)
        opts.FilePath = os.path.join(ctx.probe_dir, "_capture_state_tmp")
        tiff = getattr(ImageFileType, "TIFF", getattr(ImageFileType, "TIF", None))
        if tiff is None:
            raise RuntimeError("ImageFileType exposes no TIFF/TIF")
        opts.HLRandWFViewsFileType = tiff
        opts.ShadowViewsFileType = tiff
        ctx.doc.ExportImage(opts)
        created = [f for f in set(os.listdir(ctx.probe_dir)) - before
                   if f.lower().endswith((".tif", ".tiff"))]
        if len(created) != 1:
            raise RuntimeError("ExportImage produced {0} new TIFF(s): {1}".format(
                len(created), sorted(created)))
        if os.path.exists(path):
            os.remove(path)
        os.rename(os.path.join(ctx.probe_dir, created[0]), path)
        rec["state"] = "value"
        rec["file_size_bytes"] = os.path.getsize(path)
        rec["dims_px"] = read(lambda: list(read_tiff_dimensions(path)))
    except Exception as ex:
        rec["state"] = "raised"
        rec["error"] = "{0}: {1}".format(type(ex).__name__, ex)
        ctx.exceptions.append(exception_record("export " + file_name, ex))
    rec["elapsed_ms"] = round((time.time() - t0) * 1000.0, 1)
    ctx.exports.append(rec)
    return rec


class Context(object):
    def __init__(self, doc, probe_dir, pixel_width):
        self.doc = doc
        self.probe_dir = probe_dir
        self.pixel_width = pixel_width
        self.exports = []
        self.exceptions = []
        # Set when a transaction could not be closed; nothing runs after.
        self.halted = None
        # Round 2: production modules (None when not importable) and the
        # import record that says why.
        self.production = None
        self.production_record = None


def _step(ctx, view, question, step, writes=None, export=True):
    """Common reads, then (optionally) an export: the record of one step."""
    common, ids = common_reads(ctx.doc, view)
    rec = {"step": step, "writes": writes or {}, "common": common}
    if export:
        rec["export"] = export_view(ctx, view, "{0}_{1}".format(question.upper(), step))
    return rec, ids


def _split_summary(common):
    shape = value_of(common.get("crop_region_shape")) or {}
    return dict((k, shape.get(k)) for k in ("Split", "NumberOfSplitRegions",
                                            "IsSplitHorizontally", "IsSplitVertically",
                                            "split_regions"))


# ======================================================================
# THE QUESTIONS
# ======================================================================

# The state each question WRITES. Its restore must be shown, so a field here
# that cannot be read on either side refuses the question's remaining steps.
Q1_REQUIRED = ("crop_box", "crop_box_active", "members_sha1")
Q3_REQUIRED = ("crop_box", "scope_box", "shape_set")
Q4_REQUIRED = ("shadow_intensity", "background")
Q5_REQUIRED = ("template_id", "display_style")


def _baseline_box(baseline):
    box = baseline.get("crop_box")
    return None if box == UNREADABLE else box


def run_q1_q2(ctx, view, baseline):
    """S0, then S1 (identity CropBox write) and S2 (the capture's write),
    each from the baseline state in its own rolled-back nested group."""
    out = {"view_id": element_id_int(view.Id), "steps": [], "groups": []}
    box0 = _baseline_box(baseline)
    member_ids_by_step = {}

    def _s0():
        rec, ids = _step(ctx, view, "q1", "S0")
        out["steps"].append(rec)
        member_ids_by_step["S0"] = ids

    def _s1():
        writes = {"crop_box_identity": rec_unavailable("S0 crop box unreadable")}
        if box0 is not None:
            writes["crop_box_identity"] = write_crop_box(ctx.doc, view, box0, "Q1 S1 identity")
        rec, ids = _step(ctx, view, "q1", "S1", writes)
        out["steps"].append(rec)
        member_ids_by_step["S1"] = ids

    # The capture's own crop (color_id_buffer: crop_box_from_uv_bounds of the
    # snapped crop-A rectangle) needs the full raster and frame geometry, so
    # S2 uses S0's box inset by Q1_INSET_FT on every edge.
    out["s2_crop_source"] = ("s0_box_inset_{0}ft (the capture's crop needs the "
                             "pipeline raster; not importable without side "
                             "effects)".format(Q1_INSET_FT))

    def _s2():
        writes = {}
        target = inset_box(box0, Q1_INSET_FT) if box0 is not None else None
        if target is not None:
            target["transform"] = box0.get("transform")
        if target is None:
            writes["crop_box"] = rec_unavailable("S0 box unreadable or too small to inset")
        else:
            writes["crop_box"] = write_crop_box(ctx.doc, view, target, "Q1 S2 capture crop")
            writes["crop_box_written"] = target
            # color_id_buffer sets CropBoxActive = True right after the write.
            writes["crop_box_active"] = tx_write(
                ctx.doc, "Q1 S2 crop active",
                lambda: setattr(view, "CropBoxActive", True) or True,
                True, lambda: bool(view.CropBoxActive))
        rec, ids = _step(ctx, view, "q1", "S2", writes)
        out["steps"].append(rec)
        member_ids_by_step["S2"] = ids

    run_gated(ctx, [(view, baseline)], Q1_REQUIRED, out,
              (("S0", ("S0",), _s0), ("S1", ("S1",), _s1), ("S2", ("S2",), _s2)), "Q1")
    ids0 = member_ids_by_step.get("S0")
    report = {"members": {}, "split": {}, "diff_vs_s0": {}}
    for step in out["steps"]:
        name = step["step"]
        if step.get("refused"):
            report["members"][name] = rec_unavailable("step refused: {0}".format(step["refused"]))
            continue
        report["members"][name] = step["common"]["members"]
        report["split"][name] = _split_summary(step["common"])
        ids = member_ids_by_step.get(name)
        if name != "S0":
            report["diff_vs_s0"][name] = (
                ids_diff(ids0, ids) if ids0 is not None and ids is not None
                else rec_unavailable("member ids unreadable at S0 or {0}".format(name)))
    report["q2_api_sees_split_at_s0"] = report["split"].get("S0")
    out["report"] = report
    return out


def _discriminator_refusal(step, writes, why, reason):
    """A Q3 step whose discriminator (scope-box clear, crop-shape removal)
    did not apply: no crop write, no export, so no row that looks like
    evidence about a removal that never happened."""
    return {"step": step, "refused": why, "refused_reason": reason, "writes": writes}


def _q3_step(ctx, view, step, box0, remove_scope_box, remove_shape):
    """One Q3 step from S0's state: optional scope-box clear, optional
    crop-shape removal, then the S1 write (S0's box scaled by Q3_SHRINK)."""
    from Autodesk.Revit.DB import BuiltInParameter, ElementId
    writes = {}
    if remove_scope_box:
        writes["scope_box_cleared"] = clear_scope_box(
            ctx.doc, view, "Q3 {0} clear scope box".format(step))
        # Applied only if the write committed, Set returned True AND the
        # read-back shows no scope box. Otherwise the step would export with
        # the scope box still attached and read as "clearing it did not help".
        if not scope_box_cleared(writes["scope_box_cleared"]):
            return _discriminator_refusal(
                step, writes, "discriminator_not_applied",
                "the scope box was not cleared: write {0} (value {1!r}), read-back "
                "{2}".format(writes["scope_box_cleared"]["state"],
                             writes["scope_box_cleared"].get("value"),
                             writes["scope_box_cleared"]["read_back"]))
    if remove_shape:
        manager = value_of(call_method(view, "GetCropRegionShapeManager"))
        shape_set = read_attr(manager, "ShapeSet", bool)
        writes["shape_set_before_removal"] = shape_set
        if value_of(shape_set) is False:
            # Nothing to remove: the step would only repeat S1 (or S2).
            return _discriminator_refusal(
                step, writes, "no_crop_shape",
                "ShapeSet is false: there is no crop shape to remove, so this step "
                "would repeat the step without the removal")
        writes["crop_shape_removed"] = tx_write(
            ctx.doc, "Q3 {0} remove crop shape".format(step),
            lambda: manager.RemoveCropRegionShape() or True,
            False, lambda: bool(view.GetCropRegionShapeManager().ShapeSet))
        if not (writes["crop_shape_removed"]["state"] == "value"
                and writes["crop_shape_removed"]["took_effect"] is True):
            return _discriminator_refusal(
                step, writes, "discriminator_not_applied",
                "the crop shape was not removed: write {0}, ShapeSet before {1}, "
                "read-back {2}".format(writes["crop_shape_removed"]["state"],
                                       shape_set, writes["crop_shape_removed"]["read_back"]))
    # In S0's own frame (round 1 wrote it into whatever frame the view held).
    target = dict(scaled_box(box0, Q3_SHRINK), transform=box0.get("transform"))
    writes["crop_box"] = write_crop_box(ctx.doc, view, target, "Q3 {0} crop".format(step))
    rec, _ids = _step(ctx, view, "q3", step, writes)
    read_back = value_of(rec["common"]["crop_box"])
    # A write Revit rejected is NOT a written box: written_box stays None and
    # crop_write_state says why, so nothing downstream compares the export
    # with a box that was never accepted. The box that was attempted is kept
    # under its own name.
    written = (writes["crop_box"]["attempted"] if writes["crop_box"]["state"] == "value"
               else None)
    rec["crop_write_state"] = writes["crop_box"]["state"]
    rec["attempted_box"] = target
    rec["written_box"] = written
    rec["written_extent_ft"] = box_extent_ft(written) if written else None
    rec["read_back_box"] = read_back
    rec["read_back_extent_ft"] = box_extent_ft(read_back) if read_back else None
    rec["read_back_equal"] = boxes_equal(written, read_back)
    rec["crop_write_took_effect"] = writes["crop_box"]["took_effect"]
    return rec


def _at_export(common):
    shape = value_of(common.get("crop_region_shape")) or {}
    scope = value_of(common.get("scope_box")) or {}
    return {"shape_set": shape.get("ShapeSet"),
            "scope_box": scope.get("value"), "scope_box_is_set": scope.get("is_set")}


def run_q3_view(ctx, view, role, baseline):
    out = {"view_id": element_id_int(view.Id), "role": role, "steps": [], "groups": [],
           "restore_mechanism": "nested TransactionGroup per step, rolled back "
                                "before the next; after each rollback the view's "
                                "full state snapshot is judged against the "
                                "pre-group baseline, and every later step is "
                                "refused once it is not restored"}
    box0 = _baseline_box(baseline)

    def _s0():
        rec, _ids = _step(ctx, view, "q3", "S0")
        rec["at_export"] = _at_export(rec["common"])
        out["steps"].append(rec)

    def _body_for(step, scope, shape):
        def _body():
            if box0 is None:
                raise ValueError("the baseline crop box is unreadable; no crop write attempted")
            rec = _q3_step(ctx, view, step, box0, scope, shape)
            if not rec.get("refused"):
                rec["at_export"] = _at_export(rec["common"])
            out["steps"].append(rec)
        return _body

    plan = [("S0", ("S0",), _s0)]
    for step, scope, shape in (("S1", False, False), ("S2", True, False),
                               ("S3", False, True), ("S4", True, True)):
        plan.append((step, (step,), _body_for(step, scope, shape)))
    run_gated(ctx, [(view, baseline)], Q3_REQUIRED, out, plan, "Q3 {0}".format(out["view_id"]))
    return out


def background_record(view):
    bg_rec = call_method(view, "GetBackground")
    bg = value_of(bg_rec)
    out = {"call": {"state": bg_rec["state"], "error": bg_rec["error"]}}
    if bg is None:
        return out
    out["type_name"] = read(lambda: str(bg.GetType().Name))
    for name in ("SkyColor", "HorizonColor", "GroundColor", "BackgroundColor"):
        out[name] = read_attr(bg, name, colour_list)
    out["ImagePath"] = read_attr(bg, "ImagePath", str)
    return out


def run_q4(ctx, view, baseline):
    from Autodesk.Revit.DB import Color, ViewDisplayBackground
    out = {"view_id": element_id_int(view.Id), "steps": [], "groups": []}
    shadow0 = baseline.get("shadow_intensity")

    def _s0():
        rec, _ids = _step(ctx, view, "q4", "S0")
        rec["background"] = background_record(view)
        out["steps"].append(rec)

    def _white_background():
        white = Color(255, 255, 255)
        view.SetBackground(ViewDisplayBackground.CreateGradient(white, white, white))
        return True

    def _s1_s2():
        writes = {"shadow_intensity_before": read_attr(view, "ShadowIntensity", jsonable),
                  "shadow_intensity_set_0": tx_write(
                      ctx.doc, "Q4 S1 shadow 0",
                      lambda: setattr(view, "ShadowIntensity", 0) or 0,
                      0, lambda: jsonable(view.ShadowIntensity))}
        writes["shadow_intensity_after"] = read_attr(view, "ShadowIntensity", jsonable)
        rec, _i = _step(ctx, view, "q4", "S1", writes)
        out["steps"].append(rec)
        writes2 = {"background_before": background_record(view),
                   "set_background_white": write_white_background(
                       ctx.doc, view, "Q4 S2 background", _white_background)}
        writes2["background_after"] = background_record(view)
        rec2, _i = _step(ctx, view, "q4", "S2", writes2)
        out["steps"].append(rec2)

    def _s3():
        writes = {"shadow_intensity_at_s3": read_attr(view, "ShadowIntensity", jsonable),
                  "shadow_intensity_s0": shadow0,
                  "background_before": background_record(view),
                  "set_background_white": write_white_background(
                      ctx.doc, view, "Q4 S3 background", _white_background)}
        writes["background_after"] = background_record(view)
        rec, _i = _step(ctx, view, "q4", "S3", writes)
        out["steps"].append(rec)

    run_gated(ctx, [(view, baseline)], Q4_REQUIRED, out,
              (("S0", ("S0",), _s0), ("S1+S2", ("S1", "S2"), _s1_s2),
               ("S3", ("S3",), _s3)), "Q4")
    return out


def template_record(doc, view):
    from Autodesk.Revit.DB import BuiltInParameter, ElementId
    out = {"template_id": read(lambda: element_id_int(view.ViewTemplateId))}
    primary = call_method(view, "GetPrimaryViewId", conv=element_id_int)
    out["primary_view_id"] = primary
    out["primary_view_id_valid"] = (value_of(primary) not in (None, -1)
                                    if primary["state"] == "value" else None)
    tid = value_of(out["template_id"])
    if tid in (None, -1):
        out["non_controlled_parameter_count"] = rec_unavailable("no template attached")
        out["display_style_controlled_by_template"] = rec_unavailable("no template attached")
        return out
    template = doc.GetElement(view.ViewTemplateId)
    ids_rec = call_method(template, "GetNonControlledTemplateParameterIds",
                          conv=lambda ids: [element_id_int(i) for i in ids])
    ids = value_of(ids_rec)
    out["non_controlled_parameter_count"] = (rec_value(len(ids)) if ids is not None
                                             else ids_rec)
    style_id = value_of(read(lambda: element_id_int(
        ElementId(BuiltInParameter.MODEL_GRAPHICS_STYLE))))
    out["display_style_controlled_by_template"] = (
        rec_value(style_id not in ids) if ids is not None and style_id is not None
        else rec_unavailable("non-controlled ids or MODEL_GRAPHICS_STYLE id unreadable"))
    return out


def display_style_text(name):
    """``DisplayStyle.<name>`` through enum_text -- the SAME conversion every
    read-back goes through, so a comparison holds whether the host renders
    the enum as a name or as a number."""
    from Autodesk.Revit.DB import DisplayStyle
    return enum_text(getattr(DisplayStyle, name))


def flat_colors_attempt(ctx, view, label):
    """Try to set DisplayStyle = FlatColors, and say whether THAT write did
    it -- not whether the view happens to read FlatColors afterwards.

    ``outcome``:
      * ``took_effect`` -- the write committed and the style went from
        something else to FlatColors;
      * ``no_effect``   -- the write committed and the style is not FlatColors;
      * ``raised``      -- the write raised or did not commit;
      * ``already_target`` -- the view was already FlatColors and could not
        be moved off it first, so the write proves nothing (inconclusive).

    A view already FlatColors is first switched to Hidden Line in the same
    step, so the FlatColors write is a real test when that switch works.
    ``took_effect`` is True / False, or None when inconclusive."""
    from Autodesk.Revit.DB import DisplayStyle
    flat, hidden_line = display_style_text("FlatColors"), display_style_text("HLR")
    rec = {"display_style_before": read_attr(view, "DisplayStyle", enum_text),
           "flat_colors_as_recorded": flat}
    before = value_of(rec["display_style_before"])
    if before == flat:
        rec["pre_switch_to_hidden_line"] = tx_write(
            ctx.doc, label + " pre-switch to Hidden Line",
            lambda: setattr(view, "DisplayStyle", DisplayStyle.HLR) or True,
            hidden_line, lambda: enum_text(view.DisplayStyle))
        rec["display_style_after_pre_switch"] = read_attr(view, "DisplayStyle", enum_text)
        before = value_of(rec["display_style_after_pre_switch"])
        if before == flat or before is None:
            rec.update(outcome="already_target", took_effect=None,
                       outcome_reason="the view was already FlatColors and the switch "
                                      "to Hidden Line did not take effect (write {0}, "
                                      "read-back {1!r}), so a FlatColors write would "
                                      "prove nothing".format(
                                          rec["pre_switch_to_hidden_line"]["state"], before))
            return rec

    def _set_flat():
        view.DisplayStyle = DisplayStyle.FlatColors
        return True
    rec["set_flat_colors"] = tx_write(ctx.doc, label + " flat colors", _set_flat,
                                      flat, lambda: enum_text(view.DisplayStyle))
    rec["display_style_after"] = read_attr(view, "DisplayStyle", enum_text)
    after = value_of(rec["display_style_after"])
    if rec["set_flat_colors"]["state"] != "value":
        outcome = "raised"
    elif after == flat and before != flat:
        outcome = "took_effect"
    else:
        outcome = "no_effect"
    rec.update(outcome=outcome, took_effect=outcome == "took_effect")
    return rec


def run_q5_view(ctx, view, baseline):
    from Autodesk.Revit.DB import DisplayStyle, ElementId
    out = {"view_id": element_id_int(view.Id), "template": template_record(ctx.doc, view),
           "steps": [], "groups": []}
    # Read from the baseline, not assumed: a view with no template cannot
    # answer "attached vs detached". Its S0 still says whether DisplayStyle
    # can be set at all; S1 would repeat S0 and is refused.
    template_id = baseline.get("template_id")
    attached = None if template_id == UNREADABLE else template_id not in (None, -1)
    out["template_attached"] = attached

    def _s0():
        rec = {"step": "S0", "template_attached": attached}
        rec.update(flat_colors_attempt(ctx, view, "Q5 S0"))
        out["steps"].append(rec)

    def _s1():
        rec = {"step": "S1",
               "detach_template": detach_template(ctx.doc, view, "Q5 S1 detach")}
        rec["template_id_after_detach"] = read(lambda: element_id_int(view.ViewTemplateId))
        rec.update(flat_colors_attempt(ctx, view, "Q5 S1"))
        out["steps"].append(rec)

    plan = [("S0", ("S0",), _s0)]
    if attached:
        plan.append(("S1", ("S1",), _s1))
    run_gated(ctx, [(view, baseline)], Q5_REQUIRED, out, plan, "Q5 {0}".format(out["view_id"]))
    if not attached:
        out["steps"].append({
            "step": "S1", "refused": "no_template",
            "refused_reason": ("no template is attached (template id {0!r}): there "
                               "is nothing to detach, so S1 would repeat S0".format(
                                   template_id))})
    return out


# ======================================================================
# ROUND 2 (2026-10-02.1): production imports
# ======================================================================

def _repo_root_candidates(params):
    seeds = [params.get("repo_root"), os.environ.get("REVIT_SSM_EXPORTER_ROOT"),
             os.environ.get("VOP_REPO_ROOT"), params.get("output_directory"), os.getcwd()]
    if "__file__" in globals():
        seeds.append(os.path.dirname(os.path.abspath(__file__)))
    out = []
    for seed in seeds:
        if not seed:
            continue
        current = os.path.abspath(os.path.expanduser(str(seed)))
        for _ in range(8):
            if current not in out:
                out.append(current)
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
    return out


def import_production(params):
    """``(record, modules)``. Q3b and Q6 call PRODUCTION code -- the crop
    helper and the registration-mark functions -- rather than copies, so the
    probe measures what the capture runs. These are imported from the
    repository: found from IN[9] ``repo_root``, the REVIT_SSM_EXPORTER_ROOT
    / VOP_REPO_ROOT environment variables, the output directory or the
    working directory, walking up. Not found is recorded, and the steps that
    need it are refused; nothing is reimplemented in its place."""
    searched = []
    for root in _repo_root_candidates(params):
        searched.append(root)
        if not os.path.isfile(os.path.join(root, "vop_interwoven", "__init__.py")):
            continue
        if root not in sys.path:
            sys.path.insert(0, root)
        try:
            from vop_interwoven.revit import view_basis
            from vop_interwoven import stage_a_registration, stage_a_registered_capture
            from vop_interwoven.revit.annotation import split_stage_a_pass_membership
            from vop_interwoven.config import Config
        except Exception as ex:
            return ({"state": "raised", "root": root,
                     "error": "{0}: {1}".format(type(ex).__name__, ex)}, None)
        return ({"state": "value", "root": root},
                {"view_basis": view_basis, "registration": stage_a_registration,
                 "capture": stage_a_registered_capture,
                 "split_membership": split_stage_a_pass_membership, "Config": Config})
    return ({"state": "unavailable",
             "error": "no vop_interwoven package found; set IN[9] repo_root",
             "searched": searched[:40]}, None)


def _production_refusal(ctx):
    rec = ctx.production_record or {}
    return "production code not importable ({0}: {1})".format(
        rec.get("state"), rec.get("error"))


def production_crop_box(ctx, view):
    """``(box, uv_extents)``: the crop the capture would write for S0's own
    extents -- crop_box_from_uv_bounds (color_id_buffer's crop writes) on the
    view's crop projected by xy_bounds_from_crop_box_all_corners, both
    production. The capture snaps the rectangle to its pixel lattice first,
    which needs the pipeline raster and moves each edge by under a pixel;
    this is the unsnapped rectangle, and the record says so."""
    vb = ctx.production["view_basis"]
    # Read the crop first: xy_bounds_from_crop_box_all_corners answers an
    # AttributeError with a made-up +/-100 ft rectangle (view_basis.py), which
    # must never stand in for a measured crop here.
    crop_box_record(view)
    basis = vb.make_view_basis(view)
    b = vb.xy_bounds_from_crop_box_all_corners(view, basis)
    uv = [float(b.xmin), float(b.ymin), float(b.xmax), float(b.ymax)]
    box = vb.crop_box_from_uv_bounds(view, basis, uv[0], uv[1], uv[2], uv[3])
    if box is None:
        raise ValueError("crop_box_from_uv_bounds returned None (the view has no CropBox)")
    return box, uv


def write_production_crop(ctx, view, name):
    """Production's crop write: CropBox = crop_box_from_uv_bounds(S0 extents),
    then CropBoxActive = True, as color_id_buffer does. attempted is the box
    production built; took_effect from the read-back."""
    built = read(production_crop_box, ctx, view)
    if built["state"] != "value":
        return {"state": "raised", "error": built["error"], "attempted": None,
                "commit_status": None, "read_back": rec_unavailable("no box was built"),
                "took_effect": None}
    box, uv = built["value"]
    attempted = {"min": xyz_list(box.Min), "max": xyz_list(box.Max),
                 "transform": transform_record(box.Transform),
                 "transform_source": "crop_box_from_uv_bounds"}

    def _apply():
        view.CropBox = box
        view.CropBoxActive = True
        return True
    rec = tx_write(ctx.doc, name, _apply, attempted, lambda: crop_box_record(view),
                   crop_boxes_match)
    rec["uv_extents"] = uv
    rec["snapped"] = False
    return rec


def crop_uv_at(ctx, view):
    """The crop's UV rectangle as production projects it, for Q6's pixel
    windows. None-free: raises when it cannot be read, and the caller records
    that as unavailable."""
    vb = ctx.production["view_basis"]
    crop_box_record(view)  # raises rather than let the +/-100 ft fallback through
    b = vb.xy_bounds_from_crop_box_all_corners(view, vb.make_view_basis(view))
    return [float(b.xmin), float(b.ymin), float(b.xmax), float(b.ymax)]


# ======================================================================
# Q1b -- what the identity crop write changes
# ======================================================================

def element_detail(doc, element_id):
    """Category and OwnerViewId of one element (three-valued per field)."""
    from Autodesk.Revit.DB import ElementId
    elem = doc.GetElement(ElementId(int(element_id)))
    if elem is None:
        return {"exists": False}
    cat = read(lambda: elem.Category)
    cat_value = value_of(cat)
    return {"exists": True, "class": type(elem).__name__,
            "category": (read(lambda: str(cat_value.Name)) if cat_value is not None
                         else (rec_value(None) if cat["state"] == "value" else cat)),
            "category_id": (read(lambda: element_id_int(cat_value.Id))
                            if cat_value is not None else rec_value(None)),
            "owner_view_id": read(lambda: element_id_int(elem.OwnerViewId))}


def full_parameter_dump(view):
    """Every parameter on the view, keyed by its parameter id."""
    out = {}
    for p in view.Parameters:
        key = value_of(read(lambda: element_id_int(p.Id)))
        name = value_of(read(lambda: str(p.Definition.Name)))
        out[str(key if key is not None else "name:{0}".format(name))] = {
            "name": name,
            "built_in": value_of(read_attr(p.Definition, "BuiltInParameter", enum_text)),
            "storage_type": value_of(read(lambda: str(p.StorageType))),
            "value": read(_parameter_value, p),
            "value_string": call_method(p, "AsValueString"),
            "read_only": value_of(read_attr(p, "IsReadOnly", bool))}
    return out


def parameter_diff(before, after):
    """PURE. Which parameters changed between two full_parameter_dump()s:
    value or value string differs, or the parameter appeared or vanished. A
    parameter unreadable on either side is listed as unreadable, never as
    unchanged."""
    changed, unreadable = [], []
    for key in sorted(set(before) | set(after)):
        b, a = before.get(key), after.get(key)
        if b is None or a is None:
            changed.append({"key": key, "name": (a or b).get("name"),
                            "built_in": (a or b).get("built_in"),
                            "change": "added" if b is None else "removed"})
            continue
        if b["value"]["state"] != "value" or a["value"]["state"] != "value":
            unreadable.append({"key": key, "name": b.get("name"),
                               "before": b["value"]["state"], "after": a["value"]["state"]})
            continue
        bs, as_ = value_of(b["value_string"]), value_of(a["value_string"])
        if b["value"]["value"] != a["value"]["value"] or bs != as_:
            changed.append({"key": key, "name": b.get("name"), "built_in": b.get("built_in"),
                            "change": "value", "before": b["value"]["value"],
                            "after": a["value"]["value"], "value_string_before": bs,
                            "value_string_after": as_})
    return {"compared": len(set(before) & set(after)), "changed_count": len(changed),
            "changed": changed, "unreadable": unreadable}


def run_q1b_view(ctx, view, baseline):
    out = {"view_id": element_id_int(view.Id), "steps": [], "groups": [],
           "watch_ids": list(Q1B_WATCH_IDS)}
    box0 = _baseline_box(baseline)

    def _s0():
        rec, _ids = _step(ctx, view, "q1b", "S0")
        out["steps"].append(rec)

    def _s1():
        if box0 is None:
            raise ValueError("the baseline crop box is unreadable; no identity write")
        ids_before = member_ids(ctx.doc, view)
        params_before = full_parameter_dump(view)
        write = write_crop_box(ctx.doc, view, box0, "Q1b identity crop write")
        ids_after = member_ids(ctx.doc, view)
        params_after = full_parameter_dump(view)
        rec, _ids = _step(ctx, view, "q1b", "S1", {"crop_box_identity": write})
        added = sorted(set(ids_after) - set(ids_before))
        removed = sorted(set(ids_before) - set(ids_after))
        detail_ids = sorted(set(added) | set(removed) | set(Q1B_WATCH_IDS))
        details = dict((str(i), read(element_detail, ctx.doc, i)) for i in detail_ids)
        rec["membership"] = {"before": ids_fingerprint(ids_before),
                             "after": ids_fingerprint(ids_after),
                             "added_count": len(added), "removed_count": len(removed),
                             "added": added, "removed": removed,
                             "truncated": False, "details": details}
        rec["watch_ids"] = dict((str(i), {"in_before": i in ids_before, "in_after": i in ids_after,
                                          "added": i in added, "removed": i in removed,
                                          "detail": details[str(i)]})
                                for i in Q1B_WATCH_IDS)
        rec["parameter_diff"] = parameter_diff(params_before, params_after)
        out["steps"].append(rec)

    run_gated(ctx, [(view, baseline)], Q1_REQUIRED, out,
              (("S0", ("S0",), _s0), ("S1", ("S1",), _s1)), "Q1b {0}".format(out["view_id"]))
    return out


# ======================================================================
# Q3b -- scope-box clear with the original frame; production crop under it
# ======================================================================

def run_q3b_view(ctx, view, baseline, role):
    out = {"view_id": element_id_int(view.Id), "role": role, "steps": [], "groups": [],
           "baseline_scope_box": baseline.get("scope_box")}
    box0 = _baseline_box(baseline)

    def _s0():
        rec, _ids = _step(ctx, view, "q3b", "S0")
        rec["at_export"] = _at_export(rec["common"])
        out["steps"].append(rec)

    def _s1():
        # Scope box cleared, then S0's OWN box -- its transform and Min/Max.
        if box0 is None:
            raise ValueError("the baseline crop box is unreadable")
        writes = {"scope_box_cleared": clear_scope_box(ctx.doc, view, "Q3b S1 clear scope box")}
        if not scope_box_cleared(writes["scope_box_cleared"]):
            out["steps"].append(_discriminator_refusal(
                "S1", writes, "discriminator_not_applied",
                "the scope box was not cleared: write {0}, read-back {1}".format(
                    writes["scope_box_cleared"]["state"],
                    writes["scope_box_cleared"]["read_back"])))
            return
        writes["crop_box_s0_frame"] = write_crop_box(ctx.doc, view, box0, "Q3b S1 S0 crop")
        rec, _ids = _step(ctx, view, "q3b", "S1", writes)
        rec["at_export"] = _at_export(rec["common"])
        read_back = value_of(rec["common"]["crop_box"])
        rec["read_back_transform"] = (read_back or {}).get("transform")
        rec["read_back_extent_ft"] = box_extent_ft(read_back) if read_back else None
        rec["s0_transform"] = box0.get("transform")
        rec["s0_extent_ft"] = box_extent_ft(box0)
        out["steps"].append(rec)

    def _s2():
        # Scope box LEFT SET; production's crop for S0's extents.
        if ctx.production is None:
            out["steps"].append({"step": "S2", "refused": "production_unavailable",
                                 "refused_reason": _production_refusal(ctx)})
            return
        writes = {"production_crop": write_production_crop(ctx, view, "Q3b S2 production crop")}
        rec, _ids = _step(ctx, view, "q3b", "S2", writes)
        rec["at_export"] = _at_export(rec["common"])
        rec["scope_box_set_at_write"] = baseline.get("scope_box") not in (None, -1, UNREADABLE)
        out["steps"].append(rec)

    run_gated(ctx, [(view, baseline)], Q3_REQUIRED, out,
              (("S0", ("S0",), _s0), ("S1", ("S1",), _s1), ("S2", ("S2",), _s2)),
              "Q3b {0}".format(out["view_id"]))
    return out


# ======================================================================
# Q5b -- DisplayStyle on dependent views, through the primary
# ======================================================================

def run_q5b_view(ctx, dependent, dep_baseline, primary, primary_baseline, primary_id=None):
    out = {"view_id": element_id_int(dependent.Id), "steps": [], "groups": [],
           "primary_view_id": element_id_int(primary.Id) if primary is not None else None,
           "dependent_template_id": dep_baseline.get("template_id"),
           "primary_template_id": (primary_baseline or {}).get("template_id")}
    if primary is None:
        out["refused"] = ("not a dependent view: GetPrimaryViewId gave no view"
                          if primary_id is None else
                          "primary view {0} could not be resolved (see views)".format(primary_id))
        out["steps"] = [{"step": s, "refused": "not_dependent",
                         "refused_reason": out["refused"]} for s in ("A", "B")]
        return out
    if out["primary_template_id"] in (None, -1):
        out["note"] = ("the primary has no template, so both variants' detach is "
                       "vacuous: attempted -1, read back -1")

    def _a():
        rec = {"step": "A",
               "detach_primary_template": detach_template(ctx.doc, primary,
                                                          "Q5b A detach primary template")}
        rec["dependent_template_id_after"] = read(lambda: element_id_int(dependent.ViewTemplateId))
        rec["dependent_flat_colors"] = flat_colors_attempt(ctx, dependent, "Q5b A dependent")
        out["steps"].append(rec)

    def _b():
        rec = {"step": "B",
               "detach_primary_template": detach_template(ctx.doc, primary,
                                                          "Q5b B detach primary template"),
               "dependent_display_style_before": read_attr(dependent, "DisplayStyle", enum_text)}
        rec["primary_flat_colors"] = flat_colors_attempt(ctx, primary, "Q5b B primary")
        rec["dependent_display_style_after"] = read_attr(dependent, "DisplayStyle", enum_text)
        before = value_of(rec["dependent_display_style_before"])
        after = value_of(rec["dependent_display_style_after"])
        flat = display_style_text("FlatColors")
        rec["dependent_followed_primary"] = (
            None if before is None or after is None
            else bool(after == flat and before != flat))
        out["steps"].append(rec)

    run_gated(ctx, [(dependent, dep_baseline), (primary, primary_baseline)], Q5_REQUIRED,
              out, (("A", ("A",), _a), ("B", ("B",), _b)), "Q5b {0}".format(out["view_id"]))
    return out


# ======================================================================
# Q6 -- why registration ticks do not render
# ======================================================================

Q6_REQUIRED = ("crop_box", "crop_box_active", "members_sha1")


class _LayoutRaster(object):
    """What mark_reference_rectangle / mark_fpp_ft read off the pipeline's
    raster, and nothing else: the view basis. With no model crop or frame
    they take their own documented fallbacks (an inactive crop has no
    reference; the tick size falls back to the requested dpi). The record
    names which source each used."""

    def __init__(self, view_basis):
        self.view_basis = view_basis
        self.model_clip_bounds = None
        self.bounds_xy = None


def mark_layout(ctx, view):
    """``(basis, layout, record)`` built the way the capture builds it
    (stage_a_registered_capture: reference rectangle, tick size, segments,
    annotation avoidance) -- production functions only."""
    from Autodesk.Revit.DB import FilteredElementCollector
    prod = ctx.production
    basis = prod["view_basis"].make_view_basis(view)
    raster = _LayoutRaster(basis)
    capture, registration = prod["capture"], prod["registration"]
    reference, source = capture.mark_reference_rectangle(view, raster)
    fpp, fpp_basis = capture.mark_fpp_ft(view, raster, prod["Config"]())
    layout = (registration.registration_mark_segments(reference, fpp)
              if reference is not None else {"state": "unavailable", "reason": source})
    collected = list(FilteredElementCollector(ctx.doc, view.Id).WhereElementIsNotElementType())
    _model, anno, _unresolved, _b = prod["split_membership"](
        collected, capture_view_id_int=element_id_int(view.Id))
    avoid, avoidance = capture.annotation_avoid_rects(view, anno, basis)
    layout = registration.relocate_marks_clear_of(layout, avoid)
    record = {"reference_uv": list(reference) if reference is not None else None,
              "reference_source": source, "fpp_ft": fpp, "fpp_basis": fpp_basis,
              "avoidance": avoidance, "layout_state": layout.get("state"),
              "layout_reason": layout.get("reason"),
              "segment_count": len(layout.get("segments") or [])}
    return basis, layout, record


def create_marks(ctx, view, basis, layout, name):
    """create_registration_marks inside its own transaction, as the capture
    runs it. attempted = the segment count; read back = how many of the
    created marks exist afterwards."""
    from Autodesk.Revit.DB import ElementId
    holder = {}

    def _apply():
        holder["record"] = ctx.production["registration"].create_registration_marks(
            ctx.doc, view, basis, layout)
        return holder["record"].get("created_count")

    def _read_back():
        created = (holder.get("record") or {}).get("created") or []
        return sum(1 for m in created
                   if ctx.doc.GetElement(ElementId(int(m["id"]))) is not None)
    write = tx_write(ctx.doc, name, _apply, len(layout.get("segments") or []), _read_back)
    return write, holder.get("record")


def lines_category_id():
    from Autodesk.Revit.DB import BuiltInCategory, ElementId
    return element_id_int(ElementId(BuiltInCategory.OST_Lines))


def view_filter_records(doc, view):
    """Each view filter: name, kind, enabled, visible, and whether its
    categories include OST_Lines (a selection filter has none to read)."""
    lines_id = value_of(read(lines_category_id))
    out = []
    for fid in view.GetFilters():
        f = doc.GetElement(fid)
        rec = {"id": element_id_int(fid), "name": read(lambda: str(f.Name)),
               "kind": type(f).__name__ if f is not None else None,
               "enabled": read(lambda: bool(view.GetIsFilterEnabled(fid))),
               "visible": read(lambda: bool(view.GetFilterVisibility(fid)))}
        if f is not None and hasattr(f, "GetCategories"):
            rec["includes_ost_lines"] = read(
                lambda: lines_id in [element_id_int(c) for c in f.GetCategories()])
        else:
            rec["includes_ost_lines"] = rec_unavailable(
                "{0} has no GetCategories (not a parameter filter)".format(rec["kind"]))
        out.append(rec)
    return out


def template_vg_control(doc, view):
    """Whether the view's template controls V/G model categories, annotation
    categories and filters: each controlled when its parameter is NOT in the
    template's non-controlled set."""
    from Autodesk.Revit.DB import BuiltInParameter, ElementId
    tid = element_id_int(view.ViewTemplateId)
    if tid in (None, -1):
        return {"template_id": tid, "state": "no_template"}
    template = doc.GetElement(view.ViewTemplateId)
    free = [element_id_int(i) for i in template.GetNonControlledTemplateParameterIds()]
    out = {"template_id": tid, "state": "value"}
    for key, name in (("model_categories", "VIS_GRAPHICS_MODEL"),
                      ("annotation_categories", "VIS_GRAPHICS_ANNOTATION"),
                      ("filters", "VIS_GRAPHICS_FILTERS")):
        bip = getattr(BuiltInParameter, name, None)
        out[key] = (rec_unavailable("BuiltInParameter.{0} did not resolve".format(name))
                    if bip is None else
                    read(lambda: element_id_int(ElementId(bip)) not in free))
    return out


def mark_rows(doc, view, marks_record, collector_ids):
    """Per mark: line style and its category, the category/subcategory/element
    hidden states, and whether the view collector returns it."""
    from Autodesk.Revit.DB import ElementId
    lines_id = read(lines_category_id)

    def _lines_hidden():
        if value_of(lines_id) is None:
            raise ValueError("the OST_Lines id is unreadable: {0}".format(lines_id["error"]))
        return bool(view.GetCategoryHidden(ElementId(int(value_of(lines_id)))))
    lines_hidden = read(_lines_hidden)
    rows = []
    for m in (marks_record or {}).get("created") or []:
        elem = doc.GetElement(ElementId(int(m["id"])))
        style = value_of(read(lambda: elem.LineStyle))
        style_cat = value_of(read(lambda: style.GraphicsStyleCategory)) if style is not None else None
        rows.append({
            "id": m["id"], "key": m.get("key"), "orientation": m.get("orientation"),
            "placement": m.get("placement"),
            "uv0": (m.get("readback_uv") or [m.get("uv0"), m.get("uv1")])[0],
            "uv1": (m.get("readback_uv") or [m.get("uv0"), m.get("uv1")])[1],
            "uv_source": "readback" if m.get("readback_uv") else "requested",
            "painted": m.get("painted"), "line_style_applied": m.get("line_style_applied"),
            "line_style_id": read(lambda: element_id_int(style.Id)),
            "line_style_name": read(lambda: str(style.Name)),
            "line_style_category_id": read(lambda: element_id_int(style_cat.Id)),
            "line_style_category_name": read(lambda: str(style_cat.Name)),
            "lines_category_hidden": lines_hidden,
            "line_style_subcategory_hidden": read(lambda: bool(view.GetCategoryHidden(style_cat.Id))),
            "element_hidden": read(lambda: bool(elem.IsHidden(view))),
            "in_view_collector": (rec_value(int(m["id"]) in collector_ids)
                                  if collector_ids is not None
                                  else rec_unavailable("the view collector could not be read")),
        })
    return rows


def _q6_export(ctx, view, step, extra=None):
    rec, _ids = _step(ctx, view, "q6", step, extra)
    rec["crop_uv_at_export"] = read(crop_uv_at, ctx, view)
    rec["crop_box_active_at_export"] = read_attr(view, "CropBoxActive", bool)
    return rec


def _q6_marked(ctx, view, step, prefix):
    """Layout, marks, per-mark records, then the marked export."""
    built = read(mark_layout, ctx, view)
    if built["state"] != "value":
        return {"step": step, "refused": "layout_unavailable",
                "refused_reason": "the capture's layout raised: {0}".format(built["error"])}
    basis, layout, layout_record = built["value"]
    if layout.get("state") != "value":
        return {"step": step, "refused": "layout_unavailable", "layout": layout_record,
                "refused_reason": "the capture's layout is unavailable: {0}".format(
                    layout.get("reason"))}
    write, marks = create_marks(ctx, view, basis, layout, "{0} marks".format(prefix))
    ids = value_of(read(member_ids, ctx.doc, view))
    rec = _q6_export(ctx, view, step, {"marks": write})
    rec["layout"] = layout_record
    rec["marks_record"] = dict((k, v) for k, v in (marks or {}).items() if k != "created")
    rec["mark_rows"] = mark_rows(ctx.doc, view, marks, set(ids) if ids is not None else None)
    rec["view_filters"] = read(view_filter_records, ctx.doc, view)
    rec["template_vg_control"] = read(template_vg_control, ctx.doc, view)
    return rec


def run_q6_view(ctx, view, baseline):
    """Two variants, each in its own rolled-back group: marks with the crop
    as authored (S0 unmarked, S1 marked), and marks after production's crop
    write (S2 unmarked, S3 marked). Each marked export has an unmarked twin
    from the same state, so the analyzer can see which pixels the marks
    changed."""
    out = {"view_id": element_id_int(view.Id), "steps": [], "groups": []}
    if ctx.production is None:
        out["refused"] = _production_refusal(ctx)
        out["steps"] = [{"step": s, "refused": "production_unavailable",
                         "refused_reason": out["refused"]} for s in ("S0", "S1", "S2", "S3")]
        return out

    def _no_crop():
        out["steps"].append(_q6_export(ctx, view, "S0"))
        out["steps"].append(_q6_marked(ctx, view, "S1", "Q6 S1"))

    def _crop():
        write = write_production_crop(ctx, view, "Q6 S2 production crop")
        out["steps"].append(_q6_export(ctx, view, "S2", {"production_crop": write}))
        out["steps"].append(_q6_marked(ctx, view, "S3", "Q6 S3"))

    run_gated(ctx, [(view, baseline)], Q6_REQUIRED, out,
              (("no_crop_write", ("S0", "S1"), _no_crop),
               ("crop_write", ("S2", "S3"), _crop)), "Q6 {0}".format(out["view_id"]))
    return out


# ======================================================================
# DRIVER
# ======================================================================

def _resolve_view(doc, view_id):
    from Autodesk.Revit.DB import ElementId, View
    elem = doc.GetElement(ElementId(int(view_id)))
    if elem is None:
        raise ValueError("no element with id {0}".format(view_id))
    if not isinstance(elem, View):
        raise ValueError("element {0} is not a View ({1})".format(view_id, type(elem).__name__))
    return elem


def parse_inputs(inputs):
    def _at(i):
        return inputs[i] if len(inputs) > i else None
    out_dir = _at(0)
    if not out_dir:
        raise ValueError("IN[0] output directory is required")
    width = _at(7)
    params = {
        "output_directory": os.path.abspath(str(out_dir)),
        "q1_view": parse_view_id(_at(1), DEFAULT_Q1_VIEW),
        "q3_test_view": parse_view_id(_at(2), DEFAULT_Q3_TEST_VIEW),
        "q3_slab_view": parse_view_id(_at(3)),
        "q3_control_view": parse_view_id(_at(4)),
        "q4_view": parse_view_id(_at(5), DEFAULT_Q4_VIEW),
        "q5_views": parse_view_ids(_at(6), DEFAULT_Q5_VIEWS),
        "pixel_width": int(width) if width not in (None, "") else DEFAULT_PIXEL_WIDTH,
        "questions": select_questions(_at(8)),
    }
    options = parse_options(_at(9))
    params.update({
        "repo_root": options.get("repo_root"),
        "q1b_views": parse_view_ids(options.get("q1b_views"), DEFAULT_Q1B_VIEWS),
        "q3b_views": parse_view_ids(options.get("q3b_views"), DEFAULT_Q3B_VIEWS),
        "q5b_views": parse_view_ids(options.get("q5b_views"), DEFAULT_Q5B_VIEWS),
        "q6_views": (parse_view_ids(options.get("q6_views"), DEFAULT_Q6_VIEWS)
                     + parse_view_ids(options.get("q6_extra_views"), ())),
    })
    return params


def parse_options(value):
    """IN[9]: a dict (a Dynamo Dictionary, or a JSON string) of round-2 keys,
    OPTION_KEYS. An unknown key is refused: a misspelt key silently ignored
    would run the defaults while looking configured."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return {}
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        if hasattr(value, "Keys"):
            value = dict((str(k), value[k]) for k in value.Keys)
        else:
            raise ValueError("IN[9] must be a dictionary or a JSON object, not {0}".format(
                type(value).__name__))
    unknown = sorted(str(k) for k in value if str(k) not in OPTION_KEYS)
    if unknown:
        raise ValueError("IN[9] has unknown key(s) {0}; known: {1}".format(unknown, OPTION_KEYS))
    return dict((str(k), v) for k, v in value.items())


def _write_json(path, report):
    with open(path, "w") as fh:
        json.dump(report, fh, indent=2, sort_keys=True, default=str)


def run_probe(inputs):
    """Run the probe; returns the json path."""
    _ensure_revit()
    from Autodesk.Revit.DB import TransactionGroup, TransactionStatus
    params = parse_inputs(inputs)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    probe_dir = os.path.join(params["output_directory"], "capture_state_" + stamp)
    if not os.path.isdir(probe_dir):
        os.makedirs(probe_dir)
    json_path = os.path.join(probe_dir, "probe_capture_state_{0}.json".format(stamp))
    doc = _doc()
    ctx = Context(doc, probe_dir, params["pixel_width"])
    report = {"schema": SCHEMA, "probe": {"name": PROBE_NAME, "version": PROBE_VERSION},
              "started_at": stamp, "inputs": params, "probe_dir": probe_dir,
              "document": {"title": read_attr(doc, "Title", str),
                           "path": read_attr(doc, "PathName", str)},
              "views": {}, "questions": {}, "transaction_group": {},
              "verify": {}, "exports": ctx.exports, "exceptions": ctx.exceptions}

    # Every view the questions touch, resolved, with the BEFORE read taken
    # before any group starts: verify compares against this.
    wanted = []
    if "q1_q2" in params["questions"]:
        wanted.append(("q1_q2", params["q1_view"], "split"))
    if "q3" in params["questions"]:
        for key, role in (("q3_test_view", "test"), ("q3_slab_view", "test_slab"),
                          ("q3_control_view", "control")):
            wanted.append(("q3", params[key], role))
    if "q4" in params["questions"]:
        wanted.append(("q4", params["q4_view"], "elevation"))
    if "q5" in params["questions"]:
        for vid in params["q5_views"]:
            wanted.append(("q5", vid, "display_style"))
    for question, key, role in (("q1b", "q1b_views", "identity_write"),
                                ("q3b", "q3b_views", "scope_box"),
                                ("q5b", "q5b_views", "dependent"),
                                ("q6", "q6_views", "ticks")):
        if question in params["questions"]:
            for vid in params[key]:
                wanted.append((question, vid, role))
    views, before = {}, {}

    def _want(question, vid, role):
        if vid is None:
            report["views"].setdefault("not_provided", []).append(
                {"question": question, "role": role})
            return
        entry = report["views"].setdefault(str(vid), {"roles": [], "resolved": None})
        entry["roles"].append("{0}:{1}".format(question, role))
        if vid in views or entry["resolved"] is not None:
            return
        resolved = read(_resolve_view, doc, vid)
        entry["resolved"] = {"state": resolved["state"], "error": resolved["error"]}
        if resolved["state"] == "value":
            views[vid] = resolved["value"]
            entry["name"] = read_attr(views[vid], "Name", str)
            before[vid] = state_snapshot(doc, views[vid])

    for question, vid, role in wanted:
        _want(question, vid, role)
    # Q5b's primaries, resolved and snapshotted BEFORE the group too, so the
    # restore checks and the verify cover the view Q5b writes to.
    primary_of = {}
    if "q5b" in params["questions"]:
        for vid in params["q5b_views"]:
            if vid not in views:
                continue
            pid = value_of(call_method(views[vid], "GetPrimaryViewId", conv=element_id_int))
            primary_of[vid] = pid if pid not in (None, -1) else None
            if primary_of[vid] is not None:
                _want("q5b", primary_of[vid], "primary_of_{0}".format(vid))
    report["q5b_primaries"] = dict((str(k), v) for k, v in primary_of.items())
    if set(params["questions"]) & {"q3b", "q6"}:
        ctx.production_record, ctx.production = import_production(params)
        report["production_imports"] = ctx.production_record

    group = None
    tg = report["transaction_group"]
    tg.update(started=False, rolled_back=False, rollback_status=None)
    try:
        _force_close_dynamo_transaction()
        group = TransactionGroup(doc, "VOP capture-state probe")
        tg["started"] = group.Start() == TransactionStatus.Started
        if not tg["started"]:
            raise RuntimeError("TransactionGroup.Start did not start")
        q = report["questions"]
        if "q1_q2" in params["questions"]:
            q["q1_q2"] = _guarded(ctx, "q1_q2", views.get(params["q1_view"]),
                                  lambda v: run_q1_q2(ctx, v, before[params["q1_view"]]))
        if "q3" in params["questions"]:
            q["q3"] = {"views": []}
            for key, role in (("q3_test_view", "test"), ("q3_slab_view", "test_slab"),
                              ("q3_control_view", "control")):
                vid = params[key]
                if vid is None:
                    q["q3"]["views"].append({"role": role, "refused": "not_provided"})
                    continue
                q["q3"]["views"].append(_guarded(
                    ctx, "q3 " + role, views.get(vid),
                    lambda v, r=role, b=before.get(vid): run_q3_view(ctx, v, r, b)))
        if "q4" in params["questions"]:
            q["q4"] = _guarded(ctx, "q4", views.get(params["q4_view"]),
                               lambda v: run_q4(ctx, v, before[params["q4_view"]]))
        if "q5" in params["questions"]:
            q["q5"] = {"views": [_guarded(ctx, "q5", views.get(vid),
                                          lambda v, b=before.get(vid): run_q5_view(ctx, v, b))
                                 for vid in params["q5_views"]]}
        if "q1b" in params["questions"]:
            q["q1b"] = {"views": [_guarded(ctx, "q1b", views.get(vid),
                                           lambda v, b=before.get(vid): run_q1b_view(ctx, v, b))
                                  for vid in params["q1b_views"]]}
        if "q3b" in params["questions"]:
            roles = ["test"] + ["control"] * (len(params["q3b_views"]) - 1)
            q["q3b"] = {"views": [_guarded(
                ctx, "q3b", views.get(vid),
                lambda v, b=before.get(vid), r=role: run_q3b_view(ctx, v, b, r))
                for vid, role in zip(params["q3b_views"], roles)]}
        if "q5b" in params["questions"]:
            q["q5b"] = {"views": []}
            for vid in params["q5b_views"]:
                pid = primary_of.get(vid)
                q["q5b"]["views"].append(_guarded(
                    ctx, "q5b", views.get(vid),
                    lambda v, b=before.get(vid), p=pid: run_q5b_view(
                        ctx, v, b, views.get(p) if p is not None else None,
                        before.get(p) if p is not None else None, p)))
        if "q6" in params["questions"]:
            q["q6"] = {"views": [_guarded(ctx, "q6", views.get(vid),
                                          lambda v, b=before.get(vid): run_q6_view(ctx, v, b))
                                 for vid in params["q6_views"]]}
    except Exception as ex:
        ctx.exceptions.append(exception_record("outer", ex))
    finally:
        if group is not None and tg["started"]:
            try:
                status = group.RollBack()
                tg["rollback_status"] = enum_text(status)
                tg["rolled_back"] = status == TransactionStatus.RolledBack
            except Exception as ex:
                tg["rollback_status"] = "raised: {0}".format(ex)
                ctx.exceptions.append(exception_record("outer rollback", ex))

    # Read back AFTER the rollback; report every changed field, fix nothing.
    # The same snapshot as the baseline and every restore check.
    changed, unreadable = [], []
    for vid, view in sorted(views.items()):
        cmp_rec = read(lambda: compare_fields(before[vid], state_snapshot(doc, view)))
        report["verify"][str(vid)] = cmp_rec
        for field, entry in sorted((value_of(cmp_rec) or {}).items()):
            if entry["verdict"] == "unreadable_both":
                unreadable.append({"view_id": vid, "field": field})
            elif entry["verdict"] != "equal":
                changed.append({"view_id": vid, "field": field, "verdict": entry["verdict"]})
        if cmp_rec["state"] != "value":
            changed.append({"view_id": vid, "field": "*", "verdict": "unverifiable"})
    report["halted"] = ctx.halted
    report["verify_changed"] = changed
    # Fields Revit would not read before OR after (e.g. a plan view's
    # background): listed so their absence from verify_changed is not read
    # as a verified match.
    report["verify_unreadable_both"] = unreadable
    if changed or not tg["rolled_back"]:
        summary = "STATE CHANGED OR UNVERIFIED -- see verify_changed"
    elif unreadable:
        summary = ("ALL READABLE FIELDS RESTORED; {0} field(s) unreadable before and "
                   "after -- see verify_unreadable_both".format(len(unreadable)))
    else:
        summary = "ALL PROBED VIEWS RESTORED"
    if ctx.halted:
        summary = "PROBE HALTED ({0}); {1}".format(ctx.halted, summary)
    report["VERIFY_SUMMARY"] = summary
    report["finished_at"] = time.strftime("%Y%m%dT%H%M%S")
    # Written LAST, once every field above is final (CLAUDE.md, defect class 4).
    _write_json(json_path, report)
    return json_path


def _guarded(ctx, label, view, fn):
    if view is None:
        return {"refused": "view not resolved (see views)"}
    try:
        return fn(view)
    except Exception as ex:
        rec = exception_record(label, ex)
        ctx.exceptions.append(rec)
        return {"refused": "raised", "exception": rec}


def dynamo_main(inputs):
    return run_probe(list(inputs))


if "IN" in globals():
    try:
        OUT = dynamo_main(IN)  # noqa: F821
    except Exception as _fatal:
        OUT = {"json_path": None, "fatal": exception_record("dynamo_entrypoint", _fatal)}
