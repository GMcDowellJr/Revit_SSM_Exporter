# SSM / VOP Baseline Freeze

Status: ACTIVE (Golden Baseline)

Purpose:
Defines the frozen behavioral output for the SSM/VOP exporter prior to further refactoring.

Scope:
- View selection: deterministic sample (seeded)
- Outputs compared:
  - views_vop CSV (excluding RunId, ElapsedSec, ConfigHash)
  - views_core CSV (excluding RunId, ElapsedSec, ConfigHash)
  - occupancy PNGs (pixel-equivalent)
- Cache files are non-authoritative and excluded.

Verification:
- using ssm_vop_v1.dyn on STN_Arch_KPSC_LAB_V3_341.48 MB_2024-09-16_04-16-16pm.rvt
- SHA256 manifest comparison
- Bundle hash:
  85125f16ecef092a9d2bdcbcbd362b2486ac959e4824ec2d4fd90a9aca0f1bda

Notes:
- Output directory changes affect ConfigHash and are intentionally ignored.
- PNGs are stored under /occupancy and compared by pixel hash.
