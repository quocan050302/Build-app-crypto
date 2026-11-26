# AURUM DESK — KH-03.R6.A1 Evidence Artifacts Root

This directory stores immutable per-run evidence bundles produced by `aurum-desk/tools/run_kh_03_r6_a1_acceptance.py` and audited in a distinct OS process by `aurum-desk/tools/kh_03_r6_a1_independent_verifier.py`.

## Rules
- Every run (pass or fail) writes to a unique directory `<UTC_YYYYMMDDTHHMMSSZ>_<tested_sha12>_<nonce>/`.
- Never overwrite or edit any sealed run directory after `26_independent_OS_verifier_manifest_sha256.json` is written.
- Post-push remote readback (`28_phase2_remote_readback_<run_id>.json`) and `latest_run.json` live strictly outside the sealed run directory to prevent circular self-hashing.
- No secrets, owner private keys, external raw market payloads, or OOS data may ever be committed here.
