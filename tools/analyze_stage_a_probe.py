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
        missing = sorted(set(requested) - set(actual))
        coverage_status = 'FAIL' if missing else ('PASS' if actual else 'INCONCLUSIVE')
        checks.append(_check('requested_case_coverage', coverage_status,
                             ['REQUESTED_CASE_NOT_ANALYZED'] if missing else ([] if actual else ['NO_CASES_ANALYZED']),
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
                complete = len(matching) == len(requested_for_source)
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
    ns=ap.parse_args(argv)
    failures=0
    for jp in collect(ns.paths):
        try:
            out, msg=analyze_json(jp); print(f"✓ {jp} -> {out}: {msg}")
        except Exception as e:
            failures+=1; print(f"✗ {jp}: {type(e).__name__}: {e}")
    return 1 if failures else 0

if __name__ == '__main__':
    sys.exit(main())
