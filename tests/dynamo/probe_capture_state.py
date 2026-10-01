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
import time
import traceback


PROBE_NAME = "capture_state"
PROBE_VERSION = "2026-10-01.1"
SCHEMA = "vop.probe.capture_state.v1"

DEFAULT_Q1_VIEW = 6112047
DEFAULT_Q3_TEST_VIEW = 11999340
DEFAULT_Q4_VIEW = 2888380
DEFAULT_Q5_VIEWS = (13663964, 11999340)
DEFAULT_PIXEL_WIDTH = 2000
QUESTIONS = ("q1_q2", "q3", "q4", "q5")

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
    if value is None or (isinstance(value, str) and value.strip().lower() in ("", "all")):
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
    return None if v is None else str(v)


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


def verify_fields(doc, view):
    """The fields compared after the outer rollback."""
    ids = value_of(read(member_ids, doc, view))
    crop = read(crop_box_record, view)
    return {
        "template_id": value_of(read(lambda: element_id_int(view.ViewTemplateId)), "unreadable"),
        "crop_box": value_of(crop, "unreadable"),
        "crop_box_active": value_of(read_attr(view, "CropBoxActive", bool), "unreadable"),
        "display_style": value_of(read_attr(view, "DisplayStyle", enum_text), "unreadable"),
        "shadow_intensity": value_of(read_attr(view, "ShadowIntensity", jsonable), "unreadable"),
        "members_sha1": ids_fingerprint(ids)["sha1"] if ids is not None else "unreadable",
    }


def compare_fields(before, after):
    """Per field: equal / changed / unverifiable (either side unreadable).
    Crop boxes compare to CROP_TOLERANCE_FT, not bit for bit."""
    out = {}
    for key in sorted(set(before) | set(after)):
        b, a = before.get(key, "unreadable"), after.get(key, "unreadable")
        if b == "unreadable" or a == "unreadable":
            verdict = "unverifiable"
        elif key == "crop_box":
            eq = boxes_equal(b, a)
            verdict = "equal" if eq["local"] and eq["world"] is not False else "changed"
        else:
            verdict = "equal" if b == a else "changed"
        out[key] = {"verdict": verdict, "before": b, "after": a}
    return out


# ======================================================================
# WRITES, NESTED GROUPS, EXPORT
# ======================================================================

def tx_write(doc, name, fn):
    """``fn()`` inside its own committed Transaction, as a three-valued
    record. A raise rolls the transaction back and is recorded; so is a
    commit that does not return Committed."""
    from Autodesk.Revit.DB import Transaction, TransactionStatus
    tx = Transaction(doc, "VOP capture-state probe: " + name)
    try:
        started = tx.Start()
    except Exception as ex:
        return rec_raised(ex)
    if started != TransactionStatus.Started:
        return {"state": "raised", "value": None,
                "error": "Transaction.Start returned {0}".format(started)}
    try:
        value = fn()
    except Exception as ex:
        rec = rec_raised(ex)
        try:
            rec["rollback_status"] = str(tx.RollBack())
        except Exception as rb_ex:
            rec["rollback_status"] = "raised: {0}".format(rb_ex)
        return rec
    try:
        status = tx.Commit()
    except Exception as ex:
        return rec_raised(ex)
    if status != TransactionStatus.Committed:
        return {"state": "raised", "value": jsonable(value),
                "error": "Transaction.Commit returned {0}".format(status)}
    rec = rec_value(value if isinstance(value, dict) else jsonable(value))
    rec["commit_status"] = str(status)
    return rec


def write_crop_box(doc, view, box, name):
    """Write ``box`` (a record) as view.CropBox, its Min/Max taken in the crop
    transform the view carries AT THE WRITE. The value records that
    transform, so a read-back is compared with what was really written even
    when an earlier step (a scope box cleared) moved it."""
    from Autodesk.Revit.DB import BoundingBoxXYZ, XYZ

    def _apply():
        current = view.CropBox
        new = BoundingBoxXYZ()
        new.Transform = current.Transform
        new.Min = XYZ(*box["min"])
        new.Max = XYZ(*box["max"])
        view.CropBox = new
        return {"min": list(box["min"]), "max": list(box["max"]),
                "transform": transform_record(current.Transform)}
    return tx_write(doc, name, _apply)


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
    except Exception as ex:
        rec["body_error"] = exception_record(name, ex)
        ctx.exceptions.append(rec["body_error"])
    finally:
        try:
            status = group.RollBack()
            rec["rollback_status"] = str(status)
            rec["rolled_back"] = status == TransactionStatus.RolledBack
        except Exception as ex:
            rec["rollback_status"] = "raised: {0}".format(ex)
            ctx.exceptions.append(exception_record(name + ":rollback", ex))
    return rec


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

