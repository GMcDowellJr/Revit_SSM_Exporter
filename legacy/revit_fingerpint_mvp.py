# -*- coding: utf-8 -*-
import clr
import json

# Revit/Dynamo plumbing
clr.AddReference("RevitServices")
from RevitServices.Persistence import DocumentManager

clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import (
    FilteredElementCollector,
    LinePatternElement,
    TextNoteType,
    DimensionType,
    View,
    GraphicsStyleType,
    WorksharingUtils,
    BuiltInCategory,
    SpecTypeId,
    CategoryType,
    ElementType,
    BuiltInParameter,
    UnitUtils,
    UnitTypeId,
    ElementId,
    FillPatternElement,
    Category
)

DEBUG_INCLUDE_LINEPATTERN_SIGNATURES = False
DEBUG_INCLUDE_FILLPATTERN_SIGNATURES = False

# ------------- helpers -----------------

def add_origin(key="origin"):
    # Try XYZ-style origin
    try:
        o = g.Origin
        parts.append("grid[{}].{}={},{},{}".format(idx, key, f(o.X), f(o.Y), f(o.Z)))
        return
    except:
        pass

    # Try UV-style origin (U,V)
    try:
        o = g.Origin
        parts.append("grid[{}].{}={},".format(idx, key) + "{},<None>".format(f(o.U), f(o.V)))
        return
    except:
        pass

    # Try separate U/V properties
    for u_name, v_name in [("OriginU", "OriginV"), ("UOrigin", "VOrigin"), ("OffsetU", "OffsetV")]:
        try:
            u = getattr(g, u_name)
            v = getattr(g, v_name)
            parts.append("grid[{}].{}={},{},<None>".format(idx, key, f(u), f(v)))
            return
        except:
            pass

    parts.append("grid[{}].{}=<None>".format(idx, key))

def rgb_sig_from_color(col):
    try:
        return "{},{},{}".format(int(col.Red), int(col.Green), int(col.Blue))
    except:
        return "<None>"

def canon_str(s):
    if s is None:
        return None
    try:
        s2 = safe_str(s)
        return s2.strip()
    except:
        return None

def sig_val(v):
    if v is None:
        return "<None>"
    s = safe_str(v).strip()
    return s if s else "<None>"

def get_element_display_name(elem):
    if elem is None:
        return None

    # 1) .Name
    try:
        nm = getattr(elem, "Name", None)
        nm_c = canon_str(nm)
        if nm_c:
            return nm_c
    except:
        pass

    # 2) Common name parameters
    for bip_name in ["SYMBOL_NAME_PARAM", "ALL_MODEL_TYPE_NAME", "ALL_MODEL_INSTANCE_COMMENTS"]:
        bip = getattr(BuiltInParameter, bip_name, None)
        if bip is None:
            continue
        try:
            p = elem.get_Parameter(bip)
            if p and p.HasValue:
                s = p.AsString()
                s_c = canon_str(s)
                if s_c:
                    return s_c
        except:
            pass

    return None

def _param(elem, bip):
    try:
        return elem.get_Parameter(bip)
    except:
        return None

def _as_string(p):
    try:
        if p and p.HasValue:
            s = p.AsString()
            if s is not None:
                return safe_str(s)
    except:
        pass
    return None

def _as_double(p):
    try:
        if p and p.HasValue:
            return p.AsDouble()
    except:
        pass
    return None

def _as_int(p):
    try:
        if p and p.HasValue:
            return p.AsInteger()
    except:
        pass
    return None

def _as_bool_from_param(p):
    v = _as_int(p)
    if v is None:
        return None
    return True if v != 0 else False

def first_param(elem, bip_names=None, ui_names=None):
    # BuiltInParameter by NAME safely (no AttributeError)
    for bip_name in (bip_names or []):
        try:
            bip = getattr(BuiltInParameter, bip_name, None)
        except:
            bip = None
        if bip is None:
            continue
        try:
            p = elem.get_Parameter(bip)
            if p and p.HasValue:
                return p
        except:
            pass

    # UI-name fallback (English UI labels)
    for nm in (ui_names or []):
        try:
            p = elem.LookupParameter(nm)
            if p and p.HasValue:
                return p
        except:
            pass

    return None

def fnum(v, nd):
    return None if v is None else float(format(float(v), ".{}f".format(nd)))

def format_len_inches(feet_val):
    if feet_val is None:
        return None
    try:
        return UnitUtils.ConvertFromInternalUnits(feet_val, UnitTypeId.Inches)
    except:
        try:
            return float(feet_val) * 12.0
        except:
            return None

def rgb_dict_from_color(col):
    try:
        return {"r": int(col.Red), "g": int(col.Green), "b": int(col.Blue)}
    except:
        return None

def try_get_color_rgb_from_elem(elem):
    """
    Returns (color_int, color_rgb)
    Canonical color representation for all styles.
    """
    p = first_param(elem, bip_names=["TEXT_COLOR", "LINE_COLOR"], ui_names=["Color"])
    color_int = _as_int(p)

    if color_int is None:
        return None, None

    try:
        r = (color_int      ) & 0xFF
        g = (color_int >>  8) & 0xFF
        b = (color_int >> 16) & 0xFF
        return color_int, {"r": r, "g": g, "b": b}
    except:
        return color_int, None

