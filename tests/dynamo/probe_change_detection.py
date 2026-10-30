"""Change-detection probe (Q1) for Revit 2025 / Dynamo 3.x CPython3.

Q1: can Revit tell us cheaply which elements changed between two captured
model versions, so a later run can skip the views that did not change?

Q1b (probe 2026-10-27.1): run 20261026T095313 found that GetChangedElements
cannot compare two detached archive opens (each detached open is a new
version history). Q1b asks the same of ``Element.VersionGuid``: read for
every element in each detached open, it is (a) stable across two detached
opens of ONE file or not, (b) kept from A to B for elements that did not
change or not, and (c) per view, a fingerprint of the view, its members, its
template, its filters and its members' types (``vg_verdict``).

  1. Reflection: do ``Document.GetDocumentVersion`` and
     ``Document.GetChangedElements`` exist in THIS Revit, and with which
     signatures? Q1 stops (nothing is opened) when either is absent or its
     shape is not the one this probe knows how to call.
  2. Archive A (the earlier version) is opened EXACTLY as the batch opens it
     (pyrevit BatchCapture ``_open_options``: DetachAndPreserveWorksets, all
     worksets, no audit; warnings deleted in FailuresProcessing; the batch's
     dialog answers). Its DocumentVersion is read, and the membership of
     every probed view. Closed unsaved.
  3. Control (IN[4] ``control``): "reopen" (the default) opens A detached a
     SECOND time and reads its version: two GUIDs for one file mean each
     detached open mints its own. "copy" opens a byte copy of A, and later
     of B, NOT detached -- refused when either archive is a cloud central,
     whose copy never finished opening (run 20261026T084944).
  4. Archive B is opened the batch's way. ``GetChangedElements`` is called
     with A's GUID (detached), the control's GUID, B's own GUID (a positive
     control: it must succeed) and a random GUID (a negative control: the
     exception it raises is what "not in this history" looks like). The
     per-view intersections are made against the first call that succeeded
     (A detached first). Closed unsaved.
  5. Every archive's sha256, size and mtime are read before step 2 and again
     after step 4; a difference is listed under ``archives_changed``.
  6. (Q1b) In every DETACHED open, every element's VersionGuid is read once
     (``version_guids``); the reopen is compared with A
     (``reopen_version_guids``), B with A (``version_guid_a_vs_b``), and each
     probed view gets ``version_guid`` signals and ``vg_verdict``. The API
     calls of step 4 run only when reflection confirms them; the probe stops
     only when neither the API nor Element.VersionGuid is there.

Dynamo inputs:
    IN[0] = output directory (required). change_detection_<stamp>/ is
            created in it.
    IN[1] = archive A: full path of the EARLIER .rvt (required)
    IN[2] = archive B: full path of the LATER .rvt (required)
    IN[3] = view ids, a list (optional). Empty: a seeded sample of
            ``sample_size`` views across view types, drawn from A.
    IN[4] = options: a Dictionary or JSON object; keys in OPTION_KEYS,
            see PROBE_CHANGE_DETECTION.md. An unknown key is refused.

Output: OUT = the path of probe_change_detection_<stamp>.json. The full id
lists of every successful call are in changed_ids_<label>.json beside it.
Offline: tools/analyze_change_detection_probe.py compares the probe's
answer with the captures already made of A and B.

THE PROBE WRITES NOTHING TO ANY DOCUMENT. It opens no transaction. Each
archive is opened detached, read and closed with ``Close(False)``; the
"copy" control opens a COPY in the probe folder, never the archive, and the
copy is deleted afterwards. The archives' hashes before and after are the
evidence.

Every read is recorded as ``{"state": "value" | "unavailable" | "raised",
"value": ..., "error": ...}``. No bare ``except``.
"""
from __future__ import print_function

import hashlib
import json
import os
import random
import shutil
import sys
import time
import traceback


PROBE_NAME = "change_detection"
PROBE_VERSION = "2026-10-28.1"
SCHEMA = "vop.probe.change_detection.v1"

OPTION_KEYS = ("repo_root", "seed", "sample_size", "control", "worksets",
               "version_guid", "write_guid_maps",
               "expected_title_a", "expected_title_b")
DEFAULT_SEED = 20261022
DEFAULT_SAMPLE_SIZE = 50
# "reopen": A opened detached a SECOND time; two GUIDs for one file means
# a detached open mints its own version. "copy": a byte copy opened NOT
# detached -- refused for a cloud central (run 20261026T084944: the open of
# such a copy never returned). "none": no control.
CONTROLS = ("reopen", "copy", "none")
CLOUD_CENTRAL_PREFIXES = ("autodesk docs://", "bim 360://", "bim360://")
WORKSETS = ("all", "last_viewed")
# The batch's population (vop_interwoven.batch_capture.DEFAULT_VIEW_TYPES);
# the production tuple is used when the checkout imports, this copy only to
# name what the sample drew from if it does not.
FALLBACK_VIEW_TYPES = ("FloorPlan", "CeilingPlan", "AreaPlan", "EngineeringPlan",
                       "Section", "Elevation", "Detail", "DraftingView", "Legend")
# The three getters this probe calls on ChangedElements, by name, with no
# arguments, when reflection shows them.
CHANGED_GETTERS = (("created", "GetCreatedElementIds"),
                   ("modified", "GetModifiedElementIds"),
                   ("deleted", "GetDeletedElementIds"))
# Per-view id lists in the JSON are cut here; the counts are never cut.
VIEW_ID_LIMIT = 200

# The batch's dialog answers (pyrevit BatchCapture script.py _DIALOG_ANSWERS
# and _LEGACY_ORDER): reproduced, since a pyRevit script cannot be imported.
DIALOG_ANSWERS = {
    "Dialog_Revit_DocWarnDialog": (1,),
    "TaskDialog_Elements_Lost_On_Import": (8, 1),
    "TaskDialog_Unresolved_References": (1002,),
}
LEGACY_DIALOG_ORDER = (1002, 1001, 1)


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
    try:
        return rec_value(fn(*args))
    except Exception as ex:
        return rec_raised(ex)


def value_of(record, default=None):
    if isinstance(record, dict) and record.get("state") == "value":
        return record.get("value")
    return default


def exception_record(stage, ex):
    return {"stage": stage, "type": type(ex).__name__, "message": str(ex),
            "traceback": traceback.format_exc()}


# ======================================================================
# PURE: inputs, the sample, the signature check, the per-view signals
# ======================================================================

