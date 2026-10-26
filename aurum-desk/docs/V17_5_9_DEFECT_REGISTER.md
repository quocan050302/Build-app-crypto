# AURUM V17.5.9 — DEFECT REGISTER
## PRODUCTION CORRECTNESS REPAIR & VERIFICATION REGISTER

**Baseline Version:** AURUM V17.5.8 (`a25a479d3aa8bd6784d02c699241ba8d7081ef9e`)  
**Target Release:** AURUM V17.5.9  
**Application Root:** `aurum-desk/`  
**Development Branch:** `fix/v14-1-rr-flow`  

---

### Executive Defect Matrix

| ID | Status | Root Cause | Production Function | Test Evidence | Fix Scope |
|---|---|---|---|---|---|
| **D01** | **FIXED** | Reference provenance: Missing source candle in `closed_bars` was permitted, ambiguous price matches picked arbitrary first bar, and contradictory caller metadata was merged rather than rejected. | `backend/services/stop_placement_service.py` -> `resolve_verified_reference_node` | `tests/test_v17_5_9_reference_source_authority.py` (R01–R10) | Production reference node resolver |
| **D02** | **FIXED** | Break provenance: Caller-supplied break bar was substituted as authoritative when canonical source bar was missing from `closed_bars`. | `backend/services/stop_placement_service.py` -> `resolve_verified_break_candle` | `tests/test_v17_5_9_break_source_authority.py` (B01–B02, B07–B12) | Break candle provenance resolver |
| **D03** | **FIXED** | ARM wiring: `validate_arm_invariants()` was exported but never invoked in `_arm_paper_candidate()` before persisting executable orders. | `backend/services/scalp_watcher_orchestrator.py` -> `_arm_paper_candidate()`, `validate_arm_invariants()` | `tests/test_v17_5_9_arm_production_wiring.py` (A00–A12) | Pre-ARM invariant barrier |
| **D04** | **FIXED** | Cost configuration: Invalid higher-priority cost settings silently fell through to lower-priority settings instead of failing closed. | `backend/services/scalp_watcher_orchestrator.py` -> `resolve_ordered_cost_setting()`, `evaluate_cycle()`, `_arm_paper_candidate()` | `tests/test_v17_5_9_cost_fail_closed.py` (C01–C10) | Strict cost setting resolution & ARM guard |
| **D05** | **FIXED** | Financial parity: `assert_order_financial_parity()` checked slippage but defaulted missing fields to 0.10 and ignored all calculator fields. | `backend/services/plan_financial_context_service.py` -> `assert_order_financial_parity()` | `tests/test_v17_5_9_financial_parity.py` (F01–F14) | Comprehensive financial reconciler |
| **D06** | **FIXED** | Timestamp validation: Malformed timestamp conversions threw unhandled `ValueError`/`TypeError` crashing the watcher cycle. | `backend/services/stop_placement_service.py` -> `parse_valid_market_timestamp()`, `resolve_verified_break_candle()`, `resolve_verified_reference_node()` | `tests/test_v17_5_9_break_source_authority.py` (B03–B06), `tests/test_v17_5_9_reference_source_authority.py` (R06–R07) | Pure timestamp parser & validation boundary |
| **D07** | **FIXED** | Acceptance provenance: Runner evaluated `ACCEPTED` even when working tree was dirty (`is_dirty: true`), permitting unverified commits. | `tools/run_v17_5_9_acceptance.py` -> `get_git_info()`, `evaluate_final_verdict()` | `tests/test_v17_5_9_acceptance_runner_self_test.py` (T01–T09) | Clean Git release provenance gate |

---

### Detailed Defect Analyses