def get_type_display_name(elem):
    """
    Try to get the same name you see in the Type selector:
    SYMBOL_NAME_PARAM first, then .Name as fallback.
    """
    # 1) Type Name parameter
    try:
        p = elem.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
        if p and p.HasValue:
            nm = p.AsString()
            nm_c = canon_str(nm)
            if nm_c:
                return nm_c
    except:
        pass

    # 2) Fallback to .Name
    try:
        nm = getattr(elem, "Name", None)
        nm_c = canon_str(nm)
        if nm_c:
            return nm_c
    except:
        pass

    return None

def safe_str(x):
    try:
        return str(x)
    except:
        try:
            return unicode(x)
        except:
            return u"<unrepr>"

def make_hash(values):
    """
    Deterministic hash based on a sequence of strings.
    Uses .NET MD5 to avoid IronPython limitations.
    """
    from System.Text import Encoding
    from System.Security.Cryptography import MD5

    joined = u"|".join([safe_str(v) for v in values])
    data = Encoding.UTF8.GetBytes(joined)
    md5 = MD5.Create()
    hash_bytes = md5.ComputeHash(data)
    return "".join(["{0:02x}".format(b) for b in hash_bytes])

def get_doc():
    return DocumentManager.Instance.CurrentDBDocument

# ------------- fill patterns -----------------

def get_linestyles_fingerprint(doc):
    info = {
        "count": 0,
        "raw_count": 0,
        "names": [],
        "records": [],
        "signature_hashes": [],
        "hash": None,

        # (optional) small, domain-local debug counters
        "debug_fail_get_lines_cat": 0,
        "debug_fail_subcats": 0,
        "debug_skipped_no_name": 0,
        "debug_fail_record_build": 0,
    }

    # Only "Lines" category contains actual Line Styles (subcategories)
    try:
        lines_cat = Category.GetCategory(doc, BuiltInCategory.OST_Lines)
    except:
        info["debug_fail_get_lines_cat"] += 1
        lines_cat = None

    if not lines_cat:
        return info

    try:
        subs = list(lines_cat.SubCategories)
    except:
        info["debug_fail_subcats"] += 1
        subs = []

    info["raw_count"] = len(subs)

    records = []
    names = []

    for sc in subs:
        try:
            sc_name = canon_str(getattr(sc, "Name", None))
            if not sc_name:
                info["debug_skipped_no_name"] += 1
                continue
            names.append(sc_name)

            # weights
            try: w_proj = sc.GetLineWeight(GraphicsStyleType.Projection)
            except: w_proj = None
            try: w_cut  = sc.GetLineWeight(GraphicsStyleType.Cut)
            except: w_cut = None

            # color
            try:
                c = sc.LineColor
                rgb_sig = "{}-{}-{}".format(int(c.Red), int(c.Green), int(c.Blue))
            except:
                rgb_sig = "<None>"

            # SINGLE line pattern field (UID) with "<None>" for invalid/solid
            lp_val = "<None>"
            try:
                lp_id = sc.GetLinePatternId(GraphicsStyleType.Projection)
                if lp_id and lp_id != ElementId.InvalidElementId:
                    lp_elem = doc.GetElement(lp_id)
                    lp_val = getattr(lp_elem, "UniqueId", None) or "<None>"
            except:
                lp_val = "<None>"

            # record signature (names ARE identity here by your locked semantics)
            records.append("|".join([
                safe_str(sc_name),
                safe_str(w_proj),
                safe_str(w_cut),   # kept for now (pending decision)
                safe_str(rgb_sig),
                safe_str(lp_val),
            ]))
        except:
            info["debug_fail_record_build"] += 1
            continue

    records_sorted = sorted(records)
    info["records"] = records_sorted
    info["names"] = sorted(set(names))
    info["count"] = len(records_sorted)

    # Per-row signature hashes (metadata; NOT used in global hash)
    info["signature_hashes"] = [make_hash([r]) for r in records_sorted] if records_sorted else []
    
    info["record_rows"] = []
    if records_sorted:
        sigs = info.get("signature_hashes") or []
        # Defensive: if something ever goes out of sync, fail-soft by pairing "<None>"
        for i, r in enumerate(records_sorted):
            sh = sigs[i] if i < len(sigs) else "<None>"
            info["record_rows"].append({
                "record": r,
                "sig_hash": sh,
            })
            
    # GLOBAL hash stays EXACTLY the same semantic as before
    info["hash"] = make_hash(records_sorted) if records_sorted else None
    return info

# ------------- fill patterns -----------------

