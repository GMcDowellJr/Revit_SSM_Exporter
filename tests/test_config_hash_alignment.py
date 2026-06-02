def test_csv_config_hash_matches_root_cache_config_hash_for_real_config():
    from vop_interwoven.config import Config
    from vop_interwoven.csv_export import compute_config_hash as csv_compute_config_hash
    from vop_interwoven.root_cache import compute_config_hash as root_compute_config_hash

    cfg = Config()
    cfg.output_dir = "C:/tmp/vop-a"
    cfg.date_override = "2024-09-16"

    assert csv_compute_config_hash(cfg) == root_compute_config_hash(cfg)