def parse_view_ids(value):
    """A list of int view ids from a Dynamo list, a comma string or None.
    Dynamo-wrapped views are read by their ``Id``."""
    if value is None or value == "":
        return []
    if isinstance(value, str):
        value = [p for p in value.replace(";", ",").split(",") if p.strip()]
    if not isinstance(value, (list, tuple)):
        value = [value]
    out = []
    for item in value:
        inner = getattr(item, "Id", None)
        out.append(int(inner if inner is not None and not isinstance(item, (int, str))
                       else item))
    return list(dict.fromkeys(out))


def parse_options(value):
    """IN[4] with every default filled in, also when IN[4] is empty (run
    20261026T084811 returned {} there and failed on the first default read)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        value = {}
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        if hasattr(value, "Keys"):
            value = dict((str(k), value[k]) for k in value.Keys)
        else:
            raise ValueError("IN[4] must be a Dictionary or a JSON object, not {0}".format(
                type(value).__name__))
    unknown = sorted(str(k) for k in value if str(k) not in OPTION_KEYS)
    if unknown:
        raise ValueError("IN[4] has unknown key(s) {0}; known: {1}".format(unknown, OPTION_KEYS))
    options = dict((str(k), v) for k, v in value.items())
    control = str(options.get("control") or "reopen").strip().lower()
    if control not in CONTROLS:
        raise ValueError("IN[4] control must be one of {0}, not {1!r}".format(CONTROLS, control))
    worksets = str(options.get("worksets") or "all").strip().lower()
    if worksets not in WORKSETS:
        raise ValueError("IN[4] worksets must be one of {0}, not {1!r}".format(
            WORKSETS, worksets))
    options["control"] = control
    options["worksets"] = worksets
    options["seed"] = int(options["seed"]) if options.get("seed") not in (None, "") \
        else DEFAULT_SEED
    options["sample_size"] = (int(options["sample_size"])
                              if options.get("sample_size") not in (None, "")
                              else DEFAULT_SAMPLE_SIZE)
    if options["sample_size"] <= 0:
        raise ValueError("IN[4] sample_size must be positive")
    options["version_guid"] = _flag(options.get("version_guid"), True)
    options["write_guid_maps"] = _flag(options.get("write_guid_maps"), False)
    return options


def _flag(value, default):
    """A bool option: True/False, or the strings true/false/1/0; else refused."""
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "1", "yes"):
        return True
    if text in ("false", "0", "no"):
        return False
    raise ValueError("IN[4] expected true or false, not {0!r}".format(value))


def sample_view_ids(views, view_types, size, seed):
    """PURE. ``views``: ``[(view_id, view_type_name, is_template), ...]``.
    Up to ``size`` non-template views of ``view_types``, drawn ACROSS types:
    each type's ids shuffled by one ``random.Random(seed)`` (types in sorted
    order), then taken round-robin over the types until ``size``. The same
    population and seed give the same sample. Returns ``(ids, record)``."""
    wanted = set(view_types)
    by_type = {}
    for vid, type_name, is_template in views:
        if is_template or type_name not in wanted:
            continue
        by_type.setdefault(type_name, []).append(int(vid))
    rng = random.Random(seed)
    queues = []
    for type_name in sorted(by_type):
        ids = sorted(by_type[type_name])
        rng.shuffle(ids)
        queues.append((type_name, ids))
    chosen, taken = [], {}
    while len(chosen) < size and any(ids for _t, ids in queues):
        for type_name, ids in queues:
            if ids and len(chosen) < size:
                chosen.append(ids.pop(0))
                taken[type_name] = taken.get(type_name, 0) + 1
    record = {"rule": "seeded round-robin across view types, each type shuffled",
              "seed": seed, "size_requested": size, "size_drawn": len(chosen),
              "population": dict((t, len(v)) for t, v in sorted(by_type.items())),
              "drawn_per_type": taken, "view_types": sorted(wanted)}
    return chosen, record


def method_record(name, is_static, return_type, params):
    """PURE. One reflected method: ``params`` is ``[(type_full_name, name)]``."""
    return {"name": name, "is_static": bool(is_static), "return_type": return_type,
            "parameters": [{"type": t, "name": n} for t, n in params],
            "signature": "{0}{1} {2}({3})".format(
                "static " if is_static else "", return_type, name,
                ", ".join("{0} {1}".format(t, n) for t, n in params))}


def expected_shape(methods, name):
    """PURE. ``(method or None, reason)``: the overload of ``name`` this probe
    calls. GetDocumentVersion: static, one Document parameter, or an instance
    method with none. GetChangedElements: instance, one System.Guid
    parameter. Anything else is not called: the reason says what was found."""
    found = [m for m in methods if m["name"] == name]
    if not found:
        return None, "absent"
    for m in found:
        types = [p["type"] for p in m["parameters"]]
        if name == "GetDocumentVersion":
            if (m["is_static"] and types == ["Autodesk.Revit.DB.Document"]) or \
                    (not m["is_static"] and types == []):
                return m, "ok"
        elif name == "GetChangedElements":
            if not m["is_static"] and types == ["System.Guid"]:
                return m, "ok"
    return None, "present, but no overload of the shape this probe calls: {0}".format(
        [m["signature"] for m in found])


def view_signals(view_id, members_b, members_a, template_id, filter_ids, type_ids,
                 created, modified, deleted, limit=VIEW_ID_LIMIT):
    """PURE. What the changed-element sets say about one view.

    ``members_b`` / ``members_a``: the view's membership in B and A (None
    when unread). ``template_id``: the view's template in B (None or -1 for
    none). ``filter_ids``: the filters applied to the view or its template.
    ``type_ids``: the types B's members use. ``created`` / ``modified`` /
    ``deleted``: the API's sets.

    ``api_says_changed`` is the OR of the signals that come from the API:
    a member created or modified, an A member deleted, the view itself, its
    template, a filter, or a member's type in the changed set.
    ``membership_diff`` (A's membership against B's) is recorded beside it,
    and is NOT part of that OR: it is the probe's own read, not the API's.
    A signal whose input was not read is None, never False."""
    changed = set(created) | set(modified)
    deleted = set(deleted)

    def _hit(ids, against):
        if ids is None:
            return None
        hits = sorted(set(ids) & against)
        return {"count": len(hits), "ids": hits[:limit]}

    signals = {
        "members_created_or_modified": _hit(members_b, changed),
        "a_members_deleted": _hit(members_a, deleted),
        "view_itself": int(view_id) in changed,
        "template": (None if template_id is None else
                     (template_id not in (-1,) and int(template_id) in changed)),
        "filters": _hit(filter_ids, changed),
        "member_types": _hit(type_ids, changed),
    }
    fired = []
    for key, sig in signals.items():
        if isinstance(sig, dict):
            if sig["count"] > 0:
                fired.append(key)
        elif sig:
            fired.append(key)
    unread = sorted(k for k, s in signals.items() if s is None)
    if members_a is not None and members_b is not None:
        a, b = set(members_a), set(members_b)
        membership_diff = {"added": len(b - a), "removed": len(a - b),
                           "added_ids": sorted(b - a)[:limit],
                           "removed_ids": sorted(a - b)[:limit]}
    else:
        membership_diff = None
    return {"signals": signals, "fired": sorted(fired), "unread": unread,
            "api_says_changed": bool(fired),
            # Unchanged is a claim only when every input was read.
            "api_verdict": ("changed" if fired else
                            ("unchanged" if not unread else "unknown")),
            "membership_diff": membership_diff}


def counts_by(labels):
    """PURE. ``{label: count}`` over an iterable of labels, most first."""
    out = {}
    for label in labels:
        out[label] = out.get(label, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], str(kv[0]))))


def cloud_central(info):
    """PURE. The CentralPath of a basic_file_info() record when it names a
    cloud central (Autodesk Docs / BIM 360), else None."""
    central = value_of((value_of(info) or {}).get("CentralPath"))
    if central and str(central).strip().lower().startswith(CLOUD_CENTRAL_PREFIXES):
        return str(central)
    return None


def sample_population(views, type_name):
    """``[(id, type name, is_template)]`` for sample_view_ids; ``type_name``
    returns ``(name, how)`` (resolve_view_type_name)."""
    return [(element_id_int(v.Id), type_name(v)[0], bool(v.IsTemplate)) for v in views]


def resolve_view_type_name(read_name, enum_name):
    """PURE. A ViewType's name from production's reader (``read_name``), or,
    when that is missing or a NUMBER -- what batch_capture._view_type_name
    returned for every view under Dynamo's Python engine in run
    20261026T095313, so select_views kept none -- from ``enum_name(int)``,
    the .NET Enum.GetName lookup. Returns ``(name, how)``."""
    text = None if read_name is None else str(read_name)
    if text and not text.lstrip("-").isdigit():
        return text, "production_reader"
    if text is None:
        return None, "unreadable"
    try:
        name = enum_name(int(text))
    except Exception as ex:  # noqa: BLE001 -- recorded as the reason
        return None, "enum lookup raised {0}: {1}".format(type(ex).__name__, ex)
    return (str(name), "enum_lookup") if name else (None, "enum lookup found no name")


VOLATILE_TOKEN = "~"


def view_fingerprint(view_id, inputs, guids, limit=VIEW_ID_LIMIT, volatile=None):
    """PURE. ``{count, sha1, missing, missing_ids}`` over the view, its
    members, template, filters and member types, each as ``id:VersionGuid``
    (``-`` where ``guids`` has none; ``~`` for an id in ``volatile``, whose
    VersionGuid changes between two opens of one file and so says nothing);
    None when an input was not read."""
    volatile = volatile or ()
    if inputs is None or guids is None or any(
            inputs.get(k) is None for k in ("members", "filter_ids", "type_ids",
                                            "template_id")):
        return None
    ids = set([int(view_id)]) | set(inputs["members"]) | set(inputs["filter_ids"]) \
        | set(inputs["type_ids"])
    if inputs["template_id"] not in (None, -1):
        ids.add(int(inputs["template_id"]))
    missing = sorted(i for i in ids if guids.get(i) is None)
    text = ",".join("{0}:{1}".format(
        i, VOLATILE_TOKEN if i in volatile else (guids.get(i) or "-")) for i in sorted(ids))
    return {"count": len(ids), "sha1": hashlib.sha1(text.encode("ascii")).hexdigest(),
            "missing": len(missing), "missing_ids": missing[:limit]}


def version_guid_signals(view_id, a_inputs, b_inputs, guids_a, guids_b,
                         limit=VIEW_ID_LIMIT, volatile=None):
    """PURE. What VersionGuid says about one view between A and B.

    Signals: members added or removed (membership), members whose
    VersionGuid differs, the view itself, its template (a different template,
    or the same template with another VersionGuid), its filters, its member
    types. ``vg_verdict`` is ``changed`` when any fired, ``unchanged`` when
    none did and every input was read, else ``unknown``. A changed signal and
    unequal fingerprints are the same fact, asserted by the tests.

    ``volatile``: ids whose VersionGuid differed between two detached opens
    of A (volatile_ids). Their VersionGuid is not compared (membership still
    is): the "stable" verdict (probe 2026-10-28.1)."""
    volatile = frozenset(volatile or ())
    fa = view_fingerprint(view_id, a_inputs, guids_a, volatile=volatile)
    fb = view_fingerprint(view_id, b_inputs, guids_b, volatile=volatile)
    if fa is None or fb is None:
        return {"vg_verdict": "unknown", "fired": [], "signals": None,
                "fingerprint_a": fa, "fingerprint_b": fb,
                "reason": "an input or a VersionGuid map was not read"}

    def _guid_changed(ids):
        return sorted(i for i in ids
                      if i not in volatile and guids_a.get(i) != guids_b.get(i))

    ma, mb = set(a_inputs["members"]), set(b_inputs["members"])
    ta, tb = a_inputs["template_id"], b_inputs["template_id"]
    fa_ids, fb_ids = set(a_inputs["filter_ids"]), set(b_inputs["filter_ids"])
    ya, yb = set(a_inputs["type_ids"]), set(b_inputs["type_ids"])
    changed_members = _guid_changed(ma & mb)
    changed_filters = _guid_changed(fa_ids & fb_ids)
    changed_types = _guid_changed(ya & yb)
    signals = {
        "members_added": sorted(mb - ma)[:limit], "members_added_count": len(mb - ma),
        "members_removed": sorted(ma - mb)[:limit], "members_removed_count": len(ma - mb),
        "members_changed": changed_members[:limit],
        "members_changed_count": len(changed_members),
        "view_itself": bool(_guid_changed([int(view_id)])),
        "template": (ta != tb) or (ta not in (None, -1) and bool(_guid_changed([ta]))),
        "filters": (fa_ids != fb_ids) or bool(changed_filters),
        "member_types_set": ya != yb,
        "member_types_changed": changed_types[:limit],
        "member_types_changed_count": len(changed_types),
    }
    fired = sorted(k for k in ("members_added_count", "members_removed_count",
                               "members_changed_count", "member_types_changed_count")
                   if signals[k]) + sorted(k for k in ("view_itself", "template", "filters",
                                                       "member_types_set") if signals[k])
    return {"vg_verdict": "changed" if fired else "unchanged", "fired": fired,
            "signals": signals, "fingerprint_a": fa, "fingerprint_b": fb,
            "fingerprints_equal": fa["sha1"] == fb["sha1"]}


def volatile_ids(a, b):
    """PURE. The ids in both maps whose VersionGuid differs: for two opens of
    ONE file, the elements a detached open re-mints (1,937 of 446,169 in run
    20261028T111442)."""
    if a is None or b is None:
        return None
    return sorted(i for i in set(a) & set(b) if a[i] != b[i])


def compare_guid_maps(a, b):
    """PURE. Two ``{element id: VersionGuid}`` maps: the ids in both with the
    same VersionGuid, with a different one, and in one only."""
    if a is None or b is None:
        return None
    common = set(a) & set(b)
    differ = sum(1 for i in common if a[i] != b[i])
    return {"in_a": len(a), "in_b": len(b), "common": len(common),
            "same": len(common) - differ, "differ": differ,
            "only_a": len(set(a) - common), "only_b": len(set(b) - common),
            "differ_fraction_of_common": (differ / float(len(common))) if common else None}


def file_fingerprint(path, chunk=8 * 1024 * 1024):
    """``{size, mtime, sha256}`` of a file (stdlib)."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            digest.update(block)
    st = os.stat(path)
    return {"size": st.st_size, "mtime": st.st_mtime, "sha256": digest.hexdigest()}


