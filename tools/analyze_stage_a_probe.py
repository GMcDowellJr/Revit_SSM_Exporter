#!/usr/bin/env python3
"""External pixel analysis for Stage A Dynamo probe outputs."""
from __future__ import annotations

import argparse, hashlib, json, math, os, sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import numpy as np
except ImportError:  # Reported as an analysis limitation by the public API.
    np = None
try:
    from PIL import Image, ImageChops
    # Pillow refuses images past ~89 Mpx and RAISES past twice that, as a
    # defence against decompression-bomb input. A Stage A capture is our own
    # export and is legitimately far larger than that -- 15000 x 12356 is
    # 185 Mpx -- so the guard would reject exactly the large captures the
    # drift investigation exists to measure, and it did: several came back as
    # "0 export(s) measured".
    Image.MAX_IMAGE_PIXELS = None
except ImportError:  # Reported as an analysis limitation by the public API.
    Image = ImageChops = None

ANALYSIS_SCHEMA_VERSION = "1.0"
ANALYZER_VERSION = "3.0.0"
SUPPORTED_PROBE_SCHEMA_VERSIONS = {"1.0"}
SUPPORTED_PROBES = {
    "stage_a_image_alignment": "image_alignment",
    "stage_a_minimum_id_mutations": "minimum_id_mutations",
    "stage_a_model_linework": "model_linework",
    "stage_a_external_sources": "external_sources",
    "stage_a_graphics_semantics": "graphics_semantics",
}

DARK_THRESHOLD = 64
WHITE_THRESHOLD = 244
NEAR_WHITE_RESERVED_THRESHOLD = 224


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def resolve_path(json_path: Path, value: str | None) -> Path | None:
    if not value:
        return None
    p = Path(value)
    return p if p.is_absolute() else (json_path.parent / p)


def resolve_capture_tiff(json_path: Path, recorded: str | None) -> Path | None:
    """Find a sidecar's TIFF, preferring the file that sits beside it.

    A recorded absolute ``tiff_path`` is only valid while the capture stays
    where it was written. Capture sets get moved -- onto a NAS, onto another
    machine, out of a directory someone pointed at by mistake -- and at several
    hundred megabytes per TIFF they get moved often. The sidecar and its image
    travel together and share a stem, so the sibling is the more reliable
    reference and is tried first.

    Mirrors decode_stage_a_color_id._resolve_tiff_path, which already worked
    this way; this analyzer did not, and followed the stale absolute path into
    a TIFF_MISSING on a capture set that was entirely intact.
    """
    for candidate in (json_path.with_suffix('.tiff'), json_path.with_suffix('.tif')):
        if candidate.exists():
            return candidate
    resolved = resolve_path(json_path, recorded)
    if resolved is not None and resolved.exists():
        return resolved
    # Last resort: the recorded file name, in the sidecar's own directory.
    if recorded:
        beside = json_path.parent / Path(recorded).name
        if beside.exists():
            return beside
    return resolved


def load_rgb(path: Path) -> np.ndarray:
    with Image.open(path) as img:
        return np.asarray(img.convert('RGB'))


def base_info(path: Path) -> dict[str, Any]:
    return {'path': str(path), 'file_size_bytes': path.stat().st_size, 'sha256': sha256_file(path)}


def analyze_graphics_image(path: Path, assigned: dict[str, Any]) -> dict[str, Any]:
    arr = load_rgb(path)
    h, w = arr.shape[:2]
    flat = arr.reshape(-1, 3)
    colors, counts = np.unique(flat, axis=0, return_counts=True)
    expected = {tuple(v.get('rgb', [])): k for k, v in assigned.items() if v.get('rgb') is not None}
    expected_counts = {k: 0 for k in assigned.keys()}
    bg = off = near_white_bad = black_bad = 0
    unexpected: dict[str, int] = {}
    for color_arr, count_np in zip(colors, counts):
        c = tuple(int(x) for x in color_arr)
        count = int(count_np)
        if c in expected:
            expected_counts[expected[c]] += count
        elif c == (255, 255, 255):
            bg += count
        elif c[0] >= NEAR_WHITE_RESERVED_THRESHOLD and c[1] >= NEAR_WHITE_RESERVED_THRESHOLD and c[2] >= NEAR_WHITE_RESERVED_THRESHOLD:
            near_white_bad += count; off += count; unexpected[str(c)] = count
        else:
            off += count
            if c == (0, 0, 0):
                black_bad += count
            unexpected[str(c)] = count
    foreground = max(1, int(flat.shape[0]) - bg)
    out = base_info(path)
    out.update({
        'actual_dimensions': [int(w), int(h)], 'status': 'analyzed_external',
        'pixel_data_sha256': hashlib.sha256(arr.tobytes()).hexdigest(),
        'expected_palette_pixel_count': int(sum(expected_counts.values())),
        'expected_color_pixel_counts': expected_counts,
        'off_palette_foreground_pixel_count': int(off),
        'off_palette_foreground_percent': round(100.0 * off / foreground, 6),
        'background_pixel_count': int(bg), 'black_violation_pixel_count': int(black_bad),
        'near_white_violation_pixel_count': int(near_white_bad),
        'expected_colors_detected': int(sum(1 for c in expected_counts.values() if c > 0)),
        'missing_assigned_colors': sorted(k for k, c in expected_counts.items() if c <= 0),
        'unexpected_colors_top_50': sorted(unexpected.items(), key=lambda kv: kv[1], reverse=True)[:50],
        'visible_pixel_area_by_element': expected_counts,
    })
    return out



def recommend_graphics(results):
    rec = {'minimum_settings_required_for_exact_color_fidelity': [], 'settings_that_only_change_visibility_semantics': [], 'settings_unnecessary_in_tested_view': [], 'settings_blocked_by_view_template': [], 'questions_still_requiring_more_view_samples': []}
    metrics = {}
    for r in results:
        ia = r.get('image_analysis', {})
        if ia.get('off_palette_foreground_percent') is not None:
            metrics[r.get('variant')] = ia.get('off_palette_foreground_percent')
    if metrics:
        best = min(metrics, key=lambda k: metrics[k])
        rec['minimum_settings_required_for_exact_color_fidelity'].append('Lowest off-palette percentage in this run: {0} ({1}%). Compare cumulatively, not as a production recommendation.'.format(best, metrics[best]))
    for name in ('visible_filters_disabled', 'visibility_off_filters_disabled_diagnostic', 'neutral_phase_filter', 'current_stage_a_full_suppression'):
        if any(r.get('variant') == name for r in results):
            rec['settings_that_only_change_visibility_semantics'].append(name)
    for r in results:
        for d in r.get('mutation_diagnostics', []):
            if 'read-only' in d.get('message', '').lower() or 'template' in d.get('setting', ''):
                rec['settings_blocked_by_view_template'].append({'variant': r.get('variant'), 'diagnostic': d})
    rec['questions_still_requiring_more_view_samples'].append('Repeat the documented matrix; do not generalize from one view because filters, templates, phase filters, and halftone differ by view.')
    return rec


def _minimum_assigned(data: dict[str, Any]) -> dict[str, Any]:
    return ((data.get('assignment_set') or {}).get('assigned') or {})


def _minimum_image_path(json_path: Path, variant: dict[str, Any]) -> Path | None:
    ia = variant.get('image_analysis') or {}
    p = resolve_path(json_path, ia.get('path'))
    if p and p.exists():
        return p
    # Fallback for early/incomplete reports: derive the prescribed filename from
    # the JSON stem, resolution suffix, and variant name.
    name = variant.get('variant')
    if not name:
        return None
    stem = json_path.name
    marker = '.minimum_id_mutations.json'
    if stem.endswith(marker):
        candidate = json_path.with_name(stem[:-len(marker)] + f'.{name}.tiff')
        if candidate.exists():
            return candidate
    return p


def _minimum_normalize_analysis(analysis: dict[str, Any]) -> dict[str, Any]:
    out = dict(analysis)
    # Preserve the probe's field names while accepting graphics-analyzer aliases.
    out['file_sha256'] = out.get('file_sha256') or out.get('sha256')
    out['off_palette_foreground_pixels'] = out.get('off_palette_foreground_pixels', out.get('off_palette_foreground_pixel_count'))
    out['exact_expected_palette_pixel_count'] = out.get('exact_expected_palette_pixel_count', out.get('expected_palette_pixel_count'))
    out['assigned_colors_detected'] = out.get('assigned_colors_detected', out.get('expected_colors_detected'))
    out['visible_pixel_count_by_assigned_element'] = out.get('visible_pixel_count_by_assigned_element', out.get('visible_pixel_area_by_element'))
    out['black_violations'] = out.get('black_violations', out.get('black_violation_pixel_count'))
    out['near_white_violations'] = out.get('near_white_violations', out.get('near_white_violation_pixel_count'))
    unexpected = out.get('unexpected_rgb_values')
    if unexpected is None:
        unexpected = {k: v for k, v in out.get('unexpected_colors_top_50', [])}
    out['unexpected_rgb_values'] = unexpected
    return out


def _mutation_ok(variant: dict[str, Any]) -> bool:
    requested = set(variant.get('requested_mutations') or [])
    statuses = variant.get('mutations') or {}
    return all((statuses.get(mid) or {}).get('status') in ('APPLIED', 'ALREADY_MATCHED') for mid in requested)


def _mutation_failure_detail(variant: dict[str, Any]) -> dict[str, list[str]]:
    """Split a variant's not-applied requested mutations into template-control
    blockages vs everything else, so a probe-reported BLOCKED_BY_TEMPLATE
    status is distinguishable, with machine-readable evidence, from an
    unrelated mutation failure (FAILED/UNSUPPORTED/missing)."""
    requested = sorted(variant.get('requested_mutations') or [])
    statuses = variant.get('mutations') or {}
    blocked_by_template, other = [], []
    for mid in requested:
        m = statuses.get(mid) or {}
        if m.get('status') in ('APPLIED', 'ALREADY_MATCHED'):
            continue
        if m.get('status') == 'BLOCKED_BY_TEMPLATE' or m.get('template_controlled') is True:
            blocked_by_template.append(mid)
        else:
            other.append(mid)
    return {'blocked_by_template': blocked_by_template, 'other_failed': other}


def _variant_clean(variant: dict[str, Any]) -> bool:
    ia = variant.get('image_analysis') or {}
    status = ia.get('analysis_status') or ia.get('status')
    if status not in ('complete', 'analyzed_external'):
        return False
    if ia.get('off_palette_foreground_pixels') is None:
        return False
    if ia.get('unexpected_rgb_values') is None:
        return False
    return int(ia.get('off_palette_foreground_pixels')) == 0 and len(ia.get('unexpected_rgb_values') or {}) == 0


def recommend_minimum_mutations(variants: list[dict[str, Any]]) -> dict[str, Any]:
    rec = {
        'recommended_minimum': {'mutations': [], 'fidelity_status': 'INCONCLUSIVE', 'semantic_preservation_status': 'INCONCLUSIVE', 'resolution_status': 'INCONCLUSIVE', 'rollback_status': 'INCONCLUSIVE'},
        'required_for_color_fidelity': [],
        'required_for_model_only_scope': [],
        'prerequisite_only': [],
        'unnecessary_in_tested_view': [],
        'semantic_diagnostics_not_for_default': [],
        'blocked_or_unsupported': [],
        'view_types_still_required': ['floor_plan_active_crop', 'floor_plan_inactive_crop', 'rcp', 'section', 'elevation', 'detail_view'],
    }
    # Rollback status is evidence-derived from every executed variant's own
    # TransactionGroup rollback outcome, independent of whether any variant
    # was eligible to be the recommended minimum: a probe run can roll back
    # cleanly for all of them even when none qualifies as a recommendation,
    # so "no candidate selected" must never be reported as a rollback FAIL.
    # Every unskipped variant counts, including ones with no rollback_status
    # at all: an explicit FAIL always wins, PASS requires every variant to
    # explicitly say so, and anything short of that (missing evidence, an
    # explicit INCONCLUSIVE, or a mix) is INCONCLUSIVE -- never manufactured
    # as FAIL or PASS from incomplete evidence.
    executed_rollback_values = [v.get('rollback_status') for v in variants if not v.get('skipped')]
    if any(value == 'FAIL' for value in executed_rollback_values):
        rec['recommended_minimum']['rollback_status'] = 'FAIL'
    elif executed_rollback_values and all(value == 'PASS' for value in executed_rollback_values):
        rec['recommended_minimum']['rollback_status'] = 'PASS'
    eligible = [v for v in variants if not v.get('diagnostic') and _mutation_ok(v) and not any(((v.get('mutations') or {}).get(mid) or {}).get('classification') == 'semantic_diagnostic' for mid in (v.get('requested_mutations') or []))]
    clean = [v for v in eligible if _variant_clean(v)]
    if clean:
        best = min(clean, key=lambda v: (len(v.get('requested_mutations') or []), v.get('variant') or ''))
        rec['recommended_minimum']['mutations'] = list(best.get('requested_mutations') or [])
        rec['recommended_minimum']['fidelity_status'] = 'PASS'
        rec['recommended_minimum']['resolution_status'] = 'PASS' if (best.get('resolution') or {}).get('actual_width_px') else 'INCONCLUSIVE'
        rec['recommended_minimum']['semantic_preservation_status'] = 'INCONCLUSIVE'
    elif eligible:
        rec['recommended_minimum']['fidelity_status'] = 'FAIL'
    for v in variants:
        if v.get('diagnostic'):
            rec['semantic_diagnostics_not_for_default'].append(v.get('variant'))
        for mid, m in (v.get('mutations') or {}).items():
            if not m.get('requested'):
                continue
            status = m.get('status')
            if status in ('FAILED', 'BLOCKED_BY_TEMPLATE', 'UNSUPPORTED'):
                rec['blocked_or_unsupported'].append({'variant': v.get('variant'), 'mutation': mid, 'status': status})
    return rec

