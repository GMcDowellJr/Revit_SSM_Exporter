#!/usr/bin/env python3
"""Score hash-and-skip's would-skip verdicts against the captures (phase 1).

docs/DESIGN_CHANGE_SKIP.md. Given the runs of ONE model in series order (each
with its ``view_fingerprints_<d>.json``, written beside the capture or by the
batch's fingerprint backfill), for every consecutive pair (A = the previous
archive, B = this one):

  * the verdict production would give each of B's views,
    ``vop_interwoven.view_fingerprint.skip_verdict`` -- the one function, so
    this scores what phase 2 would do, not a copy of it;
  * for every would-skip view, whether its capture really is the same:
    tools/analyze_change_detection_probe's comparison (decoded ids per pass,
    ticks and the capture's own lines excluded, the frame, the bbox maps).
    A would-skip view whose capture differs is a FALSE UNCHANGED;
  * with ``--compare-all``, the same comparison for the views it would
    capture, which measures over-caution (slower: every view is decoded).

Acceptance (S-13, Greg 2026-10-28): 0 false unchanged over at least
``--min-pairs`` (1,000) compared would-skip views, and per view type at least
``--min-per-type`` (30) with 0 false unchanged. A type that has not met its
bar keeps being captured; the record says which types have.

    python tools/validate_change_skip.py RUN_A RUN_B [RUN_C ...] [--compare-all]
        [--out validate_change_skip.json] [--min-pairs 1000] [--min-per-type 30]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import analyze_change_detection_probe as acd  # noqa: E402
from vop_interwoven import output_layout  # noqa: E402
from vop_interwoven import view_fingerprint as vf  # noqa: E402

SCHEMA = "vop.validate_change_skip.v1"
TOOL_VERSION = "1.0.0"
OUTCOMES = ("true_unchanged", "false_unchanged", "not_comparable")


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_run(run_dir):
    """``{run_dir, meta, fingerprints, statuses, reason}``: a run's record,
    its fingerprints file (None with the reason when it has none) and each
    view's capture_status."""
    run_dir = Path(run_dir)
    path, why = output_layout.find_run_meta(str(run_dir))
    if path is None:
        return {"run_dir": str(run_dir), "meta": None, "fingerprints": None,
                "statuses": {}, "reason": why}
    meta = _load(path)
    statuses = dict((str(v.get("view_id")), v.get("capture_status"))
                    for v in meta.get("views") or [])
    stamp = meta.get("view_fingerprints") or {}
    name = stamp.get("file") or output_layout.fingerprints_name(
        output_layout.census_file_date(meta, str(run_dir)))
    fp_path = run_dir / name
    if not fp_path.is_file():
        reason = stamp.get("error") or "no {0}".format(name)
        return {"run_dir": str(run_dir), "meta": meta, "fingerprints": None,
                "statuses": statuses, "reason": reason}
    return {"run_dir": str(run_dir), "meta": meta, "fingerprints": _load(fp_path),
            "statuses": statuses, "reason": None}


def _bump(table, view_type, key):
    row = table.setdefault(view_type or "(unknown)", dict(
        (k, 0) for k in ("views", "would_skip") + OUTCOMES))
    row[key] += 1


def score_pair(run_a, run_b, compare=None, compare_all=False):
    """Score B's views against A. ``compare(view_id) -> capture_differs``
    (True / False / None) is injected; by default the captures of the two
    runs are compared with the change-detection analyzer."""
    if compare is None:
        folder_a = acd.capture_folder(run_a["run_dir"])
        folder_b = acd.capture_folder(run_b["run_dir"])

        def compare(view_id):
            fa, fb = acd.view_files(folder_a, view_id), acd.view_files(folder_b, view_id)
            if not (fa["model"] or fa["anno"]) or not (fb["model"] or fb["anno"]):
                return None, None
            entry = acd.compare_view(view_id, fa, fb)
            return acd.capture_differs(entry), entry
    pair = {"a": run_a["run_dir"], "b": run_b["run_dir"], "by_type": {}, "rules": {},
            "changed_components": {}, "false_unchanged": [], "over_cautious": []}
    if run_a["fingerprints"] is None or run_b["fingerprints"] is None:
        pair["skipped"] = "fingerprints missing: A {0}; B {1}".format(
            run_a["reason"], run_b["reason"])
        return pair
    facts_a = vf.run_facts(run_a["meta"], run_a["fingerprints"])
    facts_b = vf.run_facts(run_b["meta"], run_b["fingerprints"])
    pair["facts"] = {"a": facts_a, "b": facts_b}
    views_a = run_a["fingerprints"].get("views") or {}
    for vid, cur in sorted((run_b["fingerprints"].get("views") or {}).items()):
        base = views_a.get(vid)
        vtype = cur.get("view_type") or (base or {}).get("view_type")
        verdict = vf.skip_verdict(facts_a, facts_b, base, cur, run_a["statuses"].get(vid))
        _bump(pair["by_type"], vtype, "views")
        rule = verdict["rule"] or "would_skip"
        pair["rules"][rule] = pair["rules"].get(rule, 0) + 1
        if rule == "fingerprint_differs":
            for name in vf.changed_components(base, cur) or []:
                pair["changed_components"][name] = pair["changed_components"].get(name, 0) + 1
        if not verdict["would_skip"] and not compare_all:
            continue
        try:
            differs, entry = compare(int(vid))
        except (acd.Refusal, OSError, ValueError, KeyError) as ex:
            differs, entry = None, {"error": "{0}: {1}".format(type(ex).__name__, ex)}
        if verdict["would_skip"]:
            _bump(pair["by_type"], vtype, "would_skip")
            outcome = {True: "false_unchanged", False: "true_unchanged",
                       None: "not_comparable"}[differs]
            _bump(pair["by_type"], vtype, outcome)
            if outcome == "false_unchanged":
                pair["false_unchanged"].append({
                    "view_id": int(vid), "view_type": vtype,
                    "cause_inference": acd.classify(entry) if entry else None,
                    "model_differing_px": ((entry or {}).get("model_pixels") or {}).get(
                        "differing_px"),
                    "anno_differing_px": ((entry or {}).get("anno_pixels") or {}).get(
                        "differing_px"),
                    "frame_diff": (entry or {}).get("frame_diff")})
        elif differs is False:
            pair["over_cautious"].append({"view_id": int(vid), "view_type": vtype,
                                          "rule": verdict["rule"]})
    return pair


