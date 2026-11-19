"""Export-floor A/B probe for Revit 2025 / Dynamo 3.x CPython3.

docs/notes/export_floor_20261118T1002.md. Run 20261118T1002 paid a ~21 s
stall on 825 of 2,569 exports. Whether one export stalled was predicted, at
96 %, by ONE number: the time since the previous stall (slow iff it ended more
than ~28 s ago, or started more than ~51 s ago). The view, its size and its
type did not decide it. This probe tells the three readings apart on two
views, with nothing changed between exports:

  (a) per-view, first export after a change: the FIRST export of each view
      stalls, and its repeats are fast whatever the idle gap before them.
  (b) per new file (an on-access scan of the written TIFF): EVERY export
      stalls, the same in both staging arms, or differently between them,
      and the stall sits in ``move``/``read_dims``, not ``export_image``.
  (c) a clock: back-to-back repeats are fast, and a repeat stalls once the
      idle gap since the last stall passes a threshold. That happens whichever
      view it is, unchanged.

Inputs:
    IN[0] = output root (required). The probe writes
            <root>/probe_export_floor_<clock>/ (the TIFFs, under staging/ and
            exports/) and its record probe_export_floor_<clock>.json there.
    IN[1] = two view ids (a comma list or a list), e.g. one plan + one RCP
    IN[2] = options, a Dictionary or JSON object; keys:
              repo_root    the checkout (else REVIT_SSM_EXPORTER_ROOT, then
                           ~/Documents/Revit_SSM_Exporter)
              gaps_s       idle seconds before each repeat, measured from
                           the END of the previous export (default
                           DEFAULT_GAPS_S). Each view runs the whole list.
              staging      "temp" (production's %TEMP%), "run_folder"
                           (a staging directory under the probe's own folder)
                           or "both" (default): each arm runs the whole plan
              pixel_size   the export's fitted-axis size (default 2000)
              net_watch    true (default on Windows): a separate PowerShell
                           process logs TCP connections in SYN_SENT (owning
                           process, remote address and port) every 0.5 s to
                           net_watch.tsv for the length of the probe. Read
                           only, and stopped at the end. A failure to start
                           it is recorded and the probe goes on.
Output: OUT = the record's path.

THE PROBE CHANGES NOTHING: no transaction is opened. ExportImage writes
files only. The staging arm "run_folder" points Python's ``tempfile.tempdir``
at the probe's folder for that arm and puts it back afterwards. Production's
staging location is not touched.
"""
from __future__ import print_function

import json
import os
import sys
import time
import traceback

PROBE_NAME = "export_floor"
PROBE_VERSION = "2026-11-19.1"
SCHEMA = "vop.probe.export_floor.v1"
# Two back-to-back repeats, then gaps either side of the threshold the run
# showed (~28 s since the last stall ended), then two long ones.
DEFAULT_GAPS_S = (0, 0, 5, 10, 20, 25, 30, 35, 45, 60)
DEFAULT_PIXEL_SIZE = 2000
STAGING_ARMS = ("temp", "run_folder")
SLOW_MS = 15000.0
REQUIRED_PRODUCTION = {"vop_interwoven.color_id_buffer": (
    "_export_tiff", "EXPORT_SUBKEYS", "export_subkeys_ms")}


# ======================================================================
# PURE: plan, verdict, net-watch log (tests/test_probe_export_floor.py)
# ======================================================================

def parse_ids(value):
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        items = list(value)
    else:
        items = str(value).replace(";", ",").split(",")
    return [int(str(v).strip()) for v in items if str(v).strip()]


def parse_options(value):
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "Keys"):
        return dict((str(k), value[k]) for k in value.Keys)
    return json.loads(str(value))


def build_plan(view_ids, gaps_s=DEFAULT_GAPS_S, staging="both"):
    """PURE. The export sequence: per arm, per view, one first export (no
    gap) then one repeat per gap. Views run one after the other, so view 2's
    first export follows view 1's last repeat by its first gap."""
    arms = list(STAGING_ARMS) if str(staging or "both") == "both" else [str(staging)]
    unknown = [a for a in arms if a not in STAGING_ARMS]
    if unknown:
        raise ValueError("staging must be one of {0} or 'both', got {1}".format(
            STAGING_ARMS, staging))
    if len(view_ids) < 1:
        raise ValueError("IN[1]: at least one view id")
    plan = []
    for arm in arms:
        for view_id in view_ids:
            plan.append({"arm": arm, "view_id": int(view_id), "repeat": 0, "gap_s": 0.0})
            for i, gap in enumerate(gaps_s):
                plan.append({"arm": arm, "view_id": int(view_id), "repeat": i + 1,
                             "gap_s": float(gap)})
    return plan


def annotate(exports, slow_ms=SLOW_MS):
    """PURE. ``exports`` in run order, each with ``t_start`` (s) and ``ms``.
    Adds ``slow`` and ``since_stall_end_s`` (None before the first stall)."""
    last_end = None
    for ex in exports:
        ex["slow"] = ex.get("ms") is not None and ex["ms"] > slow_ms
        ex["since_stall_end_s"] = (None if last_end is None
                                   else round(ex["t_start"] - last_end, 3))
        if ex["slow"]:
            last_end = ex["t_start"] + ex["ms"] / 1000.0
    return exports


