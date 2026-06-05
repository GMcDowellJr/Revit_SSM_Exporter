"""Static regression tests for the AREAL HIGH single-extraction contract."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "vop_interwoven" / "pipeline.py"
RASTER = ROOT / "vop_interwoven" / "core" / "raster.py"


def _call_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _calls_named(node, names):
    return [call for call in ast.walk(node) if isinstance(call, ast.Call) and _call_name(call.func) in names]


def _is_elem_class_areal_if(node):
    return (
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "elem_class"
        and any(isinstance(comp, ast.Constant) and comp.value == "AREAL" for comp in node.test.comparators)
    )


def _find_function(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError("function {} not found".format(name))


def _find_element_loop(render_fn):
    for node in ast.walk(render_fn):
        if isinstance(node, ast.For) and isinstance(node.target, ast.Tuple):
            target_names = [elt.id for elt in node.target.elts if isinstance(elt, ast.Name)]
            if target_names == ["element_idx", "elem_wrapper"]:
                return node
    raise AssertionError("per-element loop not found")


def test_areal_path_extracts_geometry_once_before_raster_decompose():
    tree = ast.parse(PIPELINE.read_text(encoding="utf-8"))
    render_fn = _find_function(tree, "render_model_front_to_back")
    element_loop = _find_element_loop(render_fn)

    areal_extract_ifs = [
        node for node in ast.walk(element_loop)
        if _is_elem_class_areal_if(node)
        and _calls_named(ast.Module(body=node.body, type_ignores=[]), {"extract_areal_geometry"})
    ]
    assert len(areal_extract_ifs) == 1

    areal_extract_body = ast.Module(body=areal_extract_ifs[0].body, type_ignores=[])
    extraction_calls = _calls_named(areal_extract_body, {"extract_areal_geometry", "get_element_silhouette"})
    assert [_call_name(call.func) for call in extraction_calls] == ["extract_areal_geometry"]

    rasterize_areal_calls = _calls_named(element_loop, {"rasterize_areal_loops"})
    decompose_calls = _calls_named(element_loop, {"decompose_to_rects"})
    assert len(rasterize_areal_calls) == 1
    assert len(decompose_calls) == 1

    rasterize_call = rasterize_areal_calls[0]
    decompose_call = decompose_calls[0]
    assert decompose_call.lineno > rasterize_call.lineno
    assert isinstance(decompose_call.args[0], ast.Name)
    assert decompose_call.args[0].id == "_elem_cells"

    out_cells_keywords = [kw for kw in rasterize_call.keywords if kw.arg == "_out_cells"]
    assert len(out_cells_keywords) == 1
    assert isinstance(out_cells_keywords[0].value, ast.Name)
    assert out_cells_keywords[0].value.id == "_elem_cells"


def test_rasterize_silhouette_loops_does_not_extract_geometry():
    tree = ast.parse(RASTER.read_text(encoding="utf-8"))
    rasterize_fn = _find_function(tree, "rasterize_silhouette_loops")

    forbidden = {
        "extract_areal_geometry",
        "get_element_silhouette",
        "decompose_to_rects",
    }
    forbidden_calls = _calls_named(rasterize_fn, forbidden)
    assert forbidden_calls == []

    out_cell_adds = [
        call for call in ast.walk(rasterize_fn)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "add"
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "_out_cells"
    ]
    assert out_cell_adds, "_out_cells should be populated from committed raster cells"
