"""Stage A LATTICE-OVERRUN probe for Revit 2025 / Dynamo 3.x CPython3.

Group A of tools/notes/RUN_2025-02-03_to_2025-11-01_SERIES_SMALL_VIEWS.md:
the six "(N) LEVEL x - Wall Types BP" plans (4148625, 9468987, 9560929,
9594635, 9595813, 9596211) fail ``annotation_lattice_mismatch`` on every
archive 09-13..10-25. The note's diagnosis: section markers whose lines run
past the crop make the ANNOTATION export's drawn extent taller than the
model's, its derived axis comes back over the 10000 px ceiling, the export is
halved, and it is accepted off the model's pixel size. Run this on the
2025-10-25 archive.

Inputs:
    IN[0] = output ROOT (required). Each variant writes one ordinary capture
            run into <root>/<variant>/<date>__<run_id>_probe_<variant>/, and
            the probe's own record lands in
            <root>/probe_lattice_overrun_<clock>.json.
    IN[1] = view ids (default the six plans above)
    IN[2] = variants: "all" (default) or a comma list of VARIANTS
    IN[3] = options, a Dictionary or JSON object; keys:
              repo_root            the checkout (else REVIT_SSM_EXPORTER_ROOT,
                                   then the usual places)
              expected_doc_title   refuse a document whose title is not this
                                   (the archive's run_meta.doc_title value)
              expected_config_hash the run's run_meta.config_hash ("9400eaef"
                                   for the series); a mismatch is RECORDED,
                                   not refused
Output: OUT = the path of the probe's record.

THE VARIANTS, each the PRODUCTION registered capture
(``capture_run.run_capture``, the thinrunner's loop, the thinrunner's config)
with one probe-only switch, ``color_id_buffer_anno_extent_variant``, applied
inside the capture's own TransactionGroup AFTER the model pass and BEFORE the
annotation pass (stage_a_registered_capture.ANNO_EXTENT_VARIANTS), so the
model pass is the shipped one and the group's rollback is the restore:

  as_is                   nothing changed: the failure, measured.
  section_markers_hidden  the annotation members in category Views
                          (OST_Viewers) hidden in the view. Does the
                          annotation export come back at the model's size
                          without them? Then they ARE the overrun.
  annotation_crop_on      the annotation crop on, through the view's
                          VIEWER_ANNOTATION_CROP_ACTIVE parameter (2026-10-19.2;
                          .1 assigned View.AnnotationCropActive, which Revit
                          2025's ViewPlan does not have: the variant changed
                          nothing and read back as applied). Does the annotation
                          crop bound the export's extent, and at what size
                          (its offsets are recorded)?
  co_sized                (once the fix is in) the shipped capture: when the
                          annotation export overruns the ceiling, BOTH passes
                          are re-exported at the request at which the
                          annotation fits (resolution_contract.
                          co_sized_request). Absent before the fix; selecting
                          it then is refused.

WHAT THIS DECIDES, AND HOW (``verdict`` below, pure, tested offline):
  * per view and variant: ``fits`` (the annotation export's first attempt
    passed and no lattice fault) or ``overrun`` (its derived axis came back
    over the ceiling), with the annotation's measured extent against the
    model's in px;
  * what each variant COSTS in content: the annotation pass's painted
    element count against as_is (hiding markers removes them from the
    capture; an annotation crop hides every annotation that touches it,
    inside crop A too). A variant that fits by losing annotation inside the
    crop is not a fix. The pixel-level comparison inside crop A is offline:
    tools/register_stage_a_annotation.py and tools/decode_stage_a_color_id.py
    over each variant's run directory.

THE PROBE CHANGES NOTHING PERSISTENTLY: it adds no transaction of its own;
every write is the capture's, inside its rolled-back group, read back after
(``registration_marks.restore`` in each sidecar; a view not put back is the
capture's fault ``view_state_not_restored`` / ``view_membership_changed``,
summarised here as ``restore_faults``).
"""
from __future__ import print_function