def get_fillpattern_fingerprint(doc):
    info = {
        "count": 0,
        "raw_count": 0,
        "names": [],
        "signature_hashes": [],
        "hash": None,
        "records": [],
        # debug counters so you can see why things disappear
        "debug_total_elements": 0,
        "debug_kept": 0,
        "debug_skipped_no_name": 0,
        "debug_fail_getfillpattern": 0,
        "debug_fail_grid_read": 0,
    }
    

    try:
        col = list(FilteredElementCollector(doc).OfClass(FillPatternElement))
    except:
        return info
    info["raw_count"] = len(col)

    def f(v, nd=9):
        if v is None:
            return "<None>"
        try:
            return format(float(v), ".{}f".format(nd))
        except:
            return sig_val(v)

    def read_is_model(fp, target):
        # Prefer explicit property, else infer from target when possible
        is_model = None
        for attr in ["IsModelFillPattern", "IsModel", "IsModelFill"]:
            try:
                if hasattr(fp, attr):
                    is_model = getattr(fp, attr)
                    break
            except:
                pass
        if is_model is None:
            try:
                if target is not None:
                    is_model = (int(target) == 1)  # Drafting=0, Model=1 in many builds
            except:
                pass
        return is_model

    def grid_sig(fp, i):
        # Return a stable list; never raise
        idx = "{:03d}".format(int(i))
        g = None
        try:
            if hasattr(fp, "GetFillPatternGrid"):
                g = fp.GetFillPatternGrid(i)
        except:
            g = None
        if g is None:
            try:
                if hasattr(fp, "GetFillGrid"):
                    g = fp.GetFillGrid(i)
            except:
                g = None

        if g is None:
            info["debug_fail_grid_read"] += 1
            return ["grid[{}].unreadable=<None>".format(idx)]

        parts = []

        def add_float(prop_name, key):
            try:
                v = getattr(g, prop_name)
                parts.append("grid[{}].{}={}".format(idx, key, f(v)))
            except:
                parts.append("grid[{}].{}=<None>".format(idx, key))

        # origin can vary across versions; try a couple shapes
        def add_origin_2d():
            # Try UV-style origin (U,V)
            try:
                o = g.Origin
                u = getattr(o, "U", None)
                v = getattr(o, "V", None)
                if u is not None and v is not None:
                    parts.append("grid[{}].origin_uv={},{}".format(idx, f(u), f(v)))
                    return
            except:
                pass

            # Try XYZ-style origin but store only X,Y
            try:
                o = g.Origin
                x = getattr(o, "X", None)
                y = getattr(o, "Y", None)
                if x is not None and y is not None:
                    parts.append("grid[{}].origin_xy={},{}".format(idx, f(x), f(y)))
                    return
            except:
                pass

            # Try separate scalars
            for u_name, v_name in [("OriginU", "OriginV"), ("UOrigin", "VOrigin")]:
                try:
                    u = getattr(g, u_name)
                    v = getattr(g, v_name)
                    parts.append("grid[{}].origin_uv={},{}".format(idx, f(u), f(v)))
                    return
                except:
                    pass

            parts.append("grid[{}].origin=<None>".format(idx))

        add_float("Angle", "angle")
        add_origin_2d()
        add_float("Offset", "offset")
        add_float("Shift", "shift")

        return parts

    records = []
    per_hashes = []
    names= []

    for e in col:
        info["debug_total_elements"] += 1

        name = canon_str(getattr(e, "Name", None))
        if not name:
            info["debug_skipped_no_name"] += 1
            continue
        names.append(name)

        # Always keep the element, even if we can't read its FillPattern
        fp = None
        try:
            fp = e.GetFillPattern()
        except:
            fp = None

        if fp is None:
            info["debug_fail_getfillpattern"] += 1
            sig = [
                "is_solid=<None>",
                "is_model=<None>",
                "target=<None>",
                "grid_count=<None>",
                "grid[000].unreadable=<None>",
                "error=GetFillPatternFailed",
            ]
        else:
            # Core fields
            is_solid = None
            try: is_solid = fp.IsSolidFill
            except: pass

            target = None
            try: target = fp.Target
            except: pass

            is_model = read_is_model(fp, target)

            gc = None
            try: gc = fp.GridCount
            except: pass

            sig = [
                "is_solid={}".format(sig_val(is_solid)),
                "is_model={}".format(sig_val(is_model)),
                "target={}".format(sig_val(target)),
                "grid_count={}".format(sig_val(gc)),
            ]

            # Grids (fail-soft: if grid read fails, you still keep pattern)
            if gc:
                try:
                    for i in range(int(gc)):
                        sig.extend(grid_sig(fp, i))
                except:
                    info["debug_fail_grid_read"] += 1
                    sig.append("error=GridLoopFailed")

        # Keep signature deterministic
        sig_sorted = sorted(sig)
        def_hash = make_hash(sig_sorted)

        rec = {
            "id": safe_str(e.Id.IntegerValue),
            "uid":getattr(e, "UniqueId", "") or "",
            "name": name,          # metadata only
            "def_hash": def_hash,  # hashed definition
        }
        if DEBUG_INCLUDE_FILLPATTERN_SIGNATURES:
            rec["def_signature"] = sig_sorted

        records.append(rec)
        per_hashes.append(def_hash)
        info["debug_kept"] += 1

    per_hashes = sorted(per_hashes)
    info["signature_hashes"] = sorted(per_hashes)
    info["names"] = sorted(set(names))
    info["count"] = len(info["names"])
    info["hash"] = make_hash(info["signature_hashes"]) if info["signature_hashes"] else None
    info["records"] = sorted(records, key=lambda r: (r.get("name",""), r.get("id","")))
    
    info["record_rows"] = []
    try:
        recs = info.get("records") or []
        info["record_rows"] = [{
            "record_key": safe_str(r.get("uid", "")),        # <-- UniqueId
            "sig_hash":   safe_str(r.get("def_hash", "")),
            "name":       safe_str(r.get("name", "")),       # optional metadata
        } for r in recs]
    except:
        info["record_rows"] = []
    
    return info