def fingerprints_changed(before, after):
    """PURE. The fields of a ``file_fingerprint`` that differ."""
    return sorted(k for k in ("size", "mtime", "sha256")
                  if (before or {}).get(k) != (after or {}).get(k))


# ======================================================================
# REVIT
# ======================================================================

def element_id_int(value):
    if value is None:
        return None
    for attr in ("Value", "IntegerValue"):
        inner = getattr(value, attr, None)
        if inner is not None:
            return int(inner)
    return None


def _ensure_revit():
    import clr
    clr.AddReference("RevitAPI")
    clr.AddReference("RevitAPIUI")
    clr.AddReference("RevitServices")


def reflect_type(clr_type, names=None):
    """Every public method of a .NET type (or only ``names``), as
    method_record()s. Object's own members are left out."""
    out = []
    for m in clr_type.GetMethods():
        name = str(m.Name)
        if names is not None and name not in names:
            continue
        if names is None and name in ("Equals", "GetHashCode", "GetType", "ToString",
                                      "Finalize", "MemberwiseClone"):
            continue
        params = [(str(p.ParameterType.FullName), str(p.Name)) for p in m.GetParameters()]
        out.append(method_record(name, m.IsStatic, str(m.ReturnType.FullName), params))
    return out


def reflect_properties(clr_type):
    return sorted("{0} {1}".format(p.PropertyType.FullName, p.Name)
                  for p in clr_type.GetProperties())