import json
import os
import sys
import time

PROBE_NAME = "stage_a_lattice_overrun"
PROBE_VERSION = "2026-10-19.2"
SCHEMA = "vop.probe.lattice_overrun.v1"

DEFAULT_VIEW_IDS = (4148625, 9468987, 9560929, 9594635, 9595813, 9596211)
# The production switch values, plus "co_sized" (the shipped capture once the
# fix is in, which needs no switch).
VARIANTS = ("as_is", "section_markers_hidden", "annotation_crop_on", "co_sized")
OPTION_KEYS = ("repo_root", "expected_doc_title", "expected_config_hash")
LATTICE_FAULT = "annotation_lattice_mismatch"
RESTORE_FAULTS = ("view_state_not_restored", "view_membership_changed",
                  "registration_marks_left_in_project", "element_overrides_left_behind",
                  "detail_lines_left_hidden", "rollback_failed", "rollback_raised")


# ======================================================================
# PURE: what a sidecar pair says, and the verdict over the variants
# ======================================================================

def select_variants(selection, available=VARIANTS):
    if selection is None or str(selection).strip().lower() in ("", "all"):
        return list(available)
    names = [p.strip() for p in str(selection).replace(";", ",").split(",") if p.strip()]
    unknown = [n for n in names if n not in VARIANTS]
    if unknown:
        raise ValueError("unknown variant(s) {0}; known: {1}".format(unknown, VARIANTS))
    missing = [n for n in names if n not in available]
    if missing:
        raise ValueError("variant(s) {0} are not available on this build: {1}".format(
            missing, list(available)))
    return list(dict.fromkeys(names))


def _attempt(entry):
    keys = ("requested_px", "accepted_px", "actual_w", "actual_h", "dim_check")
    return dict((k, (entry or {}).get(k)) for k in keys)


def summarize_view(model_sidecar, anno_sidecar, outcome=None):
    """PURE. One view's facts under one variant, from its two sidecars (dicts
    or None) and its run_meta outcome."""
    outcome = outcome or {}
    model = model_sidecar or {}
    anno = anno_sidecar or {}
    mframe = model.get("frame") or {}
    aframe = anno.get("frame") or {}
    m_attempts = mframe.get("dim_check_attempts") or []
    a_attempts = aframe.get("dim_check_attempts") or []
    reg = anno.get("registration_marks") or model.get("registration_marks") or {}
    vertical = str(mframe.get("fit_direction") or "horizontal") == "vertical"
    ceiling = aframe.get("dim_check_ceiling_px") or mframe.get("dim_check_ceiling_px")
    first = a_attempts[0] if a_attempts else {}
    derived = first.get("actual_w") if vertical else first.get("actual_h")
    m_first = m_attempts[0] if m_attempts else {}
    m_derived = m_first.get("actual_w") if vertical else m_first.get("actual_h")
    anno_faults = [f.get("fault") for f in
                   ((anno.get("capture_integrity") or {}).get("capture_faults") or [])]
    capture_faults = [f.get("fault") for f in (reg.get("faults") or [])]
    if not a_attempts:
        state = "no_annotation_export"
    elif LATTICE_FAULT in anno_faults:
        state = "overrun" if (derived is not None and ceiling is not None
                              and derived > ceiling) else "lattice_mismatch"
    elif first.get("dim_check") == "pass":
        state = "fits"
    else:
        state = "fits_after_resize" if a_attempts[-1].get("dim_check") == "pass" else "failed"
    return {
        "view_id": outcome.get("view_id") or model.get("view_id"),
        "capture_status": outcome.get("capture_status"),
        "capture_failure_reason": outcome.get("capture_failure_reason"),
        "state": state,
        "model_attempts": [_attempt(a) for a in m_attempts],
        "model_achieved_fpp_ft": mframe.get("achieved_fpp_ft"),
        "anno_attempts": [_attempt(a) for a in a_attempts],
        "anno_first_derived_px": derived,
        "model_derived_px": m_derived,
        "anno_over_model_derived_px": (None if derived is None or m_derived is None
                                       else derived - m_derived),
        "ceiling_px": ceiling,
        "anno_faults": anno_faults,
        "capture_faults": capture_faults,
        "restore_faults": sorted(set(capture_faults) & set(RESTORE_FAULTS)),
        "painted_elements": anno.get("color_assignment_count"),
        "variant_record": reg.get("anno_extent_variant"),
        "co_sizing": reg.get("co_sizing"),
    }


