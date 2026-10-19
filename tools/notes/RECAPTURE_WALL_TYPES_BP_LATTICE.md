# Recapture: the six Wall Types BP plans, 2025-09-13..10-25

Views 4148625, 9468987, 9560929, 9594635, 9595813 (7 runs each: 09-13,
09-20, 09-27, 10-04, 10-11, 10-18, 10-25) and 9596211 (5 runs, 09-27..10-25):
**40 view-runs**, all `annotation_failed` / `annotation_lattice_mismatch`
(RUN_2025-02-03_to_2025-11-01_SERIES_SMALL_VIEWS.md §1, §2.4). Root:
`C:\Users\gmcdowell\Documents\VOP\Exports\KSRF\Hosp_Interior_AR` (renamed from
`Hops_Interior_AR` on 2026-10-19; every `run_meta.json` still records the old
path in `run_dir`, and stays that way).

Nothing here has run. Every step is a command for Greg's machine.

## 0. Before: the build

The branch carrying the probe and the fix (`f147884`, `43c23a3`, `147412d`)
must be what Revit loads. The recapture stamps every recaptured sidecar and
`run_meta.views[]` entry with the build that made it
(`registration_marks.recapture`, `recapture.exporter_version`); the fix is
`fix(capture)!`, so that build is a new MAJOR. Analysis compares the
recaptured views on that stamp, not on the run's `exporter_version`.

## 1. The probe, on the 2025-10-25 archive

Open the 10-25 archive (as the batch opened it: detached, all worksets open,
so the probe sees what the run saw). Paste
`tests/dynamo/probe_stage_a_lattice_overrun.py` into a Dynamo Python node:

```
IN[0]  C:\Users\gmcdowell\Documents\VOP\Probes\lattice_overrun
IN[1]  (empty: the six plans)
IN[2]  all
IN[3]  {"repo_root": "<checkout>", "expected_config_hash": "9400eaef"}
```

Read `verdict` in `probe_lattice_overrun_<clock>.json`
(tests/dynamo/PROBE_LATTICE_OVERRUN.md). The fix shipped assumes:

| variant | expected | if not |
|---|---|---|
| `as_is` | six `overrun`, first-attempt annotation 10121..13575 px tall as in §1.2 | the archive is not the failing one: stop |
| `section_markers_hidden` | six `fits`, `content_lost` all six | the markers are not the (whole) cause: the note's §1.2 needs revisiting; co-sizing still holds |
| `annotation_crop_on` | fits or not; `content_lost` | **if it fits on all six AND the offline pixel check shows nothing lost inside crop A, say so before recapturing**: it would keep the model lattice uncoarsened, and is a different fix |
| `co_sized` | six `fits`, `restore_faults` empty, `co_sizing.fit_px` 7111 / 9235 / 9227 / 7365 / 8781 / 5545 | a second overrun (`co_sizing.second_attempt` faulted) means the extent does not scale with PixelSize: do not recapture |

Every variant: `variant_record.took_effect` true and `restore_faults` empty,
or the probe's result is not evidence.

## 2. The recapture list

```powershell
$root = "C:\Users\gmcdowell\Documents\VOP\Exports\KSRF\Hosp_Interior_AR"
$runs = Get-ChildItem $root -Directory |
  Where-Object { $_.Name -match '^2025-(09-(13|20|27)|10-(04|11|18|25))__' } |
  ForEach-Object FullName
python tools\find_recapture_views.py $runs --out C:\temp\wtbp_finder --include-failed
```

`--include-failed` puts EVERY failed view of those runs in `recapture` (the
gated 3D views too), and `run_dir` is each run's recorded, pre-rename path.
Keep only the six plans, stamp the reason, and point `run_dir` at the folder
that exists:

```powershell
python -c @"
import json, os
SIX = {4148625, 9468987, 9560929, 9594635, 9595813, 9596211}
p = r'C:\temp\wtbp_finder\recapture_lists.json'
d = json.load(open(p))
for r in d['runs']:
    r['recapture'] = sorted(v for v in r['recapture'] if v in SIX
                            and any(f['view_id'] == v and 'annotation_lattice_mismatch'
                                    in str(f.get('reason')) for f in r['failed']))
    r['reason'] = 'annotation_lattice_mismatch'
    r['run_dir'] = r['run_dir'].replace('Hops_Interior_AR', 'Hosp_Interior_AR')
    assert os.path.normcase(os.path.normpath(r['run_dir'])) == \
        os.path.normcase(os.path.normpath(r['scanned_dir'])), (r['run_dir'], r['scanned_dir'])
    print(r['run_id'], r['recapture'])
json.dump(d, open(r'C:\temp\wtbp_finder\recapture_lists_wtbp.json', 'w'), indent=2)
"@
```

Expect seven lines, 40 ids: five per run on 09-13 and 09-20, six on the five
from 09-27. Anything else: stop and look.

If a stale `run_dir` slips through, `run_recapture` and
`plan_capture_batch.py` now REFUSE it, naming the recorded and the scanned
path (`147412d`); a message naming `Hops_Interior_AR` is that, not the fix.

## 3. The batch

Seven runs, seven archives: the pyRevit batch (BatchCapture), as the runs
were made.

```powershell
python tools\plan_capture_batch.py <Fingerprint manifest.csv> `
  --output-root C:\Users\gmcdowell\Documents\VOP\Exports `
  --recapture-lists C:\temp\wtbp_finder\recapture_lists_wtbp.json `
  --mode recapture --out C:\temp\wtbp_finder\capture_plan.json
```

Check `capture_plan.json`: seven `recapture` jobs, one per archive, each one
run with its ids; `unmatched_runs` empty. Then BatchCapture with that plan.
(Alternative per archive: open it, thinrunner IN[5] True, IN[7] the list;
runs of other archives are refused by title, which is expected.)

## 4. Verify

Per run, `run_meta.json`:

- `recaptures[-1]`: `reason "annotation_lattice_mismatch"`, `outcomes` all
  `success`, `refused_view_ids` empty;
- the six (five) entries of `views[]` `success`, stamped `recapture`.

Per view, the model sidecar's `registration_marks.co_sizing`:
`attempts 2`, `fit_px` as the probe gave it (for 10-25: the table in §1), and
`second_attempt.annotation_failure_reason` null; `capture_integrity.
capture_faults` empty in both sidecars.

## 5. Analysis, then the index

```powershell
foreach ($r in $runs) {
  python tools\register_stage_a_annotation.py "$r"
  python tools\stage_a_grid.py "$r"
  python tools\stage_a_kinds.py derive "$r"
}
python tools\stage_a_kinds.py inventory $runs
python tools\stage_a_grid_rollup.py $runs
python tools\find_recapture_views.py $runs --out C:\temp\wtbp_after
python tools\stage_a_runs_index.py --root C:\Users\gmcdowell\Documents\VOP\Exports
```

`wtbp_after`: the six `keep` in every run. The roll-up: the 40 rows gridded.
The index: `model_key` `KSRF/Hosp_Interior_AR`.

## What changes for analysis

The 40 view-runs go from not gridded to gridded, on a lattice 1.2-35.8 %
coarser than crop A alone gives them (LEVEL 3: 35.8 %); 09-06 and 11-01
keep crop A's own lattice. A series comparison across 09-06 -> 09-13 or
10-25 -> 11-01 compares two lattices of one view: the grid is in cells of
paper inches, so cell occupancy is comparable, but per-pixel measures are
not. `registration_marks.co_sizing` marks which.