def reflection(app):
    """Step 1. What Document carries, and the types its calls return."""
    import clr
    from Autodesk.Revit.DB import Document
    doc_type = clr.GetClrType(Document)
    methods = reflect_type(doc_type, ("GetDocumentVersion", "GetChangedElements"))
    rec = {"revit": {"VersionNumber": read(lambda: str(app.VersionNumber)),
                     "VersionBuild": read(lambda: str(app.VersionBuild)),
                     "SubVersionNumber": read(lambda: str(app.SubVersionNumber))},
           "api_assembly": read(lambda: str(doc_type.Assembly.FullName)),
           "document_methods": methods, "return_types": {}}
    for name in ("GetDocumentVersion", "GetChangedElements"):
        chosen, why = expected_shape(methods, name)
        rec[name] = {"present": any(m["name"] == name for m in methods),
                     "callable_shape": chosen is not None, "reason": why,
                     "chosen_signature": chosen["signature"] if chosen else None}
    for m in methods:
        ret = m["return_type"]
        if ret in rec["return_types"]:
            continue
        t = doc_type.Assembly.GetType(ret)
        rec["return_types"][ret] = (
            {"methods": [x["signature"] for x in reflect_type(t)],
             "properties": reflect_properties(t)} if t is not None
            else {"error": "type not found in the API assembly"})
    rec["q1_can_run"] = all(rec[n]["callable_shape"]
                            for n in ("GetDocumentVersion", "GetChangedElements"))
    from Autodesk.Revit.DB import Element
    props = [p for p in clr.GetClrType(Element).GetProperties() if "Version" in str(p.Name)]
    rec["element_version_properties"] = sorted(
        "{0} {1}".format(p.PropertyType.FullName, p.Name) for p in props)
    rec["element_version_guid"] = any(str(p.Name) == "VersionGuid" for p in props)
    return rec


def document_version(doc):
    """``{guid, saves}`` by the shape reflection confirmed (static first)."""
    from Autodesk.Revit.DB import Document
    try:
        ver = Document.GetDocumentVersion(doc)
        how = "static Document.GetDocumentVersion(doc)"
    except TypeError:
        ver = doc.GetDocumentVersion()
        how = "doc.GetDocumentVersion()"
    return {"guid": str(ver.VersionGUID), "saves": int(ver.NumberOfSaves), "call": how}


def basic_file_info(path):
    """BasicFileInfo.Extract(path): read without opening the model."""
    from Autodesk.Revit.DB import BasicFileInfo
    info = BasicFileInfo.Extract(path)
    out = {}
    for name in ("IsWorkshared", "IsCentral", "IsLocal", "IsCreatedLocal", "IsSavedInCurrentVersion",
                 "CentralPath", "Format", "SavedInVersion", "Username", "AllLocalChangesSavedToCentral"):
        out[name] = read(lambda n=name: _plain(getattr(info, n)))
    return out


def _dotnet_type(ex):
    """The .NET exception's full type name, when the exception is one."""
    get_type = getattr(ex, "GetType", None)
    if not callable(get_type):
        return None
    try:
        return str(get_type().FullName)
    except Exception as inner:  # noqa: BLE001 -- the name is diagnostic only
        return "unreadable: {0}".format(inner)


def _plain(v):
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    return str(v)