def acceptance(pairs, min_pairs, min_per_type):
    """PURE. The bar over every scored pair: per view type and overall."""
    totals = {}
    for pair in pairs:
        for vtype, row in (pair.get("by_type") or {}).items():
            t = totals.setdefault(vtype, dict((k, 0) for k in row))
            for k, v in row.items():
                t[k] += v
    compared = sum(r["true_unchanged"] + r["false_unchanged"] for r in totals.values())
    false = sum(r["false_unchanged"] for r in totals.values())
    types = {}
    for vtype, r in sorted(totals.items()):
        n = r["true_unchanged"] + r["false_unchanged"]
        types[vtype] = dict(r, compared=n, meets_bar=(r["false_unchanged"] == 0
                                                      and n >= min_per_type),
                            upper_bound_95=(3.0 / n) if n and not r["false_unchanged"] else None)
    return {"compared_would_skip": compared, "false_unchanged": false,
            "overall_meets_bar": false == 0 and compared >= min_pairs,
            "upper_bound_95": (3.0 / compared) if compared and not false else None,
            "min_pairs": min_pairs, "min_per_type": min_per_type,
            "types_meeting_bar": sorted(t for t, r in types.items() if r["meets_bar"]),
            "by_type": types,
            "rule": "rule of three: 0 failures in n checks bounds the rate below 3/n at 95 %"}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("runs", nargs="+", help="run directories of ONE model, in series order")
    ap.add_argument("--compare-all", action="store_true",
                    help="also compare the views it would capture (measures over-caution)")
    ap.add_argument("--min-pairs", type=int, default=1000)
    ap.add_argument("--min-per-type", type=int, default=30)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    if len(args.runs) < 2:
        ap.error("give at least two runs, in series order")
    runs = [load_run(r) for r in args.runs]
    pairs = []
    for a, b in zip(runs, runs[1:]):
        t0 = time.perf_counter()
        pair = score_pair(a, b, compare_all=args.compare_all)
        pair["seconds"] = round(time.perf_counter() - t0, 1)
        pairs.append(pair)
    record = {"schema": SCHEMA, "tool_version": TOOL_VERSION, "runs": args.runs,
              "compare_all": args.compare_all, "pairs": pairs,
              "acceptance": acceptance(pairs, args.min_pairs, args.min_per_type)}
    out = Path(args.out) if args.out else Path("validate_change_skip_{0}.json".format(
        time.strftime("%Y%m%dT%H%M%S")))
    out.write_text(json.dumps(record, indent=2, sort_keys=True, default=str), encoding="utf-8")
    acc = record["acceptance"]
    for pair in pairs:
        print("{0} -> {1}: {2}".format(Path(pair["a"]).name, Path(pair["b"]).name,
                                       pair.get("skipped") or pair["rules"]))
    print("{0:<14} {1:>6} {2:>10} {3:>8} {4:>8} {5:>6}  bar".format(
        "view type", "views", "would_skip", "same", "FALSE", "n/c"))
    for vtype, r in acc["by_type"].items():
        print("{0:<14} {1:>6} {2:>10} {3:>8} {4:>8} {5:>6}  {6}".format(
            vtype, r["views"], r["would_skip"], r["true_unchanged"], r["false_unchanged"],
            r["not_comparable"], "met" if r["meets_bar"] else "-"))
    print("overall: {0} compared, {1} false unchanged, bar {2}".format(
        acc["compared_would_skip"], acc["false_unchanged"],
        "MET" if acc["overall_meets_bar"] else "not met"))
    print("wrote {0}".format(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
