# AURUM DESK V17.7.2 — FUNCTION CHANGE MAP
**Release:** V17.7.2  
**Focus:** Detailed Source-Level Function and Structural Modifications  

---

## 1. Modified Source Files and Functions

### 1.1 `backend/services/stop_placement_types.py`
- **`StopStatus` (Enum):**
  - Added `WAITING_VOLATILITY_EVIDENCE = "WAITING_VOLATILITY_EVIDENCE"`.
  - Ensures semantic separation between diagnostic classifications (`DISTANCE_ATR_UNAVAILABLE`) and operational stop eligibility decisions.

### 1.2 `backend/services/stop_placement_service.py`
- **`compute_stop_buffer()`:**
  - Added quote sanity validation: checks finite and positive `bid` and `ask`.
  - Raises `ValueError("INVERTED_QUOTE")` if `ask < bid`.
- **`plan_structural_stop()`:**
  - Added negative spread validation: returns `status = StopStatus.INVALID_DATA.value` with reasons `("INVALID_SPREAD", "INVERTED_QUOTE")`.
  - Added causal ordering checks:
    - `anchor.occurred_at_ms > anchor.known_at_ms` -> returns `INVALID_DATA` with `CAUSAL_ORDERING_VIOLATION`.
    - `anchor.known_at_ms > as_of_ms` -> returns `INVALID_DATA` with `CAUSAL_ORDERING_VIOLATION`.
  - Added Step 8 fail-closed barrier:
    - Under `mode == PAPER_STRUCTURAL`: if `eff_m1 is None or eff_m1 <= 0.0 or not math.isfinite(eff_m1)`, immediately returns `status = StopStatus.WAITING_VOLATILITY_EVIDENCE.value`, `selected_sl = None`, `applied_variant = "NONE"`.
  - Preserved backward-compatibility: extracts valid M1 ATR from `buffer_result.components.get("atr_m1")` if `eff_m1 is None`.

### 1.3 `backend/services/cg01_context_adapter.py`
- **`adapt_cg01_quote()`:**
  - Replaced `spread = max(0.0, ask - bid)` with `spread = round(ask - bid, 4)`.
  - Marks `is_valid = False` whenever `bid <= 0`, `ask <= 0`, `ask < bid`, or non-finite.

### 1.4 `backend/services/cg01_engine.py`
- **`evaluate_cg01_candidate()`:**
  - Pre-flight quote checks: returns `INVALID_BID_ASK`, `INVERTED_QUOTE`, or `STALE_QUOTE` before evaluating strategy logic.
  - Replaced `spread = max(0.0, ask - bid)` with `spread = ask - bid`.
  - Passes verified `atr_m1_obj` and `atr_m5_obj` to `plan_structural_stop` and `build_stop_comparison_plan`.

### 1.5 `backend/services/scalp_m1_model_service.py`
- **Candidate Evaluators:**
  - Removed `max(0.0, ask - bid)` spread clipping.
  - Passes verified `atr_m1_obj` to `plan_structural_stop`.

### 1.6 `backend/database.py`
- **Engine Binding & Path Selection:**
  - Added test environment detection: `is_pytest` checks `pytest in sys.modules`, `PYTEST_CURRENT_TEST`, or `TESTING=1`.
  - In test environments, automatically creates an isolated temporary SQLite database in `/tmp/aurum_test_*`.
- **`assert_test_database_isolated()`:**
  - Added safety guard verifying that `DATABASE_PATH` does not target `aurum_desk.db`. Raises `RuntimeError` on safety hazard.

### 1.7 `backend/domain_calculator.py`
- **`validate_price_geometry()`:**
  - Added non-null checks for `entry`, `sl`, and `tp`. Returns `False, "PRICES_MUST_BE_NON_NULL"` rather than throwing unhandled `TypeError`.

### 1.8 `backend/services/trade_lifecycle_service.py`
- **`execute_fill()`:**
  - Enforced `effective_fill_qty = min(float(calc_result.quantity), float(order.quantity))` when `order.quantity > 0`.
  - Prevents position size expansion beyond frozen armed size at fill time.

### 1.9 `backend/tests/test_v17_7_adaptive_stop_loss.py`
- **Test Fixture (`clean_v17_7_db`):**
  - Added teardown of `PaperOrder`, `PaperObservation`, `WatchSetup`, `SMCScenarioSnapshot`, `SMCScenario`, `OpportunityFunnelRecord`, `OpportunityDecisionEvent`, and `DayAudit`.
  - Resets `fills_count`, `cooldown_until`, and `consecutive_losses` on each test run.
- **Upgraded Integration Gates:**
  - `SL-36`: Real `evaluate_cg01_candidate()`.
  - `SL-37..SL-40`: Real `ScalpM1ModelService` candidate evaluators.
  - `SL-43`: Real production `_arm_paper_candidate()`.
  - `SL-44`: Verified post-ARM widening rejection.
  - `SL-46`: Quantity preservation/downsizing at fill.
  - `SL-50`: Active HTTP spy across full candidate -> ARM -> fill -> close lifecycle.
  - `SL-55`: Durability verification across independent sessions.
  - `SL-57`: Multi-threaded race with `threading.Barrier(2)`.
  - `SL-58`: Causal timestamp validation.
  - `SL-60`: Full production candidate -> ARM -> fill -> exit lifecycle.
  - `DSL-10`: Missing ATR produces `WAITING_VOLATILITY_EVIDENCE` and cannot arm.
  - `DSL-16`: Distant SL full lifecycle.
  - `DSL-18`: Racing ARM duplicate prevention.
  - `DSL-19`: Active spy with distant SL.
- **Added Dedicated Gates CE-01 through CE-20:**
  - `CE-01`: Missing M1 ATR blocks `PAPER_STRUCTURAL`.
  - `CE-02`: Invalid numerical ATR (`NaN`, `Inf`, `0`, negative) fails closed.
  - `CE-03`: Insufficient closed candles fails closed.
  - `CE-04`: Future/unclosed candle exclusion from ATR.
  - `CE-05`: Optional M5 ATR handled per policy.
  - `CE-06`: Causal ordering violation rejected.
  - `CE-07`: Unrelated episode anchor rejected.
  - `CE-08`: Previously invalidated anchor rejected.
  - `CE-09`: Inverted quote spread cannot normalize to valid zero spread.
  - `CE-10`: Stale quote blocks candidate evaluation.
  - `CE-11`: Real CG01 evaluation with distant valid SL.
  - `CE-12`: Real M1 sweep reclaim evaluator with structural stop.
  - `CE-13`: Real orchestrator ARM with frozen evidence.
  - `CE-14`: Real execution coordinator fill with quantity preservation.
  - `CE-15`: Real bid/ask stop trigger and persisted close.
  - `CE-16`: Concurrent real ARM barrier creates single order.
  - `CE-17`: Test DB cannot touch runtime DB.
  - `CE-18`: Negative control live order dispatch blocked.
  - `CE-19`: Acceptance runner refuses PASS on missing mandatory gates.
  - `CE-20`: Acceptance artifact source SHA matches tested source.

### 1.10 `tools/run_v17_7_acceptance.py`
- Upgraded to enforce all 23 release gates (G01 to G23) fail-closed.
- Automated exact commit SHA recording, real test count parsing, and JSON artifact export.