class OpenEvents(object):
    """The batch's failure and dialog handlers, subscribed only while a
    document opens; every event is logged."""

    def __init__(self, app, uiapp):
        self.app, self.uiapp, self.log = app, uiapp, []
        # One bound-method object per handler, used for both += and -=: a
        # fresh ``self.on_failures`` at -= need not match the delegate added.
        self._failures, self._dialog = self.on_failures, self.on_dialog

    def on_failures(self, sender, args):
        from Autodesk.Revit.DB import FailureProcessingResult, FailureSeverity
        try:
            accessor = args.GetFailuresAccessor()
            messages = list(accessor.GetFailureMessages())
            warnings = [m for m in messages if m.GetSeverity() == FailureSeverity.Warning]
            if messages and len(warnings) == len(messages):
                accessor.DeleteAllWarnings()
                args.SetProcessingResult(FailureProcessingResult.Continue)
                self.log.append("failures_processing: {0} warnings deleted".format(len(warnings)))
            elif messages:
                self.log.append("failures_processing: {0} messages, {1} warnings, left to "
                                "Revit".format(len(messages), len(warnings)))
        except Exception as ex:  # noqa: BLE001 -- logged; Revit's own handling continues
            self.log.append("failures_processing error {0}: {1}".format(type(ex).__name__, ex))

    def on_dialog(self, sender, args):
        dialog_id = str(getattr(args, "DialogId", "") or "")
        attempts, answered = [], None
        for code in DIALOG_ANSWERS.get(dialog_id, LEGACY_DIALOG_ORDER):
            try:
                ok = args.OverrideResult(code)
            except Exception as ex:  # noqa: BLE001 -- logged; the next code is tried
                attempts.append("{0}:{1}".format(code, type(ex).__name__))
                continue
            attempts.append("{0}:{1}".format(code, ok))
            if ok:
                answered = code
                break
        self.log.append("dialog {0} answered {1} (tried {2})".format(
            dialog_id, answered, ", ".join(attempts)))

    def __enter__(self):
        self.app.FailuresProcessing += self._failures
        if self.uiapp is not None:
            self.uiapp.DialogBoxShowing += self._dialog
        return self

    def __exit__(self, *exc):
        self.app.FailuresProcessing -= self._failures
        if self.uiapp is not None:
            self.uiapp.DialogBoxShowing -= self._dialog
        return False


def open_options(detached, worksets):
    """The batch's ``_open_options`` when ``detached``; the control's
    otherwise (DoNotDetach, same worksets, no audit)."""
    from Autodesk.Revit.DB import (DetachFromCentralOption, OpenOptions,
                                   WorksetConfiguration, WorksetConfigurationOption)
    opts = OpenOptions()
    opts.DetachFromCentralOption = (DetachFromCentralOption.DetachAndPreserveWorksets
                                    if detached else DetachFromCentralOption.DoNotDetach)
    opts.Audit = False
    option = (WorksetConfigurationOption.OpenLastViewed if worksets == "last_viewed"
              else WorksetConfigurationOption.OpenAllWorksets)
    opts.SetOpenWorksetsConfiguration(WorksetConfiguration(option))
    return opts, {"detach": "DetachAndPreserveWorksets" if detached else "DoNotDetach",
                  "worksets": worksets, "audit": False}


def open_document(ctx, path, detached):
    from Autodesk.Revit.DB import ModelPathUtils
    opts, how = open_options(detached, ctx.options["worksets"])
    events = OpenEvents(ctx.app, ctx.uiapp)
    t0 = time.time()
    with events:
        doc = ctx.app.OpenDocumentFile(ModelPathUtils.ConvertUserVisiblePathToModelPath(path),
                                       opts)
    if doc is None:
        raise RuntimeError("OpenDocumentFile returned None for {0}".format(path))
    how.update({"open_ms": round((time.time() - t0) * 1000.0, 1), "events": events.log,
                "title": read(lambda: str(doc.Title)),
                "is_detached": read(lambda: bool(doc.IsDetached)),
                "is_workshared": read(lambda: bool(doc.IsWorkshared)),
                "is_modified_after_open": read(lambda: bool(doc.IsModified))})
    return doc, how


def close_document(doc, rec):
    rec["is_modified_before_close"] = read(lambda: bool(doc.IsModified))
    t0 = time.time()
    rec["close"] = read(lambda: bool(doc.Close(False)))
    rec["close_ms"] = round((time.time() - t0) * 1000.0, 1)


def read_version_guids(doc):
    """``({element id: str(VersionGuid)}, stats)`` over every element and every
    element type of ``doc``, read once. An element whose read raises is
    counted, the first error kept."""
    from Autodesk.Revit.DB import FilteredElementCollector
    out, errors, first_error = {}, 0, None
    t0 = time.time()
    for collector in (FilteredElementCollector(doc).WhereElementIsNotElementType(),
                      FilteredElementCollector(doc).WhereElementIsElementType()):
        for e in collector:
            try:
                out[element_id_int(e.Id)] = str(e.VersionGuid)
            except Exception as ex:  # noqa: BLE001 -- counted; first one kept
                errors += 1
                if first_error is None:
                    first_error = "{0}: {1}".format(type(ex).__name__, ex)
    return out, {"elements": len(out), "errors": errors, "first_error": first_error,
                 "ms": round((time.time() - t0) * 1000.0, 1)}


def view_type_name(ctx, view):
    """resolve_view_type_name over production's reader and Enum.GetName."""
    import clr
    import System
    from Autodesk.Revit.DB import ViewType
    enum_t = clr.GetClrType(ViewType)
    read_name = ctx.batch_capture._view_type_name(view)
    return resolve_view_type_name(read_name,
                                  lambda number: System.Enum.GetName(enum_t, number))


def view_by_id(doc, vid):
    from Autodesk.Revit.DB import ElementId, View
    elem = doc.GetElement(ElementId(int(vid)))
    return elem if isinstance(elem, View) else None


def membership(ctx, doc, vid):
    """The production read (stage_a_registered_capture.view_membership_ids)."""
    view = view_by_id(doc, vid)
    if view is None:
        return rec_unavailable("no view {0} in this document".format(vid))
    t0 = time.time()
    rec = read(ctx.view_membership_ids, doc, view)
    rec["ms"] = round((time.time() - t0) * 1000.0, 1)
    return rec


def changed_elements(ctx, doc, guid_text):
    """One GetChangedElements call: the record, and the id sets (or None)."""
    import System
    from Autodesk.Revit.DB import ElementId  # noqa: F401 -- loads the API types
    rec = {"guid": guid_text}
    try:
        guid = System.Guid(guid_text)
    except Exception as ex:
        rec.update(state="raised", error="Guid parse {0}: {1}".format(type(ex).__name__, ex))
        return rec, None
    t0 = time.time()
    try:
        result = doc.GetChangedElements(guid)
        rec["call_ms"] = round((time.time() - t0) * 1000.0, 3)
    except Exception as ex:
        rec["call_ms"] = round((time.time() - t0) * 1000.0, 3)
        rec.update(state="raised", exception_type=type(ex).__name__,
                   exception_message=str(ex), dotnet_type=_dotnet_type(ex))
        return rec, None
    rec["state"] = "value"
    rec["result_type"] = _plain(result.GetType().FullName if result is not None else None)
    sets = {}
    t1 = time.time()
    for label, getter in CHANGED_GETTERS:
        fn = getattr(result, getter, None)
        if fn is None:
            rec[label] = rec_unavailable("ChangedElements has no " + getter)
            continue
        got = read(lambda f=fn: sorted(element_id_int(i) for i in f()))
        if got["state"] == "value":
            sets[label] = got["value"]
            rec[label] = {"state": "value", "count": len(got["value"])}
        else:
            rec[label] = got
    rec["getters_ms"] = round((time.time() - t1) * 1000.0, 3)
    rec["by_change_type"] = modified_by_change_type(result)
    return rec, (sets if len(sets) == len(CHANGED_GETTERS) else None)


