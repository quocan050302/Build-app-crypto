# SPECIFICATION — AURUM DESK KH-03.R6.A1-v1.0

## 1. Purpose, Threat Model & Non-Claims

`KH-03.R6.A1` closes exactly five security, integration, and release-truth findings (`P1-G` through `P1-K`) identified in code review of `KH-03.R6` (`529bdaf2e533d3616b1a8e0842c048da3b81126b`, published at `28b252676a10aa21bec7ce14fbd9415db230c2f4`).

### Security Boundary & Explicit Non-Claims
- **Mitigated:** Handshake-only grant consumption; mutable plan caps / stale digest-MAC reuse; child request overriding parent deadlines/caps/retries; unreserved sentinel/error/retry byte leaks; ignored settlement failure or clamped overrun; caller-supplied `Mapping` forging `RAW_OBSERVED_VERIFIED` provenance; naive `HEAD == tested_executable_sha` rejecting legitimate published releases; dry-run accepting invalid plans on parse alone.
- **Explicit Non-Claims:**
  1. Does **NOT** defend against arbitrary code execution by a compromised local OS administrator/root or stolen owner Ed25519 signing keys.
  2. Local `127.0.0.1` loopback QA tests do **NOT** prove external network termination, Bitget Classic v2 live availability, XAUUSDT observed instrument identity, account fee tier, funding completeness, BBO state/event coverage, or execution slippage.
  3. External capture is **HARD-BLOCKED** throughout A1 (`A1_EXTERNAL_CAPTURE_ENABLED = False`, `BLOCKED_A1_EXTERNAL_CAPTURE_DISABLED`). Future R6.B requires a separate narrowly reviewed enablement commit and genuine owner-provisioned trust root + signed grant.

## 2. Scope Lock (`KEEP AS IS`)
- **Locked unchanged:** All production trading/execution/strategy/D4/A1/SI8/adapter/fee/Decimal P&L code (`CostAssumptions` maker `0.0002`, taker `0.0004`, planned risk cap `2.50 USD`, Net R:R `>= 2.0`), canonical DEV dataset (`9f301fee2e620c882b205b857fc63b73c3e36f550161f4100e64fcba7ff98c18`), OOS escrow (zero reads, zero evaluations), KH-02 C2/C3/C4, R3.C1/C2 worker policy, R4, R5, R5.C1, R5.C2 (including C2 live hard block), R6 Classic v2 source contract registry (`kh_03_r6_source_contract_service.py`), R6 market data validators (`kh_03_r6_market_data_service.py`), R6 acceptance/verifier tools, and all sealed evidence `R1..R6`.
- **Economic & Activation Flags:** `ECONOMIC=NO_PROVEN_EDGE`, `KH04_ENTRY=BLOCKED_NO_DEV_CANDIDATE`, `FRESH_OOS=BLOCKED_NO_UNTOUCHED_DATA`, `OOS_EVALUATIONS=0`, `ACTIVATION_READY=NO`, `PRODUCTION=OFF`, `LIVE=OFF`, `PAPER=OFF`, `AUTO=OFF`.

## 3. Function Ownership Mapping (`F01..F30`)

| Area | Functions | Module | Change Type |
|---|---|---|---|
| `M0` | Isolated real RED reproducer (`529bdaf2` via `git show`) | `kh_03_r6_a1_acceptance_service.py` | NEW A1 |
| `M1 / P1-G` | `F01` `run_capture_cli` | `tools/run_kh_03_r6_capture.py` | HARDENED R6 (`S1`) |
| `M1 / P1-G` | `F02..F07` session build/tasks/REST+WS/run/finalize/receipt | `services/kh_03_r6_a1_acquisition_service.py` | NEW A1 |
| `M2 / P1-H` | `F08` `canonicalize_r6_bound_plan_core`, `F12` `authorize_r6_a1_dispatch_request` | `services/kh_03_r6_a1_trust_service.py` | NEW A1 |
| `M2 / P1-H` | `F09` `build_r6_bound_capture_plan`, `F10` `verify_r6_bound_plan_integrity` | `services/kh_03_r6_bounded_transport_service.py` | HARDENED R6 (`S2`) |
| `M2 / P1-H` | `F11` `claim_r6_grant_session_atomic` | `services/kh_03_r6_durable_grant_service.py` | HARDENED R6 (`S3`) |
| `M3 / P1-I` | `F13` `reserve_r6_io_budget_atomic`, `F14` `mark_r6_attempt_started`, `F20` `settle_r6_io_budget`, `F21` `finalize_r6_session` / `recover_r6_ledger_after_restart` / `_sync_anchor_from_meta` | `services/kh_03_r6_durable_grant_service.py` | HARDENED R6 (`S3`) |
| `M3 / P1-I` | `F15` `build_r6_a1_worker_envelope` / `validate_r6_a1_worker_envelope_strict` | `services/kh_03_r6_a1_trust_service.py` | NEW A1 |
| `M3 / P1-I` | `F16..F19` owned worker, child CLI, REST/WS cumulative bounds, `supervise_r6_session` | `services/kh_03_r6_bounded_transport_service.py` | HARDENED R6 (`S2`) |
| `M4 / P1-J` | `F22` `issue_r6_a1_trusted_supervisor_result` | `services/kh_03_r6_a1_trust_service.py` | NEW A1 |
| `M4 / P1-J` | `F23..F26` provenance mint/verify, custody journal/readonly/manifest, verdicts | `services/kh_03_r6_custody_readiness_service.py` | HARDENED R6 (`S6`) |
| `M5 / P1-K` | `F27` `verify_r6_a1_release_binding`, `F28` `prepare_r6_a1_frozen_execution_context`, `F29..F30` template & gate transition | `services/kh_03_r6_a1_release_service.py`, `services/kh_03_r6_acceptance_service.py` (`S7`) | NEW A1 + HARDENED R6 (`S7`) |

## 4. Mandatory 36 A1 Engineering Gates (`G01..G36`)
- `G01_BRANCH_SHA_CLEAN` .. `G35_ECONOMIC_ACTIVATION`: 35 mandatory pre-push engineering gates.
- `G36_REMOTE_PUBLICATION`: remote SHA-256 readback of all sealed blobs, tested executable ancestry, evidence commit, and pointer.
- Success label (ONLY when 36/36 mandatory A1 gates PASS, 35/35 pre-push):
  `ENGINEERING_PASS_OFFLINE_R6_A1_END_TO_END_QA_TRUST_CLOSED_CAPTURE_BLOCKED_NO_PROVEN_EDGE`
- External stages remain strictly outside the A1 denominator (`0/4` executed):
  - `R6.B_AUTHORIZATION`: `BLOCKED_A1_EXTERNAL_CAPTURE_DISABLED` / `BLOCKED_OWNER_AUTHORIZATION`
  - `R6.B_SMOKE`: `NOT_RUN`
  - `R6.C_DEV`: `NOT_RUN`
  - `R6.D_R7_DATA_READINESS`: `BLOCKED_NO_OBSERVED_DEV_DATA`
