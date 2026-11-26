# KH-03.R6.A1 Corrective Closure Evidence Root

All runs (including failed runs) write into unique immutable per-run directories:
`aurum-desk/docs/artifacts/kh_03_r6_a1_corrective_closure/<UTC_YYYYMMDDTHHMMSSZ>_<TESTED_SHA12>_<NONCE>/`

- Never overwrites prior `kh_03_r6_a1_acquisition_trust_closure`, `kh_03_r6_observed_market_data`, or earlier sealed artifacts.
- `25_remote_readback_<run_id>.json`, `latest_run.json`, and `latest_successful_run.json` live as sibling files outside the sealed run directory (no post-manifest edits; no self-referential hash recursion).
- Raw market data, owner private keys, and OOS datasets are strictly excluded (`EXTERNAL_MARKET_REQUESTS=0`, `OOS_EVALUATIONS=0`).