def modified_by_change_type(result):
    """When reflection shows a one-parameter enum overload of
    GetModifiedElementIds, the count per enum value; else unavailable."""
    import System
    t = result.GetType()
    for m in t.GetMethods():
        if str(m.Name) != "GetModifiedElementIds":
            continue
        params = list(m.GetParameters())
        if len(params) == 1 and params[0].ParameterType.IsEnum:
            enum_t = params[0].ParameterType
            out = {"parameter_type": str(enum_t.FullName), "counts": {}}
            for value in System.Enum.GetValues(enum_t):
                name = str(System.Enum.GetName(enum_t, value))
                got = read(lambda v=value: len(list(m.Invoke(result, [v]))))
                out["counts"][name] = got["value"] if got["state"] == "value" else got
            return rec_value(out)
    return rec_unavailable("no enum overload of GetModifiedElementIds")


def category_counts(doc, ids, limit=None):
    """``{"<category> | <class>": count}`` of ids resolvable in ``doc``."""
    from Autodesk.Revit.DB import ElementId
    labels = []
    for i in ids[:limit] if limit else ids:
        e = doc.GetElement(ElementId(int(i)))
        if e is None:
            labels.append("(not in document)")
            continue
        cat = getattr(e, "Category", None)
        labels.append("{0} | {1}".format(getattr(cat, "Name", None) if cat else None,
                                         type(e).__name__))
    return counts_by(labels)


def view_filter_ids(view):
    """The filters applied to ``view``. A view that allows no graphic
    overrides (a legend, a schedule) can carry no filter: [] -- read from
    AreGraphicsOverridesAllowed(), not assumed from a GetFilters() that
    raised, which stays a raised read."""
    if not bool(view.AreGraphicsOverridesAllowed()):
        return []
    return sorted(element_id_int(i) for i in view.GetFilters())


def view_inputs(ctx, doc, vid):
    """B's reads for one view: membership, template, filters, member types."""
    out = {"view_id": vid}
    view = view_by_id(doc, vid)
    if view is None:
        out["present_in_b"] = False
        return out, None
    out["present_in_b"] = True
    out["name"] = read(lambda: str(view.Name))
    out["view_type"] = read(lambda: view_type_name(ctx, view)[0])
    mem = membership(ctx, doc, vid)
    out["membership_count"] = len(mem["value"]) if mem["state"] == "value" else None
    out["membership_ms"] = mem.get("ms")
    out["membership_error"] = mem["error"]
    template = read(lambda: element_id_int(view.ViewTemplateId))
    out["template_id"] = template
    filters = read(view_filter_ids, view)
    tmpl_filters = rec_unavailable("no template")
    tid = value_of(template)
    if tid not in (None, -1):
        tview = view_by_id(doc, tid)
        if tview is not None:
            tmpl_filters = read(view_filter_ids, tview)
    out["filters"] = filters
    out["template_filters"] = tmpl_filters
    t0 = time.time()
    type_ids = None
    if mem["state"] == "value":
        from Autodesk.Revit.DB import ElementId
        got = set()
        for i in mem["value"]:
            e = doc.GetElement(ElementId(int(i)))
            if e is None:
                continue
            tid_ = element_id_int(e.GetTypeId())
            if tid_ not in (None, -1):
                got.add(tid_)
        type_ids = sorted(got)
    out["member_type_count"] = None if type_ids is None else len(type_ids)
    out["member_types_ms"] = round((time.time() - t0) * 1000.0, 1)
    filter_ids = None
    if filters["state"] == "value":
        filter_ids = list(filters["value"]) + list(value_of(tmpl_filters) or [])
    return out, {"members": value_of(mem), "template_id": tid if template["state"] == "value"
                 else None, "filter_ids": filter_ids, "type_ids": type_ids}


class Context(object):
    def __init__(self, app, uiapp, probe_dir, options):
        self.app, self.uiapp, self.probe_dir, self.options = app, uiapp, probe_dir, options
        self.exceptions = []
        self.view_membership_ids = None
        self.view_types = FALLBACK_VIEW_TYPES
        self.batch_capture = None


def import_production(ctx, report):
    """view_membership_ids and DEFAULT_VIEW_TYPES from the checkout, fresh."""
    import importlib
    seeds = [ctx.options.get("repo_root"), os.environ.get("REVIT_SSM_EXPORTER_ROOT"),
             os.path.join(os.path.expanduser("~"), "Documents", "Revit_SSM_Exporter")]
    root = next((s for s in seeds if s and os.path.isfile(
        os.path.join(s, "vop_interwoven", "__init__.py"))), None)
    if root is None:
        raise RuntimeError("vop_interwoven not found; set IN[4] repo_root. Tried: {0}".format(
            [s for s in seeds if s]))
    for name in [n for n in list(sys.modules)
                 if n == "vop_interwoven" or n.startswith("vop_interwoven.")]:
        del sys.modules[name]
    if root in sys.path:
        sys.path.remove(root)
    sys.path.insert(0, root)
    importlib.invalidate_caches()
    rc = importlib.import_module("vop_interwoven.stage_a_registered_capture")
    bc = importlib.import_module("vop_interwoven.batch_capture")
    ctx.view_membership_ids = rc.view_membership_ids
    ctx.view_types = tuple(bc.DEFAULT_VIEW_TYPES)
    ctx.batch_capture = bc
    report["production"] = {"root": root, "loaded_from": [rc.__file__, bc.__file__],
                            "view_types": list(ctx.view_types)}


def _copy_for_control(ctx, path, label):
    folder = os.path.join(ctx.probe_dir, "_control_copy_" + label)
    os.makedirs(folder)
    dest = os.path.join(folder, os.path.basename(path))
    t0 = time.time()
    shutil.copy2(path, dest)
    return dest, folder, round((time.time() - t0) * 1000.0, 1)


def _remove_copy(folder, rec):
    try:
        shutil.rmtree(folder)
        rec["copy_removed"] = True
    except OSError as ex:
        rec["copy_removed"] = "{0}: {1}".format(type(ex).__name__, ex)


def _write_json(path, report):
    with open(path, "w") as fh:
        json.dump(report, fh, indent=2, sort_keys=True, default=str)


