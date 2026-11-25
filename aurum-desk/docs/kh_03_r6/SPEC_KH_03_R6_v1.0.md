# AURUM DESK — KH-03.R6 Specification Attachment (`KH-03.R6-v1.0`)

- **Spec ID:** `KH-03.R6-v1.0`
- **Date:** `2026-11-25`
- **Repository:** `https://github.com/quocan050302/Build-app-crypto`
- **Mandatory Branch:** `feature/v17-9-2-structural-sl-tp-from-qa`
- **Baseline Reviewed HEAD:** `fd0df117ca38f0ac72312d27265dcdabaa5a0dba`
- **Prior Accepted Executable (`KH-03.R5.C2`):** `68549a8a44a424eb58f9c21bec49199ab296eb1a`
- **Prior Accepted Evidence Commit (`KH-03.R5.C2`):** `f79419f959639994b6d631df16cd352bbcbb2e69` (`20261125T061403Z_68549a8a44a4`)

## Summary of Binding Requirements

1. **Stage Separation:**
   - Stage A (Offline Engineering & Acquisition Readiness): mandatory gates `G01..G30`, `G35`, `G36` (`32` gates). Zero external market sockets.
   - Stage B (Authorized External Smoke) & Stage C (Bounded DEV Collection): conditional gates `G31..G34` (`4` gates). Strictly `BLOCKED_OWNER_AUTHORIZATION` / `NOT_RUN_BLOCKED_OWNER_AUTHORIZATION` when no separate authenticated local owner trust root + one-shot signed grant exists.
   - Gate reporting MUST show per-stage denominators (e.g., `32/32` mandatory Stage A, `0/4` Stage B/C executed). Never report `36/36 PASS` when capture did not execute.
2. **Keep-As-Is Boundaries:**
   - Production trading, Entry/SL/TP/sizing/one-position/adapters/fee defaults/D4/A1/SI8 unchanged.
   - Canonical DEV dataset (`9f301fee2e620c882b205b857fc63b73c3e36f550161f4100e64fcba7ff98c18`), OOS escrow (`OOS_EVALUATIONS = 0`), KH-02 C2/C3/C4, R3.C1/C2 worker policy, R5.C2 live hard-block, and all prior sealed artifacts (`R1..R5.C2`) untouched.
3. **Core Engineering Deliverables (`M1`..`M6`, `T1`..`T3`):**
   - `M1`: Verified official Bitget Classic v2 source contract registry (`F01..F04`), preferring Classic v2 to match existing `XAUUSDT` adapters and strictly forbidding v2/v3 mixing or silent symbol substitution.
   - `M2`: Cross-process/restart durable one-shot owner authorization ledger (`F05..F12`) backed by SQLite (`BEGIN IMMEDIATE`, `synchronous=FULL`) + external anchor + Ed25519 signature verification (`cryptography`) with no embedded owner key and fail-closed recovery on crash/rollback/tamper/missing store.
   - `M3`: Distinct R6 bounded public transport boundary (`F13..F18`) with owned subprocess isolation, verified OS process-tree termination & reap (Windows Job Object / POSIX PGID), bounded streaming REST GET (slow-drip deadline, pre-read + decompression caps, no redirect, Retry-After), bounded public WS session (ack/epoch/heartbeat/frame/queue/bounded reconnect), and strict 0.5s multi-volume resource guard (`>=20.5 GiB` preflight, `>=20.0 GiB` runtime, `>=512 MiB` RAM, global worker cap `<= 4`, no disk waiver).
   - `M4`: Strict `Decimal` / UTC-ms OHLCV pagination/dedup/conflict quarantine/unclosed exclusion, BBO event vs inferred state-known coverage (never REST snapshot as streaming, never carry across disconnect), funding schedule/completeness (never zero-fill missing), owner fee/slippage honesty (`UNKNOWN_NOT_LOCALLY_VERIFIED` / `MISSING`), deterministic gap ledger (`F19..F24`).
   - `M5`: DEV-only / QA-only partition & symlink/junction/OOS deny-before-open guard, R6 raw provenance receipts (`QA_LOOPBACK` vs `RAW_OBSERVED_VERIFIED`), chunked atomic custody journal + anchor + crash recovery, sealed DEV dataset manifest, 36-gate stage-scoped verdicts & Vietnamese handover (`F25..F29`).
   - `M6` + `T1..T3`: `>= 56` new paired negative+positive controls (`A01..E06`), `48+43` C1/C2 paired + `288` inherited regression executed separately, `>= 30` new separate-OS verifier mutations (+ `135` inherited mutations), full repo LPT-sharded pytest with 1:1 JUnit bijection, full A1/SI8/adapter/D4 parity, exact R4 Decimal replay + 6 cost scenarios + E1/E2/E3 arms, W1/W2/W4/W1-repeat benchmarks (pytest 155 + science 8,000-bar + new R6 fixture ingest), and remote HTTPS SHA-256 readback verification.
4. **Honesty & Non-Activation:**
   - `PRODUCTION = OFF`, `LIVE = OFF`, `PAPER = OFF`, `AUTO = OFF`, `ACTIVATION_READY = NO`, `ECONOMIC = NO_PROVEN_EDGE`, `FRESH_OOS = BLOCKED_NO_UNTOUCHED_DATA`, `KH04_ENTRY = BLOCKED_NO_DEV_CANDIDATE`, `OOS_EVALUATIONS = 0`.
