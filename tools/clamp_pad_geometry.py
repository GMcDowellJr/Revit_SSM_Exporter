"""ONE derivation of Revit ExportImage's aspect-clamp frame geometry.

Revit's ``ImageExportOptions``/``ExportImage`` refuses to export beyond an
aspect limit: it pads the SHORT axis symmetrically until the image reaches
the limit. The pad is recorded in no sidecar field, so every consumer that
turns a pixel into a view-local UV coordinate -- or a UV coordinate back
into a pixel -- has to model it.

Before this module the same five lines were written out at five call sites
(``tools/decode_stage_a_color_id.py``, ``tools/colorid_to_occupancy.py``,
``tools/link_identity_resolver.py``, ``tools/notes/g2_bbox_excursion_
report.py``, and the aspect-form validator in
``tools/analyze_stage_a_probe.py``). PR #200 landed D1 and D8 together for
exactly that reason -- the forward and inverse mappings had already drifted
apart once, and nothing composed them.

THE MODEL
---------
Pixels are square. The PADDED axis therefore carries more pixels than its
extent warrants, which makes its feet-per-pixel ratio SMALLER than the
truth; the UNPADDED axis's ratio IS the truth. So take the larger of the
two ratios, and the pad on the other axis follows from it, split evenly::

    fpp   = max(crop_u / fit_w, crop_v / fit_h)
    pad_x = (image_w - crop_u / fpp) / 2
    pad_y = (image_h - crop_v / fpp) / 2

Stretching u and v independently across the full image instead implies
non-square pixels and displaces every point on the padded axis.

WHY THE TWO DENOMINATORS DIFFER, AND WHY THAT IS DELIBERATE
------------------------------------------------------------
``measured_w``/``measured_h`` are the PRODUCER's own recorded export size
(``resolution.actual_w``/``actual_h``). feet_per_pixel is derived from
those, because they are the measurement the producer's own dimension check
verified. The pads are always measured against ``image_w``/``image_h``, the
file the caller is actually reading.

On any intact capture the two agree and the distinction is invisible. Where
they disagree -- a dim_check mismatch -- the pads go NEGATIVE, and that is
the SIGNAL, not an artefact: the image is not the size the producer says it
is, and no pad model can reconcile that silently. Callers that want to act
on it read the sign; ``tools/decode_stage_a_color_id.py`` compares the two
dimension pairs directly (its Guard 1) because the sign alone only ever
caught a file SMALLER than the sidecar records.

Passing neither ``measured_*`` makes both denominators ``image_w``/
``image_h``, which is the correct reading for a caller that has no
producer-recorded size to compare against.

NOT IN THIS MODULE
------------------
``tools/analyze_stage_a_probe.py``'s ``_frame_geometry()`` is deliberately
NOT a caller. It answers a different question -- "does this image's height
match what the 10:1 clamp predicts from the crop's aspect, and if not,
refuse to report a pad at all" -- and returns ``content_rect_px`` plus a
``clamp_matches_actual_height`` verdict rather than a feet-per-pixel. That
validation is the behaviour decode's Guard 2 was modelled ON. Replacing it
with this unconditional derivation would delete it.

Leaf module: standard library only. Imports nothing from ``vop_interwoven``
and nothing from ``tools``, so ``tools/link_identity_resolver.py`` can use
it without putting the package on that standalone tool's dependency path.
"""

__all__ = ["clamp_pad_geometry"]


def clamp_pad_geometry(bounds_uv, image_w, image_h, measured_w=None, measured_h=None):
    """Return ``(feet_per_pixel, pad_x, pad_y)`` for a crop rendered into an image.

    Args:
        bounds_uv: ``(xmin, ymin, xmax, ymax)`` -- the view-local rectangle
            the export was cropped to, in feet.
        image_w, image_h: the pixel dimensions of the file being read. The
            pads are always measured against these.
        measured_w, measured_h: the producer's own recorded export
            dimensions, when available. feet_per_pixel's denominators.
            Falsy (``None`` or ``0``) falls back to ``image_w``/``image_h``.

    Raises:
        ValueError: on a degenerate crop or a non-positive dimension. Both
            are divisors here, so there is no number to return and guessing
            one would be fabricating geometry.
    """
    xmin, ymin, xmax, ymax = (float(c) for c in bounds_uv)
    crop_u_ft = xmax - xmin
    crop_v_ft = ymax - ymin
    fit_w = float(measured_w) if measured_w else float(image_w)
    fit_h = float(measured_h) if measured_h else float(image_h)
    if (
        crop_u_ft <= 0.0
        or crop_v_ft <= 0.0
        or fit_w <= 0.0
        or fit_h <= 0.0
        or float(image_w) <= 0.0
        or float(image_h) <= 0.0
    ):
        raise ValueError(
            "degenerate capture geometry: crop {0}x{1} ft rendered into {2}x{3} px "
            "(feet-per-pixel measured against {4}x{5} px)".format(
                crop_u_ft, crop_v_ft, image_w, image_h, fit_w, fit_h))
    feet_per_pixel = max(crop_u_ft / fit_w, crop_v_ft / fit_h)
    pad_x = (float(image_w) - crop_u_ft / feet_per_pixel) / 2.0
    pad_y = (float(image_h) - crop_v_ft / feet_per_pixel) / 2.0
    return (feet_per_pixel, pad_x, pad_y)
