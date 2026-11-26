# AURUM DESK — KH-03.R6.A1 | End-to-End Acquisition & Trust Boundary Closure

- **Spec Version:** `KH-03.R6.A1-v1.0`
- **Repository:** `https://github.com/quocan050302/Build-app-crypto`
- **Mandatory Branch:** `feature/v17-9-2-structural-sl-tp-from-qa`
- **Baseline Reviewed HEAD:** `28b252676a10aa21bec7ce14fbd9415db230c2f4`
- **Prior R6 Stage A Tested Executable SHA:** `529bdaf2e533d3616b1a8e0842c048da3b81126b`
- **Prior R6 Sealed Evidence Commit SHA:** `c76bd74dedf2cfe42682cb4b9d68f11e6d585def`
- **Prior R6 Run ID:** `20261126T005509Z_529bdaf2e533`
- **Mode:** `OFFLINE / LOOPBACK QA ONLY` — External market capture is **HARD-BLOCKED** (`BLOCKED_A1_EXTERNAL_CAPTURE_DISABLED`, `EXTERNAL_MARKET_REQUESTS=0`, `ACCEPTED_REAL_OBSERVED_RECORDS=0`, `OOS_EVALUATIONS=0`, `PRODUCTION/LIVE/PAPER/AUTO=OFF`, `ACTIVATION_READY=NO`, `ECONOMIC=NO_PROVEN_EDGE`).

## Mission Scope (`P1-G` .. `P1-K`)

1. **`P1-G` (End-to-End Acquisition Orchestrator — `F01..F07`):** Remove `AUTHORIZED_SESSION_CLAIMED_AND_READY` handshake-only exit in `run_kh_03_r6_capture.py`; enforce strict `--dry-run` plan validation (zero claim/socket/write, nonzero exit on invalid plan/preflight); hard-block external capture in A1 (`BLOCKED_A1_EXTERNAL_CAPTURE_DISABLED`) before consuming any real owner grant; implement real end-to-end acquisition orchestrator (`kh_03_r6_a1_acquisition_service.py`) exercised via real OS subprocess QA loopback CLI mode through ledger, supervisor, market-data validators, custody journal, and sealed QA manifest.
2. **`P1-H` (Full Plan, Grant, Registry, Request & Lease Binding — `F08..F12`):** Shared `canonicalize_r6_bound_plan_core()` with deep-frozen immutable caps (`MappingProxyType` + sorted strict tuples); recompute canonical digest + constant-time MAC + strict grant/registry/release/destination/window/lease subset on every verification; bind `claim_r6_grant_session_atomic()` to a verified immutable `R6BoundCapturePlan` storing effective PLAN caps; enforce per-attempt `authorize_r6_a1_dispatch_request()` validating `validate_r6_endpoint_contract()` on every dispatch.
3. **`P1-I` (Reservation, Child IPC Envelope, Cumulative Transport Caps & Fail-Closed Settlement — `F13..F21`):** Strict complete per-attempt cap vector rejecting zero-cap required I/O; parent-authoritative `build_r6_a1_worker_envelope()` rejecting child request overrides and missing caps/default retries; strict child IPC validation blocking non-loopback even on direct `--child-worker` invocation; cumulative REST/WS retry/reconnect/read/decode/error/sentinel accounting; fail-closed `supervise_r6_session()` checking cancel/deadline post-response and inspecting durable settlement receipt before acceptance; fail-closed `settle_r6_io_budget()` enforcing `actual_known + uncertain <= reserved` and `cumulative <= PLAN cap` with atomic overrun quarantine (zero clamping); cross-process file-locked monotonic anchor synchronization.
4. **`P1-J` (Trusted Supervisor -> Settlement -> Provenance -> Custody Chain — `F22..F26`):** Internal capability-only `issue_r6_a1_trusted_supervisor_result()` minted exclusively by `supervise_r6_session()` after actual OS reap and verified durable settlement; removes caller-supplied `Mapping` flag trust in `mint_r6_raw_provenance_receipt()`; enforces exact raw bytes match, single-use token verification, A1 `QA_LOOPBACK`-only provenance policy (never `RAW_OBSERVED_VERIFIED`), and read-only post-restart independent lineage verification in `R6CustodyJournal`.
5. **`P1-K` (Frozen Executable Release & CLI Contract — `F27..F30`):** Solves `tested_executable_sha` vs publication HEAD via `verify_r6_a1_release_binding()` and `prepare_r6_a1_frozen_execution_context()` (detached read-only worktree at exact `tested_executable_sha` or strict evidence-only diff proof rejecting any executable/config/import drift); corrects `--execute-authorized` and release receipt flags in unsigned owner review templates; separates pre-verifier pending state from final independent recomputation.

## Key Files

- **Spec:** [`SPEC_KH_03_R6_A1_v1.0.md`](./SPEC_KH_03_R6_A1_v1.0.md)
- **New A1 Services:**
  - `aurum-desk/backend/services/kh_03_r6_a1_trust_service.py` (`F08`, `F12`, `F15`, `F22`, verified-grant/lease/reservation/settlement registries, A1 external hard-block)
  - `aurum-desk/backend/services/kh_03_r6_a1_release_service.py` (`F27`, `F28`, `F29`, `F30`)
  - `aurum-desk/backend/services/kh_03_r6_a1_acquisition_service.py` (`F02..F07`)
  - `aurum-desk/backend/services/kh_03_r6_a1_acceptance_service.py` (`M0` real RED from `529bdaf2`, A1 paired fault truth, 36-gate verdict engine, Vietnamese handover)
- **Hardened R6 Services (Explicitly Mapped in `01_function_ownership_keep_as_is_manifest.json`):**
  - `aurum-desk/tools/run_kh_03_r6_capture.py` (`S1`)
  - `aurum-desk/backend/services/kh_03_r6_bounded_transport_service.py` (`S2`)
  - `aurum-desk/backend/services/kh_03_r6_durable_grant_service.py` (`S3`)
  - `aurum-desk/backend/services/kh_03_r6_custody_readiness_service.py` (`S6`)
  - `aurum-desk/backend/services/kh_03_r6_acceptance_service.py` (`S7` — unsigned template `--execute-authorized` fix)
- **New A1 Tools & Verifier:**
  - `aurum-desk/tools/run_kh_03_r6_a1_acceptance.py`
  - `aurum-desk/tools/kh_03_r6_a1_independent_verifier.py`
- **Evidence Root:** `aurum-desk/docs/artifacts/kh_03_r6_a1_acquisition_trust_closure/`
