# AURUM DESK V17.7.4.1 — ATR FAIL-CLOSED REPAIR REPORT

**Release Version:** V17.7.4.1  
**Target Branch:** `feat/v17-7-adaptive-structural-stop-loss`  
**Primary Module:** `backend/services/stop_placement_service.py`  
**Test Suite:** `backend/tests/test_v17_7_atr_provenance.py`  
**Status:** IMPLEMENTED & VERIFIED (69/69 PASS, 15/15 ATR-FINAL PASS)  

---

## 1. Overview of Defects & Root Causes

Prior to V17.7.4.1, certain ATR calculation and validation code paths allowed incomplete or malformed ATR objects to be accepted under `PAPER_STRUCTURAL` stop loss planning:

1. **Conditional Provenance Check Bypass:**
   In `compute_stop_buffer()`, `validate_stop_atr_authority()` was only invoked if `bars_count > 0` or `source_candle_count > 0`. If an ATR object had `status="AVAILABLE"` and a scalar `value > 0` but omitted `bars_count` or had non-positive candle counts, full provenance validation was bypassed.
2. **Malformed Timestamp & Cutoff Coercion:**
   In `validate_stop_atr_authority()`, `decision_cutoff` exception handlers swallowed non-numeric values (such as strings or `NaN`), or failed to enforce positive values.
3. **Optional M5 Contamination Risk:**
   If an optional M5 ATR was provided but malformed, failure to cleanly reject and exclude it risked contaminating the buffer calculation.
4. **ARM Boundary Authority Re-verification:**
   At the ARM stage, `validate_applied_stop_decision()` required uniform support for candidate dataclasses and candidate dictionary representations while strictly rejecting forged or scalar-only ATR evidence.

---

## 2. Implementation Details

### 2.1 Strict Decision Cutoff and Timestamp Validation
In `backend/services/stop_placement_service.py:validate_stop_atr_authority`:
```python
# 1. Validate decision_cutoff
if decision_cutoff is not None:
    if isinstance(decision_cutoff, bool) or not isinstance(decision_cutoff, (int, float)):
        return False, "ATR_DECISION_CUTOFF_INVALID"
    if math.isnan(decision_cutoff) or math.isinf(decision_cutoff) or decision_cutoff < 0:
        return False, "ATR_DECISION_CUTOFF_INVALID"
    int_cutoff = int(decision_cutoff)
    if atr.decision_cutoff_ms is not None and atr.decision_cutoff_ms != int_cutoff:
        return False, "ATR_DECISION_CUTOFF_INVALID"

# 2. Validate max_age_ms
if max_age_ms is not None:
    if isinstance(max_age_ms, bool) or not isinstance(max_age_ms, (int, float)):
        return False, "ATR_TIMESTAMP_MISSING_OR_INVALID"
    if math.isnan(max_age_ms) or math.isinf(max_age_ms) or max_age_ms < 0:
        return False, "ATR_TIMESTAMP_MISSING_OR_INVALID"
    int_max_age = int(max_age_ms)

# 3. Validate candle counts and required timestamps
if atr.source_candle_count is None or atr.source_candle_count <= 0:
    return False, "ATR_SOURCE_COUNT_INSUFFICIENT"
if atr.last_closed_at_ms is None or atr.last_closed_at_ms <= 0:
    return False, "ATR_TIMESTAMP_MISSING_OR_INVALID"
if atr.calculated_as_of is None or atr.calculated_as_of <= 0:
    return False, "ATR_TIMESTAMP_MISSING_OR_INVALID"
```

### 2.2 Unconditional Authority Verification in `compute_stop_buffer`
Under `PAPER_STRUCTURAL` mode:
```python
if is_paper_structural:
    if not atr_m1:
        return StopBufferResult(status=StopBufferStatus.FAILED, rejection_code="MISSING_ATR_M1")
    m1_ok, m1_reason = validate_stop_atr_authority(
        atr_m1,
        expected_symbol=symbol,
        expected_timeframe="1m",
        decision_cutoff=decision_cutoff,
    )
    if not m1_ok:
        return StopBufferResult(status=StopBufferStatus.FAILED, rejection_code=m1_reason)
```
- Missing counts, invalid cutoffs, stale data, or future candles immediately yield `FAILED` with specific rejection codes.
- Raw ATR is strictly preserved: `weighted_buffer` ($\text{ATR} \times \text{multiplier}$) is never conflated with raw ATR.

