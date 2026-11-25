# AURUM DESK — KH-03.R5.C2 | Technical Closure Evidence Index

- **Spec**: `aurum-desk/docs/kh_03_r5_c2/README.md` (`KH-03.R5.C2-v1.0`)
- **Mandatory Branch**: `feature/v17-9-2-structural-sl-tp-from-qa`
- **Baseline Reviewed HEAD (C1)**: `420dcf06601fee960ec7e04bd0c2f96b74bfd4b8`
- **Prior C1 Sealed Run**: `20261125T030440Z_3ad0c2973eea` (`aurum-desk/docs/artifacts/kh_03_r5_c1_capture_safety/latest_run.json`, read-only preserved)

## Artifacts Policy
- Each execution creates a unique immutable run directory `<YYYYMMDDTHHMMSSZ>_<TESTED_SHA12>/` containing `00`..`22`, `README.md`, `HANDOVER_KH03_R5_C2_VI.txt`, `logs/`, `junit/`, and `checksums/`.
- No prior sealed run directory or pointer (`R1..R5.C1`) is ever modified or overwritten.
- `latest_run.json` and `23_phase2_remote_readback_<run_id>.json` are written ONLY after Phase 2 push and live remote SHA-256 readback verification of all sealed run artifacts.
