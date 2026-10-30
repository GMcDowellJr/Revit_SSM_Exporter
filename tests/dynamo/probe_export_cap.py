"""Export cost and grid output against the pixel cap (Q2), Revit 2025 / Dynamo 3.x.

Q2: on capped views, does export time (and the downstream size of the image)
scale with pixel count, and what happens to the grid output as the cap is
lowered?

Each job is ONE ordinary capture run of ONE view by production's
``capture_run.run_capture`` (the thinrunner's loop and the batch's config),
with ``Config.color_id_buffer_cap_axis_px`` set to the job's cap -- the
test-only override of the per-axis sizing cap (resolution_contract.cap_axes).
Nothing else differs from a shipped capture: both passes, the ticks, the
registration, inside the registered capture's own TransactionGroup, which is
rolled back and read back as on every capture.

Jobs: every view x every cap x every repeat. Within a repeat each view's caps
run back to back in an order shuffled per view by one seeded
``random.Random`` (build_plan), so a cold first export is not always the
largest; every job records its position, so the analyzer can measure the
warm-up instead of assuming it away.

Dynamo inputs:
    IN[0] = output root (required). probe_export_cap_<stamp>/ is created in it;
            each job's run lands in jobs/v<view>_c<cap>_r<repeat>/.
    IN[1] = capped view ids, a list (required; e.g. LEVEL 1 = 10481315 and 4
            others)
    IN[2] = uncapped control view ids, a list (2 suggested)
    IN[3] = caps, a list of long-axis px          (default [10000, 7000, 5000, 3500])
    IN[4] = repeats per (view, cap)                (default 3)
    IN[5] = options: Dictionary or JSON, keys OPTION_KEYS (see PROBE_EXPORT_CAP.md)

Output: OUT = path of probe_export_cap_<stamp>.json. Offline:
    python tools/analyze_export_cap_probe.py <that folder>
registers each job's annotation capture, grids it with tools/stage_a_grid.py
unchanged, and reports export ms against megapixels and the grid's change
against the 10000 px result.

THE PROBE LEAVES THE DOCUMENT UNCHANGED. It writes nothing itself: every
write is the registered capture's, inside its rolled-back TransactionGroup,
whose ``capture_integrity`` and restore faults are summarised per job
(``restore_faults``). The probe also counts the document's elements and
element types, and reads ``IsModified``, before the first job and after the
last (``document_check``), and it never saves.
"""
from __future__ import print_function

import hashlib
import json
import os
import random
import sys
import time
import traceback

PROBE_NAME = "export_cap"
PROBE_VERSION = "2026-10-24.1"
SCHEMA = "vop.probe.export_cap.v1"

DEFAULT_CAPS = (10000, 7000, 5000, 3500)
DEFAULT_REPEATS = 3
DEFAULT_SEED = 20261022
OPTION_KEYS = ("repo_root", "seed", "expected_doc_title", "keep_images")
KEEP_IMAGES = ("first_repeat", "all")
# The registered capture's restore faults (as the lattice-overrun probe
# names them): any of these on a job means the document may not be as it was.
RESTORE_FAULTS = ("view_state_not_restored", "view_membership_changed",
                  "registration_marks_left_in_project", "element_overrides_left_behind",
                  "detail_lines_left_hidden", "rollback_failed", "rollback_raised")
REQUIRED_PRODUCTION = {
    "vop_interwoven.capture_run": ("run_capture",),
    "vop_interwoven.config": ("Config",),
    "vop_interwoven.resolution_contract": ("MAX_STAGE_A_AXIS_PX", "cap_axes"),
}


def exception_record(stage, ex):
    return {"stage": stage, "type": type(ex).__name__, "message": str(ex),
            "traceback": traceback.format_exc()}


# ======================================================================
# PURE
# ======================================================================

def parse_ids(value, what, required=False):
    if value is None or value == "" or value == []:
        if required:
            raise ValueError("{0} is required".format(what))
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


def parse_caps(value, max_axis_px):
    caps = parse_ids(value, "IN[3]") or list(DEFAULT_CAPS)
    bad = [c for c in caps if c <= 0 or c > max_axis_px]
    if bad:
        # Above the ceiling the post-export check rejects every export
        # (config.py): that is a different experiment, refused here.
        raise ValueError("caps must be in 1..{0} px; got {1}".format(max_axis_px, bad))
    return sorted(set(caps), reverse=True)


