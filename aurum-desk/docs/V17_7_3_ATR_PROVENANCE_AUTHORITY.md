# AURUM DESK V17.7.3 — ATR PROVENANCE AUTHORITY
**Authoritative ATR Evidence, Causal Timestamps & Volatility Buffer Distinction**  
**Repository:** `quocan050302/Build-app-crypto`  
**Branch:** `feat/v17-7-adaptive-structural-stop-loss`  
**Status:** `VERIFIED_FROM_UNIT_TEST`

---

## 1. Defect Analysis: Buffer Component Masquerading as Raw ATR

### The Critical Distinction
In the structural stop loss model, two distinct quantities are derived from market volatility:
1. **Raw ATR ($ATR_{\text{raw}}$)**: The true average true range over $N$ closed periods (e.g., $ATR_{M1} = 3.00$ USD). This is an authoritative, unweighted market volatility measurement used for:
   - Protective minimum distance gating: $\text{Min Distance} = \text{Multiplier} \times ATR_{\text{raw}}$
   - Distance-to-ATR ratio diagnostics: $\text{Ratio} = \frac{\text{SL Distance}}{ATR_{\text{raw}}}$
2. **Protective Buffer Component ($\text{Buffer}_{\text{contrib}}$)**: The scaled contribution added to the anchor price:
   $$\text{Buffer}_{\text{contrib}} = \text{policy.atr\_m1\_multiplier} \times ATR_{\text{raw}}$$
   For example, with multiplier $0.25$ and $ATR_{\text{raw}} = 3.00$, $\text{Buffer}_{\text{contrib}} = 0.75$.

### Defect V173-P1-01
In V17.7.2, `plan_structural_stop()` contained a fallback pathway:
```python
eff_m1 = atr_m1 if atr_m1 is not None else (
    atr_m1_obj.value if atr_m1_obj is not None and atr_m1_obj.status == StopATRStatus.AVAILABLE
    else buffer_result.components.get("atr_m1") # <--- DEFECTIVE FALLBACK
)
```
When `buffer_result.components.get("atr_m1")` was returned, it returned `0.75` instead of `3.00`.
Consequences:
- Distance / ATR was calculated as $22.50 / 0.75 = 30.0\times$ instead of $22.50 / 3.00 = 7.5\times$, grossly distorting metrics.
- A missing raw ATR could be bypassed by an unverified buffer scalar, violating the volatility gate fail-closed contract.

---

## 2. Remediated Architecture & Types

### 2.1 Extended `StopATRResult` Contract
In `backend/services/stop_placement_types.py`:
```python
@dataclass(frozen=True)
class StopATRResult:
    timeframe: str
    period: int
    value: Optional[float]
    status: StopATRStatus
    reason: str
    symbol: str = "XAUUSDT"
    source: str = "AGGREGATE_5M_SPLIT_OR_CLOSED_CANDLES"
    source_candle_count: int = 0
    last_closed_candle_timestamp: Optional[int] = None
    calculated_as_of: Optional[int] = None
    decision_cutoff: Optional[int] = None
```

### 2.2 `StopBufferResult` Explicit Provenance
In `backend/services/stop_placement_types.py`:
```python
@dataclass(frozen=True)
class StopBufferResult:
    buffer_price: float
    dominant_source: str
    components: Dict[str, float]
    raw_atr_m1: Optional[float] = None
    raw_atr_m5: Optional[float] = None
```
The raw ATR values are explicitly carried alongside the weighted components, preventing any confusion between the weighted buffer contribution and the original raw volatility metric.

### 2.3 Strict Causal Validation in `compute_stop_atr()`
In `backend/services/stop_placement_service.py`:
- **Symbol Matching**: Confirms candle series symbol matches the requested symbol.
- **Unclosed Candle Exclusion**: Candles with close timestamp $> \text{as\_of\_ms}$ are strictly excluded.
- **Future Candle Rejection**: Future bars cannot contribute to ATR calculation.
- **Finite Price Verification**: NaN, infinite, zero, or negative ATR values are rejected with `StopATRStatus.UNAVAILABLE`.
- **Minimum Period Enforcement**: Requires at least `period` closed candles to compute valid SMA of True Range.

### 2.4 Fail-Closed Resolution in `plan_structural_stop()`
- If `mode == "PAPER_STRUCTURAL"` and valid M1 ATR is absent or non-positive:
  Returns `StopPlacementResult` with:
  - `status = StopPlacementStatus.WAITING_VOLATILITY_EVIDENCE`
  - `selected_sl = None`
  - `selected_tp = None`
  - `blocked_reason = "REQUIRED_M1_ATR_MISSING"`
  - Completely blocks ARM transition.
- Explicit `StopATRStatus.UNAVAILABLE` in `atr_m1_obj` can **never** be overridden by passing a loose scalar `atr_m1`.

---

## 3. ATR Test Suite Evidence (ATR-01 to ATR-20)

Verified by 20 dedicated tests in `backend/tests/test_v17_7_atr_provenance.py`:

| Test ID | Test Function | Verified Property | Result |
|---|---|---|---|
| `ATR-01` | `test_atr_01_raw_atr_and_weighted_buffer_are_distinct` | Raw ATR (3.00) != weighted buffer (0.75); both preserved. | **PASS** |
| `ATR-02` | `test_atr_02_weighted_buffer_cannot_satisfy_missing_atr_gate` | Buffer alone cannot satisfy missing ATR gate in planner. | **PASS** |
| `ATR-03` | `test_atr_03_missing_raw_atr_fails_closed` | Missing raw ATR returns `WAITING_VOLATILITY_EVIDENCE`. | **PASS** |
| `ATR-04` | `test_atr_04_explicitly_unavailable_atr_cannot_be_overridden_by_scalar`| Explicit `UNAVAILABLE` object blocks scalar override. | **PASS** |
| `ATR-05` | `test_atr_05_wrong_timeframe_fails` | Requesting M1 with M5 candles returns `UNAVAILABLE`. | **PASS** |
| `ATR-06` | `test_atr_06_wrong_symbol_fails` | Requesting XAUUSDT with BTCUSDT candles returns `UNAVAILABLE`.| **PASS** |
| `ATR-07` | `test_atr_07_insufficient_closed_candles_fails` | 5 candles for 14-period ATR returns `UNAVAILABLE`. | **PASS** |
| `ATR-08` | `test_atr_08_future_candle_fails` | Candle timestamp in future relative to `as_of` rejected. | **PASS** |
| `ATR-09` | `test_atr_09_unclosed_candle_fails` | Unclosed bar at `as_of` is filtered out. | **PASS** |
| `ATR-10` | `test_atr_10_nan_and_infinite_atr_fail` | NaN/Inf candle prices return `UNAVAILABLE`. | **PASS** |
| `ATR-11` | `test_atr_11_zero_and_negative_atr_fail` | Negative candle prices or flat ATR fails closed in planner. | **PASS** |
| `ATR-12` | `test_atr_12_invalid_status_value_combination_fails` | Non-available status with positive value returns `None`. | **PASS** |
| `ATR-13` | `test_atr_13_valid_m1_atr_succeeds` | Valid 14 closed candles produce authoritative `AVAILABLE`. | **PASS** |
| `ATR-14` | `test_atr_14_optional_m5_handling_is_correct` | Optional M5 is validated if present, recorded if absent. | **PASS** |
| `ATR-15` | `test_atr_15_correct_distance_raw_atr_ratio` | Ratio uses raw ATR (3.00), not weighted buffer (0.75). | **PASS** |
| `ATR-16` | `test_atr_16_no_accidental_4x_atr_rejection` | Stop at 7.5x ATR remains eligible (no 4x cap). | **PASS** |
| `ATR-17` | `test_atr_17_cg01_uses_verified_atr` | CG01 evaluator integrates authoritative raw ATR. | **PASS** |
| `ATR-18` | `test_atr_18_m1_strategy_uses_verified_atr` | M1 scalp model integrates authoritative raw ATR. | **PASS** |
| `ATR-19` | `test_atr_19_shadow_compare_remains_backward_compatible` | Variant C in `SHADOW_COMPARE` records honest reason. | **PASS** |
| `ATR-20` | `test_atr_20_paper_structural_cannot_arm_with_invalid_provenance`| Missing ATR completely blocks candidate ARM transition. | **PASS** |

### Execution Evidence:
```bash
./backend/venv/bin/python -m pytest backend/tests/test_v17_7_atr_provenance.py -v
============================= test session starts ==============================
collected 20 items

backend/tests/test_v17_7_atr_provenance.py::test_atr_01_raw_atr_and_weighted_buffer_are_distinct PASSED [  5%]
backend/tests/test_v17_7_atr_provenance.py::test_atr_02_weighted_buffer_cannot_satisfy_missing_atr_gate PASSED [ 10%]
backend/tests/test_v17_7_atr_provenance.py::test_atr_03_missing_raw_atr_fails_closed PASSED [ 15%]
backend/tests/test_v17_7_atr_provenance.py::test_atr_04_explicitly_unavailable_atr_cannot_be_overridden_by_scalar PASSED [ 20%]
backend/tests/test_v17_7_atr_provenance.py::test_atr_05_wrong_timeframe_fails PASSED [ 25%]
backend/tests/test_v17_7_atr_provenance.py::test_atr_06_wrong_symbol_fails PASSED [ 30%]
backend/tests/test_v17_7_atr_provenance.py::test_atr_07_insufficient_closed_candles_fails PASSED [ 35%]
backend/tests/test_v17_7_atr_provenance.py::test_atr_08_future_candle_fails PASSED [ 40%]
backend/tests/test_v17_7_atr_provenance.py::test_atr_09_unclosed_candle_fails PASSED [ 45%]
backend/tests/test_v17_7_atr_provenance.py::test_atr_10_nan_and_infinite_atr_fail PASSED [ 50%]
backend/tests/test_v17_7_atr_provenance.py::test_atr_11_zero_and_negative_atr_fail PASSED [ 55%]
backend/tests/test_v17_7_atr_provenance.py::test_atr_12_invalid_status_value_combination_fails PASSED [ 60%]
backend/tests/test_v17_7_atr_provenance.py::test_atr_13_valid_m1_atr_succeeds PASSED [ 65%]
backend/tests/test_v17_7_atr_provenance.py::test_atr_14_optional_m5_handling_is_correct PASSED [ 70%]
backend/tests/test_v17_7_atr_provenance.py::test_atr_15_correct_distance_raw_atr_ratio PASSED [ 75%]
backend/tests/test_v17_7_atr_provenance.py::test_atr_16_no_accidental_4x_atr_rejection PASSED [ 80%]
backend/tests/test_v17_7_atr_provenance.py::test_atr_17_cg01_uses_verified_atr PASSED [ 85%]
backend/tests/test_v17_7_atr_provenance.py::test_atr_18_m1_strategy_uses_verified_atr PASSED [ 90%]
backend/tests/test_v17_7_atr_provenance.py::test_atr_19_shadow_compare_remains_backward_compatible PASSED [ 95%]
backend/tests/test_v17_7_atr_provenance.py::test_atr_20_paper_structural_cannot_arm_with_invalid_provenance PASSED [100%]

============================== 20 passed in 0.17s ==============================
```