#### D01 — Reference Provenance & Authority
- **Reproduction Input:** Caller supplies reference with valid timestamp but missing from `closed_bars`, or reference price matching 2+ candles at different timestamps, or caller metadata contradictory to confirmed swing node.
- **Observed Baseline Behavior:** When candle was absent from `closed_bars`, validation succeeded without source proof. Ambiguous price matches arbitrarily picked the first bar. Contradictory caller prices were merged.
- **Expected Behavior:** Reject with `REFERENCE_CANDLE_MISSING`, `AMBIGUOUS_REFERENCE_SOURCE`, or `REFERENCE_METADATA_MISMATCH`.
- **Root Cause:** Permissive `if closed_bars:` check with no `else: return UNAVAILABLE`, arbitrary indexing `ref_occ = matching_bars[0]`, and lack of strict equality check against `confirmed_swings`.
- **Production Patch:** Hardened `resolve_verified_reference_node()` in `backend/services/stop_placement_service.py`:
  - Strict validation of normalized direction (`LONG`/`BUY` vs `SHORT`/`SELL`).
  - Strict finite positive numeric price check (`INVALID_REFERENCE_PRICE`).
  - Exact comparison against confirmed swing metadata (`REFERENCE_METADATA_MISMATCH`).
  - Unambiguous matching requirement: `len(matching_bars) > 1 -> AMBIGUOUS_REFERENCE_SOURCE`.
  - Authoritative source candle lookup in `closed_bars` (`REFERENCE_CANDLE_MISSING`).
  - Authoritative extreme validation: swing High must match candle High, swing Low must match candle Low (`REFERENCE_EXTREME_MISMATCH`).
- **Tests:** `tests/test_v17_5_9_reference_source_authority.py` (10/10 PASS).

#### D02 — Break Candle Provenance
- **Reproduction Input:** Break descriptor supplied with `bar` object but absent from `closed_bars`.
- **Observed Baseline Behavior:** `source_bar = supplied_bar` allowed caller to establish structural break provenance without canonical closed bars.
- **Expected Behavior:** Reject with `MISSING_BREAK_SOURCE_CANDLE`.
- **Root Cause:** Permissive fallback `if source_bar is None: if supplied_bar is not None: source_bar = supplied_bar`.
- **Production Patch:** Removed unverified fallback in `resolve_verified_break_candle()`. Strictly requires source bar in `closed_bars`, validates OHLC integrity, checks consistency with supplied bar if both exist (`BREAK_CANDLE_MISMATCH`), verifies bar closure (`BREAK_CONFIRMATION_BEFORE_CLOSE`), and enforces strict close-through.
- **Tests:** `tests/test_v17_5_9_break_source_authority.py` (12/12 PASS).

#### D03 — ARM Invariant Validator Production Wiring
- **Reproduction Input:** Invalid direction, NaN stop loss, zero quantity, expired candidate, or stale quote passed into production `_arm_paper_candidate()`.
- **Observed Baseline Behavior:** `validate_arm_invariants()` was never called in the production ARM flow; invalid candidates could bypass shared validation.
- **Expected Behavior:** `validate_arm_invariants()` executed right before `models.PaperOrder` persistence; returns controlled failure and creates no `PaperOrder`.
- **Root Cause:** Helper function was authored and exported in V17.5.8 but left disconnected from the production `_arm_paper_candidate()` pipeline.
- **Production Patch:**
  - Hardened `validate_arm_invariants()` in `backend/services/scalp_watcher_orchestrator.py`: added direction check (`INVALID_DIRECTION`), safe numeric extraction for quantity and price geometry (entry, SL, TP).
  - Wired `validate_arm_invariants()` directly into `_arm_paper_candidate()` before database order creation.
  - Recorded specific rejection reasons in `WatchSetup.rejection_reason`.
- **Tests:** `tests/test_v17_5_9_arm_production_wiring.py` (13/13 PASS, including spy test proving production execution).