def parse_options(value):
    if value is None or (isinstance(value, str) and not value.strip()):
        return {}
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        if hasattr(value, "Keys"):
            value = dict((str(k), value[k]) for k in value.Keys)
        else:
            raise ValueError("IN[5] must be a Dictionary or a JSON object")
    unknown = sorted(str(k) for k in value if str(k) not in OPTION_KEYS)
    if unknown:
        raise ValueError("IN[5] has unknown key(s) {0}; known: {1}".format(unknown, OPTION_KEYS))
    return dict((str(k), v) for k, v in value.items())


def build_plan(view_ids, caps, repeats, seed):
    """PURE. The jobs in run order: for each repeat, for each view, that
    view's caps in an order shuffled by one ``random.Random(seed)``. Every
    job carries its global index and its position within the view's block."""
    rng = random.Random(seed)
    jobs = []
    for repeat in range(1, int(repeats) + 1):
        for vid in view_ids:
            order = list(caps)
            rng.shuffle(order)
            for pos, cap in enumerate(order):
                jobs.append({"index": len(jobs), "repeat": repeat, "view_id": int(vid),
                             "cap": int(cap), "position_in_view_block": pos,
                             "label": "v{0}_c{1}_r{2}".format(vid, cap, repeat)})
    return jobs


def _first(d, *keys):
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None:
            return d.get(k)
    return None


def summarize_job(model_sidecar, anno_sidecar, outcome=None):
    """PURE. One job's facts from its two sidecars (dicts or None) and its
    run_meta outcome: the achieved size, the attempts, the export times the
    registered capture recorded (``registration_marks.timings_ms``), and the
    faults that say whether the document was restored."""
    model = model_sidecar or {}
    anno = anno_sidecar or {}
    frame = model.get("frame") or {}
    reg = model.get("registration_marks") or anno.get("registration_marks") or {}
    timings = reg.get("timings_ms") or {}
    attempts = frame.get("dim_check_attempts") or []
    integrity = model.get("capture_integrity") or {}
    faults = sorted(set(str(f.get("fault")) for f in (reg.get("faults") or [])
                        if isinstance(f, dict)))
    w, h = frame.get("actual_w"), frame.get("actual_h")
    return {
        "capture_status": (outcome or {}).get("capture_status"),
        "capture_failure_reason": (outcome or {}).get("capture_failure_reason"),
        "actual_w": w, "actual_h": h,
        "megapixels": (None if w is None or h is None else round(w * h / 1.0e6, 6)),
        "max_axis_px": frame.get("max_axis_px"), "cap_applied": frame.get("cap_applied"),
        "pre_cap_px": frame.get("pre_cap_px"), "pixel_size": frame.get("pixel_size"),
        "achieved_fpp_ft": frame.get("achieved_fpp_ft"),
        "dim_check": frame.get("dim_check"), "attempts": len(attempts),
        "model_export_ms": timings.get("model_export"),
        "annotation_export_ms": timings.get("annotation_export"),
        "model_pass_ms": timings.get("model_pass"),
        "annotation_pass_ms": timings.get("annotation_pass"),
        "capture_total_ms": timings.get("total"),
        "faults": faults,
        "restore_faults": sorted(set(faults) & set(RESTORE_FAULTS)),
        "rolled_back": integrity.get("rolled_back"),
        "marks_still_in_project": integrity.get("marks_still_in_project"),
        "restore_failures": integrity.get("restore_failures"),
    }


def document_check_verdict(before, after):
    """PURE. ``{"unchanged": bool, "changed": [field...]}`` over the counts."""
    changed = sorted(k for k in ("element_count", "type_count")
                     if (before or {}).get(k) != (after or {}).get(k))
    return {"unchanged": not changed, "changed": changed,
            "is_modified_before": (before or {}).get("is_modified"),
            "is_modified_after": (after or {}).get("is_modified")}


# ======================================================================
# REVIT
# ======================================================================

def purge_cached_modules(modules):
    names = sorted(k for k in list(modules)
                   if k == "vop_interwoven" or k.startswith("vop_interwoven."))
    for name in names:
        del modules[name]
    return names


