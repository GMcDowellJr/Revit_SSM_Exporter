from vop_interwoven.config import Config
from vop_interwoven.streaming import StreamingExporter
from vop_interwoven.metrics_manifest import load_manifest_json


def test_streaming_vop_header_uses_cfg_and_includes_metadata_for_manifest_mode(tmp_path):
    cfg = Config(csv_compat_mode=False)
    exporter = StreamingExporter(
        output_dir=str(tmp_path),
        cfg=cfg,
        doc=None,
        export_png=False,
        export_csv=True,
        export_json=False,
    )
    try:
        manifest_cols = load_manifest_json(cfg.metrics_manifest_path).data["outputs"]["csv_columns"]
        expected = ["Date", "RunId", "ViewId", "ViewUniqueId", "ViewName", "ViewType"] + manifest_cols
        assert exporter.csv_vop_writer.fieldnames == expected
    finally:
        exporter.finalize()