def verdict(exports, short_gap_s=10.0, long_gap_s=35.0):
    """PURE. Which reading the exports support. Needs both evidence kinds:
    repeats after a SHORT idle gap (<= short_gap_s since the last stall
    ended) and after a LONG one (>= long_gap_s)."""
    done = [e for e in exports if e.get("ms") is not None]
    firsts = [e for e in done if e["repeat"] == 0]
    repeats = [e for e in done if e["repeat"] > 0]
    short = [e for e in repeats if e.get("since_stall_end_s") is not None
             and e["since_stall_end_s"] <= short_gap_s]
    long_ = [e for e in repeats if e.get("since_stall_end_s") is None
             or e["since_stall_end_s"] >= long_gap_s]
    frac = (lambda xs: None if not xs else
            round(sum(1 for x in xs if x["slow"]) / float(len(xs)), 3))
    out = {"exports": len(done), "slow": sum(1 for e in done if e["slow"]),
           "first_export_slow": frac(firsts), "repeat_short_gap_slow": frac(short),
           "repeat_long_gap_slow": frac(long_),
           "n_short": len(short), "n_long": len(long_)}
    by_arm = {}
    for e in done:
        by_arm.setdefault(e["arm"], []).append(e)
    out["slow_by_arm"] = dict((a, frac(xs)) for a, xs in sorted(by_arm.items()))
    slow = [e for e in done if e["slow"]]
    parts = {}
    for e in slow:
        for part, ms in (e.get("parts_ms") or {}).items():
            parts.setdefault(part, []).append(ms)
    out["slow_part_median_ms"] = dict(
        (p, sorted(v)[len(v) // 2]) for p, v in sorted(parts.items()))
    out["stall_part"] = (max(out["slow_part_median_ms"],
                             key=out["slow_part_median_ms"].get)
                         if out["slow_part_median_ms"] else None)
    if not short or not long_:
        out["reading"] = "inconclusive: needs repeats after short and long idle gaps"
    elif out["repeat_short_gap_slow"] >= 0.8 and out["repeat_long_gap_slow"] >= 0.8:
        out["reading"] = "b_every_export"
    elif out["repeat_short_gap_slow"] <= 0.2 and out["repeat_long_gap_slow"] >= 0.8:
        out["reading"] = "c_clock"
    elif out["repeat_short_gap_slow"] <= 0.2 and out["repeat_long_gap_slow"] <= 0.2:
        out["reading"] = ("a_first_export_of_view" if (out["first_export_slow"] or 0) >= 0.8
                          else "no_stall_reproduced")
    else:
        out["reading"] = "mixed"
    return out


def parse_net_watch(lines):
    """PURE. ``[{"t", "pid", "process", "remote", "port"}]`` from the
    PowerShell log (tab-separated: ISO time, pid, process, address, port)."""
    out = []
    for line in lines:
        cells = line.rstrip("\r\n").split("\t")
        if len(cells) != 5 or not cells[1].strip().isdigit():
            continue
        out.append({"t": cells[0].strip(), "pid": int(cells[1]), "process": cells[2].strip(),
                    "remote": cells[3].strip(), "port": cells[4].strip()})
    return out


def summarize_net_watch(entries):
    """PURE. Distinct (process, remote, port) seen in SYN_SENT, with how many
    0.5 s samples each was seen in (about twice the seconds it waited)."""
    counts = {}
    for e in entries:
        key = (e["process"], e["remote"], e["port"])
        counts[key] = counts.get(key, 0) + 1
    return [{"process": k[0], "remote": k[1], "port": k[2], "samples": n}
            for k, n in sorted(counts.items(), key=lambda kv: -kv[1])]


NET_WATCH_PS = (
    "$p = '{path}'; while ($true) {{ $t = (Get-Date).ToString('o'); "
    "Get-NetTCPConnection -State SynSent -ErrorAction SilentlyContinue | ForEach-Object {{ "
    "$n = (Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).ProcessName; "
    "\"$t`t$($_.OwningProcess)`t$n`t$($_.RemoteAddress)`t$($_.RemotePort)\" }} | "
    "Out-File -Append -Encoding utf8 $p; Start-Sleep -Milliseconds 500 }}")


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
        raise RuntimeError("vop_interwoven not found; set repo_root. Tried: {0}".format(
            [c for c in candidates if c]))
    if root in sys.path:
        sys.path.remove(root)
    sys.path.insert(0, root)
    missing = []
    for name, symbols in REQUIRED_PRODUCTION.items():
        module = importlib.import_module(name)
        missing.extend("{0}.{1}".format(name, s) for s in symbols if not hasattr(module, s))
    if missing:
        raise RuntimeError("the vop_interwoven loaded lacks {0}".format(missing))
    return root


def _start_net_watch(probe_dir):
    import subprocess
    path = os.path.join(probe_dir, "net_watch.tsv")
    script = NET_WATCH_PS.format(path=path.replace("'", "''"))
    proc = subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive",
                             "-Command", script],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc, path


def _export(doc, view, out_path, pixel_size, record_entry):
    from vop_interwoven import color_id_buffer as cib
    attempts = []
    t0 = time.time()
    try:
        cib._export_tiff(doc, view, out_path, pixel_size, view_id=view.Id.IntegerValue,
                         timing_out=attempts, run_key=record_entry["run_key"])
    finally:
        t1 = time.time()
        record_entry["t_start"] = round(t0, 3)
        record_entry["ms"] = round((t1 - t0) * 1000.0, 3)
        record_entry["attempts"] = attempts
        record_entry["parts_ms"] = dict(
            (k.split(".", 1)[1], v) for k, v in cib.export_subkeys_ms(attempts).items())
    return t1


def run_probe(inputs):
    import tempfile
    inputs = list(inputs)
    at = (lambda i: inputs[i] if len(inputs) > i else None)
    if not at(0):
        raise ValueError("IN[0] output root is required")
    view_ids = parse_ids(at(1))
    options = parse_options(at(2))
    repo = _ensure_repo(options.get("repo_root"))
    gaps = [float(g) for g in (options.get("gaps_s") or DEFAULT_GAPS_S)]
    plan = build_plan(view_ids, gaps, options.get("staging") or "both")
    pixel_size = int(options.get("pixel_size") or DEFAULT_PIXEL_SIZE)
    from RevitServices.Persistence import DocumentManager
    from RevitServices.Transactions import TransactionManager
    from Autodesk.Revit.DB import ElementId
    doc = DocumentManager.Instance.CurrentDBDocument
    TransactionManager.Instance.ForceCloseTransaction()
    stamp = time.strftime("%Y%m%dT%H%M%S")
    probe_dir = os.path.join(os.path.abspath(str(at(0))), "probe_export_floor_" + stamp)
    os.makedirs(os.path.join(probe_dir, "exports"))
    record = {"schema": SCHEMA, "probe": {"name": PROBE_NAME, "version": PROBE_VERSION},
              "started_at": stamp, "finalized": False, "repo_root": repo,
              "doc_title": getattr(doc, "Title", None),
              "inputs": {"view_ids": view_ids, "gaps_s": gaps, "pixel_size": pixel_size,
                         "staging": options.get("staging") or "both"},
              "system_temp": tempfile.gettempdir(), "exports": [], "exceptions": []}
    watch = None
    if options.get("net_watch", os.name == "nt"):
        try:
            watch = _start_net_watch(probe_dir)
            record["net_watch"] = {"path": watch[1], "started": True}
        except Exception as ex:
            record["net_watch"] = {"started": False,
                                   "error": "{0}: {1}".format(type(ex).__name__, ex)}
    saved_tempdir = tempfile.tempdir
    last_end = time.time()
    try:
        for i, step in enumerate(plan):
            if step["arm"] == "run_folder":
                stage = os.path.join(probe_dir, "staging")
                if not os.path.isdir(stage):
                    os.makedirs(stage)
                tempfile.tempdir = stage
            else:
                tempfile.tempdir = saved_tempdir
            wait = step["gap_s"] - (time.time() - last_end)
            if wait > 0:
                time.sleep(wait)
            view = doc.GetElement(ElementId(step["view_id"]))
            entry = dict(step, run_key="probe_export_floor_" + stamp, index=i,
                         idle_s=round(time.time() - last_end, 3))
            out_path = os.path.join(probe_dir, "exports", "{0}_{1}_{2:02d}.tiff".format(
                step["arm"], step["view_id"], step["repeat"]))
            try:
                last_end = _export(doc, view, out_path, pixel_size, entry)
            except Exception as ex:
                last_end = time.time()
                entry["error"] = "{0}: {1}".format(type(ex).__name__, ex)
                record["exceptions"].append({"index": i, "traceback": traceback.format_exc()})
            record["exports"].append(entry)
    finally:
        tempfile.tempdir = saved_tempdir
        if watch is not None:
            try:
                watch[0].terminate()
            except Exception as ex:
                record["net_watch"]["stop_error"] = "{0}: {1}".format(type(ex).__name__, ex)
    annotate(record["exports"])
    record["verdict"] = verdict(record["exports"])
    if watch is not None and os.path.isfile(watch[1]):
        with open(watch[1], encoding="utf-8-sig", errors="replace") as handle:
            record["net_watch"]["syn_sent"] = summarize_net_watch(parse_net_watch(handle))
    record["finished_at"] = time.strftime("%Y%m%dT%H%M%S")
    record["finalized"] = True
    json_path = os.path.join(probe_dir, "probe_export_floor_{0}.json".format(stamp))
    # Written LAST, once every field is final (CLAUDE.md, defect class 4).
    with open(json_path, "w") as handle:
        json.dump(record, handle, indent=2, sort_keys=True, default=str)
    return json_path


if "IN" in globals():
    try:
        OUT = run_probe(IN)  # noqa: F821
    except Exception as _fatal:
        OUT = {"json_path": None, "fatal": "{0}: {1}".format(type(_fatal).__name__, _fatal),
               "traceback": traceback.format_exc()}