# ------------- identity & context -----------------

def get_identity_fingerprint(doc):
    app = doc.Application
    info = {}

    info["project_title"] = safe_str(doc.Title)

    try:
        if doc.IsWorkshared:
            # Central path or model path
            try:
                mp = WorksharingUtils.GetModelPath(doc)
                info["central_path"] = safe_str(mp.CentralServerPath)
            except:
                info["central_path"] = safe_str(doc.PathName)
        else:
            info["central_path"] = safe_str(doc.PathName)
    except:
        info["central_path"] = safe_str(doc.PathName)

    info["is_workshared"] = bool(getattr(doc, "IsWorkshared", False))

    # Revit version/build
    info["revit_version_number"] = safe_str(app.VersionNumber)
    info["revit_version_name"]   = safe_str(app.VersionName)
    info["revit_build"]          = safe_str(app.VersionBuild)

    return info

# ------------- units fingerprint (minimal, no UnitType) -----------------

def get_units_fingerprint(doc):
    """
    Version-safe units snapshot (Revit 2022+).
    - 'repr' is the raw Units.ToString() for quick sanity.
    - 'specs' holds explicit Length/Area/Volume format options.
    """
    result = {
        "repr": None,
        "specs": {},
        "hash": None
    }

    try:
        u = doc.GetUnits()
    except:
        return result

    result["repr"] = safe_str(u)

    records = []

    specs = [
        ("length", SpecTypeId.Length),
        ("area",   SpecTypeId.Area),
        ("volume", SpecTypeId.Volume)
    ]

    for label, spec_id in specs:
        try:
            fmt = u.GetFormatOptions(spec_id)
        except:
            continue

        try:
            unit_id   = safe_str(fmt.GetUnitTypeId())
        except:
            unit_id   = "<no-unit>"

        try:
            symbol_id = safe_str(fmt.GetSymbolTypeId())
        except:
            symbol_id = "<no-symbol>"

        try:
            acc = fmt.Accuracy
        except:
            acc = None

        rec = {
            "spec": label,
            "unit_id": unit_id,
            "symbol_id": symbol_id,
            "accuracy": acc
        }
        result["specs"][label] = rec
        records.append("{}|{}|{}|{}".format(label, unit_id, symbol_id, acc))

    if records:
        result["hash"] = make_hash(sorted(records))

    return result

# ------------- lineweights fingerprint -----------------