#### D04 — Cost Configuration Fail-Closed Authority
- **Reproduction Input:** Higher-priority setting `slippage_usd="INVALID"` configured alongside lower-priority `default_slippage="0.10"`.
- **Observed Baseline Behavior:** Orchestrator skipped the invalid higher-priority setting and silently fell through to `default_slippage=0.10`.
- **Expected Behavior:** Reject with `INVALID_COST_CONFIGURATION` and block ARM.
- **Root Cause:** `get_system_config_cost_value()` returned the first valid value across keys without distinguishing `NOT_CONFIGURED` from `INVALID`.
- **Production Patch:**
  - Implemented `resolve_ordered_cost_setting(db, ordered_keys, setting_name)` returning structured status: `VALID`, `NOT_CONFIGURED`, `INVALID`.
  - If highest-priority configured key is invalid, fails closed with `INVALID` instead of falling back.
  - `evaluate_cycle()` detects cost configuration errors and records `policy_dict["cost_config_error"]` and `policy_dict["costs_valid"] = False`.
  - `_arm_paper_candidate()` verifies cost configuration validity before ARM.
- **Tests:** `tests/test_v17_5_9_cost_fail_closed.py` (10/10 PASS).

#### D05 — Financial Parity Reconciliation
- **Reproduction Input:** Plan, snapshot, and fill objects with mismatched risk budget, quantity, fees, or net R:R, or missing mandatory fields.
- **Observed Baseline Behavior:** `assert_order_financial_parity()` only checked entry slippage and defaulted absent values to `0.10`, reporting `is_parity=True`.
- **Expected Behavior:** Full cross-stage reconciliation across planned entry, SL, TP, quantity, risk budget, gross/net R:R, maker/taker fees, and slippage; missing mandatory fields fail with `INCOMPLETE` / `MISMATCH`.
- **Root Cause:** Implementation was an early prototype checking only slippage.
- **Production Patch:** Fully expanded `assert_order_financial_parity()` in `backend/services/plan_financial_context_service.py` with structured result: `status` (`PASS`, `MISMATCH`, `INCOMPLETE`), `is_parity`, `compared_fields`, `mismatches`, `missing_fields`.
- **Tests:** `tests/test_v17_5_9_financial_parity.py` (14/14 PASS).

#### D06 — Market Timestamp Conversion Safety
- **Reproduction Input:** String `"NaN"`, `"invalid"`, boolean `True`, or negative timestamp passed as break/reference timestamp.
- **Observed Baseline Behavior:** Unhandled `ValueError` or `TypeError` thrown during `int(b_open)` or `int(_candle_open_ms())`.
- **Expected Behavior:** Structured controlled failure with stable reason code (`INVALID_BREAK_TIMESTAMP`, `INVALID_REFERENCE_TIMESTAMPS`), without crashing the cycle.
- **Root Cause:** Raw `int()` cast without type or boundary validation.
- **Production Patch:** Created pure helper `parse_valid_market_timestamp(value, *, field_name, allow_zero=False)` in `backend/services/stop_placement_service.py` and wrapped all timestamp resolution points in `resolve_verified_reference_node()` and `resolve_verified_break_candle()`.
- **Tests:** `tests/test_v17_5_9_break_source_authority.py` (B03–B06), `tests/test_v17_5_9_reference_source_authority.py` (R06–R07).

#### D07 — Acceptance Provenance & Clean Git Verification
- **Reproduction Input:** Acceptance runner executed on dirty repository or with missing/stale JUnit artifacts.
- **Observed Baseline Behavior:** In V17.5.8, runner noted `is_dirty: true` but still emitted `verdict: "ACCEPTED"`.
- **Expected Behavior:** Final verdict is `BLOCKED` if `is_dirty is True` or if critical files are missing; stale/missing JUnit fails the respective gate.
- **Root Cause:** Verdict calculation lacked a clean-status gate check.
- **Production Patch:** Authored `tools/run_v17_5_9_acceptance.py` with `evaluate_final_verdict(is_dirty=...)`, fresh JUnit timestamp enforcement, dynamic test count parsing, and manifest SHA256 integrity verification.
- **Tests:** `backend/tests/test_v17_5_9_acceptance_runner_self_test.py` (9/9 PASS).