def analyze_minimum_id_mutations(json_path: Path, data: dict[str, Any]) -> list[str]:
    assigned = _minimum_assigned(data)
    summary = []
    baseline_hash = full_hash = None
    for v in data.get('variants', []):
        p = _minimum_image_path(json_path, v)
        if p and p.exists() and _mutation_ok(v):
            analysis = _minimum_normalize_analysis(analyze_graphics_image(p, assigned))
            existing = v.get('image_analysis') or {}
            existing.update(analysis)
            v['image_analysis'] = existing
            summary.append(f"minimum {v.get('variant')}: off_palette={existing.get('off_palette_foreground_percent')} unexpected={len(existing.get('unexpected_rgb_values') or {})}")
            if v.get('variant') in ('detached_ASF', 'faithful_baseline', 'reduced_candidate') and baseline_hash is None:
                baseline_hash = existing.get('pixel_data_sha256')
            if v.get('variant') == 'full_suppression_reference':
                full_hash = existing.get('pixel_data_sha256')
        else:
            v['image_analysis'] = {'path': str(p) if p else None, 'analysis_status': 'not_analyzed', 'status': 'error', 'error': 'referenced TIFF missing/path not provided or mutation attestation failed'}
    for v in data.get('variants', []):
        ia = v.get('image_analysis') or {}
        if baseline_hash and ia.get('pixel_data_sha256'):
            ia['pixel_difference_from_faithful_baseline'] = 0 if ia.get('pixel_data_sha256') == baseline_hash else 'DIFFERENT_PIXEL_HASH'
        if full_hash and ia.get('pixel_data_sha256'):
            ia['pixel_difference_from_full_suppression'] = 0 if ia.get('pixel_data_sha256') == full_hash else 'DIFFERENT_PIXEL_HASH'
    data['recommendation'] = recommend_minimum_mutations(data.get('variants', []))
    data['external_analysis_status'] = 'analyzed_external'
    return summary

def connected_components(mask: np.ndarray) -> dict[str, Any]:
    h, w = mask.shape
    if h * w > 4_000_000:
        return {'skipped': True, 'reason': 'image exceeds inexpensive component threshold'}
    seen = np.zeros_like(mask, dtype=bool)
    sizes = []
    ys, xs = np.nonzero(mask)
    for x, y in zip(xs.tolist(), ys.tolist()):
        if seen[y, x]:
            continue
        stack = [(x, y)]; seen[y, x] = True; count = 0
        while stack:
            px, py = stack.pop(); count += 1
            for nx, ny in ((px+1,py),(px-1,py),(px,py+1),(px,py-1)):
                if 0 <= nx < w and 0 <= ny < h and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True; stack.append((nx, ny))
        sizes.append(count)
    sizes.sort(reverse=True)
    return {'count': len(sizes), 'largest': sizes[:20], 'total_dark_component_pixels': int(sum(sizes))}


def analyze_linework_image(path: Path, reference_dims=None) -> dict[str, Any]:
    arr = load_rgb(path); h, w = arr.shape[:2]
    bg_mask = np.all(arr >= WHITE_THRESHOLD, axis=2)
    dark_mask = np.all(arr <= DARK_THRESHOLD, axis=2) & ~bg_mask
    gray_mask = (np.abs(arr[:,:,0].astype(int)-arr[:,:,1].astype(int)) <= 2) & (np.abs(arr[:,:,1].astype(int)-arr[:,:,2].astype(int)) <= 2) & ~bg_mask
    non_bg = ~bg_mask
    ys, xs = np.nonzero(non_bg)
    foreground = int(non_bg.sum()); gray = int(gray_mask.sum()); dark = int(dark_mask.sum())
    out = base_info(path)
    out.update({'dimensions':[int(w),int(h)], 'status':'analyzed_external',
        'dark_line_pixel_count': dark, 'grayscale_non_background_pixel_count': gray,
        'non_dark_gray_foreground_pixel_count': max(0, gray-dark), 'foreground_pixel_count': foreground,
        'unexpected_color_pixel_count': int((~gray_mask & non_bg).sum()), 'background_pixel_count': int(bg_mask.sum()),
        'non_background_content_rect': ([int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())] if xs.size else None),
        'connected_components': connected_components(dark_mask)})
    if reference_dims:
        out['reference_dimension_agreement'] = {'reference_dimensions': reference_dims, 'matches': list(reference_dims)==[int(w),int(h)]}
    return out


def classify_mode(mode_result, reference_dims):
    ia = mode_result.get('images',[{}])[0].get('analysis',{}) if mode_result.get('images') else {}
    if not ia:
        return {'candidate_status':'rejected','analysis_error':'mode produced no TIFF images to analyze','manual_review_required':True,'dimension_alignment':'fail','fill_contamination':'unknown','fill_contamination_evidence':{'reason':'missing image analysis'}}
    if ia.get('status') == 'error':
        return {'candidate_status':'rejected','analysis_error':ia.get('error'),'manual_review_required':True,'dimension_alignment':'fail','fill_contamination':'unknown','fill_contamination_evidence':{'reason':ia.get('error')}}
    dims_available = bool(ia.get('dimensions') and reference_dims)
    dims_match = bool(dims_available and ia.get('dimensions') == reference_dims)
    dark = int(ia.get('dark_line_pixel_count') or 0); unexpected = int(ia.get('unexpected_color_pixel_count') or 0); non_dark_gray = int(ia.get('non_dark_gray_foreground_pixel_count') or 0); foreground = int(ia.get('foreground_pixel_count') or 0)
    tol = max(10, int(round(0.001 * max(1, foreground))))
    fill_ok = 'acceptable' if unexpected == 0 and non_dark_gray <= tol else 'unacceptable'
    candidate = 'rejected' if mode_result.get('exceptions') or dark <= 0 else ('recommended' if fill_ok == 'acceptable' and dims_match else 'inconclusive')
    dimension_alignment = 'pass' if dims_match else ('fail' if dims_available else 'inconclusive')
    return {'internal_edges':'uncertain','hidden_back_edges':'uncertain','fill_contamination':fill_ok,'fill_contamination_evidence':{'unexpected_color_pixel_count':unexpected,'non_dark_gray_foreground_pixel_count':non_dark_gray,'non_dark_gray_tolerance':tol},'link_behavior':'requires_manual_review','dwg_behavior':'requires_manual_review','dimension_alignment':dimension_alignment,'candidate_status':candidate,'manual_review_required':True}


def write_diff(a: Path, b: Path, out: Path):
    with Image.open(a) as ia, Image.open(b) as ib:
        ima, imb = ia.convert('RGB'), ib.convert('RGB')
        if ima.size != imb.size:
            return {'created':False,'reason':'dimension mismatch','a_size':list(ima.size),'b_size':list(imb.size)}
        diff = ImageChops.difference(ima, imb); diff.save(out)
    return {'created':True,'path':str(out),'sha256':sha256_file(out)}




def first_image_path(json_path, mode_result):
    images = mode_result.get('images') or []
    if not images:
        return None
    return resolve_path(json_path, images[0].get('path'))

def repeatability(images):
    if len(images) < 2:
        return {'available': False, 'reason': 'fewer than two sequential exports'}
    a0 = (images[0].get('analysis') or {})
    a1 = (images[1].get('analysis') or {})
    return {
        'available': True,
        'same_sha256': a0.get('sha256') == a1.get('sha256'),
        'same_dimensions': a0.get('dimensions') == a1.get('dimensions'),
        'first_dimensions': a0.get('dimensions'),
        'second_dimensions': a1.get('dimensions'),
        'first_sha256': a0.get('sha256'),
        'second_sha256': a1.get('sha256'),
    }

def rank_modes(results):
    rows=[]
    for r in results:
        ia = r.get('images',[{}])[0].get('analysis',{}) if r.get('images') else {}
        rows.append({'mode':r.get('mode'),'candidate_status':r.get('classification',{}).get('candidate_status'),'dark_line_pixel_count':ia.get('dark_line_pixel_count'),'unexpected_color_pixel_count':ia.get('unexpected_color_pixel_count'),'non_dark_gray_foreground_pixel_count':ia.get('non_dark_gray_foreground_pixel_count'),'dimension_alignment':r.get('classification',{}).get('dimension_alignment')})
    return sorted(rows, key=lambda x:(x.get('candidate_status')!='recommended', -(x.get('dark_line_pixel_count') or 0), (x.get('unexpected_color_pixel_count') or 0)+(x.get('non_dark_gray_foreground_pixel_count') or 0)))


def bounds_tuple(b):
    return [b['u_min'], b['v_min'], b['u_max'], b['v_max']] if isinstance(b, dict) else b


def affine_fit(detections):
    usable=[d for d in detections if d.get('found_exact') and d.get('centroid_px') is not None]
    if len(usable)<4: return {'available':False,'reason':f'fewer than 4 markers with an exact color-match centroid ({len(usable)} available, {len(detections)} total detections including nearest-color fallbacks); an affine fit needs >=4 correspondences for 6 unknowns and fallback centroids are excluded because they are not reliable marker positions','markers_used':len(usable)}
    A=np.asarray([[d['expected_uv'][0],d['expected_uv'][1],1.0] for d in usable],float)
    bx=np.asarray([d['centroid_px'][0] for d in usable],float); by=np.asarray([d['centroid_px'][1] for d in usable],float)
    cx=np.linalg.lstsq(A,bx,rcond=None)[0]; cy=np.linalg.lstsq(A,by,rcond=None)[0]
    per=[]; mags=[]
    for det in usable:
        u,v=det['expected_uv']; px=float(cx[0]*u+cx[1]*v+cx[2]); py=float(cy[0]*u+cy[1]*v+cy[2])
        rx=det['centroid_px'][0]-px; ry=det['centroid_px'][1]-py; mag=math.hypot(rx,ry); mags.append(mag)
        per.append({'name':det['name'],'predicted_px_affine_fit':[px,py],'residual_px':[rx,ry],'residual_magnitude_px':mag})
    return {'available':True,'method':'least_squares_affine_6dof','form':'px_x = a*u + b*v + c; px_y = d*u + e*v + f','markers_used':len(usable),'matrix':{'a':float(cx[0]),'b':float(cx[1]),'c':float(cx[2]),'d':float(cy[0]),'e':float(cy[1]),'f':float(cy[2])},'per_marker_residual_px':per,'max_residual_px':max(mags) if mags else None}


def analyze_alignment_image(path: Path, markers, bounds):
    arr=load_rgb(path); h,w=arr.shape[:2]; flat=arr.reshape(-1,3)
    colors, counts=np.unique(flat, axis=0, return_counts=True); bg=colors[int(np.argmax(counts))]
    non_bg=np.any(arr != bg, axis=2); ys,xs=np.nonzero(non_bg); content=[int(xs.min()),int(ys.min()),int(xs.max()),int(ys.max())] if xs.size else None
    res={'path':str(path),'file_size':path.stat().st_size,'sha256':sha256_file(path),'status':'analyzed_external','actual_width':int(w),'actual_height':int(h),'distinct_colors':int(len(colors)),'background_rgb':[int(x) for x in bg],'content_rect_px':content}
    bt=bounds_tuple(bounds); dets=[]
    for m in markers or []:
        target=np.asarray(m['rgb'], dtype=np.uint8); exact=np.all(arr==target, axis=2); yy,xx=np.nonzero(exact)
        found=bool(xx.size)
        if found:
            centroid=[float(xx.mean()), float(yy.mean())]; nearest_rgb=list(map(int,target)); best_d=0
        else:
            dist=np.sum((arr.astype(int)-target.astype(int))**2, axis=2); y,x=np.unravel_index(int(np.argmin(dist)), dist.shape); centroid=[float(x),float(y)]; nearest_rgb=[int(v) for v in arr[y,x]]; best_d=int(dist[y,x])
        predicted=residual=None
        if centroid and bt:
            u0,v0,u1,v1=bt; predicted=[((m['uv'][0]-u0)/(u1-u0))*float(w)-0.5, ((v1-m['uv'][1])/(v1-v0))*float(h)-0.5]; residual=[centroid[0]-predicted[0], centroid[1]-predicted[1]]
        dets.append({'name':m['name'],'expected_uv':m['uv'],'rgb':m['rgb'],'found_exact':found,'centroid_px':centroid,'nearest_rgb':nearest_rgb,'nearest_distance_sq':best_d,'predicted_px_center_equation':predicted,'residual_px':residual})
    res['markers']=dets; res['affine_fit_residual']=affine_fit(dets); res['marker_analysis_available']=bool(dets); res['missing_markers']=[d['name'] for d in dets if not d['found_exact']]
    res['padding_px']={'left':content[0] if content else 0,'top':content[1] if content else 0,'right':(w-content[2]-1) if content else 0,'bottom':(h-content[3]-1) if content else 0}
    if bt:
        res['tested_mapping']='u = u_min + (x + 0.5) * UV_width / image_width; v = v_max - (y + 0.5) * UV_height / image_height'; res['mapping_frame']='full_exported_image_frame_not_content_rect'; res['x_axis_direction']='+U to the right' if bt[2]>bt[0] else 'unknown'; res['y_axis_direction']='-V downward / +V upward' if bt[3]>bt[1] else 'unknown'
        if dets and all(d['residual_px'] for d in dets): res['max_marker_residual_px']=max(math.hypot(*d['residual_px']) for d in dets)
    return res




def alignment_evidence_status(data):
    exports = data.get('exports', {})
    exported_images = [img for exp in exports.values() for img in exp.get('images', [])]
    marker_count = len(data.get('calibration_markers', []))
    previous = data.get('evidence_status', {}) if isinstance(data.get('evidence_status'), dict) else {}
    markers_requested = bool(previous.get('markers_requested', marker_count > 0))
    all_have_image_analysis = bool(exported_images) and all(img.get('status') == 'analyzed_external' for img in exported_images)
    all_have_marker_analysis = bool(markers_requested) and bool(marker_count) and bool(exported_images) and all(img.get('marker_analysis_available') for img in exported_images)
    any_missing = any(bool(img.get('missing_markers')) for img in exported_images)
    return {
        'all_have_image_analysis': all_have_image_analysis,
        'all_have_marker_analysis': all_have_marker_analysis,
        'markers_requested': markers_requested,
        'marker_count': marker_count,
        'any_missing_markers': any_missing,
        'status': 'analyzed_external' if all_have_image_analysis else 'incomplete_external_analysis',
    }

def content_width_px(model_img):
    content = model_img.get('content_rect_px') if model_img else None
    if content and len(content) == 4:
        return max(1, int(content[2]) - int(content[0]) + 1)
    return int(model_img.get('actual_width') or 0) if model_img else 0


def placement(model_bounds, canvas_bounds, model_img):
    content_width = content_width_px(model_img)
    if not model_bounds or not canvas_bounds or not model_img or not content_width:
        return {'available': False, 'reason': 'missing model/canvas bounds or content image dimensions'}
    mb, cb = bounds_tuple(model_bounds), bounds_tuple(canvas_bounds)
    density = float(content_width) / max(1e-9, mb[2] - mb[0])
    canvas_w = (cb[2] - cb[0]) * density
    canvas_h = (cb[3] - cb[1]) * density
    off_x = (mb[0] - cb[0]) * density
    off_y = (cb[3] - mb[3]) * density
    vals = [canvas_w, canvas_h, off_x, off_y]
    rounding = [abs(v - round(v)) for v in vals]
    return {'available': True, 'observed_px_per_model_unit': density, 'density_source_content_width_px': int(content_width), 'tiff_actual_width_px': int(model_img.get('actual_width') or 0), 'canvas_pixel_dimensions_float': [canvas_w, canvas_h], 'model_image_offset_float': [off_x, off_y], 'offset_integral': [rounding[2] < 1e-6, rounding[3] < 1e-6], 'max_rounding_error_px': max(rounding), 'max_rounding_error_model_units': max(rounding) / density, 'lossless_padding_sufficient': max(rounding) < 1e-6, 'resampling_required_if_exact_canvas_needed': max(rounding) >= 1e-6}


