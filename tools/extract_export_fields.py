"""One CSV row per Stage A EXPORT: the frame record of every per-view sidecar.

    python tools/extract_export_fields.py <run_dir> <out.csv> [--all-statuses]

Reads ``<run_dir>/color_id/`` (v2) or ``color_id_buffer/`` (v1): the model
sidecar ``<stem>.json`` and the annotation sidecar ``<stem>_anno.json`` of
each view. Built for the export-floor diagnosis
(docs/notes/export_floor_20261118T1002.md) and joined there to
``views_timing_<d>.csv`` on ``view_id``.

What a row is, and what it is NOT:

* ONE row per sidecar: its own top-level frame record
  (``stage_a_sidecar_shapes.frame_record``, either shape). The first version
  walked the whole JSON for anything frame-shaped and so also emitted the
  ``_frame_summary`` copies under ``registration_marks.co_sizing``
  (``first_attempt`` / ``second_attempt`` / ``seeded_attempt``, a model AND an
  annotation summary each): 5 rows for a two-attempt co-sized file, 3 for a
  confirmed seed, the model summaries labelled with the annotation file's
  pass. Run 20261118T1002's 33 duplicated view/pass pairs were exactly that.
* Only this run's captures, by default: a view whose run_meta
  ``capture_status`` is not ``success`` is skipped, because a carried
  (``unchanged``) view's sidecar is its SOURCE run's capture and an
  ``unmeasurable``/failed view's may be a stale file from another run.
  ``--all-statuses`` keeps them, with ``capture_status`` saying which.
* ``view_name`` comes from run_meta's ``views[]`` entry, else from the file
  stem: no sidecar carries the view's name (the first version read a key
  that does not exist and left the column empty on every row).

Files in the capture folder that are not a sidecar (``*.decoded.json``,
``*_anno.reg.json``, anything not named ``<stem>_<view id>[_anno].json``, or a
sidecar whose ``view_id`` disagrees with its name) are counted as skipped; an
unreadable or malformed JSON is counted and named on stderr. Neither stops
the run. Exit 0 written; 2 no capture folder under ``run_dir``.
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from vop_interwoven import output_layout  # noqa: E402
import stage_a_sidecar_shapes as shapes  # noqa: E402

_SIDECAR_NAME = re.compile(r"^(?P<name>.*)_(?P<id>-?\d+)(?P<anno>_anno)?\.json$")

# The columns of the version Greg ran on 2026-11-19, in its order, then the
# ones the export-floor diagnosis added.
COLUMNS = ("file", "pass", "view_id", "view_name", "fit_direction", "pixel_size",
           "pre_cap_px", "cap_applied", "actual_w", "actual_h", "dim_check",
           "backoff_stop_reason", "n_attempts", "attempt_px", "attempt_dims",
           "co_sizing_state", "co_sizing_attempts", "view_scale")
ADDED_COLUMNS = ("capture_status", "export_ms", "export_image_ms", "list_stage_ms",
                 "move_ms", "read_dims_ms", "cleanup_ms", "n_export_images",
                 "first_export_image_for_view", "export_image_ordinal",
                 "file_bytes", "listed_to_moved_ms", "previous_export_view_id",
                 "gap_since_previous_export_ms")
EXPORT_PARTS = ("export_image", "list_stage", "move", "read_dims", "cleanup")
TELEMETRY_PASS = {"model": "model", "anno": "annotation"}


def capture_folder(run_dir):
    """The run's capture folder (v2 name first), or None."""
    for layout in (output_layout.V2, output_layout.V1):
        path = os.path.join(run_dir, output_layout.CAPTURE_DIR[layout])
        if os.path.isdir(path):
            return path
    return None


def run_views(run_dir):
    """``{view_id: views[] entry}`` from the run's run_meta, or None when the
    run folder holds none (or two: not guessed between)."""
    path, _why = output_layout.find_run_meta(run_dir)
    if path is None:
        return None
    with open(path, encoding="utf-8") as handle:
        meta = json.load(handle)
    return dict((str(v.get("view_id")), v) for v in meta.get("views") or []
                if isinstance(v, dict))


def _cell(value):
    return "" if value is None else value


def _joined(values):
    return "|".join("" if v is None else str(v) for v in values)


def export_cells(sidecar, pass_name):
    """The ADDED export-floor cells for one sidecar's pass. Empty for a
    sidecar written before the export timer was split."""
    marks = sidecar.get("registration_marks") or {}
    timings = marks.get("timings_ms") or {}
    tpass = TELEMETRY_PASS[pass_name]
    out = {"export_ms": timings.get("{0}_export".format(tpass))}
    for part in EXPORT_PARTS:
        out[part + "_ms"] = timings.get("{0}_export.{1}".format(tpass, part))
    tel = [t for t in marks.get("export_telemetry") or ()
           if isinstance(t, dict) and t.get("pass") == tpass]
    out["n_export_images"] = len(tel) if "export_telemetry" in marks else None
    first = tel[0] if tel else {}
    last = tel[-1] if tel else {}
    for key in ("first_export_image_for_view", "export_image_ordinal",
                "previous_export_view_id", "gap_since_previous_export_ms"):
        out[key] = first.get(key)
    out["file_bytes"] = last.get("file_bytes")
    out["listed_to_moved_ms"] = (
        round(sum(float(t["listed_to_moved_ms"]) for t in tel
                  if t.get("listed_to_moved_ms") is not None), 3) if tel else None)
    return out