def run_q1_q2(ctx, view):
    """S0, then S1 (identity CropBox write) and S2 (the capture's write),
    each from S0's state in its own rolled-back nested group."""
    out = {"view_id": element_id_int(view.Id), "steps": [], "groups": []}
    s0, ids0 = _step(ctx, view, "q1", "S0")
    out["steps"].append(s0)
    box0 = value_of(s0["common"]["crop_box"])
    member_ids_by_step = {"S0": ids0}

    def _s1():
        writes = {"crop_box_identity": rec_unavailable("S0 crop box unreadable")}
        if box0 is not None:
            writes["crop_box_identity"] = write_crop_box(ctx.doc, view, box0, "Q1 S1 identity")
        rec, ids = _step(ctx, view, "q1", "S1", writes)
        out["steps"].append(rec)
        member_ids_by_step["S1"] = ids
    out["groups"].append(nested_group(ctx, "Q1 S1", _s1))

    # The capture's own crop (color_id_buffer: crop_box_from_uv_bounds of the
    # snapped crop-A rectangle) needs the full raster and frame geometry, so
    # S2 uses S0's box inset by Q1_INSET_FT on every edge.
    out["s2_crop_source"] = ("s0_box_inset_{0}ft (the capture's crop needs the "
                             "pipeline raster; not importable without side "
                             "effects)".format(Q1_INSET_FT))

    def _s2():
        writes = {}
        target = inset_box(box0, Q1_INSET_FT) if box0 is not None else None
        if target is None:
            writes["crop_box"] = rec_unavailable("S0 box unreadable or too small to inset")
        else:
            writes["crop_box"] = write_crop_box(ctx.doc, view, target, "Q1 S2 capture crop")
            writes["crop_box_written"] = target
            # color_id_buffer sets CropBoxActive = True right after the write.
            writes["crop_box_active"] = tx_write(
                ctx.doc, "Q1 S2 crop active",
                lambda: setattr(view, "CropBoxActive", True) or True)
        rec, ids = _step(ctx, view, "q1", "S2", writes)
        out["steps"].append(rec)
        member_ids_by_step["S2"] = ids
    out["groups"].append(nested_group(ctx, "Q1 S2", _s2))

    report = {"members": {}, "split": {}, "diff_vs_s0": {}}
    for step in out["steps"]:
        name = step["step"]
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


def _q3_step(ctx, view, step, box0, remove_scope_box, remove_shape):
    """One Q3 step from S0's state: optional scope-box clear, optional
    crop-shape removal, then the S1 write (S0's box scaled by Q3_SHRINK)."""
    from Autodesk.Revit.DB import BuiltInParameter, ElementId
    writes = {}
    if remove_scope_box:
        def _clear():
            p = view.get_Parameter(BuiltInParameter.VIEWER_VOLUME_OF_INTEREST_CROP)
            if p is None:
                raise ValueError("VIEWER_VOLUME_OF_INTEREST_CROP is not on this view")
            return bool(p.Set(ElementId.InvalidElementId))
        writes["scope_box_cleared"] = tx_write(ctx.doc, "Q3 {0} clear scope box".format(step), _clear)
    if remove_shape:
        manager = value_of(call_method(view, "GetCropRegionShapeManager"))
        shape_set = value_of(read_attr(manager, "ShapeSet", bool))
        if shape_set:
            writes["crop_shape_removed"] = tx_write(
                ctx.doc, "Q3 {0} remove crop shape".format(step),
                lambda: manager.RemoveCropRegionShape() or True)
        else:
            writes["crop_shape_removed"] = rec_unavailable(
                "ShapeSet is {0!r}; nothing to remove".format(shape_set))
    target = scaled_box(box0, Q3_SHRINK)
    writes["crop_box"] = write_crop_box(ctx.doc, view, target, "Q3 {0} crop".format(step))
    rec, _ids = _step(ctx, view, "q3", step, writes)
    read_back = value_of(rec["common"]["crop_box"])
    written = value_of(writes["crop_box"]) or dict(target, transform=None)
    rec["written_box"] = written
    rec["written_extent_ft"] = box_extent_ft(target)
    rec["read_back_box"] = read_back
    rec["read_back_extent_ft"] = box_extent_ft(read_back) if read_back else None
    rec["read_back_equal"] = boxes_equal(written, read_back)
    return rec