def verdict(by_variant):
    """PURE. ``by_variant``: {variant: [summarize_view(...), ...]}. Per
    variant: how many views fit, which overran, any restore fault, and the
    painted-element delta against as_is per view (negative = annotation
    removed from the capture). The probe states these; it does not choose
    the fix."""
    base = dict((s["view_id"], s) for s in by_variant.get("as_is") or [])
    out = {}
    for variant, summaries in by_variant.items():
        deltas = {}
        for s in summaries:
            b = base.get(s["view_id"])
            if variant != "as_is" and b is not None and None not in (
                    s.get("painted_elements"), b.get("painted_elements")):
                deltas[str(s["view_id"])] = s["painted_elements"] - b["painted_elements"]
        out[variant] = {
            "views": len(summaries),
            "fits": sorted(s["view_id"] for s in summaries
                           if s["state"] in ("fits", "fits_after_resize")),
            "overrun": sorted(s["view_id"] for s in summaries if s["state"] == "overrun"),
            "other": sorted((s["view_id"], s["state"]) for s in summaries
                            if s["state"] not in ("fits", "fits_after_resize", "overrun")),
            "restore_faults": dict((str(s["view_id"]), s["restore_faults"])
                                   for s in summaries if s["restore_faults"]),
            "painted_delta_vs_as_is": deltas,
            "content_lost": sorted(int(v) for v, d in deltas.items() if d < 0),
        }
    return out


def parse_options(value):
    if value is None or value == "":
        return {}
    if isinstance(value, str):
        value = json.loads(value)
    try:
        options = dict(value)
    except (TypeError, ValueError):
        raise ValueError("IN[3] must be a Dictionary or a JSON object")
    unknown = sorted(k for k in options if k not in OPTION_KEYS)
    if unknown:
        raise ValueError("IN[3] has unknown key(s) {0}; known: {1}".format(unknown, OPTION_KEYS))
    return options


# ======================================================================
# REVIT
# ======================================================================

def _ensure_repo(repo_root):
    candidates = [repo_root, os.environ.get("REVIT_SSM_EXPORTER_ROOT"),
                  os.path.join(os.path.expanduser("~"), "Documents", "Revit_SSM_Exporter"),
                  os.path.join(os.path.expanduser("~"), "Documents", "GitHub",
                               "Revit_SSM_Exporter")]
    try:
        import vop_interwoven  # noqa: F401
        return "already importable"
    except ImportError:
        pass
    for root in [c for c in candidates if c]:
        if os.path.isdir(os.path.join(root, "vop_interwoven")):
            if root not in sys.path:
                sys.path.insert(0, root)
            __import__("vop_interwoven")
            return root
    raise RuntimeError("vop_interwoven not found; set IN[3] repo_root. Tried: {0}".format(
        [c for c in candidates if c]))


def _available_variants():
    from vop_interwoven import stage_a_registered_capture as rc
    from vop_interwoven import resolution_contract as res
    found = [v for v in VARIANTS[:3] if v in rc.ANNO_EXTENT_VARIANTS]
    if hasattr(res, "co_sized_request"):
        found.append("co_sized")
    return found