def _resolution_identity(run):
    """Return the producer contract fields that identify one resolution case."""
    images = run.get('images') or []
    resolution = (images[0].get('resolution') or {}) if images else (run.get('resolution') or {})
    return tuple(resolution.get(key) for key in
                 ('policy', 'target_dpi', 'requested_width_px', 'accepted_width_px', 'predicted_height_px'))

def _enrich_report(json_path: Path, data: dict[str, Any], probe_id: str) -> list[str]:
    """Run the existing probe-specific pixel calculations in place."""
    name = probe_id.lower()
    summary=[]
    if 'minimum_id_mutations' in name or name == 'stage_a_minimum_id_mutations':
        summary.extend(analyze_minimum_id_mutations(json_path, data))

    elif 'graphics_semantics' in name:
        for v in data.get('variants',[]):
            p=resolve_path(json_path, v.get('export',{}).get('path'))
            if p and p.exists():
                v['image_analysis']=analyze_graphics_image(p, v.get('assigned_elements',{})); summary.append(f"graphics {v.get('variant')}: off_palette={v['image_analysis'].get('off_palette_foreground_percent')} black={v['image_analysis'].get('black_violation_pixel_count')}")
            else:
                v['image_analysis']={'path': str(p) if p else None, 'status':'error', 'error':'referenced TIFF missing or path not provided'}
        data['recommendation']=recommend_graphics(data.get('variants',[]))

    elif 'model_linework' in name:
        references = data.get('reference_runs') or []
        if not references and data.get('reference_element_id_tiff'):
            references = [data['reference_element_id_tiff']]
        reference_dims = {}
        for ref in references:
            ref_dims = None
            for img in ref.get('images',[]):
                p=resolve_path(json_path, img.get('path'))
                if p and p.exists(): img['analysis']=analyze_linework_image(p); ref_dims=img['analysis'].get('dimensions') or ref_dims
                else: img['analysis']={'path': str(p) if p else None, 'status':'error', 'error':'referenced TIFF missing or path not provided'}
            if ref.get('images'):
                ref['sequential_export_repeatability'] = repeatability(ref.get('images', []))
            if ref_dims:
                reference_dims[_resolution_identity(ref)] = ref_dims
        for m in data.get('modes',[]):
            ref_dims = reference_dims.get(_resolution_identity(m))
            for img in m.get('images',[]):
                p=resolve_path(json_path, img.get('path'))
                if p and p.exists(): img['analysis']=analyze_linework_image(p, ref_dims)
                else: img['analysis']={'path': str(p) if p else None, 'status':'error', 'error':'referenced TIFF missing or path not provided'}
            if m.get('images'):
                m['sequential_export_repeatability'] = repeatability(m.get('images', []))
            m['classification']=classify_mode(m, ref_dims); summary.append(f"linework {m.get('mode')}: non_dark_gray={m['images'][0]['analysis'].get('non_dark_gray_foreground_pixel_count') if m.get('images') else None} unexpected={m['images'][0]['analysis'].get('unexpected_color_pixel_count') if m.get('images') else None}")
        data['difference_images']=[]
        modes_by_resolution = {}
        for mode in data.get('modes', []):
            p = first_image_path(json_path, mode)
            if p and p.exists():
                modes_by_resolution.setdefault(_resolution_identity(mode), []).append((mode, p))
        for resolution_id, modes_with_images in modes_by_resolution.items():
            if len(modes_with_images) < 2:
                continue
            ref_mode, refp = modes_with_images[0]
            for m, p in modes_with_images[1:]:
                suffix = str(resolution_id[1] or resolution_id[3] or 'resolution')
                out=json_path.parent / f"{json_path.stem}.{suffix}.{m.get('mode')}_minus_{ref_mode.get('mode')}.diff.tiff"; data['difference_images'].append({'mode':m.get('mode'),'against':ref_mode.get('mode'),'resolution_identity':resolution_id,'diff':write_diff(refp,p,out)})
        data['ranked_modes']=rank_modes(data.get('modes',[]))
    elif 'external_sources' in name:
        for variant in data.get('variants', []):
            export = variant.get('export') or {}
            p = resolve_path(json_path, export.get('path'))
            assigned = {str(a.get('assignment_key', i)): a
                        for i, a in enumerate(variant.get('assignments') or [])
                        if a.get('rgb') is not None}
            if p and p.exists():
                variant['image_analysis'] = analyze_graphics_image(p, assigned)
                summary.append(f"external {variant.get('variant')}: expected={variant['image_analysis'].get('expected_palette_pixel_count')}")
            else:
                variant['image_analysis'] = {'path': str(p) if p else None, 'status': 'error',
                                             'error': 'referenced TIFF missing or path not provided'}
    elif 'alignment' in name:
        markers=data.get('calibration_markers',[])
        for mode, exp in data.get('exports',{}).items():
            bounds=exp.get('target_bounds_uv')
            for img in exp.get('images',[]):
                p=resolve_path(json_path, img.get('path'))
                if p and p.exists():
                    eff = img.get('effective_pixel_size')
                    resolution = img.get('resolution')
                    marker_units = img.get('marker_residual_units')
                    img.clear()
                    img.update(analyze_alignment_image(p, markers, bounds))
                    img['effective_pixel_size'] = eff
                    if resolution is not None:
                        img['resolution'] = resolution
                    if marker_units is not None:
                        img['marker_residual_units'] = marker_units
                    img['pixel_size_equals_actual_width'] = (img.get('actual_width') == eff)
                else:
                    img.clear(); img.update({'path': str(p) if p else None, 'status':'error', 'error':'referenced TIFF missing or path not provided'})
            vals=[im.get('affine_fit_residual',{}).get('max_residual_px') for im in exp.get('images',[]) if im.get('affine_fit_residual',{}).get('available')]
            if vals: summary.append(f"alignment {mode}: affine_max={vals[0]:.6f}")
        model_img = None
        for preferred in ('model_bounds', 'original'):
            for key, export in data.get('exports', {}).items():
                if key.split('.', 1)[0] != preferred:
                    continue
                images = export.get('images', [])
                if images and images[0].get('status') == 'analyzed_external':
                    model_img = images[0]
                    break
            if model_img:
                break
        if model_img:
            data['model_to_canvas_placement'] = placement(data.get('bounds', {}).get('pre_annotation_model_uv'), data.get('bounds', {}).get('canvas_uv'), model_img)
        elif 'model_to_canvas_placement' not in data:
            data['model_to_canvas_placement'] = {'available': False, 'reason': 'no analyzed model export available'}
        data['evidence_status'] = alignment_evidence_status(data)
    return summary