def _at_export(common):
    shape = value_of(common.get("crop_region_shape")) or {}
    scope = value_of(common.get("scope_box")) or {}
    return {"shape_set": shape.get("ShapeSet"),
            "scope_box": scope.get("value"), "scope_box_is_set": scope.get("is_set")}


def run_q3_view(ctx, view, role):
    out = {"view_id": element_id_int(view.Id), "role": role, "steps": [], "groups": [],
           "restore_mechanism": "nested TransactionGroup per step, rolled back "
                                "before the next; the crop box is read after each "
                                "rollback and compared with S0's"}
    s0, _ids = _step(ctx, view, "q3", "S0")
    s0["at_export"] = _at_export(s0["common"])
    out["steps"].append(s0)
    box0 = value_of(s0["common"]["crop_box"])
    if box0 is None:
        out["refused"] = "S0 crop box unreadable; no crop write attempted"
        return out
    plan = (("S1", False, False), ("S2", True, False), ("S3", False, True), ("S4", True, True))
    for step, scope, shape in plan:
        holder = {}

        def _body(step=step, scope=scope, shape=shape, holder=holder):
            holder["rec"] = _q3_step(ctx, view, step, box0, scope, shape)
            holder["rec"]["at_export"] = _at_export(holder["rec"]["common"])
        group = nested_group(ctx, "Q3 {0} {1}".format(out["view_id"], step), _body)
        rec = holder.get("rec") or {"step": step, "refused": group.get("body_error")}
        rec["group"] = group
        after = read(crop_box_record, view)
        rec["restore_check"] = {"crop_box_after_rollback": after,
                                "equal_to_s0": boxes_equal(box0, value_of(after))}
        out["steps"].append(rec)
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


def run_q4(ctx, view):
    from Autodesk.Revit.DB import Color, ViewDisplayBackground
    out = {"view_id": element_id_int(view.Id), "steps": [], "groups": []}
    s0, _ids = _step(ctx, view, "q4", "S0")
    s0["background"] = background_record(view)
    out["steps"].append(s0)
    shadow0 = value_of(s0["common"]["shadow_intensity"])

    def _white_background():
        white = Color(255, 255, 255)
        view.SetBackground(ViewDisplayBackground.CreateGradient(white, white, white))
        return True

    def _s1_s2():
        writes = {"shadow_intensity_before": read_attr(view, "ShadowIntensity", jsonable),
                  "shadow_intensity_set_0": tx_write(
                      ctx.doc, "Q4 S1 shadow 0",
                      lambda: setattr(view, "ShadowIntensity", 0) or 0)}
        writes["shadow_intensity_after"] = read_attr(view, "ShadowIntensity", jsonable)
        rec, _i = _step(ctx, view, "q4", "S1", writes)
        out["steps"].append(rec)
        writes2 = {"background_before": background_record(view),
                   "set_background_white": tx_write(ctx.doc, "Q4 S2 background",
                                                    _white_background)}
        writes2["background_after"] = background_record(view)
        rec2, _i = _step(ctx, view, "q4", "S2", writes2)
        out["steps"].append(rec2)
    out["groups"].append(nested_group(ctx, "Q4 S1+S2", _s1_s2))

    def _s3():
        writes = {"shadow_intensity_at_s3": read_attr(view, "ShadowIntensity", jsonable),
                  "shadow_intensity_s0": shadow0,
                  "background_before": background_record(view),
                  "set_background_white": tx_write(ctx.doc, "Q4 S3 background",
                                                   _white_background)}
        writes["background_after"] = background_record(view)
        rec, _i = _step(ctx, view, "q4", "S3", writes)
        out["steps"].append(rec)
    out["groups"].append(nested_group(ctx, "Q4 S3", _s3))
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


