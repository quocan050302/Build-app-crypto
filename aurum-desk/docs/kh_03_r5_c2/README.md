# AURUM DESK — KH-03.R5.C2 | Technical Closure Specification

- **Spec Version**: `KH-03.R5.C2-v1.0`
- **Mandatory Branch**: `feature/v17-9-2-structural-sl-tp-from-qa`
- **Baseline Reviewed HEAD (C1)**: `420dcf06601fee960ec7e04bd0c2f96b74bfd4b8`
- **Prior C1 Tested Executable SHA**: `3ad0c2973eeaa11f1e635b7a82beddeb4b5a8db8`
- **Prior C1 Evidence Commit SHA**: `d4fd57e885fc1d525611b7227dd4971b9aacab14` (`20261125T030440Z_3ad0c2973eea`)

## 1. Scope & Non-Claims
KH-03.R5.C2 is a tightly scoped **offline technical closure** addressing three concrete review findings on C1 (`P1-D`, `P1-E`, `P1-F`). It is **NOT** R6, **NOT** live market acquisition, and **NOT** a strategy or economic change.

1. **`P1-D` — Fault Control Truth & Evidence Contract**:
   - Eliminates literal boolean PASS flags (`_rec(red, True)` / `_rec(True, grn)`) and shared-positive reuse across all 48 C1 controls.
   - Replaces boolean aggregation with execution-derived paired receipts (`FaultCaseSpec`, `FaultExecutionReceipt`, `execute_fault_case_pair`, `validate_fault_execution_receipt`, `derive_fault_control_verdict`) evaluated via registered pure predicates over raw observations.
   - Explicitly separates `BASELINE_BUG_RED` (only when backed by an immutable baseline executable witness), `NEGATIVE_CONTRACT`, and `VERIFIER_MUTATION`, and reports inherited 288 regression controls separately without relabeling legacy negative-only checks as paired.
   - Closes the active C1 certification surface (`derive_r5_c1_readiness_verdicts`, `kh_03_r5_c1_independent_verifier.py`) so unsupported legacy boolean proofs fail closed with `LEGACY_CONTROL_PROOF_INSUFFICIENT_USE_C2` (historical sealed `420dcf06` artifacts remain untouched read-only records).
2. **`P1-E` — Actual Blocking Worker Lifecycle**:
   - Replaces naked `asyncio.to_thread(...)` in `capture_r5_public_batch` with an owned bounded lifecycle adapter (`R5CaptureWorkerTracker`, `submit_tracked_transport_call`, `drain_owned_transport_workers`, `build_worker_lifecycle_receipt`) bounded by both per-session concurrency and a process-wide active capture worker cap (`<= 4`).
   - Distinguishes `RESERVED`, `QUEUED`, `STARTED`, `EXITED_OK`, `EXITED_ERROR`, `CANCELLED_BEFORE_START`, `STILL_RUNNING_AT_RETURN`, and `QUARANTINED`. Never treats `asyncio.Task.done()` as proof a blocking OS thread exited.
   - Re-validates grant expiry, real monotonic deadline, cancellation, plan binding, and resource monitor at actual worker start inside the worker wrapper (via thread-safe `threading.Event` and immutable call snapshot), computing per-call timeout at actual start.
   - If any blocking worker remains running past bounded `drain_timeout_sec`, returns `FAIL_UNDRAINED_INFLIGHT_WORKER` (`undrained_inflight_workers > 0`, `owned_executor_shutdown_complete = False`), quarantines the session, retains global capacity until real worker exit, and prevents any late acceptance/staging/custody writes.
   - Charges unknown wire bytes conservatively at the reserved bound on timeout/transport failure; reports staging unlink errors honestly without silent `except: pass`.
3. **`P1-F` — Grant Identity & One-Shot Consumption Ledger**:
   - Introduces `R5GrantConsumptionLedgerC2` and `_canonical_grant_identity_c2` enforcing **ONE-SHOT BOUNDED SESSION** semantics: one verified grant authorizes at most ONE capture session (including its bounded batches/retries inside total approved caps).
   - Blocks sequential replay, concurrent barrier replay, second plan generation from the same scope receipt, newly issued scope receipts for the same logical grant, and conflicting payloads reusing `(namespace, verifier, issuer, grant_id)`.
   - Burns a claimed grant conservatively on abort, zero-dispatch error, timeout, or quarantine; enforces unique `attempt_id`, idempotent settlement, no over-release/underflow, and no TTL eviction of spent grants.
   - Process/restart boundary: QA uses a process-local in-memory ledger; **ALL live network capture execution paths are hard-blocked** (`LIVE_GRANT_REPLAY_DURABILITY = NOT_PROVEN_BLOCKED`, `LIVE_TRANSPORT_TERMINATION = NOT_PROVEN_BLOCKED`, `G18_RESTART_TRUST_BOUNDARY = PASS_LIVE_HARD_BLOCK_PROVEN`) until a separately reviewed R6 trust boundary supplies genuine external owner attestation, durable cross-process consumption authority, and bounded live transport termination.