def _probe_identity(data: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """Return explicit identity, family and native report; never guess from a filename."""
    explicit = data.get('probe_id')
    native = data.get('native_report') if isinstance(data.get('native_report'), dict) else data
    embedded = (native.get('probe') or {}).get('name') if isinstance(native.get('probe'), dict) else None
    probe_id = str(explicit or embedded or '').lower()
    if not probe_id:
        raise ValueError('MISSING_PROBE_ID: report requires probe_id or probe.name')
    if explicit and embedded and str(explicit).lower() != str(embedded).lower():
        raise ValueError('INCONSISTENT_PROBE_ID: probe_id and native probe.name disagree')
    if probe_id not in SUPPORTED_PROBES:
        raise ValueError('UNKNOWN_PROBE_TYPE: {0}'.format(probe_id))
    version = data.get('probe_schema_version')
    if version is not None and str(version) not in SUPPORTED_PROBE_SCHEMA_VERSIONS:
        raise ValueError('UNSUPPORTED_PROBE_SCHEMA_VERSION: {0}'.format(version))
    return probe_id, SUPPORTED_PROBES[probe_id], native


def _status(value: Any, good: set[str], bad: set[str]) -> str:
    normalized = str(value or '').lower()
    if normalized in good:
        return 'PASS'
    if normalized in bad:
        return 'FAIL'
    return 'INCONCLUSIVE'


def _check(name: str, status: str, reasons: list[str], evidence: Any = None) -> dict[str, Any]:
    return {'check_id': name, 'status': status, 'reason_codes': sorted(set(reasons)), 'evidence': evidence}


def _requested_names(data: dict[str, Any], native: dict[str, Any], family: str) -> list[str]:
    settings = data.get('requested_settings') or {}
    keys = {'minimum_id_mutations': ('selection', 'variants'), 'model_linework': ('display_cases', 'rendering_modes'),
            'external_sources': ('selection', 'variants'), 'image_alignment': ('modes', 'mode'),
            'graphics_semantics': ('selection', 'variants')}.get(family, ())
    value = next((settings.get(k) for k in keys if settings.get(k) is not None), None)
    if value is None:
        inputs = native.get('inputs') or {}
        value = next((inputs.get(k) for k in keys if inputs.get(k) is not None), None)
    if value is None or value == 'all':
        return []
    return [value] if isinstance(value, str) else list(value)


def _actual_names(native: dict[str, Any], family: str) -> list[str]:
    if family == 'image_alignment':
        return sorted({k.split('.', 1)[0] for k, v in (native.get('exports') or {}).items()
                       if not v.get('skipped')})
    key, label = ('modes', 'mode') if family == 'model_linework' else ('variants', 'variant')
    return [v.get(label) for v in native.get(key, []) if not v.get('skipped') and v.get(label)]


def _alignment_coverage(data: dict[str, Any], native: dict[str, Any]) -> dict[str, Any]:
    """Check the mode × resolution cases requested by the alignment envelope."""
    settings = data.get('requested_settings') or {}
    mode_value = settings.get('mode', settings.get('modes'))
    resolution_value = settings.get('resolution_cases')
    exports = native.get('exports') or {}
    actual = sorted(key for key, export in exports.items() if not export.get('skipped'))
    actual_modes = sorted({key.split('.', 1)[0] for key in actual})
    actual_resolutions = sorted({key.split('.', 1)[1] for key in actual if '.' in key})
    modes = (['original', 'model_bounds', 'canvas_bounds'] if mode_value == 'all' else
             ([] if mode_value is None else ([mode_value] if isinstance(mode_value, str) else list(mode_value))))
    resolutions = ([] if resolution_value in (None, 'all') else
                   ([resolution_value] if isinstance(resolution_value, str) else list(resolution_value)))
    if modes and resolutions:
        requested = sorted('{0}.{1}'.format(mode, resolution) for mode in modes for resolution in resolutions)
        missing = sorted(set(requested) - set(actual))
    elif modes:
        requested, missing = sorted(modes), sorted(set(modes) - set(actual_modes))
    elif resolutions:
        requested = sorted(resolutions)
        missing = sorted(set(resolutions) - set(actual_resolutions))
    else:
        requested, missing = [], []
    status = 'FAIL' if missing else ('PASS' if actual else 'INCONCLUSIVE')
    return _check('requested_case_coverage', status,
                  ['REQUESTED_CASE_NOT_ANALYZED'] if missing else ([] if actual else ['NO_CASES_ANALYZED']),
                  {'requested': requested, 'analyzed': actual, 'not_analyzed': missing,
                   'requested_modes': modes, 'requested_resolution_cases': resolutions})


def _external_variant_visual_status(variant: dict[str, Any]) -> str:
    """Derive tri-state (plus UNSUPPORTED) visual evidence from refreshed
    external TIFF metrics.

    ``linked_per_element_linkelementid_coloring`` is handled separately from
    every other source-family variant: the campaign established that a clean,
    non-exceptional False return from the LinkElementId override API is an
    explicit, expected capability finding in the current Revit/API
    environment - not a required Stage A PASS criterion - and must read as
    'UNSUPPORTED', never collapsed into the same 'FAIL' a real unexpected
    exception produces. See probe_stage_a_external_sources.py's
    LINK_OVERRIDE_* status vocabulary, recorded per-assignment as
    ``link_override_status``.
    """
    if variant.get('exceptions') or variant.get('conclusion') == 'FAIL':
        return 'FAIL'
    analysis = variant.get('image_analysis') or {}
    if (analysis.get('status') not in ('analyzed_external', 'complete') or
            not _artifact_dimensions(analysis)):
        return 'INCONCLUSIVE'
    name = variant.get('variant')
    if name == 'forced_linked_override_failure_hide_instance_fallback':
        return 'PASS' if variant.get('hidden_link_fallback') else 'FAIL'
    assignments = variant.get('assignments') or []
    if not assignments:
        return 'INCONCLUSIVE'
    counts = analysis.get('expected_color_pixel_counts') or {}
    if name == 'linked_per_element_linkelementid_coloring':
        link_items = [item for item in assignments if item.get('source_type') == 'LINK']
        if not link_items:
            return 'INCONCLUSIVE'
        statuses = {item.get('link_override_status') for item in link_items}
        if 'FAILED' in statuses:
            return 'FAIL'
        if None in statuses:
            # Missing field: an older/legacy report shape. Fall back to the
            # generic paint_success-based read rather than asserting a
            # capability determination the report never actually recorded.
            if not any(item.get('paint_success') for item in link_items):
                return 'INCONCLUSIVE'
        elif statuses <= {'APPLIED'}:
            rendered = any(int(counts.get(str(item.get('assignment_key')), 0) or 0) > 0 for item in link_items)
            return 'PASS' if rendered else 'INCONCLUSIVE'
        elif 'UNSUPPORTED' in statuses:
            return 'UNSUPPORTED'
        rendered = any(int(counts.get(str(item.get('assignment_key')), 0) or 0) > 0 for item in link_items)
        return 'PASS' if rendered else 'INCONCLUSIVE'
    required_sources = {item.get('source_type') for item in assignments if item.get('source_type')}
    for source in required_sources:
        source_items = [item for item in assignments if item.get('source_type') == source]
        if not any(item.get('paint_success') for item in source_items):
            return 'FAIL'
        if not any(int(counts.get(str(item.get('assignment_key')), 0) or 0) > 0 for item in source_items):
            return 'INCONCLUSIVE'
    return 'PASS' if required_sources else 'INCONCLUSIVE'


def _safety_checks(data: dict[str, Any], native: dict[str, Any]) -> list[dict[str, Any]]:
    execution = data.get('execution_status')
    checks = [_check('raw_execution', _status(execution, {'completed'}, {'failed'}),
                     [] if execution == 'completed' else ['RAW_EXECUTION_FAILED' if execution == 'failed' else 'RAW_EXECUTION_STATUS_MISSING'], execution)]
    variants = native.get('variants') or native.get('modes') or []
    executed = [v for v in variants if not v.get('skipped')]
    raw_rb = data.get('rollback_status')
    rb_values = [v.get('rollback_status') or (v.get('transaction_group') or {}).get('rollback_status') for v in executed]
    rb_statuses = [_status(v, {'pass', 'succeeded', 'rolled_back', 'rolledback'}, {'fail', 'failed', 'rollback_failed'}) for v in rb_values]
    rb = _status(raw_rb, {'succeeded', 'pass', 'rolled_back'}, {'failed', 'fail'})
    if rb == 'INCONCLUSIVE' and rb_statuses:
        rb = 'FAIL' if 'FAIL' in rb_statuses else ('PASS' if all(s == 'PASS' for s in rb_statuses) else 'INCONCLUSIVE')
    checks.append(_check('rollback', rb, [] if rb == 'PASS' else ['ROLLBACK_FAILED' if rb == 'FAIL' else 'ROLLBACK_EVIDENCE_MISSING'],
                         {'aggregate': raw_rb, 'variant_values': rb_values}))
    restoration = _status(data.get('state_restoration_status'), {'restored'}, {'not_restored'})
    state_values = [(v.get('state') or {}).get('captured_state_equal_after_rollback') for v in executed]
    if restoration == 'INCONCLUSIVE' and state_values:
        restoration = 'FAIL' if False in state_values else ('PASS' if all(v is True for v in state_values) else 'INCONCLUSIVE')
    checks.append(_check('state_restoration', restoration, [] if restoration == 'PASS' else
                         ['STATE_RESTORATION_FAILED' if restoration == 'FAIL' else 'STATE_RESTORATION_EVIDENCE_MISSING'], state_values))
    return checks


def _artifact_dimensions(analysis: dict[str, Any]) -> list[int] | None:
    dims = analysis.get('actual_dimensions') or analysis.get('dimensions')
    if dims:
        return [int(dims[0]), int(dims[1])]
    if analysis.get('actual_width') and analysis.get('actual_height'):
        return [int(analysis['actual_width']), int(analysis['actual_height'])]
    return None


def _comparison_reference(data: dict[str, Any]) -> str | None:
    """The job's configured comparison_reference, preferring the manifest job
    record's full as-configured settings (job_requested_settings) over the
    envelope's requested_settings, which correctly no longer carries
    analysis-only keys once the adapter strips them before dispatch."""
    settings = data.get('job_requested_settings')
    if settings is None:
        settings = data.get('requested_settings')
    return (settings or {}).get('comparison_reference')


def _resolve_comparison_reference(data: dict[str, Any], native: dict[str, Any], family: str) -> dict[str, Any] | None:
    """Implement the ``comparison_reference`` resolution contract.

    ``comparison_reference`` names another variant/mode (e.g. ``detached_AS``)
    this job's result should be compared against. It must never be inert
    metadata: this looks for matching, complete evidence *within this same
    probe report* and reports an explicit resolved/not-resolved outcome with
    provenance. When the reference lives in a different job's report (as for
    the Stage 1 attached_AS -> detached_AS mutation closure, which runs as two
    separate probe invocations), in-report resolution correctly cannot
    succeed; that cross-job resolution is instead a deterministic
    campaign-level closure decision (see campaign_planner.py
    conditional_fallbacks/closures) - a separation the acceptance record makes
    explicit via ``resolved_in_this_report: false`` rather than silently
    passing or hiding the request.
    """
    ref = _comparison_reference(data)
    if not ref:
        return None
    variants = native.get('variants') or native.get('modes') or []
    key = 'mode' if family == 'model_linework' else 'variant'
    match = next((v for v in variants if v.get(key) == ref), None)
    if match is None:
        # Not resolvable from this report alone is expected when the
        # reference is a separate, not-yet-run job (e.g. Stage 1's
        # attached_AS -> detached_AS fallback). That is a campaign-level
        # closure decision, not evidence this job's own acceptance should be
        # gated on - so it is reported for provenance/visibility only and
        # deliberately excluded from the PASS/FAIL/INCONCLUSIVE rollup.
        return _check('comparison_reference_resolution', 'NOT_APPLICABLE', [],
                       {'requested_reference': ref, 'resolved_in_this_report': False,
                        'note': 'cross-job resolution, if configured, is a campaign-level closure decision'})
    ia = match.get('image_analysis') or {}
    dims = _artifact_dimensions(ia)
    attested = family != 'minimum_id_mutations' or _mutation_ok(match)
    provenance = {'requested_reference': ref, 'resolved_in_this_report': True,
                  'reference_artifact_path': ia.get('path'),
                  'reference_artifact_sha256': ia.get('sha256') or ia.get('pixel_data_sha256')}
    if not (dims and attested):
        return _check('comparison_reference_resolution', 'INCONCLUSIVE', ['COMPARISON_REFERENCE_EVIDENCE_INCOMPLETE'], provenance)
    return _check('comparison_reference_resolution', 'PASS', [], provenance)


def _family_checks(data: dict[str, Any], native: dict[str, Any], family: str) -> list[dict[str, Any]]:
    checks = []
    reference_check = _resolve_comparison_reference(data, native, family)
    if reference_check is not None:
        checks.append(reference_check)
    if family == 'image_alignment':
        checks.append(_alignment_coverage(data, native))
    else:
        requested, actual = _requested_names(data, native, family), _actual_names(native, family)
        if family == 'external_sources':
            # A requested external-source variant that is present but skipped
            # for a diagnosed, explicit reason (e.g. a supplied DWG excluded
            # because ViewSpecific == True, or no candidates of that source
            # type were discoverable) is a complete, non-blocking conclusion -
            # the external_source_{host,link,dwg} checks below grade it
            # NOT_APPLICABLE. requested_case_coverage must not contradict that
            # by treating the same skip as a missing/unanalyzed case; only a
            # variant genuinely absent from the report counts as missing here,
            # and "covered but only via an explicit skip" must not fall back
            # to the empty-`actual` NO_CASES_ANALYZED reason code either.
            covered = {v.get('variant') for v in (native.get('variants') or [])}
            missing = sorted(set(requested) - covered) if requested else []
            coverage_status = 'FAIL' if missing else ('PASS' if covered else 'INCONCLUSIVE')
            reasons = ['REQUESTED_CASE_NOT_ANALYZED'] if missing else ([] if covered else ['NO_CASES_ANALYZED'])
        else:
            missing = sorted(set(requested) - set(actual))
            coverage_status = 'FAIL' if missing else ('PASS' if actual else 'INCONCLUSIVE')
            reasons = ['REQUESTED_CASE_NOT_ANALYZED'] if missing else ([] if actual else ['NO_CASES_ANALYZED'])
        checks.append(_check('requested_case_coverage', coverage_status, reasons,
                             {'requested': requested, 'analyzed': actual, 'not_analyzed': missing}))

    if family in ('minimum_id_mutations', 'external_sources', 'graphics_semantics'):
        variants = native.get('variants') or []
        failed_attestation, blocked_by_template_only, missing_tiffs, dimension_mismatch, contaminated, evaluated = [], [], [], [], [], []
        attestation_detail = []
        for variant in variants:
            if variant.get('skipped'):
                continue
            name = variant.get('variant')
            attestation_failed = (family == 'minimum_id_mutations' and not _mutation_ok(variant)) or variant.get('mutation_attestation_passed') is False
            if attestation_failed:
                failed_attestation.append(name)
                if family == 'minimum_id_mutations':
                    detail = _mutation_failure_detail(variant)
                    attestation_detail.append({'variant': name, **detail})
                    if detail['blocked_by_template'] and not detail['other_failed']:
                        blocked_by_template_only.append(name)
                # A TIFF was, by probe design, never exported/compared for a
                # variant whose mutation attestation failed - it is not
                # evidence of a missing/bad TIFF, so it must not be folded
                # into required_tiffs/dimensions/palette_fidelity below.
                continue
            ia = variant.get('image_analysis') or {}
            dims = _artifact_dimensions(ia)
            if not dims:
                missing_tiffs.append(name)
                continue
            evaluated.append(name)
            resolution = variant.get('resolution') or (variant.get('export') or {}).get('resolution') or {}
            required_w = resolution.get('accepted_width_px') or resolution.get('requested_width_px')
            required_h = resolution.get('predicted_height_px')
            if (required_w and dims[0] != int(required_w)) or (required_h and dims[1] != int(required_h)):
                dimension_mismatch.append({'case': name, 'required': [required_w, required_h], 'actual': dims})
            if ia.get('off_palette_foreground_pixel_count', ia.get('off_palette_foreground_pixels', 0)) not in (None, 0):
                contaminated.append(name)
        attestation_reasons = []
        if failed_attestation:
            attestation_reasons.append('MUTATION_ATTESTATION_FAILED')
            # A clean template-control blockage (every unmet mutation on that
            # variant is BLOCKED_BY_TEMPLATE, none failed for an unrelated
            # reason) gets its own reason code so a planner can branch on it
            # deterministically without mistaking a confounded/unrelated
            # failure for a pure template block.
            if blocked_by_template_only and len(blocked_by_template_only) == len(failed_attestation):
                attestation_reasons.append('MUTATION_BLOCKED_BY_TEMPLATE_ONLY')
        checks.append(_check('mutation_attestation', 'FAIL' if failed_attestation else ('PASS' if variants else 'INCONCLUSIVE'),
                             attestation_reasons, {'failed_variants': failed_attestation, 'detail': attestation_detail} if attestation_detail else failed_attestation))
        # TIFF-dependent checks are scoped to `evaluated` variants only: a
        # variant excluded because its mutation attestation failed (the TIFF
        # was intentionally never exported/compared) must read as
        # NOT_APPLICABLE, never as a vacuous PASS on empty evidence.
        no_real_evidence = bool(failed_attestation) and not evaluated and not missing_tiffs
        checks.append(_check('required_tiffs',
                             'NOT_APPLICABLE' if no_real_evidence else ('INCONCLUSIVE' if missing_tiffs else ('PASS' if evaluated else 'INCONCLUSIVE')),
                             ['REQUIRED_TIFF_MISSING'] if missing_tiffs else [],
                             {'missing': missing_tiffs, 'not_applicable_attestation_failed': failed_attestation}))
        checks.append(_check('dimensions',
                             'FAIL' if dimension_mismatch else ('NOT_APPLICABLE' if no_real_evidence else ('PASS' if evaluated else 'INCONCLUSIVE')),
                             ['DIMENSION_MISMATCH'] if dimension_mismatch else [], dimension_mismatch))
        checks.append(_check('palette_fidelity',
                             'FAIL' if contaminated else ('NOT_APPLICABLE' if no_real_evidence else ('PASS' if evaluated else 'INCONCLUSIVE')),
                             ['OFF_PALETTE_CONTAMINATION'] if contaminated else [], contaminated))
        # A color raster cannot, by itself, prove preservation of Revit visibility semantics.
        semantic = native.get('rendered_semantic_preservation') or {}
        semantic_status = _status(semantic.get('status') if isinstance(semantic, dict) else semantic,
                                  {'pass', 'proven'}, {'fail', 'disproven'})
        checks.append(_check('rendered_semantic_preservation', semantic_status,
                             [] if semantic_status == 'PASS' else
                             ['SEMANTIC_PRESERVATION_FAILED' if semantic_status == 'FAIL' else 'MANUAL_SEMANTIC_REVIEW_REQUIRED'], semantic))
        if family == 'external_sources':
            families = native.get('required_source_family_status') or {}
            required_variants = {
                'HOST': ('host_reference_coloring',),
                'LINK': ('linked_per_element_linkelementid_coloring',
                         'forced_linked_override_failure_hide_instance_fallback'),
                'DWG': ('dwg_importinstance_coloring',),
            }
            # `requested` (computed above from job.requested_settings/inputs
            # selection) is empty for an unrestricted/"all" selection - unchanged
            # legacy behavior evaluates every source family, exactly as before.
            # A job that explicitly restricts its selection to one family's
            # variants (an RVT-link-only or DWG-only job) must not be evaluated
            # against, or fail requested-case coverage because of, a family it
            # never requested: an RVT-link job is graded only on its RVT-link
            # cases, and a DWG job only on its DWG cases.
            for source, names in required_variants.items():
                requested_for_source = [n for n in names if not requested or n in requested]
                if not requested_for_source:
                    continue
                raw_evidence = families.get(source) or {}
                present = [variant for variant in variants if variant.get('variant') in requested_for_source]
                matching = [variant for variant in present if not variant.get('skipped')]
                skipped = [variant for variant in present if variant.get('skipped')]
                if not present:
                    # Requested but not present in this report at all - the
                    # generic requested_case_coverage check above already flags
                    # this; avoid a second, redundant family-level FAIL/INCONCLUSIVE.
                    continue
                if not matching:
                    # Every requested variant for this family was skipped (no
                    # eligible candidates - e.g. a supplied DWG excluded because
                    # ViewSpecific == True): an explicit, non-blocking outcome,
                    # never a silent/generic "missing evidence" INCONCLUSIVE.
                    checks.append(_check('external_source_{0}'.format(source.lower()), 'NOT_APPLICABLE',
                                         ['EXTERNAL_SOURCE_CASE_INELIGIBLE'],
                                         {'required_variants': requested_for_source,
                                          'skipped_variants': [{'variant': v.get('variant'), 'reason': v.get('reason')}
                                                               for v in skipped],
                                          'raw_family_status': raw_evidence}))
                    continue
                statuses = [_external_variant_visual_status(variant) for variant in matching]
                # `matching` can carry more than one entry per variant name
                # when a job requested more than one resolution case
                # (resolution_policy="both", multiple target_dpi values):
                # _run_native() appends one report entry per (resolution case
                # x selected variant). Completeness is checked against the
                # distinct variant *names* present, not the raw entry count -
                # `all(s in ('PASS', 'UNSUPPORTED') ...)` below still requires
                # every one of those entries (every resolution case) to pass.
                complete = {variant.get('variant') for variant in matching} == set(requested_for_source)
                # UNSUPPORTED is an explicit, non-failing capability
                # determination (see _external_variant_visual_status) and must
                # count toward PASS alongside a rendered-color PASS - it must
                # never be folded into the same reason code as a real FAIL.
                source_status = ('FAIL' if 'FAIL' in statuses else
                                 ('PASS' if complete and statuses and all(s in ('PASS', 'UNSUPPORTED') for s in statuses)
                                  else 'INCONCLUSIVE'))
                evidence = {'required_variants': requested_for_source,
                            'variant_statuses': {variant.get('variant'): status
                                                 for variant, status in zip(matching, statuses)},
                            'skipped_variants': [{'variant': v.get('variant'), 'reason': v.get('reason')} for v in skipped],
                            'raw_family_status': raw_evidence}
                checks.append(_check('external_source_{0}'.format(source.lower()), source_status,
                                     [] if source_status == 'PASS' else
                                     ['EXTERNAL_SOURCE_VISUAL_FAILURE' if source_status == 'FAIL' else
                                      'EXTERNAL_SOURCE_VISUAL_EVIDENCE_MISSING'], evidence))

    elif family == 'model_linework':
        modes = native.get('modes') or []
        dim_bad, repeat_bad, contamination, missing = [], [], [], []
        for mode in modes:
            name = mode.get('mode')
            images = mode.get('images') or []
            if not images or any(not _artifact_dimensions(i.get('analysis') or {}) for i in images):
                missing.append(name); continue
            classification = mode.get('classification') or {}
            if classification.get('dimension_alignment') == 'fail': dim_bad.append(name)
            repeat = mode.get('sequential_export_repeatability') or {}
            if repeat.get('available') and (not repeat.get('same_sha256') or not repeat.get('same_dimensions')): repeat_bad.append(name)
            if classification.get('fill_contamination') == 'unacceptable': contamination.append(name)
        checks.extend([
            _check('required_tiffs', 'INCONCLUSIVE' if missing else ('PASS' if modes else 'INCONCLUSIVE'), ['REQUIRED_TIFF_MISSING'] if missing else [], missing),
            _check('dimensions', 'FAIL' if dim_bad else ('PASS' if modes and not missing else 'INCONCLUSIVE'), ['DIMENSION_MISMATCH'] if dim_bad else [], dim_bad),
            _check('repeatability', 'FAIL' if repeat_bad else ('PASS' if modes and not missing else 'INCONCLUSIVE'), ['REPEATABILITY_MISMATCH'] if repeat_bad else [], repeat_bad),
            _check('linework_contamination', 'FAIL' if contamination else ('PASS' if modes and not missing else 'INCONCLUSIVE'), ['LINEWORK_COLOR_CONTAMINATION'] if contamination else [], contamination),
            _check('internal_and_hidden_edges', 'INCONCLUSIVE', ['MANUAL_EDGE_REVIEW_REQUIRED']),
        ])

    elif family == 'image_alignment':
        exports = native.get('exports') or {}
        images = [i for e in exports.values() for i in e.get('images', [])]
        missing = [name for name, e in exports.items() if not e.get('skipped') and not e.get('images')]
        dim_bad, dim_unknown, repeat_bad, repeat_unknown = [], [], [], []
        for name, export in exports.items():
            for image in export.get('images', []):
                dims = _artifact_dimensions(image)
                resolution = image.get('resolution') or {}
                required_width = (resolution.get('accepted_width_px') or
                                  image.get('effective_pixel_size'))
                required_height = resolution.get('predicted_height_px')
                if not dims or not required_width or not required_height:
                    dim_unknown.append(name)
                elif (dims[0] != int(required_width) or
                      dims[1] != int(required_height)):
                    dim_bad.append({'case': name,
                                    'required': [int(required_width), int(required_height)],
                                    'actual': dims})
            equal = export.get('sequential_export_equality')
            if equal is False or (isinstance(equal, dict) and not all(equal.get(k) is True for k in ('same_dimensions', 'same_sha256'))):
                repeat_bad.append(name)
            elif equal is not True:
                repeat_unknown.append(name)
        marker_complete = bool(images) and all(i.get('marker_analysis_available') and not i.get('missing_markers') for i in images)
        checks.extend([
            _check('required_tiffs', 'INCONCLUSIVE' if missing or not images else 'PASS', ['REQUIRED_TIFF_MISSING'] if missing or not images else [], missing),
            _check('dimensions', 'FAIL' if dim_bad else ('INCONCLUSIVE' if dim_unknown or not images else 'PASS'),
                   ['DIMENSION_MISMATCH'] if dim_bad else (['DIMENSION_EVIDENCE_INCOMPLETE'] if dim_unknown else []),
                   {'mismatches': dim_bad, 'incomplete_cases': sorted(set(dim_unknown))}),
            _check('calibration_marker_fit', 'PASS' if marker_complete else 'INCONCLUSIVE', [] if marker_complete else ['CALIBRATION_EVIDENCE_INCOMPLETE']),
            _check('repeatability', 'FAIL' if repeat_bad else ('INCONCLUSIVE' if repeat_unknown or not images else 'PASS'),
                   ['REPEATABILITY_MISMATCH'] if repeat_bad else (['REPEATABILITY_EVIDENCE_MISSING'] if repeat_unknown else []),
                   {'mismatches': repeat_bad, 'incomplete_cases': repeat_unknown}),
            _check('model_to_annotation_canvas_offset', 'PASS' if (native.get('model_to_canvas_placement') or {}).get('lossless_padding_sufficient') else 'INCONCLUSIVE', ['CANVAS_OFFSET_NOT_ESTABLISHED'] if not (native.get('model_to_canvas_placement') or {}).get('lossless_padding_sufficient') else [], native.get('model_to_canvas_placement')),
        ])
    return checks


def _acceptance_record(json_path: Path, data: dict[str, Any], probe_id: str,
                       family: str, native: dict[str, Any], limitations: list[dict[str, Any]],
                       analysis_errors: list[dict[str, Any]]) -> dict[str, Any]:
    checks = _safety_checks(data, native) + _family_checks(data, native, family)
    if analysis_errors:
        checks.append(_check('analyzer_execution', 'INCONCLUSIVE', ['ANALYZER_EXCEPTION'], analysis_errors))
    fail = [c for c in checks if c['status'] == 'FAIL']
    inconclusive = [c for c in checks if c['status'] == 'INCONCLUSIVE']
    acceptance = 'FAIL' if fail else ('INCONCLUSIVE' if inconclusive or limitations else 'PASS')
    reasons = sorted({r for c in checks for r in c['reason_codes']})
    artifacts = list(data.get('artifact_paths') or [])
    if not artifacts:
        paths = native.get('paths') or native.get('output_files') or {}
        values = paths.values() if isinstance(paths, dict) else paths
        for value in values:
            artifacts.extend(value if isinstance(value, list) else [value])
    record = {
        'analysis_schema_version': ANALYSIS_SCHEMA_VERSION,
        'campaign_id': data.get('campaign_id'), 'batch_id': data.get('batch_id'),
        'run_id': data.get('run_id'), 'job_id': data.get('job_id'),
        'probe_id': probe_id, 'probe_schema_version': str(data.get('probe_schema_version') or (native.get('probe') or {}).get('version') or 'legacy'),
        'analyzer_version': ANALYZER_VERSION,
        'source_report': {'path': str(json_path), 'sha256': sha256_file(json_path)},
        'comparison_reference': _comparison_reference(data),
        'artifact_references': artifacts,
        'analysis_status': 'ERROR' if analysis_errors else ('LIMITED' if limitations else 'COMPLETED'),
        'acceptance_status': acceptance, 'execution_status': data.get('execution_status'),
        'reason_codes': reasons,
        'summary': {'probe_family': family, 'checks_passed': sum(c['status'] == 'PASS' for c in checks),
                    'checks_failed': len(fail), 'checks_inconclusive': len(inconclusive)},
        'metrics': {'probe_specific': native}, 'checks': checks, 'limitations': limitations,
        'errors': list(data.get('errors') or []) + analysis_errors,
        'warnings': list(data.get('warnings') or []),
        'analyzed_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
    }
    # Transitional compatibility: existing consumers can still read enriched native fields.
    for key, value in native.items():
        record.setdefault(key, value)
    return record


def analyze_json(json_path: Path) -> tuple[Path, str]:
    """Analyze one raw report and write a sibling acceptance record without overwriting it."""
    json_path = Path(json_path)
    data = json.loads(json_path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError('MALFORMED_RAW_REPORT: top-level JSON must be an object')
    if isinstance(data.get('jobs'), list) and data.get('schema_version') == '1.0':
        records = []
        for job in data['jobs']:
            envelope = job.get('raw_result_envelope')
            if not isinstance(envelope, dict):
                continue
            merged = dict(envelope)
            for key in ('campaign_id', 'batch_id', 'run_id'):
                merged[key] = data.get(key)
            merged['job_id'] = job.get('job_id')
            # The envelope's own `requested_settings` reflects only what the
            # adapter actually forwarded to run_probe() - analysis-only keys
            # like comparison_reference are correctly stripped from it before
            # dispatch. The executor separately preserves the job's full
            # as-configured settings on the outer manifest job record; that is
            # the one that still carries comparison_reference for provenance.
            merged['job_requested_settings'] = job.get('requested_settings')
            probe_id, family, native = _probe_identity(merged)
            limitations, analysis_errors, summary = [], [], []
            if Image is None or np is None:
                limitations.append({'code': 'IMAGE_ANALYSIS_DEPENDENCY_UNAVAILABLE',
                                    'message': 'Pillow and NumPy are required for TIFF analysis; rollback was evaluated independently.'})
            else:
                try:
                    summary.extend(_enrich_report(json_path, native, probe_id))
                except Exception as exc:
                    analysis_errors.append({'code': 'ANALYZER_EXCEPTION', 'type': type(exc).__name__, 'message': str(exc)})
            records.append(_acceptance_record(json_path, merged, probe_id, family, native, limitations, analysis_errors))
        if not records:
            raise ValueError('MANIFEST_HAS_NO_ANALYZABLE_JOBS')
        out = json_path.with_name(json_path.stem + '.analyzed.json')
        out.write_text(json.dumps({'analysis_schema_version': ANALYSIS_SCHEMA_VERSION,
                                   'source_manifest': str(json_path), 'acceptance_records': records},
                                  indent=2, sort_keys=True), encoding='utf-8')
        return out, 'analyzed {0} manifest job(s)'.format(len(records))
    probe_id, family, native = _probe_identity(data)
    limitations, analysis_errors, summary = [], [], []
    if Image is None or np is None:
        limitations.append({'code': 'IMAGE_ANALYSIS_DEPENDENCY_UNAVAILABLE',
                            'message': 'Pillow and NumPy are required for TIFF analysis; rollback was evaluated independently.'})
    else:
        try:
            summary = _enrich_report(json_path, native, probe_id)
        except Exception as exc:
            analysis_errors.append({'code': 'ANALYZER_EXCEPTION', 'type': type(exc).__name__, 'message': str(exc)})
    record = _acceptance_record(json_path, data, probe_id, family, native, limitations, analysis_errors)
    out = json_path.with_name(json_path.stem + '.analyzed.json')
    out.write_text(json.dumps(record, indent=2, sort_keys=True), encoding='utf-8')
    return out, '; '.join(summary) or 'analyzed {0}: {1}'.format(json_path.name, record['acceptance_status'])


# ---------------------------------------------------------------------------
# Stage A color-ID export metrics (edge-drift + white-blend diagnosis)
#
# Diagnostic-only. Nothing here is consumed by the Stage A pipeline, by the
# acceptance records above, or by decode_stage_a_color_id.py; it exists so the
# drift and blend investigations have one measurement implementation instead of
# per-experiment ad-hoc scripts. All of it is pure post-hoc pixel analysis of an
# already-exported TIFF plus its sidecar -- it never snaps, repairs, or
# re-quantizes a capture, and it makes no readiness or correctness claim.
# ---------------------------------------------------------------------------

WHITE_CODE = 0xFFFFFF
_METRIC_STRIPE_ROWS = 512          # rows per pass; bounds peak working set
_OVERSHOOT_MIN_NORM = 0.02         # >2% beyond an endpoint counts as ringing
_ALPHA_CHANNEL_TOL = 3.0           # 8-bit units of per-channel unblend disagreement
_ALPHA_MIN, _ALPHA_MAX = 0.02, 0.995
_MAX_OFF_COLOR_KEYS = 500000       # dict cap; new keys dropped past it (flagged)
_MAX_TRANSITION_SAMPLES = 20000
_ROW_SAMPLE_BUDGET = _MAX_TRANSITION_SAMPLES // 2  # leave room for the column pass
_MAX_PASTEL_PROFILED = 16
_PASTEL_SOLID_MIN = 0.5    # a composited element has a solid interior; an edge does not
_MIN_TRANSITION_SPAN = 8   # 8-bit units between anchors below which a run is not a transition
_MAX_TRANSITION_FOR_OVERSHOOT = 32  # px; wider runs are regions, not resampled edges
_TRANSITION_HIST_CAP = 64


def _rgb_to_code(arr: np.ndarray) -> np.ndarray:
    """Pack an (h, w, 3) uint8 RGB block into an (h, w) uint32 color code."""
    a = arr.astype(np.uint32)
    return (a[..., 0] << 16) | (a[..., 1] << 8) | a[..., 2]


def _code_to_rgb(code: int) -> list[int]:
    code = int(code)
    return [(code >> 16) & 0xFF, (code >> 8) & 0xFF, code & 0xFF]


def _palette_from_sidecar(sidecar: dict[str, Any]) -> tuple[np.ndarray, dict[int, list[str]]]:
    """Every color Stage A deliberately assigned in this capture.

    HOST element colors (``color_assignment_map``) and LINK per-category colors
    (``link_category_color_map``) are the only "on-palette" colors; white is
    tracked separately as the background. Two labels can legitimately share a
    code only if the capture had a palette collision, so labels are a list.
    """
    labels: dict[int, list[str]] = {}
    for key, rgb in (sidecar.get('color_assignment_map') or {}).items():
        if not rgb or len(rgb) < 3:
            continue
        code = (int(rgb[0]) << 16) | (int(rgb[1]) << 8) | int(rgb[2])
        labels.setdefault(code, []).append('element:{0}'.format(key))
    for name, rgb in (sidecar.get('link_category_color_map') or {}).items():
        if not rgb or len(rgb) < 3:
            continue
        code = (int(rgb[0]) << 16) | (int(rgb[1]) << 8) | int(rgb[2])
        labels.setdefault(code, []).append('link_category:{0}'.format(name))
    codes = np.array(sorted(labels.keys()), dtype=np.uint32)
    return codes, labels


def _is_palette(code: np.ndarray, palette_sorted: np.ndarray) -> np.ndarray:
    """Membership test that stays O(n log p) instead of np.isin's O(n*p)."""
    if palette_sorted.size == 0:
        return np.zeros(code.shape, dtype=bool)
    idx = np.searchsorted(palette_sorted, code)
    np.clip(idx, 0, palette_sorted.size - 1, out=idx)
    return palette_sorted[idx] == code


def _percentiles(values: np.ndarray, points=(50, 75, 90, 99, 100)) -> dict[str, float]:
    if values.size == 0:
        return {}
    return {'p{0}'.format(p): float(np.percentile(values, p)) for p in points}


def _native_resolution(sidecar: dict[str, Any], width_px: int, height_px: int,
                       frame: dict[str, Any]) -> dict[str, Any]:
    """native_px = extent_ft * dpi * 12 / view_scale, and the realized scale factor.

    ``bounds_xy`` is the view-local rectangle in feet the export was cropped to
    (see color_id_buffer.compute_model_crop). When the sidecar records it as
    null the crop never applied, so native density is not derivable and every
    field below stays None rather than being guessed from pixel dimensions.
    """
    res = sidecar.get('resolution') or {}
    bounds = sidecar.get('bounds_xy')
    # The canvas can be wider than the model content: Revit pads the short
    # axis to keep the export inside its 10:1 aspect limit, and that padding
    # is not model pixels. Realized density has to be measured across the
    # content, or a clamped view reports a scale factor it never rendered at
    # (a 1:20-aspect view would come back at twice its real density).
    content_rect = frame.get('content_rect_px') or [0, 0, int(width_px), int(height_px)]
    content_width_px = max(0, int(content_rect[2]) - int(content_rect[0]))
    content_height_px = max(0, int(content_rect[3]) - int(content_rect[1]))
    out: dict[str, Any] = {
        'width_px': int(width_px),
        'height_px': int(height_px),
        'content_width_px': content_width_px,
        'content_height_px': content_height_px,
        'max_axis_px': int(max(width_px, height_px)),
        'requested_pixel_size': res.get('requested_pixel_size'),
        'accepted_pixel_size': res.get('pixel_size'),
        'export_dpi': res.get('export_dpi'),
        'view_scale': res.get('view_scale'),
        'extent_u_ft': None, 'extent_v_ft': None,
        'native_width_px': None, 'native_height_px': None,
        'scale_factor': None, 'pixel_size_backoff': None,
    }
    requested, accepted = res.get('requested_pixel_size'), res.get('pixel_size')
    if requested is not None and accepted is not None:
        out['pixel_size_backoff'] = int(accepted) != int(requested)
    dpi, scale = res.get('export_dpi'), res.get('view_scale')
    if not bounds or len(bounds) < 4 or not dpi or not scale:
        return out
    extent_u = float(bounds[2]) - float(bounds[0])
    extent_v = float(bounds[3]) - float(bounds[1])
    out['extent_u_ft'], out['extent_v_ft'] = extent_u, extent_v
    if extent_u <= 0 or extent_v <= 0 or float(scale) <= 0:
        return out
    per_ft = float(dpi) * 12.0 / float(scale)
    out['native_width_px'] = extent_u * per_ft
    out['native_height_px'] = extent_v * per_ft
    if out['native_width_px'] > 0:
        # Measured across the content, not the padded canvas.
        out['scale_factor'] = float(content_width_px) / out['native_width_px']
        # Kept alongside it so the difference on a clamped capture is visible
        # rather than having to be inferred from the frame block.
        out['canvas_scale_factor'] = float(width_px) / out['native_width_px']
    return out


def _frame_geometry(sidecar: dict[str, Any], width_px: int, height_px: int) -> dict[str, Any]:
    """Where the model content sits inside the canvas after Revit's 10:1 clamp.

    Revit refuses to export an image whose aspect exceeds 10:1 and pads the
    short axis symmetrically instead; ``bounds_xy`` does not reflect that
    padding, so a naive "assigned pixel outside bounds_xy" count is wrong by
    the pad on a clamped view. UNCONFIRMED: the 10:1 figure and the symmetric-
    padding behaviour come from the 2026-09-15 run, not from documented API
    behaviour -- ``clamp_matches_actual_height`` is the field that says whether
    the correction predicted this capture's real height.
    """
    bounds = sidecar.get('bounds_xy')
    out: dict[str, Any] = {'aspect': None, 'clamped_aspect': None, 'clamp_applied': None,
                           'pad_x_px': 0.0, 'pad_y_px': 0.0,
                           'content_rect_px': [0, 0, int(width_px), int(height_px)],
                           'predicted_height_px': None, 'clamp_matches_actual_height': None}
    if not bounds or len(bounds) < 4:
        return out
    extent_u = float(bounds[2]) - float(bounds[0])
    extent_v = float(bounds[3]) - float(bounds[1])
    if extent_u <= 0 or extent_v <= 0:
        return out
    aspect = extent_u / extent_v
    clamped = min(max(aspect, 0.1), 10.0)
    out['aspect'], out['clamped_aspect'] = aspect, clamped
    out['clamp_applied'] = abs(clamped - aspect) > 1e-9
    out['predicted_height_px'] = float(width_px) / clamped
    out['clamp_matches_actual_height'] = abs(out['predicted_height_px'] - float(height_px)) <= 2.0
    pad_x = pad_y = 0.0
    if aspect > clamped:          # too wide: short axis is V, so height is padded
        pad_y = max(0.0, (float(height_px) - float(width_px) / aspect) / 2.0)
    elif aspect < clamped:        # too tall: short axis is U, so width is padded
        pad_x = max(0.0, (float(width_px) - float(height_px) * aspect) / 2.0)
    out['pad_x_px'], out['pad_y_px'] = pad_x, pad_y
    out['content_rect_px'] = [int(math.floor(pad_x)), int(math.floor(pad_y)),
                              int(math.ceil(width_px - pad_x)), int(math.ceil(height_px - pad_y))]
    return out


def _off_runs_along_axis1(off: np.ndarray, rgb: np.ndarray,
                          max_samples: int) -> tuple[np.ndarray, list]:
    """Maximal runs of off-palette pixels along axis 1 of a block.

    Sentinel columns keep a run from wrapping past the end of one line into
    the start of the next. Only runs with a real pixel on both sides are
    sampled for overshoot -- one touching the block edge has no second
    anchor, so there is no transition to measure across it.
    """
    h, w = off.shape
    if h == 0 or w == 0:
        return np.zeros(0, dtype=np.int64), []
    sent = np.zeros((h, w + 2), dtype=bool)
    sent[:, 1:-1] = off
    d = np.diff(sent.ravel().astype(np.int8))
    starts = np.flatnonzero(d == 1) + 1
    ends = np.flatnonzero(d == -1) + 1
    if not starts.size:
        return np.zeros(0, dtype=np.int64), []
    lengths = (ends - starts).astype(np.int64)
    stride = w + 2
    line = (starts // stride).astype(np.int64)
    lo_col = (starts % stride).astype(np.int64) - 1
    hi_col = (ends % stride).astype(np.int64) - 1
    samples = []
    anchored = np.flatnonzero((lo_col > 0) & (hi_col < w))
    if anchored.size and max_samples > 0:
        want = min(int(anchored.size), int(max_samples))
        pick = anchored[np.linspace(0, anchored.size - 1, want).astype(np.int64)]
        for k in pick:
            r, c0, c1 = int(line[k]), int(lo_col[k]), int(hi_col[k])
            samples.append((rgb[r, c0 - 1].copy(), rgb[r, c0:c1].copy(), rgb[r, c1].copy()))
    return lengths, samples


def _scan_columns(arr: np.ndarray, palette_sorted: np.ndarray,
                  max_samples: int) -> tuple[np.ndarray, list]:
    """The same run scan, down columns instead of across rows.

    A boundary's transition runs perpendicular to it, so a row-only scan
    measures horizontal edges along their length rather than across their
    width: three blended rows spanning the canvas come back as runs the width
    of the image, which are then discarded as regions and yield no overshoot
    samples at all. Plans are full of horizontal walls and linework, so
    without this pass the width percentiles and the ringing rate would both
    depend on which way the drawing happens to be oriented.

    Columns are taken in full-height slices so a vertical run is never cut by
    a stripe seam.
    """
    h, w = arr.shape[:2]
    lengths_all, samples_all = [], []
    x = 0
    while x < w:
        x1 = min(w, x + _METRIC_STRIPE_ROWS)
        block = arr[:, x:x1]
        code = _rgb_to_code(block)
        off = ~(_is_palette(code, palette_sorted) | (code == WHITE_CODE))
        lengths, samples = _off_runs_along_axis1(
            np.ascontiguousarray(off.T),
            np.ascontiguousarray(block.transpose(1, 0, 2)),
            max(0, max_samples - len(samples_all)))
        if lengths.size:
            lengths_all.append(lengths)
        samples_all.extend(samples)
        x = x1
    pooled = np.concatenate(lengths_all) if lengths_all else np.zeros(0, dtype=np.int64)
    return pooled, samples_all


def _scan_export(arr: np.ndarray, palette_sorted: np.ndarray,
                 content_rect: list[int]) -> dict[str, Any]:
    """One stripe-wise pass collecting every edge/run/solidity/color statistic.

    Stripes carry a one-row halo so vertical adjacency and 3x3 solidity are
    computed across stripe seams rather than silently truncated at them.
    """
    h, w = arr.shape[:2]
    acc = {
        'palette_px': 0, 'white_px': 0, 'off_palette_px': 0,
        'hard_color_white_edges': 0, 'hard_color_color_edges': 0,
        'blended_palette_edges': 0, 'blended_white_edges': 0,
        'solid_3x3_palette_px': 0, 'interior_palette_px': 0,
        'out_of_bbox_palette_px': 0,
    }
    off_counts: dict[int, int] = {}
    off_truncated = False
    run_lengths: list[np.ndarray] = []
    sample_rgb: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
    cx0, cy0, cx1, cy1 = content_rect

    y = 0
    while y < h:
        y1 = min(h, y + _METRIC_STRIPE_ROWS)
        top, bot = max(0, y - 1), min(h, y1 + 1)
        block = arr[top:bot]
        code = _rgb_to_code(block)
        is_pal = _is_palette(code, palette_sorted)
        is_white = code == WHITE_CODE
        is_off = ~(is_pal | is_white)

        lo, hi = y - top, y - top + (y1 - y)          # rows owned by this stripe
        own_pal, own_white, own_off = is_pal[lo:hi], is_white[lo:hi], is_off[lo:hi]
        acc['palette_px'] += int(own_pal.sum())
        acc['white_px'] += int(own_white.sum())
        acc['off_palette_px'] += int(own_off.sum())

        # Assigned pixels outside the 10:1-corrected content rectangle.
        outside = own_pal.copy()
        rows = np.arange(y, y1)
        row_inside = (rows >= cy0) & (rows < cy1)
        col_inside = np.zeros(w, dtype=bool)
        col_inside[max(0, cx0):max(0, cx1)] = True
        outside &= ~(row_inside[:, None] & col_inside[None, :])
        acc['out_of_bbox_palette_px'] += int(outside.sum())

        # Horizontal adjacency inside owned rows; vertical adjacency reaches one
        # row into the halo so the pair straddling a stripe seam is counted
        # exactly once, by the stripe above it. On the last stripe there is no
        # row below the image, so `vhi` stops one short instead of dropping the
        # whole vertical pass (which would undercount every horizontal edge in
        # a single-stripe image by half).
        vhi = min(hi + 1, bot - top)
        for a_pal, a_white, a_off, b_pal, b_white, b_off, a_code, b_code in (
            (own_pal[:, :-1], own_white[:, :-1], own_off[:, :-1],
             own_pal[:, 1:], own_white[:, 1:], own_off[:, 1:],
             code[lo:hi, :-1], code[lo:hi, 1:]),
            (is_pal[lo:vhi - 1], is_white[lo:vhi - 1], is_off[lo:vhi - 1],
             is_pal[lo + 1:vhi], is_white[lo + 1:vhi], is_off[lo + 1:vhi],
             code[lo:vhi - 1], code[lo + 1:vhi]),
        ):
            if a_pal.shape != b_pal.shape or a_pal.size == 0:
                continue
            acc['hard_color_white_edges'] += int(((a_pal & b_white) | (a_white & b_pal)).sum())
            acc['hard_color_color_edges'] += int((a_pal & b_pal & (a_code != b_code)).sum())
            acc['blended_palette_edges'] += int(((a_pal & b_off) | (a_off & b_pal)).sum())
            acc['blended_white_edges'] += int(((a_white & b_off) | (a_off & b_white)).sum())

        # 3x3 solidity for owned rows that have a full neighbourhood.
        s0, s1 = max(lo, 1), min(hi, bot - top - 1)
        if s1 > s0 and w > 2:
            centre = code[s0:s1, 1:-1]
            same = np.ones(centre.shape, dtype=bool)
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dy == 0 and dx == 0:
                        continue
                    same &= code[s0 + dy:s1 + dy, 1 + dx:w - 1 + dx] == centre
            centre_pal = is_pal[s0:s1, 1:-1]
            acc['interior_palette_px'] += int(centre_pal.sum())
            acc['solid_3x3_palette_px'] += int((same & centre_pal).sum())

        # Row-wise runs of off-palette pixels. The column pass below covers
        # the other orientation; the two are pooled.
        if own_off.shape[0]:
            lengths, row_samples = _off_runs_along_axis1(
                own_off, block[lo:hi], _ROW_SAMPLE_BUDGET - len(sample_rgb))
            if lengths.size:
                run_lengths.append(lengths)
            sample_rgb.extend(row_samples)

        # Off-palette color census, for the pastel/unblend stage.
        if not off_truncated and own_off.any():
            vals, cnts = np.unique(code[lo:hi][own_off], return_counts=True)
            for v, c in zip(vals.tolist(), cnts.tolist()):
                if v in off_counts:
                    off_counts[v] += c
                elif len(off_counts) < _MAX_OFF_COLOR_KEYS:
                    off_counts[v] = c
                else:
                    off_truncated = True
        y = y1

    row_lengths = np.concatenate(run_lengths) if run_lengths else np.zeros(0, dtype=np.int64)
    row_samples = len(sample_rgb)
    col_lengths, col_samples = _scan_columns(
        arr, palette_sorted, _MAX_TRANSITION_SAMPLES - row_samples)
    sample_rgb.extend(col_samples)

    acc['off_color_counts'] = off_counts
    acc['off_color_census_truncated'] = off_truncated
    acc['transition_lengths'] = np.concatenate([row_lengths, col_lengths])
    acc['transition_row_runs'] = int(row_lengths.size)
    acc['transition_column_runs'] = int(col_lengths.size)
    acc['transition_samples'] = sample_rgb
    acc['transition_row_samples'] = row_samples
    acc['transition_column_samples'] = len(col_samples)
    return acc


def _anchor_kind(left: np.ndarray, right: np.ndarray, palette_sorted: np.ndarray,
                 span: int, width: int) -> str:
    """Classify a run by what sits on each side of it and how wide it is.

    Only ``color_white`` and ``color_color`` are real transitions.

    A run whose two anchors are the same color is not a transition at all --
    it is the interior of an off-palette region, which is exactly what a whole
    element composited over white looks like -- and normalizing an excursion
    by its zero endpoint separation would report an arbitrarily large
    "overshoot" for a capture with no resampling in it.

    A very wide run is excluded for the same reason even when its anchors do
    differ. Resampling spreads an edge across the kernel's support, a handful
    of pixels; a 300-pixel span of one off-palette color bounded by white on
    one side and another element on the other is a third region, and scoring
    its distance from the line between those two anchors as "ringing" would
    report a hard-edged composited capture as resampled.
    """
    if span < _MIN_TRANSITION_SPAN:
        return 'degenerate'
    if width > _MAX_TRANSITION_FOR_OVERSHOOT:
        return 'wide_region'
    kinds = []
    for value in (left, right):
        code = (int(value[0]) << 16) | (int(value[1]) << 8) | int(value[2])
        if code == WHITE_CODE:
            kinds.append('white')
        elif bool(_is_palette(np.array([code], dtype=np.uint32), palette_sorted)[0]):
            kinds.append('palette')
        else:
            kinds.append('other')
    if 'other' in kinds:
        return 'other'
    if kinds == ['white', 'white']:
        return 'degenerate'
    return 'color_white' if 'white' in kinds else 'color_color'


def _overshoot(sample_rgb, palette_sorted: np.ndarray) -> dict[str, Any]:
    """Ringing magnitude across sampled 1-D transitions.

    For a run of off-palette pixels anchored by two different exact colors, a
    monotone (box/bilinear) resample keeps every run pixel inside
    ``[min, max]`` per channel. A negative-lobe kernel or a sharpening pass
    pushes pixels beyond an endpoint; that excess is the overshoot.

    Two normalizations are deliberately avoided. Dividing a channel's
    excursion by that channel's own endpoint gap turns a small wobble on a
    near-equal channel into a huge ratio, so the excess is divided by the
    transition's *overall* endpoint separation instead. And a run whose
    anchors match is dropped entirely rather than divided by zero -- see
    ``_anchor_kind``.

    The headline rate covers color-to-white transitions, the population the
    34-60% baseline figure was measured on. Every other population is
    reported beside it with its own count, never folded into it.
    """
    buckets: dict[str, list[float]] = {
        'color_white': [], 'color_color': [], 'other': [], 'degenerate': [], 'wide_region': []}
    for left, run, right in sample_rgb or []:
        a, b, r = left.astype(np.int16), right.astype(np.int16), run.astype(np.int16)
        lo, hi = np.minimum(a, b), np.maximum(a, b)
        span = int((hi - lo).max())
        kind = _anchor_kind(left, right, palette_sorted, span, int(r.shape[0]))
        if kind in ('degenerate', 'wide_region'):
            buckets[kind].append(0.0)
            continue
        excess = np.maximum(np.maximum(lo[None, :] - r, r - hi[None, :]), 0)
        buckets[kind].append(float(excess.max()) / max(span, 1))

    def summarize(values: list[float]) -> dict[str, Any]:
        arr = np.asarray(values, dtype=float)
        if arr.size == 0:
            return {'count': 0, 'rate': None, 'normalized': {}}
        return {'count': int(arr.size),
                'rate': float((arr > _OVERSHOOT_MIN_NORM).mean()),
                'normalized': _percentiles(arr)}

    color_white = summarize(buckets['color_white'])
    color_color = summarize(buckets['color_color'])
    measurable = summarize(buckets['color_white'] + buckets['color_color'])
    headline = color_white if color_white['count'] else measurable
    return {
        'sampled_transitions': sum(len(v) for v in buckets.values()),
        'measurable_transitions': measurable['count'],
        'overshoot_rate': headline['rate'],
        'normalized_overshoot': headline['normalized'],
        'overshoot_population': ('color_white' if color_white['count']
                                 else ('color_color' if color_color['count'] else None)),
        'color_white_transitions': color_white['count'],
        'color_color_transitions': color_color['count'],
        # Runs bounded by the same color on both sides: region interiors, not
        # transitions. A high count here with a low hard_edge_ratio is the
        # signature of composited whole elements, not of resampling.
        'degenerate_runs': len(buckets['degenerate']),
        # Runs too wide to be a resampled edge: a third region, not a
        # transition. Counted so their exclusion is visible, never scored.
        'wide_region_runs': len(buckets['wide_region']),
        'max_width_scored_px': _MAX_TRANSITION_FOR_OVERSHOOT,
        'by_anchor_kind': {kind: summarize(values) for kind, values in buckets.items()
                           if kind not in ('degenerate', 'wide_region')},
        'measurable': measurable,
    }


def _unblend_over_white(off_code: int, palette_sorted: np.ndarray) -> dict[str, Any] | None:
    """Solve off == alpha*P + (1-alpha)*white for some palette color P.

    Per channel, alpha = (255 - off) / (255 - P). A real constant-alpha
    composite over white gives the same alpha on every channel where P differs
    from white; an anti-aliased or resampled edge pixel generally does not.
    Channels where P is already 255 carry no information and are skipped, but
    the off pixel must still be 255 there.
    """
    if palette_sorted.size == 0:
        return None
    off = np.array(_code_to_rgb(off_code), dtype=np.float64)
    pal = palette_sorted.astype(np.uint32)
    p = np.stack([(pal >> 16) & 0xFF, (pal >> 8) & 0xFF, pal & 0xFF], axis=1).astype(np.float64)
    denom = 255.0 - p
    usable = denom > 0.5
    with np.errstate(divide='ignore', invalid='ignore'):
        alphas = np.where(usable, (255.0 - off[None, :]) / np.where(usable, denom, 1.0), np.nan)
    n_usable = usable.sum(axis=1)
    valid = n_usable > 0
    # Saturated channels of P must be (near-)saturated in the off color too.
    valid &= np.all(usable | (np.abs(off[None, :] - 255.0) <= _ALPHA_CHANNEL_TOL), axis=1)
    if not valid.any():
        return None
    mean_alpha = np.nanmean(np.where(usable, alphas, np.nan), axis=1)
    valid &= (mean_alpha > _ALPHA_MIN) & (mean_alpha < _ALPHA_MAX)
    if not valid.any():
        return None
    recon = mean_alpha[:, None] * p + (1.0 - mean_alpha[:, None]) * 255.0
    err = np.max(np.abs(recon - off[None, :]), axis=1)
    err = np.where(valid, err, np.inf)
    best = int(np.argmin(err))
    if not np.isfinite(err[best]) or err[best] > _ALPHA_CHANNEL_TOL:
        return None
    return {'palette_rgb': _code_to_rgb(int(pal[best])), 'palette_code': int(pal[best]),
            'alpha': float(mean_alpha[best]), 'max_channel_error': float(err[best])}


def _pastel_profile(arr: np.ndarray, palette_sorted: np.ndarray,
                    codes: list[int]) -> dict[int, dict[str, Any]]:
    """Per-pastel-color solidity and palette neighbours -- the B2 discriminator.

    A color-to-white edge produced by resampling lands *exactly* on the alpha
    ray from the palette color to white, so it unblends just as cleanly as a
    deliberately composited element does. Solving the alpha is therefore not
    enough on its own. What separates them is shape: a whole element drawn at
    constant alpha has a large interior where every 3x3 neighbourhood is that
    same pastel color, while a resampled edge has essentially none.

    The palette colors found adjacent to each pastel color answer the other
    half of B2: if a pastel region unblends against white while sitting next
    to another element's color, white is the blend target, not that element.
    """
    if not codes:
        return {}
    h, w = arr.shape[:2]
    wanted = np.array(sorted(set(int(c) for c in codes)), dtype=np.uint32)
    profile = {int(c): {'total_px': 0, 'solid_3x3_px': 0, 'neighbor_palette_codes': set()}
               for c in wanted.tolist()}
    y = 0
    while y < h:
        y1 = min(h, y + _METRIC_STRIPE_ROWS)
        top, bot = max(0, y - 1), min(h, y1 + 1)
        code = _rgb_to_code(arr[top:bot])
        lo, hi = y - top, y - top + (y1 - y)
        s0, s1 = max(lo, 1), min(hi, bot - top - 1)
        for target in wanted.tolist():
            own = code[lo:hi] == target
            profile[target]['total_px'] += int(own.sum())
            if s1 <= s0 or w <= 2:
                continue
            centre = code[s0:s1, 1:-1] == target
            if not centre.any():
                continue
            same = centre.copy()
            neighbours = set()
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dy == 0 and dx == 0:
                        continue
                    shifted = code[s0 + dy:s1 + dy, 1 + dx:w - 1 + dx]
                    same &= shifted == target
                    around = shifted[centre]
                    if around.size:
                        values = np.unique(around)
                        values = values[_is_palette(values, palette_sorted)]
                        neighbours.update(int(v) for v in values.tolist())
            profile[target]['solid_3x3_px'] += int(same.sum())
            profile[target]['neighbor_palette_codes'].update(neighbours)
        y = y1
    for record in profile.values():
        record['neighbor_palette_codes'] = sorted(record['neighbor_palette_codes'])[:32]
        record['solid_3x3_fraction'] = (record['solid_3x3_px'] / record['total_px']
                                        if record['total_px'] else None)
    return profile


def _pastel_analysis(off_counts: dict[int, int], palette_sorted: np.ndarray,
                     labels: dict[int, list[str]], total_px: int) -> dict[str, Any]:
    """Which off-palette colors are whole-element composites over white."""
    ranked = sorted(off_counts.items(), key=lambda kv: kv[1], reverse=True)
    matches = []
    for code, count in ranked[:_MAX_PASTEL_PROFILED * 8]:
        solved = _unblend_over_white(code, palette_sorted)
        if solved is None:
            continue
        solved.update({'off_rgb': _code_to_rgb(code), 'off_code': int(code),
                       'pixel_count': int(count),
                       'pixel_fraction': (count / total_px) if total_px else None,
                       'palette_labels': labels.get(solved['palette_code'], [])})
        matches.append(solved)
        if len(matches) >= _MAX_PASTEL_PROFILED:
            break
    alphas = np.asarray([m['alpha'] for m in matches], dtype=float)
    hist: dict[str, int] = {}
    for m in matches:
        key = '{0:.2f}'.format(round(m['alpha'], 2))
        hist[key] = hist.get(key, 0) + 1
    return {
        'matched_color_count': len(matches),
        'matched_pixel_count': int(sum(m['pixel_count'] for m in matches)),
        'distinct_palette_colors_blended': len({m['palette_code'] for m in matches}),
        'alpha_histogram': hist,
        'alpha_percentiles': _percentiles(alphas) if alphas.size else {},
        'matches': matches,
    }


def _apply_pastel_profile(blend: dict[str, Any], profile: dict[int, dict[str, Any]]) -> None:
    """Split unblend matches into composited elements and resample residue.

    ``matched_color_count`` counts every color that solves cleanly against the
    palette over white, which on a resampled export is mostly edge residue.
    ``composited_color_count`` counts only the ones that also have a solid
    interior, and is the number to read as "pastel element count".
    """
    composited = 0
    for match in blend['matches']:
        record = profile.get(match['off_code'])
        if not record:
            continue
        match['solid_3x3_px'] = record['solid_3x3_px']
        match['solid_3x3_fraction'] = record['solid_3x3_fraction']
        match['neighbor_palette_rgb'] = [_code_to_rgb(c) for c in record['neighbor_palette_codes']]
        match['is_composited_region'] = bool(
            record['solid_3x3_fraction'] is not None
            and record['solid_3x3_fraction'] >= _PASTEL_SOLID_MIN)
        if match['is_composited_region']:
            composited += 1
    blend['composited_color_count'] = composited
    blend['composited_pixel_count'] = int(sum(
        m['pixel_count'] for m in blend['matches'] if m.get('is_composited_region')))
    composited_alphas = np.asarray(
        [m['alpha'] for m in blend['matches'] if m.get('is_composited_region')], dtype=float)
    blend['composited_alpha_percentiles'] = (_percentiles(composited_alphas)
                                             if composited_alphas.size else {})
    blend['composited_alpha_histogram'] = {}
    for match in blend['matches']:
        if not match.get('is_composited_region'):
            continue
        key = '{0:.2f}'.format(round(match['alpha'], 2))
        blend['composited_alpha_histogram'][key] = \
            blend['composited_alpha_histogram'].get(key, 0) + 1


def stage_a_export_metrics(tiff_path: Path, sidecar: dict[str, Any]) -> dict[str, Any]:
    """Every metric the drift/blend investigation requires, for one export.

    ``sidecar`` is a Stage A color-ID sidecar (color_id_buffer.py) or any probe
    record carrying the same keys: ``resolution``, ``bounds_xy``,
    ``color_assignment_map``, ``link_category_color_map``.
    """
    if Image is None or np is None:
        raise RuntimeError('Pillow and NumPy are required for stage_a_export_metrics')
    tiff_path = Path(tiff_path)
    arr = load_rgb(tiff_path)
    h, w = arr.shape[:2]
    palette_sorted, labels = _palette_from_sidecar(sidecar)
    # frame first: the resolution block needs the clamp-corrected content
    # rectangle to report a density the capture actually rendered at.
    frame = _frame_geometry(sidecar, w, h)
    resolution = _native_resolution(sidecar, w, h, frame)
    scan = _scan_export(arr, palette_sorted, frame['content_rect_px'])

    blend = _pastel_analysis(scan['off_color_counts'], palette_sorted, labels, h * w)
    _apply_pastel_profile(blend, _pastel_profile(
        arr, palette_sorted, [m['off_code'] for m in blend['matches']]))

    total_px = h * w
    hard = scan['hard_color_white_edges']
    blended = scan['blended_palette_edges'] + scan['blended_white_edges']
    lengths = scan['transition_lengths']
    hist: dict[str, int] = {}
    if lengths.size:
        capped = np.minimum(lengths, _TRANSITION_HIST_CAP)
        vals, cnts = np.unique(capped, return_counts=True)
        hist = {('>={0}'.format(_TRANSITION_HIST_CAP) if int(v) >= _TRANSITION_HIST_CAP else str(int(v))): int(c)
                for v, c in zip(vals.tolist(), cnts.tolist())}

    image = base_info(tiff_path)
    # Carried through so a capture can be dated without trusting a file
    # modification time, which copying a capture set resets.
    if sidecar.get('captured_at'):
        image['captured_at'] = sidecar['captured_at']
    return {
        'image': image,
        'palette_color_count': int(palette_sorted.size),
        'resolution': resolution,
        'frame': frame,
        'pixels': {
            'total_px': int(total_px),
            'palette_px': scan['palette_px'],
            'white_px': scan['white_px'],
            'off_palette_px': scan['off_palette_px'],
            'off_palette_fraction': (scan['off_palette_px'] / total_px) if total_px else None,
            'off_palette_distinct_colors': len(scan['off_color_counts']),
            'off_palette_census_truncated': scan['off_color_census_truncated'],
            'out_of_bbox_palette_px': scan['out_of_bbox_palette_px'],
        },
        'edges': {
            'hard_edge_count': hard,
            'blended_edge_count': blended,
            'hard_edge_ratio': (hard / (hard + blended)) if (hard + blended) else None,
            'hard_color_color_edge_count': scan['hard_color_color_edges'],
            'blended_palette_edge_count': scan['blended_palette_edges'],
            'blended_white_edge_count': scan['blended_white_edges'],
        },
        'transitions': {
            'count': int(lengths.size),
            # Both orientations are scanned and pooled. A boundary's
            # transition runs perpendicular to it, so a row-only scan measures
            # horizontal edges along their length instead of across them.
            'row_runs': scan['transition_row_runs'],
            'column_runs': scan['transition_column_runs'],
            'row_samples': scan['transition_row_samples'],
            'column_samples': scan['transition_column_samples'],
            'width_histogram': hist,
            'width_percentiles': _percentiles(lengths.astype(float)) if lengths.size else {},
            'mean_width': float(lengths.mean()) if lengths.size else None,
            'overshoot': _overshoot(scan['transition_samples'], palette_sorted),
        },
        'solidity': {
            'interior_palette_px': scan['interior_palette_px'],
            'solid_3x3_palette_px': scan['solid_3x3_palette_px'],
            'fraction_3x3_solid': (scan['solid_3x3_palette_px'] / scan['interior_palette_px'])
                                   if scan['interior_palette_px'] else None,
        },
        'white_blend': blend,
    }


def _metrics_export_records(json_path: Path, data: dict[str, Any]) -> list[tuple[str, Path, dict[str, Any]]]:
    """Every (label, tiff, sidecar) triple a metrics input file describes.

    Accepts a bare Stage A color-ID sidecar, or a probe envelope/native report
    carrying an ``exports`` list whose entries repeat the sidecar's keys. No
    other shape is inferred: an unrecognized file raises rather than silently
    measuring nothing.
    """
    def _records_from(node: dict[str, Any]) -> list[tuple[str, Path, dict[str, Any]]]:
        found = []
        exports = node.get('exports')
        if isinstance(exports, list):
            for i, rec in enumerate(exports):
                if not isinstance(rec, dict):
                    continue
                tiff = resolve_capture_tiff(json_path, rec.get('tiff_path'))
                if tiff is None:
                    continue
                # A probe record that names its own production sidecar defers
                # to it entirely: that file carries the palette, and the probe
                # record deliberately does not duplicate it. Without this the
                # palette would default to {} and every pixel would count as
                # off-palette -- a full set of well-formed, meaningless numbers.
                side = resolve_path(json_path, rec.get('sidecar_path'))
                if side is not None and not side.exists():
                    # Same relocation problem as the TIFF: prefer the sidecar
                    # named beside the report when the recorded path is stale.
                    beside = json_path.parent / Path(rec['sidecar_path']).name
                    side = beside if beside.exists() else side
                merged = dict(rec)
                if side is not None and side.exists():
                    try:
                        loaded = json.loads(side.read_text(encoding='utf-8'))
                    except Exception:
                        loaded = None
                    if isinstance(loaded, dict):
                        merged = dict(loaded)
                        merged['tiff_path'] = str(tiff)
                        for key in ('case', 'label'):
                            if rec.get(key) is not None:
                                merged[key] = rec[key]
                # Inherit a palette the enclosing report actually carries, but
                # never invent an empty one: an absent field is how "wrong file
                # pointed at" is told apart from "capture that painted nothing".
                for key in ('color_assignment_map', 'link_category_color_map'):
                    if key not in merged and node.get(key) is not None:
                        merged[key] = node[key]
                found.append((str(rec.get('label') or rec.get('case') or i), tiff, merged))
        return found

    records = _records_from(data)
    native = data.get('native_report')
    if not records and isinstance(native, dict):
        records = _records_from(native)
    if not records and data.get('tiff_path'):
        tiff = resolve_capture_tiff(json_path, data.get('tiff_path'))
        if tiff is not None:
            # A capture written by a probe labels itself; a hand-run Stage A
            # sidecar falls back to its view id, then to the file name.
            label = data.get('probe_label') or data.get('view_id') or json_path.stem
            records = [(str(label), tiff, data)]
    if not records:
        raise ValueError('NO_EXPORT_RECORDS: {0} has neither "tiff_path" nor an "exports" list'.format(json_path.name))
    # Every metric here is palette-relative. Measuring against an empty palette
    # does not fail -- it reports every pixel as off-palette, a hard_edge_ratio
    # of 0 and no pastels, which reads exactly like a catastrophically drifted
    # capture. Refuse instead, and say where the palette was expected.
    # A palette that is ABSENT means the wrong file was pointed at -- a probe
    # report carries no color_assignment_map, and measuring against an empty
    # one does not fail, it reports every pixel as off-palette and reads like a
    # catastrophically drifted capture.
    #
    # A palette that is PRESENT AND EMPTY is a different thing entirely: a real
    # production capture in which nothing was painted. D2's step 0 is exactly
    # that by construction -- every model category hidden, as the zero-content
    # control the later steps are read against. Refusing it would throw away
    # the control.
    missing = [label for label, tiff, side in records
               if tiff.exists() and 'color_assignment_map' not in side
               and 'link_category_color_map' not in side]
    if missing:
        raise ValueError(
            'NO_PALETTE: {0} describes export(s) {1} with no color_assignment_map field '
            'at all. Point --export-metrics at the probe\'s captures/ directory (each '
            'capture keeps production\'s own sidecar, which carries the palette) rather '
            'than at the probe report.'.format(json_path.name, ', '.join(sorted(missing)[:5])))
    return records


_METRIC_TABLE_COLUMNS = (
    ('label', lambda m: None),
    ('W', lambda m: m['resolution']['width_px']),
    ('H', lambda m: m['resolution']['height_px']),
    ('req_px', lambda m: m['resolution']['requested_pixel_size']),
    ('native_px', lambda m: m['resolution']['native_width_px']),
    ('scale', lambda m: m['resolution']['scale_factor']),
    ('hard_edges', lambda m: m['edges']['hard_edge_count']),
    ('blend_edges', lambda m: m['edges']['blended_edge_count']),
    ('hard_ratio', lambda m: m['edges']['hard_edge_ratio']),
    ('off_px', lambda m: m['pixels']['off_palette_px']),
    ('overshoot', lambda m: m['transitions']['overshoot']['overshoot_rate']),
    # The sample count behind that rate. A rate over a handful of transitions
    # is noise, and without this column it reads identically to one over
    # hundreds.
    ('n_trans', lambda m: m['transitions']['overshoot']['measurable_transitions']),
    ('trans_p50', lambda m: m['transitions']['width_percentiles'].get('p50')),
    ('trans_p90', lambda m: m['transitions']['width_percentiles'].get('p90')),
    ('oob_px', lambda m: m['pixels']['out_of_bbox_palette_px']),
    ('solid3x3', lambda m: m['solidity']['fraction_3x3_solid']),
    ('pastel_colors', lambda m: m['white_blend']['composited_color_count']),
    ('pastel_alpha_p50', lambda m: m['white_blend']['composited_alpha_percentiles'].get('p50')),
)


def _fmt_cell(value: Any) -> str:
    if value is None:
        return '-'
    if isinstance(value, float):
        return '{0:.4g}'.format(value)
    return str(value)


def format_metrics_table(rows: list[tuple[str, dict[str, Any]]]) -> str:
    """Markdown table of the required per-export metrics, one row per export."""
    header = [name for name, _ in _METRIC_TABLE_COLUMNS]
    lines = ['| ' + ' | '.join(header) + ' |',
             '| ' + ' | '.join('---' for _ in header) + ' |']
    for label, metrics in rows:
        cells = [label] + [_fmt_cell(getter(metrics)) for name, getter in _METRIC_TABLE_COLUMNS[1:]]
        lines.append('| ' + ' | '.join(cells) + ' |')
    return '\n'.join(lines)


def analyze_metrics_json(json_path: Path, seen_tiffs=None
                         ) -> tuple[Path, list[tuple[str, dict[str, Any]]]]:
    """Write ``<stem>.metrics.json`` beside a probe/sidecar file and return its rows.

    ``seen_tiffs`` carries resolved TIFF paths already measured in this run and
    is added to as it goes. A probe directory holds both the probe report and
    every capture's own sidecar, and the report's records point at those same
    TIFFs, so measuring both would decode each image twice -- at roughly 550 MB
    per Stage A capture, gigabytes of needless work.
    """
    json_path = Path(json_path)
    data = json.loads(json_path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError('MALFORMED_RAW_REPORT: top-level JSON must be an object')
    rows, errors = [], []
    for label, tiff, sidecar in _metrics_export_records(json_path, data):
        if not tiff.exists():
            errors.append({'label': label, 'code': 'TIFF_MISSING', 'path': str(tiff)})
            continue
        key = str(tiff.resolve())
        if seen_tiffs is not None and key in seen_tiffs:
            errors.append({'label': label, 'code': 'DUPLICATE_TIFF', 'path': key,
                           'message': 'already measured from another input in this run'})
            continue
        try:
            metrics = stage_a_export_metrics(tiff, sidecar)
            recorded = sidecar.get('tiff_path')
            # Only an ABSOLUTE recorded path can be stale. A relative one is
            # resolved against the sidecar's directory by design, which is the
            # intended case and not a relocation.
            if (recorded and Path(recorded).is_absolute()
                    and Path(recorded).resolve() != tiff.resolve()):
                # Not silent: the capture set has been moved since it was
                # written, and the reader should know which file was measured.
                metrics['image']['recorded_path'] = recorded
                metrics['image']['resolved_beside_sidecar'] = True
            rows.append((label, metrics))
            if seen_tiffs is not None:
                seen_tiffs.add(key)
        except Exception as exc:
            errors.append({'label': label, 'code': 'METRICS_EXCEPTION',
                           'type': type(exc).__name__, 'message': str(exc)})
    out = json_path.with_name(json_path.stem + '.metrics.json')
    out.write_text(json.dumps({'analysis_schema_version': ANALYSIS_SCHEMA_VERSION,
                               'analyzer_version': ANALYZER_VERSION,
                               'source': str(json_path),
                               'generated_at': datetime.now(timezone.utc).isoformat(),
                               'errors': errors,
                               'exports': {label: metrics for label, metrics in rows}},
                              indent=2, sort_keys=True), encoding='utf-8')
    return out, rows


def collect(paths):
    out=[]
    for arg in paths:
        p=Path(arg)
        if p.is_dir(): out += sorted(x for x in p.rglob('*.json') if not x.name.endswith('.analyzed.json'))
        else: out.append(p)
    return out


def main(argv=None):
    ap=argparse.ArgumentParser(description='Analyze Stage A Dynamo probe JSON/TIFF outputs externally with Pillow + NumPy')
    ap.add_argument('paths', nargs='+', help='Probe output directories or JSON files')
    ap.add_argument('--export-metrics', action='store_true',
                    help='Diagnostic mode: emit the per-export edge-drift/white-blend metric set for each '
                         'Stage A color-ID sidecar (or probe record carrying an "exports" list) instead of '
                         'running probe acceptance. Writes <stem>.metrics.json beside each input.')
    ap.add_argument('--metrics-table', metavar='PATH', default=None,
                    help='With --export-metrics, also write one markdown table of every measured export here '
                         '(use "-" for stdout).')
    ns=ap.parse_args(argv)
    if ns.export_metrics:
        return _main_export_metrics(ns)
    failures=0
    for jp in collect(ns.paths):
        try:
            out, msg=analyze_json(jp); print(f"✓ {jp} -> {out}: {msg}")
        except Exception as e:
            failures+=1; print(f"✗ {jp}: {type(e).__name__}: {e}")
    return 1 if failures else 0


def _main_export_metrics(ns) -> int:
    if Image is None or np is None:
        print('✗ Pillow and NumPy are required for --export-metrics'); return 1
    failures, table_rows = 0, []
    seen_tiffs: set[str] = set()
    inputs = [jp for jp in collect(ns.paths)
              if not jp.name.endswith(('.metrics.json', '.analyzed.json'))]

    # Standalone capture sidecars first, probe reports second. A probe
    # directory holds both, and the report's records point at the same TIFFs,
    # so without this each capture is decoded twice -- at roughly 550 MB per
    # Stage A capture that is gigabytes of needless work. The capture's own
    # sidecar is production's, so it is the one worth keeping.
    def _is_bare_sidecar(path: Path) -> bool:
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            return False
        return isinstance(payload, dict) and bool(payload.get('tiff_path'))

    inputs.sort(key=lambda path: (0 if _is_bare_sidecar(path) else 1, str(path)))

    # Row labels come from each file's path relative to the common root, not
    # from its stem. A campaign gives every job its own directory, and capture
    # file names repeat across them by design -- d1_determinism.rep0 exists
    # under every D1 job -- so a stem alone produces two identical rows with
    # nothing to say which view each belongs to. The relative path also names
    # the job, which is what a reader actually wants in the table.
    def _row_label(path: Path, row: str, row_count: int) -> str:
        try:
            relative = Path(os.path.relpath(path, _table_root))
        except ValueError:
            # Different drives on Windows: no relative path exists.
            relative = path
        stem = relative.with_suffix('').as_posix()
        # The per-record label only earns its place when a file yields more
        # than one row; otherwise it just repeats the file name.
        return '{0}:{1}'.format(stem, row) if row_count > 1 else stem

    try:
        _table_root = (os.path.commonpath([str(path.parent) for path in inputs])
                       if inputs else '')
    except ValueError:
        _table_root = ''

    for jp in inputs:
        try:
            out, rows = analyze_metrics_json(jp, seen_tiffs=seen_tiffs)
        except Exception as e:
            failures += 1; print(f"✗ {jp}: {type(e).__name__}: {e}"); continue
        for label, metrics in rows:
            table_rows.append((_row_label(jp, label, len(rows)), metrics))
        if rows:
            print(f"✓ {jp} -> {out}: {len(rows)} export(s) measured")
        else:
            # A tick and "0 measured" reads like success. Name the reason here
            # rather than making someone open the .metrics.json to find it.
            try:
                why = json.loads(out.read_text(encoding='utf-8')).get('errors') or []
            except Exception:
                why = []
            codes = {e.get('code') for e in why}
            if why and codes == {'DUPLICATE_TIFF'}:
                # Everything here was already measured from the capture's own
                # sidecar. That is dedupe working, not a failure.
                print(f"– {jp}: {len(why)} capture(s) already measured; skipped")
                continue
            detail = '; '.join(
                '{0}{1}'.format(e.get('code', '?'),
                                ': ' + str(e.get('message') or e.get('type') or '')
                                if (e.get('message') or e.get('type')) else '')
                for e in why[:3]) or 'no export records'
            failures += 1
            print(f"✗ {jp} -> {out}: nothing measured ({detail})")
    if ns.metrics_table and table_rows:
        table = format_metrics_table(table_rows)
        if ns.metrics_table == '-':
            print(table)
        else:
            Path(ns.metrics_table).write_text(table + '\n', encoding='utf-8')
            print(f"✓ metrics table -> {ns.metrics_table}")
    return 1 if failures else 0

if __name__ == '__main__':
    sys.exit(main())