def run_probe(inputs, app=None, uiapp=None):
    _ensure_revit()
    inputs = list(inputs)

    def _at(i):
        return inputs[i] if len(inputs) > i else None
    if not _at(0):
        raise ValueError("IN[0] output directory is required")
    path_a, path_b = _at(1), _at(2)
    if not path_a or not path_b:
        raise ValueError("IN[1] (archive A) and IN[2] (archive B) are required")
    path_a, path_b = os.path.abspath(str(path_a)), os.path.abspath(str(path_b))
    for p in (path_a, path_b):
        if not os.path.isfile(p):
            raise IOError("not on disk (not synced by Desktop Connector?): {0}".format(p))
    options = parse_options(_at(4))
    given_ids = parse_view_ids(_at(3))
    if app is None:
        from RevitServices.Persistence import DocumentManager
        uiapp = DocumentManager.Instance.CurrentUIApplication
        app = uiapp.Application
    stamp = time.strftime("%Y%m%dT%H%M%S")
    probe_dir = os.path.join(os.path.abspath(str(_at(0))), "change_detection_" + stamp)
    os.makedirs(probe_dir)
    json_path = os.path.join(probe_dir, "probe_change_detection_{0}.json".format(stamp))
    progress_path = os.path.join(probe_dir, "progress_{0}.json".format(stamp))
    ctx = Context(app, uiapp, probe_dir, options)
    report = {"schema": SCHEMA, "probe": {"name": PROBE_NAME, "version": PROBE_VERSION},
              "started_at": stamp, "finalized": False,
              "inputs": {"archive_a": path_a, "archive_b": path_b, "view_ids": given_ids,
                         "options": options},
              "opens": {}, "calls": {}, "views": [], "exceptions": ctx.exceptions}

    def _progress(phase):
        report["phase"] = phase
        _write_json(progress_path, report)

    report["reflection"] = reflection(app)
    api_ok = report["reflection"]["q1_can_run"]
    vg_on = options["version_guid"] and report["reflection"]["element_version_guid"]
    report["version_guid_run"] = vg_on
    if not api_ok and not vg_on:
        report["stopped"] = "reflection: {0}; Element.VersionGuid present: {1}".format(dict(
            (n, report["reflection"][n]["reason"])
            for n in ("GetDocumentVersion", "GetChangedElements")),
            report["reflection"]["element_version_guid"])
        report["finalized"] = True
        _write_json(json_path, report)
        return json_path
    import_production(ctx, report)
    report["archives_before"] = {"a": file_fingerprint(path_a), "b": file_fingerprint(path_b)}
    report["basic_file_info"] = {"a": read(basic_file_info, path_a),
                                 "b": read(basic_file_info, path_b)}
    _progress("fingerprinted")

    versions = {}
    a_members, a_inputs = {}, {}
    guid_maps = {}
    view_ids = list(given_ids)

    def _read_guids(doc, label, rec):
        """Every element's VersionGuid in this open (Q1b), and its file."""
        if not vg_on:
            return None
        got = read(read_version_guids, doc)
        if got["state"] != "value":
            rec["version_guids"] = got
            return None
        guids, stats = got["value"]
        rec["version_guids"] = stats
        if options["write_guid_maps"]:
            name = "version_guids_{0}.json".format(label)
            _write_json(os.path.join(probe_dir, name),
                        dict((str(k), v) for k, v in guids.items()))
            rec["version_guids"]["file"] = name
        return guids

    # --- A, detached: version, the sample, A's membership ------------------
    rec = report["opens"]["a_detached"] = {"path": path_a}
    doc = None
    try:
        doc, how = open_document(ctx, path_a, detached=True)
        rec.update(how)
        rec["version"] = read(document_version, doc)
        versions["a_detached"] = value_of(rec["version"])
        if not view_ids:
            # The batch's selection rule (non-template, DEFAULT_VIEW_TYPES),
            # with the type name read through resolve_view_type_name: under
            # Dynamo production's reader returned numbers in run
            # 20261026T095313 and select_views kept none (recorded below).
            bc = ctx.batch_capture
            views = bc.collect_views(doc)
            _kept, selection = bc.select_views(views, ctx.view_types)
            how = counts_by(view_type_name(ctx, v)[1] for v in views)
            view_ids, report["sample"] = sample_view_ids(
                sample_population(views, lambda v: view_type_name(ctx, v)),
                ctx.view_types, options["sample_size"], options["seed"])
            report["sample"]["production_select_views"] = selection
            report["sample"]["type_name_read_by"] = how
        else:
            report["sample"] = {"rule": "IN[3] view ids", "size_drawn": len(view_ids)}
        guid_maps["a"] = _read_guids(doc, "a_detached", rec)
        for vid in view_ids:
            row, inputs_ = view_inputs(ctx, doc, vid)
            a_inputs[vid] = inputs_
            a_members[vid] = None if inputs_ is None else inputs_["members"]
            rec.setdefault("membership", {})[str(vid)] = {
                "present": row.get("present_in_b"), "error": row.get("membership_error"),
                "ms": row.get("membership_ms"), "count": row.get("membership_count")}
    except Exception as ex:
        rec["error"] = exception_record("a_detached", ex)
        ctx.exceptions.append(rec["error"])
    finally:
        if doc is not None:
            close_document(doc, rec)
    _progress("a_detached")

    # --- A, detached AGAIN (control "reopen") ---------------------------------
    if options["control"] == "reopen":
        rec = report["opens"]["a_detached_reopen"] = {"path": path_a}
        doc = None
        try:
            doc, how = open_document(ctx, path_a, detached=True)
            rec.update(how)
            rec["version"] = read(document_version, doc)
            versions["a_detached_reopen"] = value_of(rec["version"])
            again = _read_guids(doc, "a_detached_reopen", rec)
            # Q1b (a): one file, two detached opens. differ > 0 means a
            # detached open re-mints VersionGuids and the signal is unusable.
            report["reopen_version_guids"] = compare_guid_maps(guid_maps.get("a"), again)
            vol = volatile_ids(guid_maps.get("a"), again)
            if vol is not None:
                guid_maps["volatile"] = set(vol)
                report["reopen_version_guids"]["volatile_ids"] = vol[:VIEW_ID_LIMIT]
                report["reopen_version_guids"]["volatile_by_category"] = read(
                    category_counts, doc, vol)
            del again
        except Exception as ex:
            rec["error"] = exception_record("a_detached_reopen", ex)
            ctx.exceptions.append(rec["error"])
        finally:
            if doc is not None:
                close_document(doc, rec)
        first = (versions.get("a_detached") or {}).get("guid")
        again = (versions.get("a_detached_reopen") or {}).get("guid")
        report["reopen_same_guid"] = (None if not (first and again) else first == again)
        _progress("a_detached_reopen")

    # --- A, not detached, from a copy (control "copy") ----------------------
    clouds = dict((k, cloud_central(report["basic_file_info"][k])) for k in ("a", "b"))
    if options["control"] == "copy" and any(clouds.values()):
        report["opens"]["copy_control_refused"] = {
            "reason": "a cloud central cannot be opened attached from a local copy; the "
                      "open of one never returned (run 20261026T084944)",
            "central_paths": clouds}
    if options["control"] == "copy" and not any(clouds.values()):
        rec = report["opens"]["a_copy_not_detached"] = {"source": path_a}
        doc, folder = None, None
        try:
            copy, folder, rec["copy_ms"] = _copy_for_control(ctx, path_a, "a")
            rec["path"] = copy
            doc, how = open_document(ctx, copy, detached=False)
            rec.update(how)
            rec["version"] = read(document_version, doc)
            versions["a_copy_not_detached"] = value_of(rec["version"])
        except Exception as ex:
            rec["error"] = exception_record("a_copy_not_detached", ex)
            ctx.exceptions.append(rec["error"])
        finally:
            if doc is not None:
                close_document(doc, rec)
            if folder is not None:
                _remove_copy(folder, rec)
        _progress("a_copy_not_detached")

    import uuid
    random_guid = str(uuid.UUID(int=random.Random(options["seed"]).getrandbits(128)))

    def _ask_b(label, path, detached, per_view):
        rec = report["opens"][label] = {"path": path}
        doc, folder = None, None
        try:
            if not detached:
                rec["source"] = path
                path, folder, rec["copy_ms"] = _copy_for_control(ctx, path, "b")
                rec["path"] = path
            doc, how = open_document(ctx, path, detached=detached)
            rec.update(how)
            rec["version"] = read(document_version, doc)
            own = value_of(rec["version"])
            versions[label] = own
            guids_b = _read_guids(doc, label, rec) if detached else None
            if guids_b is not None:
                report["version_guid_a_vs_b"] = compare_guid_maps(guid_maps.get("a"), guids_b)
            asks = [("a_detached", (versions.get("a_detached") or {}).get("guid")),
                    ("a_detached_reopen",
                     (versions.get("a_detached_reopen") or {}).get("guid")),
                    ("a_copy_not_detached",
                     (versions.get("a_copy_not_detached") or {}).get("guid")),
                    ("own_guid_positive_control", (own or {}).get("guid")),
                    ("random_guid_negative_control", random_guid)]
            primary = None
            if not api_ok:
                asks = []
                report["calls"]["skipped"] = rec_unavailable(
                    "reflection did not confirm GetDocumentVersion / GetChangedElements")
            for against, guid in asks:
                key = "{0}__vs__{1}".format(label, against)
                if not guid:
                    report["calls"][key] = rec_unavailable("no GUID for " + against)
                    continue
                call, sets = changed_elements(ctx, doc, guid)
                report["calls"][key] = call
                if sets is not None:
                    ids_path = os.path.join(probe_dir, "changed_ids_{0}.json".format(key))
                    _write_json(ids_path, dict(sets, guid=guid, document=label))
                    call["ids_file"] = os.path.basename(ids_path)
                    union = sorted(set(sets["created"]) | set(sets["modified"]))
                    call["created_or_modified_by_category"] = read(
                        category_counts, doc, union)
                    if primary is None and against in ("a_detached", "a_detached_reopen",
                                                       "a_copy_not_detached"):
                        primary = (key, sets)
            if per_view and (primary is not None or guids_b is not None):
                report["per_view_against"] = primary[0] if primary is not None else None
                sets = primary[1] if primary is not None else None
                for vid in view_ids:
                    t0 = time.time()
                    row, inputs_ = view_inputs(ctx, doc, vid)
                    if inputs_ is not None and sets is not None:
                        t1 = time.time()
                        row.update(view_signals(
                            vid, inputs_["members"], a_members.get(vid),
                            inputs_["template_id"], inputs_["filter_ids"],
                            inputs_["type_ids"], sets["created"], sets["modified"],
                            sets["deleted"]))
                        row["intersection_ms"] = round((time.time() - t1) * 1000.0, 3)
                    if inputs_ is not None and guids_b is not None:
                        t1 = time.time()
                        vg = version_guid_signals(vid, a_inputs.get(vid), inputs_,
                                                  guid_maps.get("a"), guids_b)
                        row["version_guid"] = vg
                        row["vg_verdict"] = vg["vg_verdict"]
                        if guid_maps.get("volatile") is not None:
                            stable = version_guid_signals(
                                vid, a_inputs.get(vid), inputs_, guid_maps.get("a"), guids_b,
                                volatile=guid_maps["volatile"])
                            row["version_guid_stable"] = stable
                            row["vg_verdict_stable"] = stable["vg_verdict"]
                        row["vg_ms"] = round((time.time() - t1) * 1000.0, 3)
                    row["view_total_ms"] = round((time.time() - t0) * 1000.0, 1)
                    report["views"].append(row)
            return primary is not None or guids_b is not None
        except Exception as ex:
            rec["error"] = exception_record(label, ex)
            ctx.exceptions.append(rec["error"])
            return False
        finally:
            if doc is not None:
                close_document(doc, rec)
            if folder is not None:
                _remove_copy(folder, rec)
            _progress(label)

    per_view_done = _ask_b("b_detached", path_b, True, per_view=True)
    if options["control"] == "copy" and not any(clouds.values()):
        # Per view here only when the detached B answered nothing.
        _ask_b("b_copy_not_detached", path_b, False, per_view=not per_view_done)

    report["versions"] = versions
    report["archives_after"] = {"a": file_fingerprint(path_a), "b": file_fingerprint(path_b)}
    report["archives_changed"] = dict(
        (k, fingerprints_changed(report["archives_before"][k], report["archives_after"][k]))
        for k in ("a", "b"))
    report["ARCHIVES_SUMMARY"] = (
        "ARCHIVES UNCHANGED (size, mtime, sha256)" if not any(report["archives_changed"].values())
        else "ARCHIVE CHANGED -- see archives_changed")
    report["finished_at"] = time.strftime("%Y%m%dT%H%M%S")
    report["finalized"] = True
    # Written LAST, once every field above is final (CLAUDE.md, defect class 4).
    _write_json(json_path, report)
    return json_path


def dynamo_main(inputs):
    return run_probe(list(inputs))


if "IN" in globals():
    try:
        OUT = dynamo_main(IN)  # noqa: F821
    except Exception as _fatal:
        OUT = {"json_path": None, "fatal": exception_record("dynamo_entrypoint", _fatal)}