def get_objectstyles_fingerprint(doc):
    """
    Object Styles / Category graphics fingerprint (non-import categories).

    Per ROW (category + each subcategory row):
      - parent category name
      - row name (subcategory name or "<self>")
      - CategoryType (Model, Annotation, Tag, etc.)
      - Projection lineweight index
      - Cut lineweight index
      - Line color (RGB sig)
      - Projection line pattern Id
      - Cut line pattern Id
      - Category material Id (if any)

    Output:
      - count: number of rows
      - hash: global hash of row hashes
      - signature_hashes: per-row hashes (sorted)
      - category_hashes: per parent category hash (row hashes under that parent)
      - records: row signature strings (sorted)
    """
    info = {
        "count": 0,
        "raw_count": 0,
        "names": [],
        "hash": None,
        "signature_hashes": [],
        "category_hashes": {},
        "records": [],
        # debug counters
        "debug_total_categories": 0,
        "debug_rows_emitted": 0,
        "debug_skipped_import": 0,
        "debug_fail_row": 0
    }
    
    row_pairs = []
    
    try:
        cats = doc.Settings.Categories
    except:
        return info

    def row_sig(cat_obj, parent_name, row_name, cat_type):
        # Projection / cut lineweights
        try:
            w_proj = cat_obj.GetLineWeight(GraphicsStyleType.Projection)
        except:
            w_proj = None

        try:
            w_cut = cat_obj.GetLineWeight(GraphicsStyleType.Cut)
        except:
            w_cut = None

        # Line color
        try:
            col = cat_obj.LineColor
            rgb_sig = rgb_sig_from_color(col)
        except:
            rgb_sig = "<None>"

        # Line pattern (Object Styles has ONE pattern, not proj/cut)
        try:
            lp_id = cat_obj.GetLinePatternId(GraphicsStyleType.Projection)
            lp_val = "<None>"
            if lp_id and lp_id.IntegerValue > 0:
                lp_e = doc.GetElement(lp_id)
                lp_val = canon_str(getattr(lp_e, "UniqueId", None)) or "<None>"
        except:
            lp_val = "<None>"

        # Category material Id
        # Material (UID for stability)
        try:
            mat_id = cat_obj.Material
            mat_val = "<None>"
            if mat_id and mat_id.IntegerValue > 0:
                m = doc.GetElement(mat_id)
                mat_val = canon_str(getattr(m, "UniqueId", None)) or "<None>"
        except:
            mat_val = "<None>"

        # Deterministic row signature
        return "|".join([
            parent_name,
            row_name,
            cat_type,
            safe_str(w_proj),
            safe_str(w_cut),
            rgb_sig,
            lp_val,
            mat_val
        ])

    records = []
    row_hashes = []
    names = []
    per_parent_hashes = {}  # parent_name -> [row_hash,...]

    for cat in cats:
        info["debug_total_categories"] += 1
        if cat is None:
            continue

        # Skip import categories
        try:
            from Autodesk.Revit.DB import CategoryType
            if cat.CategoryType == CategoryType.Import:
                info["debug_skipped_import"] += 1
                continue
        except:
            pass

        # Parent name
        try:
            parent_name = canon_str(cat.Name)
        except:
            continue

        # Category type
        try:
            cat_type = safe_str(cat.CategoryType)
        except:
            cat_type = "<unknown>"

        # Emit the parent row ("<self>")
        try:
            sig = row_sig(cat, parent_name, "<self>", cat_type)
            row_key = "{}|{}".format(parent_name, "<self>")
            names.append(row_key)
            h = make_hash([sig])  # stable, deterministic
            records.append(sig)
            row_hashes.append(h)
            row_pairs.append((sig, h))
            per_parent_hashes.setdefault(parent_name, []).append(h)
            info["debug_rows_emitted"] += 1
        except:
            info["debug_fail_row"] += 1

        # Emit each subcategory row
        try:
            subs = cat.SubCategories
        except:
            subs = None

        if subs:
            for sub in subs:
                try:
                    sub_name = canon_str(sub.Name)
                    row_key = "{}|{}".format(parent_name, sub_name)
                    names.append(row_key)
                    sig = row_sig(sub, parent_name, sub_name, cat_type)
                    h = make_hash([sig])
                    records.append(sig)
                    row_hashes.append(h)
                    per_parent_hashes.setdefault(parent_name, []).append(h)
                    info["debug_rows_emitted"] += 1
                except:
                    info["debug_fail_row"] += 1
                    continue

    records_sorted = sorted(records)
    row_hashes_sorted = sorted(row_hashes)
    
    info["raw_count"] = len(names)
    info["names"] = sorted(set(names))
    info["count"] = len(info["names"])
    info["records"] = records_sorted
    info["signature_hashes"] = row_hashes_sorted
    info["count"] = len(records_sorted)
    info["hash"] = make_hash(row_hashes_sorted) if row_hashes_sorted else None
    info["record_rows"] = []
    if row_pairs:
        row_pairs_sorted = sorted(row_pairs, key=lambda t: t[0])
        info["record_rows"] = [{"record": s, "sig_hash": h} for (s, h) in row_pairs_sorted]
        
    # Per-parent rollups
    cat_hashes = {}
    for pname, hs in per_parent_hashes.items():
        hs_sorted = sorted(hs)
        cat_hashes[pname] = make_hash(hs_sorted) if hs_sorted else None
    info["category_hashes"] = cat_hashes

    return info

# ------------- line patterns fingerprint -----------------

