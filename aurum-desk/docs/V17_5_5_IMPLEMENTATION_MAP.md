# AURUM V17.5.5 — IMPLEMENTATION MAP

**Branch:** `fix/v14-1-rr-flow`  
**Base Commit:** `8e882a4a5081924b6c0a95ad744f59131ea30b29`  
**Patch Target:** `fix(v17.5.5): enforce research entry context and verify origin chart binding`  
**Status:** IMPLEMENTED, LOCALLY VERIFIED, ALL 11 GATES PASS  

---

## 1. Arch Overview & 4 Closures

V17.5.5 closes four critical gaps in the Aurum algorithmic trading architecture across production execution, research shadow evaluation, and browser chart verification:

1. **Strict Entry Context Gate:** Research variants in `advance_stop_comparison` are strictly blocked from entry (`WAIT_ENTRY`) unless the plan possesses `context_status == "VERIFIED"` and a valid `cost_snapshot`. Variants already in `OPEN` state are never blocked from evaluating stop/target exits.
2. **Shared Cost Authority:** Fee rates (`0.0002` maker, `0.0004` taker) and slippage models are unified under `resolve_execution_cost_context` in `plan_financial_context_service.py`. Explicit `0.0` slippage is preserved and distinguished from missing snapshots.
3. **Protected Origin Binding Bridge:** `select_protected_origin_for_break` verifies causal swing origins prior to market break timing and decision cutoff. Wired into `ScalpM1ModelService` (`evaluate_m1_continuation_candidate` and `evaluate_m1_fvg_continuation_candidate`) and forwards structural proof into `build_selected_break_descriptor` and `resolve_stop_anchor`.
4. **Browser Chart Evidence & Overlay Diagnostics:** Read-only `window.__AURUM_CHART_OVERLAY_DIAGNOSTICS__()` hook exposed in `ChartComponent.tsx` reporting attached primitive status, prices, and canvas geometry. Verified via Playwright automated browser tests asserting inverted Y-coordinate geometry (`tpY > entryY > slY` for SHORT, `slY > entryY > tpY` for LONG), rapid selection switching, and mobile viewport compliance.

---

## 2. Detailed Module & Function Mapping

### Closure 1: Entry Context Gate

#### Dataclass: `ComparisonEntryGateResult`
- **Location:** `backend/services/stop_placement_types.py`
- **Fields:**
  - `allowed: bool`
  - `reason_code: str` (`"ALLOWED"`, `"CONTEXT_INCOMPLETE"`, `"COST_UNAVAILABLE"`, `"INVALID_COST"`, `"EXPIRED"`, `"NOT_ELIGIBLE"`)
  - `explanation: str`
  - `context_status: Optional[str]`
  - `cost_available: bool`
- **Methods:** `to_dict() -> Dict[str, Any]`

#### Function: `evaluate_stop_comparison_entry_gate`
- **Location:** `backend/services/stop_placement_research_service.py`
- **Signature:**
  ```python
  def evaluate_stop_comparison_entry_gate(
      *,
      plan: Union[StopComparisonPlan, Dict[str, Any]],
      variant: Dict[str, Any],
      quote: Union[Dict[str, Any], Any],
      as_of_ms: int,
      cost_result: Dict[str, Any],
  ) -> ComparisonEntryGateResult
  ```
- **Evaluation Order:**
  1. `context_status == "VERIFIED"`: Blocks with `CONTEXT_INCOMPLETE` if missing, None, or unverified.
  2. `cost_result.get("available") is True`: Blocks with `COST_UNAVAILABLE` if missing or None.
  3. Cost value sanity: Blocks with `INVALID_COST` if fee rates or slippage are negative or non-finite.
  4. Expiry check: Blocks with `EXPIRED` if `as_of_ms >= expires_at_ms`.
  5. Variant eligibility: Blocks with `NOT_ELIGIBLE` if `is_valid`, `can_execute`, or `meets_min_rr` is False.

#### Wiring: `advance_stop_comparison`
- **Location:** `backend/services/stop_placement_research_service.py`
- **Caller Behavior:**
  - Checked ONLY under `if v_state == "WAIT_ENTRY":`
  - When `not gate.allowed`: sets `v_data["wait_reason"] = gate.reason_code` and `v_data["gate_explanation"] = gate.explanation`, then `continue`.
  - Variants in `OPEN` state bypass the entry gate and proceed directly to `evaluate_order_exit`.

---

### Closure 2: Shared Cost Authority

#### Function: `resolve_execution_cost_context`
- **Location:** `backend/services/plan_financial_context_service.py`
- **Signature:**
  ```python
  def resolve_execution_cost_context(
      *,
      snapshot: Optional[Dict[str, Any]] = None,
      candidate: Any = None,
      settings: Optional[Dict[str, Any]] = None,
      config: Any = None,
      as_of_ms: Optional[int] = None,
  ) -> Dict[str, Any]
  ```
- **Authoritative Fee Rates:**
  - `maker_fee_rate = 0.0002` (0.02%)
  - `taker_fee_rate = 0.0004` (0.04%)
- **Validation Rules:**
  - Preserves explicit `0.0` slippage (`math.isclose(v, 0.0)`).
  - Rejects negative or non-finite values (`math.isnan`, `math.isinf`, `< 0.0`).
  - Returns `available=False, reason="MISSING_COST_SNAPSHOT"` when snapshot, candidate, settings, and config are all empty/None.