def _ensure_repo(repo_root):
    """Import vop_interwoven FRESH from the checkout and check it carries
    what this probe drives (the lattice-overrun probe's pattern)."""
    import importlib
    candidates = [repo_root, os.environ.get("REVIT_SSM_EXPORTER_ROOT"),
                  os.path.join(os.path.expanduser("~"), "Documents", "Revit_SSM_Exporter")]
    purged = purge_cached_modules(sys.modules)
    importlib.invalidate_caches()
    root = next((c for c in candidates if c and os.path.isdir(
        os.path.join(c, "vop_interwoven"))), None)
    if root is None:
        raise RuntimeError("vop_interwoven not found; set IN[5] repo_root. Tried: {0}".format(
            [c for c in candidates if c]))
    if root in sys.path:
        sys.path.remove(root)
    sys.path.insert(0, root)
    files, missing = {}, []
    for name, symbols in sorted(REQUIRED_PRODUCTION.items()):
        module = importlib.import_module(name)
        files[name] = getattr(module, "__file__", None)
        missing.extend("{0}.{1}".format(name, s) for s in symbols if not hasattr(module, s))
    if missing:
        raise RuntimeError("the vop_interwoven loaded lacks {0} (from {1})".format(missing, files))
    return {"root": root, "purged_modules": len(purged), "loaded_from": files}


def capture_config(cap):
    """The batch's Config (BatchCapture _config), with the cap override."""
    from vop_interwoven.config import Config
    cfg = Config()
    cfg.debug_json_detail = "summary"
    cfg.enable_color_id_buffer_stage_a = True
    cfg.color_id_buffer_registered_capture = True
    cfg.color_id_buffer_cap_axis_px = int(cap)
    return cfg


def document_counts(doc):
    from Autodesk.Revit.DB import FilteredElementCollector
    return {"element_count": FilteredElementCollector(doc).WhereElementIsNotElementType()
            .GetElementCount(),
            "type_count": FilteredElementCollector(doc).WhereElementIsElementType()
            .GetElementCount(),
            "is_modified": bool(doc.IsModified)}


def _load(path):
    if not path or not os.path.isfile(path):
        return None
    with open(path) as handle:
        return json.load(handle)


def _size(path):
    return os.path.getsize(path) if path and os.path.isfile(path) else None


def _sha256(path):
    if not path or not os.path.isfile(path):
        return None
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def run_job(doc, job, jobs_root, keep_images="first_repeat"):
    from vop_interwoven.capture_run import run_capture
    from vop_interwoven.run_meta import view_outcomes
    root = os.path.join(jobs_root, job["label"])
    t0 = time.time()
    result, run_dir, run_id = run_capture(doc, [job["view_id"]], capture_config(job["cap"]),
                                          root, date_override="probe_cap",
                                          export_view_raster=False)
    elapsed = round(time.time() - t0, 3)
    summaries = (result or {}).get("view_summaries") or []
    vs = next((s for s in summaries if str(s.get("view_id")) == str(job["view_id"])), {})
    outcome = next((o for o in view_outcomes(summaries)
                    if str(o.get("view_id")) == str(job["view_id"])), {})
    out = dict(job, run_dir=run_dir, run_id=run_id, run_capture_s=elapsed,
               sidecar_path=vs.get("sidecar_path"),
               annotation_sidecar_path=vs.get("annotation_sidecar_path"),
               model_tiff_bytes=_size(vs.get("tiff_path")),
               annotation_tiff_bytes=_size(vs.get("annotation_tiff_path")))
    out.update(summarize_job(_load(vs.get("sidecar_path")),
                             _load(vs.get("annotation_sidecar_path")), outcome))
    # Every image is hashed (repeats are compared by hash); a later repeat's
    # TIFFs are then deleted unless keep_images is "all": a 10000 px capture
    # is ~300 MB uncompressed, and the grid reads the first repeat only.
    out["model_tiff_sha256"] = _sha256(vs.get("tiff_path"))
    out["annotation_tiff_sha256"] = _sha256(vs.get("annotation_tiff_path"))
    out["images_deleted"] = []
    if keep_images != "all" and job["repeat"] > 1:
        for key in ("tiff_path", "annotation_tiff_path"):
            path = vs.get(key)
            if path and os.path.isfile(path):
                os.remove(path)
                out["images_deleted"].append(os.path.basename(path))
    return out


