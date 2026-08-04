#!/usr/bin/env python3
"""External pixel analysis for Stage A Dynamo probe outputs."""
from __future__ import annotations

import argparse, hashlib, json, math, os, sys
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageChops

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
    dims_match = bool(ia.get('dimensions') and reference_dims and ia.get('dimensions') == reference_dims)
    dark = int(ia.get('dark_line_pixel_count') or 0); unexpected = int(ia.get('unexpected_color_pixel_count') or 0); non_dark_gray = int(ia.get('non_dark_gray_foreground_pixel_count') or 0); foreground = int(ia.get('foreground_pixel_count') or 0)
    tol = max(10, int(round(0.001 * max(1, foreground))))
    fill_ok = 'acceptable' if unexpected == 0 and non_dark_gray <= tol else 'unacceptable'
    candidate = 'rejected' if mode_result.get('exceptions') or dark <= 0 else ('recommended' if fill_ok == 'acceptable' and dims_match else 'inconclusive')
    return {'internal_edges':'uncertain','hidden_back_edges':'uncertain','fill_contamination':fill_ok,'fill_contamination_evidence':{'unexpected_color_pixel_count':unexpected,'non_dark_gray_foreground_pixel_count':non_dark_gray,'non_dark_gray_tolerance':tol},'link_behavior':'requires_manual_review','dwg_behavior':'requires_manual_review','dimension_alignment':'pass' if dims_match else 'fail','candidate_status':candidate,'manual_review_required':True}


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

def analyze_json(json_path: Path) -> tuple[Path, str]:
    data=json.loads(json_path.read_text())
    name=(data.get('probe',{}).get('name') or json_path.name).lower()
    summary=[]
    if 'graphics_semantics' in name:
        for v in data.get('variants',[]):
            p=resolve_path(json_path, v.get('export',{}).get('path'))
            if p and p.exists():
                v['image_analysis']=analyze_graphics_image(p, v.get('assigned_elements',{})); summary.append(f"graphics {v.get('variant')}: off_palette={v['image_analysis'].get('off_palette_foreground_percent')} black={v['image_analysis'].get('black_violation_pixel_count')}")
            else:
                v['image_analysis']={'path': str(p) if p else None, 'status':'error', 'error':'referenced TIFF missing or path not provided'}
        data['recommendation']=recommend_graphics(data.get('variants',[]))

    elif 'model_linework' in name:
        ref=data.get('reference_element_id_tiff') or {}; ref_dims=None
        for img in ref.get('images',[]):
            p=resolve_path(json_path, img.get('path'))
            if p and p.exists(): img['analysis']=analyze_linework_image(p); ref_dims=img['analysis'].get('dimensions') or ref_dims
            else: img['analysis']={'path': str(p) if p else None, 'status':'error', 'error':'referenced TIFF missing or path not provided'}
        if ref.get('images'):
            ref['sequential_export_repeatability'] = repeatability(ref.get('images', []))
        for m in data.get('modes',[]):
            for img in m.get('images',[]):
                p=resolve_path(json_path, img.get('path'))
                if p and p.exists(): img['analysis']=analyze_linework_image(p, ref_dims)
                else: img['analysis']={'path': str(p) if p else None, 'status':'error', 'error':'referenced TIFF missing or path not provided'}
            if m.get('images'):
                m['sequential_export_repeatability'] = repeatability(m.get('images', []))
            m['classification']=classify_mode(m, ref_dims); summary.append(f"linework {m.get('mode')}: non_dark_gray={m['images'][0]['analysis'].get('non_dark_gray_foreground_pixel_count') if m.get('images') else None} unexpected={m['images'][0]['analysis'].get('unexpected_color_pixel_count') if m.get('images') else None}")
        data['difference_images']=[]
        modes_with_images = [(m, first_image_path(json_path, m)) for m in data.get('modes', [])]
        modes_with_images = [(m, p) for m, p in modes_with_images if p and p.exists()]
        if len(modes_with_images)>1:
            ref_mode, refp = modes_with_images[0]
            for m, p in modes_with_images[1:]:
                out=json_path.parent / f"{json_path.stem}.{m.get('mode')}_minus_{ref_mode.get('mode')}.diff.tiff"; data['difference_images'].append({'mode':m.get('mode'),'against':ref_mode.get('mode'),'diff':write_diff(refp,p,out)})
        data['ranked_modes']=rank_modes(data.get('modes',[]))
    elif 'alignment' in name or any(k in data for k in ('exports','calibration_markers')):
        markers=data.get('calibration_markers',[])
        for mode, exp in data.get('exports',{}).items():
            bounds=exp.get('target_bounds_uv')
            for img in exp.get('images',[]):
                p=resolve_path(json_path, img.get('path'))
                if p and p.exists():
                    eff=img.get('effective_pixel_size'); img.clear(); img.update(analyze_alignment_image(p, markers, bounds)); img['effective_pixel_size']=eff; img['pixel_size_equals_actual_width']=(img.get('actual_width')==eff)
                else:
                    img.clear(); img.update({'path': str(p) if p else None, 'status':'error', 'error':'referenced TIFF missing or path not provided'})
            vals=[im.get('affine_fit_residual',{}).get('max_residual_px') for im in exp.get('images',[]) if im.get('affine_fit_residual',{}).get('available')]
            if vals: summary.append(f"alignment {mode}: affine_max={vals[0]:.6f}")
        model_img = None
        for preferred in ('model_bounds', 'original'):
            images = data.get('exports', {}).get(preferred, {}).get('images', [])
            if images and images[0].get('status') == 'analyzed_external':
                model_img = images[0]
                break
        data['model_to_canvas_placement'] = placement(data.get('bounds', {}).get('pre_annotation_model_uv'), data.get('bounds', {}).get('canvas_uv'), model_img)
        data['evidence_status'] = alignment_evidence_status(data)
    else:
        return json_path, f"SKIP unrecognized {json_path}"
    out=json_path.with_name(json_path.stem + '.analyzed.json')
    out.write_text(json.dumps(data, indent=2, sort_keys=True))
    return out, '; '.join(summary) or f'analyzed {json_path.name}'


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