def content_cells(sidecar):
    """Size-of-content proxies for docs/notes/export_floor_20261118T1002.md
    step 6 (the membership counts themselves are not persisted for a
    two-pass capture): ``near_face_w_count``, the model sidecar's painted
    host elements; ``link_instance_count``, the RevitLinkInstances the view
    graphics state read (0 when it read none applicable, empty when the read
    failed or the sidecar carries none)."""
    nfw = sidecar.get("near_face_w_map")
    links = ((sidecar.get("view_graphics_state") or {}).get("link_instances") or {})
    if links.get("state") == "value" and isinstance(links.get("value"), list):
        link_count = len(links["value"])
    elif links.get("state") == "not_applicable":
        link_count = 0
    else:
        link_count = None
    return {"near_face_w_count": len(nfw) if isinstance(nfw, (list, dict)) else None,
            "link_instance_count": link_count}


def sidecar_row(rel_path, sidecar, pass_name, view_name, capture_status):
    """PURE. The row for one sidecar: its own top-level frame only."""
    frame = shapes.frame_record(sidecar)
    attempts = [a for a in frame.get("dim_check_attempts") or () if isinstance(a, dict)]
    co = (sidecar.get("registration_marks") or {}).get("co_sizing") or {}
    row = {
        "file": rel_path, "pass": pass_name, "view_id": sidecar.get("view_id"),
        "view_name": view_name, "fit_direction": frame.get("fit_direction"),
        "pixel_size": frame.get("pixel_size"), "pre_cap_px": frame.get("pre_cap_px"),
        "cap_applied": frame.get("cap_applied"), "actual_w": frame.get("actual_w"),
        "actual_h": frame.get("actual_h"), "dim_check": frame.get("dim_check"),
        "backoff_stop_reason": frame.get("backoff_stop_reason"),
        "n_attempts": len(attempts),
        "attempt_px": _joined(a.get("requested_px") for a in attempts),
        "attempt_dims": _joined("{0}x{1}".format(a.get("actual_w"), a.get("actual_h"))
                                for a in attempts),
        "co_sizing_state": co.get("state"), "co_sizing_attempts": co.get("attempts"),
        "view_scale": frame.get("view_scale"), "capture_status": capture_status,
    }
    row.update(export_cells(sidecar, pass_name))
    row.update(content_cells(sidecar))
    return row


def extract(run_dir, all_statuses=False):
    """``(rows, report)``. ``report`` counts what was skipped and why."""
    folder = capture_folder(run_dir)
    if folder is None:
        raise FileNotFoundError("no color_id/ or color_id_buffer/ under {0}".format(run_dir))
    views = run_views(run_dir)
    report = {"sidecars": 0, "skipped_not_sidecar": [], "malformed": [],
              "skipped_status": 0, "run_meta": views is not None}
    rows = []
    for path in sorted(glob.glob(os.path.join(folder, "*.json"))):
        base = os.path.basename(path)
        match = _SIDECAR_NAME.match(base)
        if match is None or base.endswith((".decoded.json", ".reg.json",
                                           ".registered.json")):
            report["skipped_not_sidecar"].append(base)
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                sidecar = json.load(handle)
        except (OSError, ValueError) as ex:
            report["malformed"].append("{0}: {1}: {2}".format(base, type(ex).__name__, ex))
            continue
        if not isinstance(sidecar, dict) or str(sidecar.get("view_id")) != match.group("id"):
            report["skipped_not_sidecar"].append(base)
            continue
        view_id = match.group("id")
        entry = (views or {}).get(view_id) or {}
        status = entry.get("capture_status") if views is not None else None
        if views is not None and status != "success" and not all_statuses:
            report["skipped_status"] += 1
            continue
        report["sidecars"] += 1
        rel = os.path.join(os.path.basename(folder), base)
        pass_name = "anno" if match.group("anno") else "model"
        rows.append(sidecar_row(rel, sidecar, pass_name,
                                entry.get("view_name") or match.group("name"), status))
    return rows, report


def write_csv(rows, out_path):
    columns = list(COLUMNS) + list(ADDED_COLUMNS)
    with open(out_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for row in rows:
            writer.writerow([_cell(row.get(c)) for c in columns])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_dir")
    parser.add_argument("out_csv")
    parser.add_argument("--all-statuses", action="store_true",
                        help="keep views run_meta does not record as captured "
                             "in this run (carried, unmeasurable, failed)")
    args = parser.parse_args(argv)
    try:
        rows, report = extract(args.run_dir, all_statuses=args.all_statuses)
    except FileNotFoundError as ex:
        print("refused: {0}".format(ex), file=sys.stderr)
        return 2
    write_csv(rows, args.out_csv)
    if not report["run_meta"]:
        print("warning: no single run_meta under the run folder; every sidecar "
              "kept, capture_status empty", file=sys.stderr)
    for line in report["malformed"]:
        print("malformed: {0}".format(line), file=sys.stderr)
    print("rows {0} (sidecars {1}, skipped by capture_status {2}, not a sidecar {3}, "
          "malformed {4})".format(len(rows), report["sidecars"], report["skipped_status"],
                                  len(report["skipped_not_sidecar"]),
                                  len(report["malformed"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