def get_linepattern_fingerprint(doc):
    info = {
        "count": 0,
        "raw_count": 0,
        "names": [],
        "records": [],
        "signature_hashes": [],
        "hash": None,

        # debug counters
        "debug_missing_name": 0,
        "debug_fail_getpattern": 0,
        "debug_fail_segment_read": 0,
        "debug_kept": 0,
        
        "debug_getpattern_ex_types": {},
        "debug_getpattern_ex_samples": [],
        "debug_segment_ex_types": {},
        "debug_segment_ex_samples": [],
    }

    try:
        col = list(FilteredElementCollector(doc).OfClass(LinePatternElement))
    except:
        return info

    info["raw_count"] = len(col)

    names = []
    records = []
    per_hashes = []

    def fnum(v, nd=9):
        if v is None:
            return "<None>"
        try:
            return format(float(v), ".{}f".format(nd))
        except:
            return sig_val(v)

    for e in col:
        # name is metadata only
        name = canon_str(getattr(e, "Name", None))
        if not name:
            info["debug_missing_name"] += 1
            name = "<unnamed>"
        names.append(name)

        uid = None
        try:
            uid = canon_str(getattr(e, "UniqueId", None))
        except:
            uid = None

        lp = None
        try:
            # Use static overload to avoid pythonnet/IronPython method-binding issues
            lp = LinePatternElement.GetLinePattern(doc, e.Id)
        except Exception as ex:
            info["debug_fail_getpattern"] += 1

            t = ex.__class__.__name__
            info["debug_getpattern_ex_types"][t] = info["debug_getpattern_ex_types"].get(t, 0) + 1

            if len(info["debug_getpattern_ex_samples"]) < 5:
                info["debug_getpattern_ex_samples"].append({
                    "name": name,
                    "id": safe_str(e.Id.IntegerValue),
                    "uid": uid,
                    "ex_type": t,
                    "ex_msg": safe_str(str(ex)),
                })
            lp = None

        sig = []

        if lp is None:
            # Fail-soft: keep element, but signature will collapse unless we add distinguishing info.
            # We add uid as metadata marker ONLY for the failure case to avoid "all same hash".
            sig.append("error=GetLinePatternFailed")
            sig.append("uid={}".format(sig_val(uid)))
        else:
            segs = None
            try:
                # Prefer method (often binds better in pythonnet) if present
                get_segs = getattr(lp, "GetSegments", None)
                if get_segs:
                    segs = list(get_segs())
                else:
                    segs = list(getattr(lp, "Segments"))
            except Exception as ex:
                segs = None
                info["debug_fail_segment_read"] += 1

                # Optional: capture why segments are unreadable (bounded)
                t = ex.__class__.__name__
                info.setdefault("debug_segment_ex_types", {})
                info["debug_segment_ex_types"][t] = info["debug_segment_ex_types"].get(t, 0) + 1
                if len(info.setdefault("debug_segment_ex_samples", [])) < 5:
                    info["debug_segment_ex_samples"].append({
                        "name": name,
                        "id": safe_str(e.Id.IntegerValue),
                        "uid": uid,
                        "ex_type": t,
                        "ex_msg": safe_str(str(ex)),
                    })

            if segs is None:
                sig.append("error=SegmentsUnreadable")
                sig.append("uid={}".format(sig_val(uid)))
            else:
                # IMPORTANT: do NOT sort; segment order is part of the definition.
                sig.append("segment_count={}".format(sig_val(len(segs))))
                for i, s in enumerate(segs):
                    idx = "{:03d}".format(int(i))
                    try:
                        # Segment type (pythonnet sometimes fails to bind enum properties cleanly)
                        stype = None
                        try:
                            # 1) property
                            stype = getattr(s, "SegmentType", None)
                        except:
                            stype = None

                        if stype is None:
                            try:
                                # 2) method form (if present)
                                m = getattr(s, "GetSegmentType", None)
                                if m:
                                    stype = m()
                            except:
                                stype = None

                        # Segment type (Revit API: LinePatternSegment.Type)
                        stype_out = "<None>"
                        try:
                            st = s.Type
                            try:
                                stype_out = canon_str(st.ToString()) or "<None>"
                            except:
                                stype_out = safe_str(int(st))
                        except:
                            stype_out = "<None>"
                        
                        try:
                            slen = getattr(s, "Length", None)
                        except:
                            slen = None
                        sig.append("seg[{}].type={}".format(idx, sig_val(stype_out)))
                        sig.append("seg[{}].len={}".format(idx, sig_val(fnum(slen, 9))))
                    except:
                        info["debug_fail_segment_read"] += 1
                        sig.append("seg[{}].error=SegmentReadFailed".format(idx))

        # Deterministic: keep order (don’t sort), hash the definition signature
        def_hash = make_hash(sig)

        rec = {
            "id": safe_str(e.Id.IntegerValue),
            "name": name,          # metadata only
            "uid": uid,            # metadata only
            "def_hash": def_hash,  # hashed definition (or failure-signature)
        }
        if DEBUG_INCLUDE_LINEPATTERN_SIGNATURES:
            rec["def_signature"] = sig

        records.append(rec)
        per_hashes.append(def_hash)
        info["debug_kept"] += 1

    info["names"] = sorted(set(names))
    info["count"] = len(info["names"])
    info["records"] = sorted(records, key=lambda r: (r.get("name",""), r.get("id","")))
    info["signature_hashes"] = sorted(per_hashes)
    info["hash"] = make_hash(info["signature_hashes"]) if info["signature_hashes"] else None

    info["record_rows"] = []
    try:
        recs = info.get("records") or []
        info["record_rows"] = [{
            "record_key": safe_str(r.get("uid", "")),        # <-- UniqueId
            "sig_hash":   safe_str(r.get("def_hash", "")),
            "name":       safe_str(r.get("name", "")),       # optional metadata
        } for r in recs]
    except:
        info["record_rows"] = []
    
    return info

# ------------- text types fingerprint -----------------