### 2.3 Handling of Optional M5 ATR
- If M5 is absent and not mandated by policy, buffer computation proceeds with M1 alone.
- If M5 is present, it must strictly pass authority validation for symbol, timeframe (`5m`), and causal cutoff.
- If M5 fails validation, it is rejected and excluded without contaminating the M1 buffer.

---

## 3. Verification & Test Evidence

The following 15 dedicated regression tests (`ATR-FINAL-01` through `ATR-FINAL-15`) were added to `backend/tests/test_v17_7_atr_provenance.py` and all pass:

| Test ID | Test Function | Verified Behavior | Status |
|---|---|---|---|
| `ATR-FINAL-01` | `test_atr_final_01_missing_source_counts_rejected` | Missing/zero `source_candle_count` rejected with `ATR_SOURCE_COUNT_INSUFFICIENT` | PASS |
| `ATR-FINAL-02` | `test_atr_final_02_available_with_invalid_metadata_rejected` | `AVAILABLE` status with missing timestamp fails closed | PASS |
| `ATR-FINAL-03` | `test_atr_final_03_non_numeric_decision_cutoff_rejected` | String `"invalid"` decision cutoff rejected with `ATR_DECISION_CUTOFF_INVALID` | PASS |
| `ATR-FINAL-04` | `test_atr_final_04_negative_decision_cutoff_rejected` | Negative cutoff `-1000` rejected with `ATR_DECISION_CUTOFF_INVALID` | PASS |
| `ATR-FINAL-05` | `test_atr_final_05_missing_required_timestamp_rejected` | Null `last_closed_at_ms` rejected with `ATR_TIMESTAMP_MISSING_OR_INVALID` | PASS |
| `ATR-FINAL-06` | `test_atr_final_06_future_atr_source_rejected` | Candle closed after decision cutoff rejected with `ATR_SOURCE_AFTER_DECISION` | PASS |
| `ATR-FINAL-07` | `test_atr_final_07_stale_required_atr_rejected` | Candle age exceeding max age rejected with `ATR_STALE_FOR_DECISION` | PASS |
| `ATR-FINAL-08` | `test_atr_final_08_invalid_optional_m5_never_changes_sl` | Corrupt M5 object does not alter selected structural SL or buffer | PASS |
| `ATR-FINAL-09` | `test_atr_final_09_weighted_buffer_cannot_impersonate_raw_atr` | `weighted_buffer` (0.75) != `raw_atr` (3.0); distance ratio uses raw ATR | PASS |
| `ATR-FINAL-10` | `test_atr_final_10_arm_rejects_scalar_only_atr_evidence` | ARM validation barrier rejects decision with unverified scalar ATR | PASS |
| `ATR-FINAL-11` | `test_atr_final_11_arm_rejects_inconsistent_atr_provenance` | ARM barrier rejects mismatched symbol/timeframe | PASS |
| `ATR-FINAL-12` | `test_atr_final_12_valid_causal_atr_accepted` | Fully causal, continuous ATR passes with `ELIGIBLE_FOR_PAPER` | PASS |
| `ATR-FINAL-13` | `test_atr_final_13_valid_distant_sl_beyond_4x_atr_eligible` | SL distance = 6.0 (6x ATR 1.0) remains `ELIGIBLE_FOR_PAPER` | PASS |
| `ATR-FINAL-14` | `test_atr_final_14_invalid_financial_eligibility_rejected` | Financial violation (risk budget exceeded) rejected for financial reasons | PASS |
| `ATR-FINAL-15` | `test_atr_final_15_historical_replay_timestamps_compatible` | Historical timestamps from past years validated correctly without unit confusion | PASS |

Total ATR suite result: **69 passed** (36 original provenance tests + 15 ATR-FINAL tests + 18 other suite tests).
