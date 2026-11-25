# AURUM DESK — KH-03.R5.C1 | Capture Safety & Authorization Hardening

- **Spec Version**: `KH-03.R5.C1-v1.0`
- **Mandatory Branch**: `feature/v17-9-2-structural-sl-tp-from-qa`
- **Baseline Review HEAD**: `09493feb845210a629f3c0f174a2c6edfaf2ef23` (KH-03.R5 publication head)
- **Prior Accepted R5 Run**: `20261124T135139Z_d9e24be7864d` (`aurum-desk/docs/artifacts/kh_03_r5_market_data_foundation/latest_run.json`)

## Scope (Offline Capture-Safety Hardening Only)
Closes the 3 P1 capture-safety & authorization findings from KH-03.R5 plus adjacent custody/abort truth gaps without modifying production trading/signal/SL/TP/sizing/risk/cost code, locked C2/C3/C4 transport/escrow modules, historical R1..R5 sealed evidence, or canonical dataset:

1. **P1-A (`kh_03_r5_market_data_foundation_service.py` + `kh_03_r5_capture_and_custody_service.py`)**:
   - Strict `R5OperatorGrant` schema/type/window/budget/run_id/destination validation (rejecting bool, NaN/Inf, negative, missing fields, OOS purposes, silent widening/clamping).
   - Default-deny `R5OwnerAttestationVerifier` (`DefaultDenyOwnerAttestationVerifier`) replacing caller-controlled dict flags (`trust_context`).
   - Process-local non-copyable cryptographic scope -> plan binding (`R5TrustedScopeReceipt` / `R5TrustedPlanReceipt`) defeating mutated plans even with recomputed `plan_digest_sha256` and post-enqueue mutation; mandatory expiry/run_id/destination re-validation at dispatch.
2. **P1-B (`kh_03_r5_capture_and_custody_service.py`)**:
   - Reusable `_check_r5_dispatch_guard` re-run after semaphore acquire, after every rate-limit/retry wait, after transport response, and before staging write.
   - Per-call timeout strictly `<= min(transport_timeout_ceiling_sec, remaining_session_sec, remaining_grant_sec)`.
   - Strict `Retry-After` parsing (seconds + HTTP-date) that NEVER shortens server-instructed delays and stops immediately when `wait >= remaining_budget`.
   - Atomic request reservation + wire-byte reservation separating `wire_bytes_observed_total` (including 429/5xx bodies) from `bytes_accepted_total`, plus fail-closed `BLOCKED_TRANSPORT_READ_CAP_UNSUPPORTED` when locked C4 transport cannot enforce a narrower per-call wire read cap.
   - Bounded drain and late-response discard on cancel/abort.
3. **P1-C & Adjacent Custody/Abort Truth (`kh_03_r5_capture_and_custody_service.py`)**:
   - Fail-closed `validate_r5_resource_sample_strict` + `_require_r5_resource_monitor` rejecting `None`, empty dicts, missing keys, bool/str, NaN/Inf, negative, stale (`>2s`), future timestamps, wrong volume identity, exceptions, and `sample_ok is not True`.
   - Mandatory 64-hex `raw_sha256`, stat-before-read oversize denial (`>4 MiB`), bounded chunked read, and symlink/NTFS-junction containment enforcement in `R5DevCustodyJournal.persist_snapshot`.
   - Owned-only abort cleanup removing ONLY files created by the aborted session and counting removed staged files as `0` accepted records.

## Orthogonal Safety Locks (Inviolable in KH-03.R5.C1)
- `PRODUCTION=OFF | LIVE=OFF | PAPER=OFF | AUTO=OFF | ACTIVATION_READY=NO`
- `EXTERNAL_MARKET_REQUESTS_ATTEMPTED=0 | ACCEPTED_REAL_OBSERVED_RECORDS=0`
- `OOS_EVALUATIONS=0 | OOS_READS_EXECUTED=0 | FRESH_OOS=BLOCKED_NO_UNTOUCHED_DATA`
- `CAPTURE_AUTHORIZATION=BLOCKED_OWNER_AUTHORIZATION | LIVE_NETWORK_VALIDATION=NOT_RUN_BY_DESIGN`
- `RESEARCH_ECONOMIC_VERDICT=NO_PROVEN_EDGE | KH04_ENTRY=BLOCKED_NO_DEV_CANDIDATE`
- `DATA_READINESS=INCOMPLETE | OBSERVED_BBO_COVERAGE=MISSING | FUNDING_READINESS=MISSING | ACCOUNT_FEE_READINESS=UNKNOWN_NOT_LOCALLY_VERIFIED | EXECUTION_FIDELITY=INCOMPLETE`

## Reproduction & Verification Commands
- Full offline acceptance orchestrator (must run on clean committed `HEAD` on mandatory branch):
  ```bash
  python aurum-desk/tools/run_kh_03_r5_c1_acceptance.py
  ```
- Independent separate-OS verifier (read-only audit):
  ```bash
  python aurum-desk/tools/kh_03_r5_c1_independent_verifier.py \
    --run-dir aurum-desk/docs/artifacts/kh_03_r5_c1_capture_safety/<RUN_ID> \
    --expected-run-id <RUN_ID> \
    --expected-tested-sha <TESTED_EXECUTABLE_SHA> \
    --no-write-manifest
  ```

## Sealed Runs
- Pointer updated only after Phase 2 evidence push + live HTTPS SHA-256 readback: `latest_run.json`.