def get_texttype_fingerprint(doc):
    info = {
        "count": 0,
        "names": [],
        "hash": None,

        # new
        "records": [],
        "signature_hashes": [],
        "raw_count": 0,
        "debug_missing_name": 0
    }

    types = list(FilteredElementCollector(doc).OfClass(TextNoteType))
    info["raw_count"] = len(types)

    names = []
    missing = 0
    records = []
    sig_hashes = []

    for t in types:
        type_name = get_type_display_name(t)
        if type_name:
            type_name = canon_str(type_name)
            names.append(type_name)
        else:
            missing += 1
            type_name = "<unnamed>"

        # --- core fields (same pattern you validated in the TextStyles exercise) ---
        font = _as_string(first_param(t, bip_names=["TEXT_FONT"], ui_names=["Text Font"]))
        size_ft = _as_double(first_param(t, bip_names=["TEXT_SIZE"], ui_names=["Text Size"]))
        size_in = fnum(format_len_inches(size_ft), 6)
        
        font = canon_str(font)

        width_factor = _as_double(first_param(t, bip_names=["TEXT_WIDTH_SCALE"], ui_names=["Width Factor"]))
        width_factor_n = fnum(width_factor, 6)

        background_i = _as_int(first_param(t, bip_names=["TEXT_BACKGROUND"], ui_names=["Background"]))

        # Graphics
        p_lw = first_param(t, bip_names=["TEXT_LINE_WEIGHT", "LINE_PEN"], ui_names=["Line Weight"])
        line_weight = _as_int(p_lw)

        color_int, color_rgb = try_get_color_rgb_from_elem(t)

        # Border / tabs / styles
        show_border = _as_bool_from_param(first_param(t, ui_names=["Show Border", "Show border"]))
        leader_border_offset_ft = _as_double(first_param(t, ui_names=["Leader/Border Offset", "Leader / Border Offset"]))
        leader_border_offset_in = fnum(format_len_inches(leader_border_offset_ft), 6)

        tab_size_ft = _as_double(first_param(t, ui_names=["Tab Size", "Tab size"]))
        tab_size_in = fnum(format_len_inches(tab_size_ft), 6)

        bold = _as_bool_from_param(first_param(t, ui_names=["Bold"]))
        italic = _as_bool_from_param(first_param(t, ui_names=["Italic"]))
        underline = _as_bool_from_param(first_param(t, ui_names=["Underline"]))

        # Leader Arrowhead (metadata only; do NOT put in core signature)
        leader_arrow_uid = None
        leader_arrow_name = None
        try:
            p_arrow = first_param(t, bip_names=["LEADER_ARROWHEAD"], ui_names=["Leader Arrowhead"])
            if p_arrow and p_arrow.HasValue:
                ah_eid = p_arrow.AsElementId()
                if ah_eid and ah_eid.IntegerValue > 0:
                    ah = doc.GetElement(ah_eid)
                    if ah:
                        leader_arrow_uid = ah.UniqueId
                        # robust display name
                        try:
                            leader_arrow_name = get_element_display_name(ah)
                        except:
                            leader_arrow_name = None
        except:
            pass

        # --- signature tuple (core) ---
        signature_tuple = [
            "font={}".format(sig_val(font)),
            "size_in={}".format(sig_val(size_in)),
            "width_factor={}".format(sig_val(width_factor_n)),
            "background={}".format(sig_val(background_i)),
            "line_weight={}".format(sig_val(line_weight)),
            "color_int={}".format(sig_val(color_int)),

            "show_border={}".format(sig_val(show_border)),
            "leader_border_offset_in={}".format(sig_val(leader_border_offset_in)),
            "tab_size_in={}".format(sig_val(tab_size_in)),
            "bold={}".format(sig_val(bold)),
            "italic={}".format(sig_val(italic)),
            "underline={}".format(sig_val(underline)),
        ]
        sig_hash = make_hash(signature_tuple)

        rec = {
            "type_id": safe_str(t.Id.IntegerValue),
            "type_uid": getattr(t, "UniqueId", "") or "",
            "type_name": type_name,

            "font": font,
            "text_size_ft": size_ft,
            "text_size_in": size_in,
            "width_factor": width_factor_n,
            "background_raw": background_i,
            "line_weight": line_weight,

            "color_int": color_int,
            "color_rgb": color_rgb,

            "show_border": show_border,
            "leader_border_offset_in": leader_border_offset_in,
            "tab_size_in": tab_size_in,
            "bold": bold,
            "italic": italic,
            "underline": underline,

            "leader_arrowhead_uid": leader_arrow_uid,
            "leader_arrowhead_name": leader_arrow_name,

            "signature_tuple": signature_tuple,
            "signature_hash": sig_hash
        }

        records.append(rec)
        sig_hashes.append(sig_hash)

    info["debug_missing_name"] = missing

    names_sorted = sorted(set(names))
    info["count"] = len(names_sorted)
    info["names"] = names_sorted

    # new: records + signature-based hash
    info["records"] = sorted(records, key=lambda r: (r.get("type_name",""), r.get("type_id","")))
    info["signature_hashes"] = sorted(sig_hashes)
    info["hash"] = make_hash(sorted(sig_hashes)) if sig_hashes else None
    
    info["record_rows"] = []
    try:
        recs = info.get("records") or []
        info["record_rows"] = [{
            "record_key": safe_str(r.get("type_uid", "")) or safe_str(r.get("uid", "")),
            "sig_hash":  safe_str(r.get("signature_hash", "")),
            "name":      safe_str(r.get("type_name", "")),   # optional metadata
        } for r in recs]
    except:
        info["record_rows"] = []
    
    return info

# ------------- dimension types fingerprint -----------------