def _thinrunner_config():
    """The thinrunner's Config (thinrunner_streaming: Config(), summary
    detail, Stage A on, registered capture on): the series' config."""
    from vop_interwoven.config import Config
    cfg = Config()
    cfg.debug_json_detail = "summary"
    cfg.enable_color_id_buffer_stage_a = True
    cfg.color_id_buffer_registered_capture = True
    return cfg


def _load(path):
    if not path or not os.path.isfile(path):
        return None
    with open(path) as handle:
        return json.load(handle)


def run_variant(doc, view_ids, variant, root):
    from vop_interwoven.capture_run import run_capture
    cfg = _thinrunner_config()
    if variant != "co_sized":
        cfg.color_id_buffer_anno_extent_variant = variant
        # Measured ALONE: the shipped co-sizing (when present) is off.
        cfg.color_id_buffer_co_size = False
    t0 = time.time()
    result, run_dir, run_id = run_capture(doc, list(view_ids), cfg,
                                          os.path.join(root, variant),
                                          date_override="probe_" + variant,
                                          export_view_raster=False)
    from vop_interwoven.run_meta import view_outcomes
    view_summaries = (result or {}).get("view_summaries") or []
    by_id = dict((str(vs.get("view_id")), vs) for vs in view_summaries)
    status = dict((str(o.get("view_id")), o) for o in view_outcomes(view_summaries))
    summaries = []
    for vid in view_ids:
        vs = by_id.get(str(vid)) or {}
        summaries.append(summarize_view(_load(vs.get("sidecar_path")),
                                        _load(vs.get("annotation_sidecar_path")),
                                        status.get(str(vid)) or {"view_id": vid}))
    return {"run_dir": run_dir, "run_id": run_id,
            "elapsed_s": round(time.time() - t0, 1), "views": summaries}


def main(inputs):
    root = inputs[0] if len(inputs) > 0 else None
    if not root:
        raise ValueError("IN[0] output root is required")
    options = parse_options(inputs[3] if len(inputs) > 3 else None)
    repo = _ensure_repo(options.get("repo_root"))
    view_ids = [int(v) for v in (inputs[1] if len(inputs) > 1 and inputs[1]
                                 else DEFAULT_VIEW_IDS)]
    variants = select_variants(inputs[2] if len(inputs) > 2 else None,
                               _available_variants())
    from RevitServices.Persistence import DocumentManager
    doc = DocumentManager.Instance.CurrentDBDocument
    title = getattr(doc, "Title", None)
    expected = options.get("expected_doc_title")
    if expected:
        from vop_interwoven.batch_capture import title_matches
        if not title_matches(expected, title):
            raise ValueError("the open document is {0!r}, not {1!r}".format(title, expected))
    # The hash run_meta.config_hash records (its config_hash_basis).
    from vop_interwoven.root_cache import compute_config_hash
    config_hash = compute_config_hash(_thinrunner_config())
    record = {"schema": SCHEMA, "probe": PROBE_NAME, "probe_version": PROBE_VERSION,
              "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "doc_title": title,
              "repo_root": repo, "view_ids": view_ids, "variants": variants,
              "config_hash": config_hash,
              "config_hash_matches": (None if not options.get("expected_config_hash")
                                      else config_hash == options["expected_config_hash"]),
              "runs": {}}
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, "probe_lattice_overrun_{0}.json".format(
        time.strftime("%Y%m%dT%H%M%S")))
    for variant in variants:
        try:
            record["runs"][variant] = run_variant(doc, view_ids, variant, root)
        except Exception as ex:
            record["runs"][variant] = {"error": "{0}: {1}".format(type(ex).__name__, ex),
                                       "views": []}
    record["verdict"] = verdict(dict((v, r.get("views") or [])
                                     for v, r in record["runs"].items()))
    record["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    with open(path, "w") as handle:
        json.dump(record, handle, indent=2, sort_keys=True, default=str)
    return path


if "IN" in globals():
    OUT = main(IN)  # noqa: F821 -- Dynamo's inputs
