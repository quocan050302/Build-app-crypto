# AURUM DESK — KH-03.R6 Observed Market Data Acquisition Evidence Root

This directory stores immutable per-run verification artifacts for `KH-03.R6`:
- Each run lives in a unique subdirectory `<YYYYMMDDTHHMMSSZ>_<TESTED_SHA12>_<NONCE6>/` (or `<YYYYMMDDTHHMMSSZ>_<TESTED_SHA12>/`) containing artifacts `00`..`27`, `README.md`, `HANDOVER_KH03_R6_VI.txt`, `logs/`, `junit/`, and `checksums/`, sealed by the separate-OS independent verifier manifest `26_independent_OS_verifier_manifest_sha256.json`.
- Post-push remote readback receipts (`28_phase2_remote_readback_<run_id>.json`) and stage-scoped pointers (`latest_run.json`, `latest_offline_run.json`) live as sibling files under this root so sealed run directories are NEVER modified after sealing.
- Raw market data payloads, signed owner grants, owner trust roots, and durable SQLite ledgers are NEVER stored in Git; they remain exclusively on local ACL-restricted fixed volumes outside the repository.