def get_dimtype_fingerprint(doc):
    info = {
        "count": 0,
        "names": [],
        "hash": None,

        # new
        "records": [],
        "signature_hashes": [],
        "raw_count": 0,
        "debug_missing_name": 0
    }

    types = list(FilteredElementCollector(doc).OfClass(DimensionType))
    info["raw_count"] = len(types)

    names = []
    missing = 0
    records = []
    sig_hashes = []

    for d in types:
        type_name = get_type_display_name(d)
        if type_name:
            type_name = canon_str(type_name)
            if type_name:
                names.append(type_name)
            else:
                missing += 1
                continue
        else:
            missing += 1
            continue

        # --- minimal dim-style signature (text + graphics + ticks) ---
        text_font = _as_string(first_param(d, ui_names=["Text Font"]))
        text_font = canon_str(text_font)

        text_size_ft = _as_double(first_param(d, ui_names=["Text Size"]))
        text_size_in = fnum(format_len_inches(text_size_ft), 6)

        lw = _as_int(first_param(d, ui_names=["Line Weight"]))
        color_int, color_rgb = try_get_color_rgb_from_elem(d)

        # Tick Mark (arrowhead) – store UniqueId metadata + include NAME in signature (more stable than ids)
       
        tick_name = _as_string(first_param(d, ui_names=["Tick Mark"]))
        tick_uid = None
        try:
            p_tick = first_param(d, ui_names=["Tick Mark"])
            if p_tick and p_tick.HasValue:
                tid = p_tick.AsElementId()
                if tid and tid.IntegerValue > 0:
                    te = doc.GetElement(tid)
                    if te:
                        tick_uid = te.UniqueId
                        # prefer element.Name where available
                        try:
                            tick_name = tick_name or get_element_display_name(te)
                            if tick_name is not None:
                                tick_name = canon_str(tick_name)
                        except:
                            pass
        except:
            pass

        # Witness line control is common; keep as metadata + optional signature
        witness = _as_string(first_param(d, ui_names=["Witness Line Control"]))
        witness = canon_str(witness)

        tick_name = canon_str(tick_name)

        signature_tuple = [
            "text_font={}".format(sig_val(text_font)),
            "text_size_in={}".format(sig_val(text_size_in)),
            "line_weight={}".format(sig_val(lw)),
            "color_int={}".format(sig_val(color_int)),
            "tick_mark={}".format(sig_val(tick_name)),
            "witness_ctrl={}".format(sig_val(witness)),
        ]

        sig_hash = make_hash(signature_tuple)

        rec = {
            "type_id": safe_str(d.Id.IntegerValue),
            "type_uid": getattr(d, "UniqueId", "") or "",
            "type_name": type_name,

            "text_font": text_font,
            "text_size_ft": text_size_ft,
            "text_size_in": text_size_in,

            "line_weight": lw,
            "color_int": color_int,
            "color_rgb": color_rgb,

            "tick_mark_name": tick_name,
            "tick_mark_uid": tick_uid,
            "witness_line_control": witness,

            "signature_tuple": signature_tuple,
            "signature_hash": sig_hash
        }

        records.append(rec)
        sig_hashes.append(sig_hash)

    info["debug_missing_name"] = missing

    names_sorted = sorted(set(names))
    info["count"] = len(names_sorted)
    info["names"] = names_sorted

    info["records"] = sorted(records, key=lambda r: (r.get("type_name",""), r.get("type_id","")))
    info["signature_hashes"] = sorted(sig_hashes)
    info["hash"] = make_hash(sorted(sig_hashes)) if sig_hashes else None

    info["record_rows"] = []
    try:
        recs = info.get("records") or []
        info["record_rows"] = [{
            "record_key": safe_str(r.get("type_uid", "")),
            "sig_hash":  safe_str(r.get("signature_hash", "")),
            "name":      safe_str(r.get("type_name", "")),   # optional metadata
        } for r in recs]
    except:
        info["record_rows"] = []
        
    return info

# ------------- view templates fingerprint -----------------

def get_viewtemplate_fingerprint(doc):
    info = {
        "count": 0,
        "names": [],
        "hash": None
    }

    try:
        col = FilteredElementCollector(doc).OfClass(View)
        names = []
        for v in col:
            try:
                if v.IsTemplate:
                    names.append(canon_str(v.Name))
            except:
                continue
        names_sorted = sorted(set(names))
        info["count"] = len(names_sorted)
        info["names"] = names_sorted
        info["hash"]  = make_hash(names_sorted)
    except:
        pass

    return info

# ------------- main -----------------

doc = get_doc()

fingerprint = {}
fingerprint["identity"]        = get_identity_fingerprint(doc)
fingerprint["units"]           = get_units_fingerprint(doc)
fingerprint["objectstyles"] = get_objectstyles_fingerprint(doc)
fingerprint["line_patterns"]   = get_linepattern_fingerprint(doc)
fingerprint["text_types"]      = get_texttype_fingerprint(doc)
fingerprint["dimension_types"] = get_dimtype_fingerprint(doc)
fingerprint["view_templates"]  = get_viewtemplate_fingerprint(doc)
fingerprint["fill_patterns"] = get_fillpattern_fingerprint(doc)
fingerprint["line_styles"] = get_linestyles_fingerprint(doc)

OUT = json.dumps(fingerprint, indent=2, sort_keys=True)