def run_q5_view(ctx, view):
    from Autodesk.Revit.DB import DisplayStyle, ElementId
    out = {"view_id": element_id_int(view.Id), "template": template_record(ctx.doc, view),
           "steps": [], "groups": []}

    def _set_flat():
        view.DisplayStyle = DisplayStyle.FlatColors
        return True

    def _s0():
        rec = {"step": "S0", "template_attached": True,
               "display_style_before": read_attr(view, "DisplayStyle", enum_text),
               "set_flat_colors": tx_write(ctx.doc, "Q5 S0 flat colors", _set_flat)}
        rec["display_style_after"] = read_attr(view, "DisplayStyle", enum_text)
        rec["took_effect"] = value_of(rec["display_style_after"]) == "FlatColors"
        out["steps"].append(rec)
    out["groups"].append(nested_group(ctx, "Q5 {0} S0".format(out["view_id"]), _s0))

    def _s1():
        rec = {"step": "S1",
               "detach_template": tx_write(
                   ctx.doc, "Q5 S1 detach",
                   lambda: setattr(view, "ViewTemplateId", ElementId.InvalidElementId) or True)}
        rec["template_id_after_detach"] = read(lambda: element_id_int(view.ViewTemplateId))
        rec["display_style_before"] = read_attr(view, "DisplayStyle", enum_text)
        rec["set_flat_colors"] = tx_write(ctx.doc, "Q5 S1 flat colors", _set_flat)
        rec["display_style_after"] = read_attr(view, "DisplayStyle", enum_text)
        rec["took_effect"] = value_of(rec["display_style_after"]) == "FlatColors"
        out["steps"].append(rec)
    out["groups"].append(nested_group(ctx, "Q5 {0} S1".format(out["view_id"]), _s1))
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
    return {
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
    views, before = {}, {}
    for question, vid, role in wanted:
        if vid is None:
            report["views"].setdefault("not_provided", []).append(
                {"question": question, "role": role})
            continue
        entry = report["views"].setdefault(str(vid), {"roles": [], "resolved": None})
        entry["roles"].append("{0}:{1}".format(question, role))
        if vid in views or entry["resolved"] is not None:
            continue
        resolved = read(_resolve_view, doc, vid)
        entry["resolved"] = {"state": resolved["state"], "error": resolved["error"]}
        if resolved["state"] == "value":
            views[vid] = resolved["value"]
            entry["name"] = read_attr(views[vid], "Name", str)
            before[vid] = verify_fields(doc, views[vid])

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
                                  lambda v: run_q1_q2(ctx, v))
        if "q3" in params["questions"]:
            q["q3"] = {"views": []}
            for key, role in (("q3_test_view", "test"), ("q3_slab_view", "test_slab"),
                              ("q3_control_view", "control")):
                vid = params[key]
                if vid is None:
                    q["q3"]["views"].append({"role": role, "refused": "not_provided"})
                    continue
                q["q3"]["views"].append(_guarded(ctx, "q3 " + role, views.get(vid),
                                                 lambda v, r=role: run_q3_view(ctx, v, r)))
        if "q4" in params["questions"]:
            q["q4"] = _guarded(ctx, "q4", views.get(params["q4_view"]),
                               lambda v: run_q4(ctx, v))
        if "q5" in params["questions"]:
            q["q5"] = {"views": [_guarded(ctx, "q5", views.get(vid),
                                          lambda v: run_q5_view(ctx, v))
                                 for vid in params["q5_views"]]}
    except Exception as ex:
        ctx.exceptions.append(exception_record("outer", ex))
    finally:
        if group is not None and tg["started"]:
            try:
                status = group.RollBack()
                tg["rollback_status"] = str(status)
                tg["rolled_back"] = status == TransactionStatus.RolledBack
            except Exception as ex:
                tg["rollback_status"] = "raised: {0}".format(ex)
                ctx.exceptions.append(exception_record("outer rollback", ex))

    # Read back AFTER the rollback; report every changed field, fix nothing.
    changed = []
    for vid, view in sorted(views.items()):
        cmp_rec = read(lambda: compare_fields(before[vid], verify_fields(doc, view)))
        report["verify"][str(vid)] = cmp_rec
        for field, entry in sorted((value_of(cmp_rec) or {}).items()):
            if entry["verdict"] != "equal":
                changed.append({"view_id": vid, "field": field, "verdict": entry["verdict"]})
        if cmp_rec["state"] != "value":
            changed.append({"view_id": vid, "field": "*", "verdict": "unverifiable"})
    report["verify_changed"] = changed
    report["VERIFY_SUMMARY"] = ("ALL PROBED VIEWS RESTORED" if not changed and tg["rolled_back"]
                                else "STATE CHANGED OR UNVERIFIED -- see verify_changed")
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