## 2. Source Ownership
- **Modified (strictly within C2 scope)**:
  - `aurum-desk/backend/services/kh_03_r5_market_data_foundation_service.py` (grant identity, `R5GrantConsumptionLedgerC2`, `R5GrantSessionLeaseC2`, scope/plan binding)
  - `aurum-desk/backend/services/kh_03_r5_capture_and_custody_service.py` (`R5CaptureWorkerTracker`, actual-start guard, bounded drain/quarantine, lease lifecycle, conservative byte settlement, honest abort cleanup, live durability/termination hard-block)
  - `aurum-desk/backend/services/kh_03_r5_c1_readiness_and_red_service.py` (close active C1 G26 gate against unsupported legacy boolean proofs)
  - `aurum-desk/tools/kh_03_r5_c1_independent_verifier.py` (fail-closed C1 verifier on unsupported legacy boolean proofs)
  - `aurum-desk/tools/run_kh_03_r5_c1_acceptance.py` (distinct QA grants + C2 paired control receipts)
  - `aurum-desk/backend/tests/test_kh03_r5_c1_custody_abort_and_determinism.py` (48 repaired paired execution receipts)
  - `aurum-desk/backend/tests/test_kh03_r5_capture_provenance.py`, `test_kh03_r5_readiness_and_verifier.py`, `test_kh03_r5_c1_scope_binding.py`, `test_kh03_r5_c1_deadline_cancel_and_budget.py`, `test_kh03_r5_c1_resource_fail_closed.py`, `test_kh03_r5_c1_independent_verifier_mutations.py` (distinct QA grant minting under one-shot policy + C1 verifier paired proof support)
- **Added (C2 modules, tools, tests, docs)**:
  - `aurum-desk/backend/services/kh_03_r5_c2_fault_truth_and_readiness_service.py`
  - `aurum-desk/tools/kh_03_r5_c2_independent_verifier.py`
  - `aurum-desk/tools/run_kh_03_r5_c2_acceptance.py`
  - `aurum-desk/backend/tests/test_kh03_r5_c2_fault_control_truth.py`
  - `aurum-desk/backend/tests/test_kh03_r5_c2_worker_lifecycle.py`
  - `aurum-desk/backend/tests/test_kh03_r5_c2_grant_replay.py`
  - `aurum-desk/backend/tests/test_kh03_r5_c2_verifier_mutations.py`
  - `aurum-desk/backend/tests/test_kh03_r5_c2_async_resource_regression.py`
  - `aurum-desk/docs/kh_03_r5_c2/README.md`
  - `aurum-desk/docs/artifacts/kh_03_r5_c2_technical_closure/README.md`
- **Keep-As-Is (zero edits)**: all production trading/signal/SL/TP/sizing/risk/cost/fee files, C2/C3/C4 transport/escrow modules, R3.C1/C2 worker policy service, R1..R4 files, historical R5 runner/verifier, canonical dataset, and all prior sealed evidence/pointers (`R1..R5.C1`).

## 3. Threat Model & Explicit Boundaries
- **Enforced offline**: exact paired negative+positive execution proof, truthful blocking OS thread lifecycle & quarantine, atomic one-shot grant consumption & payload conflict denial, conservative unknown-byte billing, honest abort cleanup failure reporting, and hard-blocking all live network paths in C2.
- **Not claimed within Python process**: defense against arbitrary same-process hostile code patching interpreter globals/memory, forced OS thread termination in Python, durable cross-process live grant consumption (hard-blocked), live socket cancellation under locked C4 stdlib transport (hard-blocked), or out-of-band human owner identity (default-deny).

## 4. Release Contract & Commands
- **Immutable Safety Locks**: `PRODUCTION=OFF | LIVE=OFF | PAPER=OFF | AUTO=OFF | ACTIVATION_READY=NO | ECONOMIC=NO_PROVEN_EDGE | KH04_ENTRY=BLOCKED_NO_DEV_CANDIDATE | FRESH_OOS=BLOCKED_NO_UNTOUCHED_DATA | OOS_EVALUATIONS=0 | EXTERNAL_MARKET_REQUESTS_ATTEMPTED=0 | ACCEPTED_REAL_OBSERVED_RECORDS=0 | CAPTURE_AUTHORIZATION=BLOCKED_OWNER_AUTHORIZATION | LIVE_NETWORK_VALIDATION=NOT_RUN_BY_DESIGN | DATA_READINESS=INCOMPLETE | BBO=MISSING | FUNDING=MISSING | ACCOUNT_FEE_READINESS=UNKNOWN_NOT_LOCALLY_VERIFIED | EXECUTION_FIDELITY=INCOMPLETE | LIVE_GRANT_REPLAY_DURABILITY=NOT_PROVEN_BLOCKED | LIVE_TRANSPORT_TERMINATION=NOT_PROVEN_BLOCKED`.
- **Run Full Offline Acceptance**:
  ```bash
  python aurum-desk/tools/run_kh_03_r5_c2_acceptance.py
  ```
- **Run Independent Separate-OS Verifier (Read-Only)**:
  ```bash
  python aurum-desk/tools/kh_03_r5_c2_independent_verifier.py \
    --run-dir aurum-desk/docs/artifacts/kh_03_r5_c2_technical_closure/<RUN_ID> \
    --expected-run-id <RUN_ID> \
    --expected-tested-sha <TESTED_EXECUTABLE_SHA> \
    --no-write-manifest
  ```
