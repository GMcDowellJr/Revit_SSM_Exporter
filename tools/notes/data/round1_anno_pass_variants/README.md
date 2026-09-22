# Round-1 annotation-pass variant probe output (2026-09-22)

The JSON half of the round-1 run, as Greg supplied it. Committed because
`ROUND1_ANNO_PASS_VARIANTS_FINDINGS.md` quotes figures out of these files and a
report whose inputs are not in the repository cannot be re-derived or contested.

```
Elevation_CropActive_19293413.anno_pass_variants.json   combined report, 5 variants
Plan_CropActive_19290402.anno_pass_variants.json        combined report, 5 variants
Elevation_CropActive_19293413_anno.json                 the V3 annotation sidecar
Plan_CropActive_19290402_anno.json                      the V3 annotation sidecar
```

**The TIFFs are not here** — they run to hundreds of megabytes and do not belong
in a checkout. That is why the findings report marks measurements 2, 4 and 5 as
NOT MEASURED rather than reporting them: the fitted registration, the per-side
margins and the blend/grey/other split all need pixels.

**Both sidecars are the V3 variant**, identifiable by `frame_px` matching the
`frame_prime` record and `model_suppression_mode: "external"`. Production names
the annotation sidecar `<view>_<id>_anno.json` with no variant suffix — each
variant wrote into its own directory — so the variant is recoverable only from
the content, not the filename. Worth knowing before comparing them to a later run.

No V0 sidecar was supplied, which is why the report confirms F2's recovered state
(135/135 dimensions and so on) rather than the 2/135 → 135/135 delta.