def _write_json(path, record):
    with open(path, "w") as handle:
        json.dump(record, handle, indent=2, sort_keys=True, default=str)


def run_probe(inputs):
    inputs = list(inputs)

    def _at(i):
        return inputs[i] if len(inputs) > i else None
    if not _at(0):
        raise ValueError("IN[0] output root is required")
    options = parse_options(_at(5))
    repo = _ensure_repo(options.get("repo_root"))
    from vop_interwoven.resolution_contract import MAX_STAGE_A_AXIS_PX
    capped = parse_ids(_at(1), "IN[1] capped view ids", required=True)
    controls = [v for v in parse_ids(_at(2), "IN[2]") if v not in capped]
    caps = parse_caps(_at(3), MAX_STAGE_A_AXIS_PX)
    repeats = int(_at(4)) if _at(4) not in (None, "") else DEFAULT_REPEATS
    if repeats < 1:
        raise ValueError("IN[4] repeats must be at least 1")
    seed = int(options.get("seed", DEFAULT_SEED))
    keep_images = str(options.get("keep_images") or "first_repeat")
    if keep_images not in KEEP_IMAGES:
        raise ValueError("IN[5] keep_images must be one of {0}".format(KEEP_IMAGES))
    from RevitServices.Persistence import DocumentManager
    from RevitServices.Transactions import TransactionManager
    doc = DocumentManager.Instance.CurrentDBDocument
    title = getattr(doc, "Title", None)
    if options.get("expected_doc_title"):
        from vop_interwoven.batch_capture import title_matches
        if not title_matches(options["expected_doc_title"], title):
            raise ValueError("the open document is {0!r}, not {1!r}".format(
                title, options["expected_doc_title"]))
    stamp = time.strftime("%Y%m%dT%H%M%S")
    probe_dir = os.path.join(os.path.abspath(str(_at(0))), "probe_export_cap_" + stamp)
    jobs_root = os.path.join(probe_dir, "jobs")
    os.makedirs(jobs_root)
    json_path = os.path.join(probe_dir, "probe_export_cap_{0}.json".format(stamp))
    progress_path = os.path.join(probe_dir, "progress_{0}.json".format(stamp))
    plan = build_plan(capped + controls, caps, repeats, seed)
    record = {"schema": SCHEMA, "probe": {"name": PROBE_NAME, "version": PROBE_VERSION},
              "started_at": stamp, "finalized": False, "doc_title": title,
              "doc_path": getattr(doc, "PathName", None), "repo": repo,
              "inputs": {"capped_view_ids": capped, "control_view_ids": controls,
                         "caps": caps, "repeats": repeats, "seed": seed,
                         "keep_images": keep_images,
                         "max_axis_px": MAX_STAGE_A_AXIS_PX},
              "plan_size": len(plan), "jobs": [], "exceptions": []}
    TransactionManager.Instance.ForceCloseTransaction()
    record["document_before"] = document_counts(doc)
    for job in plan:
        try:
            record["jobs"].append(run_job(doc, job, jobs_root, keep_images))
        except Exception as ex:
            err = exception_record(job["label"], ex)
            record["exceptions"].append(err)
            record["jobs"].append(dict(job, error=err["type"] + ": " + err["message"]))
        _write_json(progress_path, record)
    record["document_after"] = document_counts(doc)
    record["document_check"] = document_check_verdict(record["document_before"],
                                                      record["document_after"])
    faulted = [j["label"] for j in record["jobs"] if j.get("restore_faults")]
    record["jobs_with_restore_faults"] = faulted
    record["RESTORE_SUMMARY"] = (
        "DOCUMENT UNCHANGED (element and type counts; no restore faults)"
        if record["document_check"]["unchanged"] and not faulted
        else "DOCUMENT MAY HAVE CHANGED -- see document_check and jobs_with_restore_faults")
    record["finished_at"] = time.strftime("%Y%m%dT%H%M%S")
    record["finalized"] = True
    # Written LAST, once every field above is final (CLAUDE.md, defect class 4).
    _write_json(json_path, record)
    return json_path


def dynamo_main(inputs):
    return run_probe(list(inputs))


if "IN" in globals():
    try:
        OUT = dynamo_main(IN)  # noqa: F821
    except Exception as _fatal:
        OUT = {"json_path": None, "fatal": exception_record("dynamo_entrypoint", _fatal)}
