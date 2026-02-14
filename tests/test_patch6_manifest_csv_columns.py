import csv

from vop_interwoven.config import Config
from vop_interwoven.csv_export import export_pipeline_to_csv, get_vop_csv_header
from vop_interwoven.metrics_manifest import load_manifest_json


def test_get_vop_csv_header_uses_manifest_columns_when_compat_off():
    cfg = Config(csv_compat_mode=False)
    manifest_cols = load_manifest_json(cfg.metrics_manifest_path).data["outputs"]["csv_columns"]
    assert get_vop_csv_header(cfg) == ["Date", "RunId", "ViewId", "ViewUniqueId", "ViewName", "ViewType"] + manifest_cols


def test_export_pipeline_to_csv_manifest_header_exact_order_when_compat_off(tmp_path):
    cfg = Config(csv_compat_mode=False)
    manifest_cols = load_manifest_json(cfg.metrics_manifest_path).data["outputs"]["csv_columns"]

    pipeline_result = {
        "views": [
            {
                "success": True,
                "view_id": 1,
                "view_name": "v",
                "raster": {
                    "width": 1,
                    "height": 1,
                    "cell_size_ft": 1.0,
                    "bounds_xy": {"xmin": 0, "ymin": 0, "xmax": 1, "ymax": 1},
                },
                "metrics": {"TotalCells": 1, "Cells_Empty": 1},
            }
        ]
    }

    out = export_pipeline_to_csv(pipeline_result, str(tmp_path), cfg, doc=None)
    with open(out["vop_csv_path"], "r", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        row = next(reader)

    assert header == ["Date", "RunId", "ViewId", "ViewUniqueId", "ViewName", "ViewType"] + manifest_cols
    assert len(row) == len(header)