#### Callers:
1. `read_verified_comparison_cost_snapshot(plan)` in `stop_placement_research_service.py`.
2. `resolve_stop_comparison_execution_context(cand, ...)` in `stop_placement_research_service.py`.

---

### Closure 3: Protected Origin Binding Bridge

#### Function: `select_protected_origin_for_break`
- **Location:** `backend/services/stop_placement_service.py`
- **Signature:**
  ```python
  def select_protected_origin_for_break(
      *,
      direction: str,
      break_event: Dict[str, Any],
      confirmed_swings: Tuple[Sequence[Dict[str, Any]], Sequence[Dict[str, Any]]],
      closed_bars: Sequence[Any],
      as_of_ms: int,
      decision_cutoff_ms: Optional[int] = None,
      tick_size: float = 0.01,
  ) -> Optional[Dict[str, Any]]
  ```
- **Validation Criteria:**
  - SHORT: Evaluates Swing Highs (`SH`). Origin price must exceed break bar high (or entry reference) within tick tolerance.
  - LONG: Evaluates Swing Lows (`SL`). Origin price must be below break bar low within tick tolerance.
  - Temporal Causal Invariant: `origin_occurred_at_ms <= break_time_ms <= decision_cutoff_ms <= as_of_ms`.
  - Returns: Dict containing `origin_pivot_id`, `origin_kind`, `origin_price`, `origin_occurred_at_ms`, `origin_known_at_ms`.

#### Integration in `ScalpM1ModelService`:
- **Files:** `backend/services/scalp_m1_model_service.py`
- **Callers:**
  1. `evaluate_m1_continuation_candidate()`:
     - Calls `select_protected_origin_for_break()`.
     - Passes proof into `build_selected_break_descriptor(..., origin_pivot_id=origin_proof["origin_pivot_id"], episode_start_ms=origin_proof["origin_occurred_at_ms"], origin_price=origin_proof["origin_price"], origin_occurred_at_ms=origin_proof["origin_occurred_at_ms"])`.
     - Forwards `tick_size=cfg.tick_size` to `resolve_stop_anchor()`.
  2. `evaluate_m1_fvg_continuation_candidate()`:
     - Calls `select_protected_origin_for_break()`.
     - Passes proof into `build_selected_break_descriptor(...)` and `tick_size=cfg.tick_size` to `resolve_stop_anchor()`.

---

### Closure 4: Chart Evidence & Diagnostics

#### Diagnostics Hook: `window.__AURUM_CHART_OVERLAY_DIAGNOSTICS__()`
- **Location:** `frontend/src/ChartComponent.tsx`
- **Interface:**
  ```typescript
  {
    hasPrimitive: boolean;
    selectionKey: string;
    selectedBinding: { source: string; id: string; orderId: string; setupId: string } | null;
    data: {
      id: string;
      direction: 'LONG' | 'SHORT';
      state: string;
      plannedEntry: number;
      stopLoss: number;
      takeProfit: number;
      quantity: number;
      readOnly: boolean;
    } | null;
    geometry: {
      entryY: number | null;
      slY: number | null;
      tpY: number | null;
      lpY: number | null;
      startX: number;
      endX: number;
    } | null;
    timestamp: number;
  }
  ```

#### Playwright Browser Test Suite:
- **Location:** `frontend/tests/e2e/v17_5_5_chart_evidence.e2e.ts`
- **Test Scenarios:**
  - `P01`: Seeded SHORT order renders primitive with correct prices and projected canvas geometry (`tpY > entryY > slY`).
  - `P02`: Seeded LONG order renders primitive with correct prices and projected canvas geometry (`slY > entryY > tpY`).
  - `P03`: Rapid selection switching cleanly detaches prior primitive and activates latest without lingering coordinate remnants.
  - `P04`: Mobile viewport (`390x844`) renders without horizontal document overflow (`scrollWidth <= clientWidth + 2`).

---

## 3. Test Coverage Matrix

| Test File | Category | Tests | Status |
| :--- | :--- | :---: | :---: |
| `backend/tests/test_v17_5_5_entry_context_gate.py` | Entry Context & Cost Gate | 10 | PASS |
| `backend/tests/test_v17_5_5_cost_authority.py` | Shared Fee Rates & Slippage | 6 | PASS |
| `backend/tests/test_v17_5_5_origin_bridge.py` | Origin Selection & Validation | 5 | PASS |
| `backend/tests/test_v17_5_5_cycle_to_chart_contract.py` | Pipeline & Financial Parity | 2 | PASS |
| `frontend/tests/e2e/v17_5_5_chart_evidence.e2e.ts` | Playwright Browser E2E | 4 | PASS |
| `backend/tests/test_v17_5_4_*.py` | V17.5.4 Target Suites | 16 | PASS |
| `backend/tests/test_v17_5_3_*.py` | V17.5.3 Boundary Probes & Cycle | 17 | PASS |
| `backend/tests/test_v17_5_2_*.py` | V17.5.2 Baseline Suites | 7 | PASS |
| `backend/tests/test_v17_5_1_*.py` | V17.5.1 Feature Suites & Probes | 26 | PASS |
| `backend/tests/test_v17_5_*.py` | Existing V17.5 Suites | 31 | PASS |
| `backend/tests/test_prior_*.py` | Prior Regressions (CG01, sessions) | 35 | PASS |
| `frontend/src/**/*.test.ts(x)` | Frontend Vitest (48 files) | 501 | PASS |
