"""Final-state totals scanner for manifest-locked metrics."""

from __future__ import annotations

from typing import Any, Callable


ANNO_BUCKETS = ("TEXT", "TAG", "DIM", "DETAIL", "LINES", "REGION", "OTHER")
DEFAULT_MODEL_CLASSES = ("WALL", "DOOR", "STAIR", "COLUMN", "LIGHT", "OTHER")


def _safe_seq(value: Any):
    if value is None:
        return []
    return value


def _get_source_type(raster: Any, key_index: int) -> str | None:
    if key_index is None or key_index < 0:
        return None

    em = getattr(raster, "element_meta", None)
    if em is None:
        return None

    meta = None
    if isinstance(em, dict):
        meta = em.get(key_index)
        if meta is None:
            meta = em.get(str(key_index))
    elif isinstance(em, list) and 0 <= key_index < len(em):
        meta = em[key_index]

    if isinstance(meta, dict):
        return meta.get("source_type")
    return None


def _get_element_meta(raster: Any, key_index: int) -> dict[str, Any]:
    if key_index is None or key_index < 0:
        return {}

    em = getattr(raster, "element_meta", None)
    if em is None:
        return {}

    meta = None
    if isinstance(em, dict):
        meta = em.get(key_index)
        if meta is None:
            meta = em.get(str(key_index))
    elif isinstance(em, list) and 0 <= key_index < len(em):
        meta = em[key_index]

    return meta if isinstance(meta, dict) else {}


def _normalize_anno_type(raw_type: Any) -> str:
    t = str(raw_type or "OTHER").upper()
    if t in ANNO_BUCKETS:
        return t
    if "REGION" in t:
        return "REGION"
    if "LINE" in t:
        return "LINES"
    return "OTHER"


def _default_model_class_resolver(meta: dict[str, Any]) -> str:
    explicit = str(meta.get("class") or "").upper().strip()
    if explicit in DEFAULT_MODEL_CLASSES:
        return explicit

    category = str(meta.get("category") or "").upper()
    if "WALL" in category:
        return "WALL"
    if "DOOR" in category:
        return "DOOR"
    if "STAIR" in category:
        return "STAIR"
    if "COLUMN" in category:
        return "COLUMN"
    if "LIGHT" in category:
        return "LIGHT"
    return "OTHER"


def scan_final_state_totals(
    raster: Any,
    manifest: dict[str, Any],
    *,
    model_presence_mode: str = "any",
    model_class_resolver: Callable[[dict[str, Any]], str] | None = None,
) -> dict[str, int]:
    """Scan finalized raster grids and emit locked aggregate totals."""
    total = int(getattr(raster, "W", 0)) * int(getattr(raster, "H", 0))
    edge_keys = _safe_seq(getattr(raster, "model_edge_key", []))
    proxy_keys = _safe_seq(getattr(raster, "model_proxy_key", []))
    anno_keys = _safe_seq(getattr(raster, "anno_key", []))
    anno_meta = _safe_seq(getattr(raster, "anno_meta", []))
    occ_host = _safe_seq(getattr(raster, "occ_host", []))
    occ_link = _safe_seq(getattr(raster, "occ_link", []))
    occ_dwg = _safe_seq(getattr(raster, "occ_dwg", []))

    classes = (
        manifest.get("families", {})
        .get("model_classes_multihot", {})
        .get("classes", list(DEFAULT_MODEL_CLASSES))
    )

    resolve_class = model_class_resolver or _default_model_class_resolver

    out = {
        "TotalCells": total,
        "Cells_Empty": 0,
        "Cells_ModelOnly": 0,
        "Cells_AnnoOnly": 0,
        "Cells_ExtOnly": 0,
        "Cells_ModelAnno": 0,
        "Cells_ModelExt": 0,
        "Cells_AnnoExt": 0,
        "Cells_All3": 0,
        "AnnoPresentFinal": 0,
        "ExtFinalCells_Any": 0,
        "ExtFinalCells_Only": 0,
        "ExtFinalCells_DWG": 0,
        "ExtFinalCells_RVT": 0,
        "ExtFinalCells_DWG_RVT": 0,
    }

    for bucket in ANNO_BUCKETS:
        out[f"AnnoFinalCells_{bucket}"] = 0
    for cls in classes:
        out[f"ModelClassCells_{cls}"] = 0

    for idx in range(total):
        has_model = bool(raster.has_model_present(idx, mode=model_presence_mode))
        has_model = has_model or (idx < len(occ_host) and bool(occ_host[idx]))
        has_model = has_model or (idx < len(occ_link) and bool(occ_link[idx]))
        has_model = has_model or (idx < len(occ_dwg) and bool(occ_dwg[idx]))
        anno_idx = anno_keys[idx] if idx < len(anno_keys) else -1
        has_anno = anno_idx != -1

        k_edge = edge_keys[idx] if idx < len(edge_keys) else -1
        k_proxy = proxy_keys[idx] if idx < len(proxy_keys) else -1

        src_edge = _get_source_type(raster, k_edge)
        src_proxy = _get_source_type(raster, k_proxy)

        host = (src_edge == "HOST") or (src_proxy == "HOST") or (idx < len(occ_host) and bool(occ_host[idx]))
        dwg = (src_edge == "DWG") or (src_proxy == "DWG") or (idx < len(occ_dwg) and bool(occ_dwg[idx]))
        rvt = (src_edge == "LINK") or (src_proxy == "LINK") or (idx < len(occ_link) and bool(occ_link[idx]))
        ext = dwg or rvt

        if has_model and has_anno and ext:
            out["Cells_All3"] += 1
        elif has_model and has_anno:
            out["Cells_ModelAnno"] += 1
        elif has_model and ext:
            out["Cells_ModelExt"] += 1
        elif has_anno and ext:
            out["Cells_AnnoExt"] += 1
        elif has_model:
            out["Cells_ModelOnly"] += 1
        elif has_anno:
            out["Cells_AnnoOnly"] += 1
        elif ext:
            out["Cells_ExtOnly"] += 1
        else:
            out["Cells_Empty"] += 1

        if has_anno:
            out["AnnoPresentFinal"] += 1
            if 0 <= anno_idx < len(anno_meta):
                anno_type = _normalize_anno_type(anno_meta[anno_idx].get("type"))
            else:
                anno_type = "OTHER"
            out[f"AnnoFinalCells_{anno_type}"] += 1

        if ext:
            out["ExtFinalCells_Any"] += 1
            if not host:
                out["ExtFinalCells_Only"] += 1
        if dwg:
            out["ExtFinalCells_DWG"] += 1
        if rvt:
            out["ExtFinalCells_RVT"] += 1
        if dwg and rvt:
            out["ExtFinalCells_DWG_RVT"] += 1

        class_hits: set[str] = set()
        for key in (k_edge, k_proxy):
            if key is None or key < 0:
                continue
            class_token = str(resolve_class(_get_element_meta(raster, key)) or "OTHER").upper()
            if class_token not in classes:
                class_token = "OTHER"
            class_hits.add(class_token)

        for class_token in class_hits:
            out[f"ModelClassCells_{class_token}"] += 1

    return out
