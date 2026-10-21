# V16.39.7 ACCEPTANCE REPORT — CLOSE ENTRY-MODE VERIFICATION AND LESSON APPROVAL ACCEPTANCE

## 1. Metadata
- **Repository:** `quocan050302/Build-app-crypto`
- **Project:** `aurum-desk`
- **Branch:** `fix/v14-1-rr-flow`
- **Baseline Commit:** `3451bb98c6db0caaea5f3dee53fd45147094f071` (*fix(v16.39.6): preserve entry modes and enforce causal production acceptance*)
- **Final Patch Commit:** `15aa9dfca7d4c5d917df6fc7e1ef51c0e5934d8b`
- **Status:** PASS

---

## 2. Executive Summary & 3 Core Deliverables (Ba Đầu Việc)
1. **Compliance Gate Respects Mode Verification Status (Rules 025, 071–085):**
   - In `assess_entry_compliance_for_review`: CG01 models fail closed to `UNVERIFIABLE` when `historical_entry_mode.verification_status != "VERIFIED"`.
   - Distinguishes missing entry mode (`"Chưa đủ dữ liệu xác minh chế độ vào lệnh lịch sử."`) from MARKET retest violations without falsely blaming retests (`Rule 082`).
   - Preserves partial passing checks (`PRICE_GEOMETRY`, `TRIGGER_CHAIN_VERIFIED`) in `checks_passed` (`Rule 080`).
   - Preserves review compatibility for non-CG01 models (`Rule 029, 030, 075`).
2. **Comprehensive Multi-Source Conflict Detection in Historical Resolver (Rules 026–027, 041–070):**
   - Added `HistoricalEntryModeSourceDescriptor` and `collect_historical_entry_mode_sources(...)`.
   - Detects order metadata self-conflicts (order_type `LIMIT` with entry_mode `MARKET`).
   - Detects cross-source discrepancies: scenario plan vs fill snapshot, arm snapshot vs fill snapshot, scenario vs arm snapshot.
   - Detects intra-source snapshot conflicts without premature returns.
   - Preserves legacy order type fallback as `source="FALLBACK_ORDER_TYPE"`, `verification_status="UNVERIFIED"`.
3. **HTTP Approve & Activate API -> Scorer/Orchestrator Threshold Crossing Acceptance (Rules 121–170):**
   - Tested real HTTP requests to `POST /api/v1/lessons/{id}/approve-and-activate` with revision checks and transaction rollback atomicity on validation failure (`422`, `Rule 169`).
   - Verified that a pending lesson causes `applied_delta = 0.0` (no effect).
   - Validated candidate with realistic base score `72.5 >= 70.0` (`ELIGIBLE_BY_SCORE`).
   - Activated penalty rule (`score_delta = -5.0` on spread `<= 0.50`). Candidate crossing drops score to `72.5 - 5.0 = 67.5 < 70.0` -> decision becomes `BELOW_THRESHOLD`.
   - Calling `POST /api/v1/lessons/{id}/set-enable` with `enabled=False` invalidates cache and restores candidate decision to `ELIGIBLE_BY_SCORE`.

---

## 3. Bug Fixes (Before vs After)

| Bug ID | Component | Before (v16.39.6) | After (v16.39.7) |
|---|---|---|---|
| **Bug 1 (Fallback Trust)** | `assess_entry_compliance_for_review` | Resolver fallback returned `UNVERIFIED`, but compliance evaluator still returned `VERIFIED_COMPLIANT`. | Fails closed: CG01 returns `UNVERIFIABLE` with reason `"Chưa đủ dữ liệu xác minh chế độ vào lệnh lịch sử."` and `HISTORICAL_ENTRY_MODE_UNVERIFIED` in failed checks. |
| **Bug 2 (Order Self-Conflict)** | `resolve_historical_entry_mode` | Order with type `LIMIT` and metadata `entry_mode="MARKET"` could return `VERIFIED`. | Evaluates intra-source compatibility; returns `PROVENANCE_CONFLICT` with issue `ORDER_METADATA_SELF_CONFLICT`. |
| **Bug 3 (Scenario/Fill Conflict)** | `resolve_historical_entry_mode` | Early return on scenario branch skipped comparing with `fill_snapshot`. | Collects all active descriptors and performs pairwise mode cross-checking; detects scenario vs fill discrepancy as `PROVENANCE_CONFLICT`. |
| **Bug 4 (Atomicity on Validation Error)** | `approve_and_activate_lesson` | On validation failure, state could mutate partially in session before raising 422. | Explicit `db.rollback()` guarantees transaction atomicity. |

---

## 4. Test Execution Summary

### Acceptance Suite (New in V16.39.7)
- **File:** `backend/tests/test_v16_39_7_mode_trust_lesson_api_acceptance.py`
- **Command:** `pytest tests/test_v16_39_7_mode_trust_lesson_api_acceptance.py -v`
- **Result:** **20 passed in 0.65s**

### Mandatory Regression Suites
| Test Group | Command / Pattern | Tests Passed | Result |
|---|---|---|---|
| **All V16.39 Suites** | `pytest -k "v16_39"` | 120 | PASS (5.02s) |
| **V16.38 Adaptive Paper** | `pytest -k "v16_38"` | 28 | PASS (2.22s) |
| **CG01 Pipeline & Contracts** | `pytest -k "cg01"` | 221 | PASS (11.48s) |
| **All V16.3* Test Files** | `pytest tests/test_v16_3*.py` | 304 | PASS (10.63s) |

---

## 5. Risk & Invariant Verifications
- **No live Bitget / Telegram calls:** All test environments mock or isolate transport layers.
- **Score Threshold:** Strict `70.0` threshold preserved.
- **Risk & Sizing:** No modifications to `risk_pct`, leverage, capital, or margin mode.
- **Position Limits:** 3 fills/day policy and open position guards intact.
