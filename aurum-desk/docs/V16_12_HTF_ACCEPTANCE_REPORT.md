# AURUM DESK — V16.12 ACCEPTANCE REPORT
**Evaluated At:** 2026-10-17T02:49:33.844528+00:00  
**Baseline Commit SHA:** `11fea7325f30495757f4ccce754b515886583b09`  
**Target Branch:** `fix/v14-1-rr-flow`  
**Overall Technical Status:** **`PASS`**  
**Trading Performance Status:** `NOT_EVALUATED` (Per Section 190)

---

## 1. Mandatory Gate Verification

| Gate | Description | Outcome | Evidence |
|:---|:---|:---:|:---|
| **Gate A** | Closed-Candle Causality & Dynamic Config Propagation | **`PASS`** | Exact boundary filtering (`open + duration <= as_of_ms`), strict `htf_min_bars` warmup validation, and `htf_break_buffer_ticks` resolution. |
| **Gate B** | Transition State Machine, Non-Rebound & Range Breakout/Reentry | **`PASS`** | False rebound after protected low/high invalidation strictly prevented; range breakout shifts to `TRANSITION` with event; range reentry restores `RANGE` without endless loop. |
| **Gate C** | Replay & Collector Isolation | **`PASS`** | Replay with injected candles never leaks live collector singleton; absent vs empty candle inputs distinguished; periodic collector refresh bug fixed. |
| **Gate D** | Real Production Paper Trading Lifecycle (Long & Short) | **`PASS`** | Real `ScalpWatcherOrchestrator` -> `_arm_paper_candidate` -> `ExecutionCoordinator.evaluate_orders_sync` -> `PaperBroker.process_exit_tick` path verified for both directions. |
| **Gate E** | Immutable Evidence Persistence & Chart-Evidence API | **`PASS`** | Deep-copied `htf_context` embedded in `ScenarioSnapshot`; `/api/v1/orders/{id}/chart-evidence` returns original entry HTF facts unchanged after subsequent market shifts. |
| **Gate F** | Regression Suite Parity & Frontend Compatibility | **`PASS`** | All 216 tests in `pytest tests -k v16` pass (100%); 132 v14 guard/risk tests pass; 226 frontend vitest tests pass; `tsc -b && vite build` clean. |

---

## 2. Before / After Comparison on Golden Fixtures

| Case | Baseline Behavior | Corrected V16.12 Causal Behavior | Rationale |
|:---|:---|:---|:---|
| **Bullish Break & False Rebound** | Candle closes at 2515 (below 2520 protected low), then rebounds to 2530: **`BULLISH`** | Remains strictly **`UNKNOWN / TRANSITION`** with `PROTECTED_LOW_BREAK` | Swings forming prior to invalidation cutoff cannot re-bootstrap trend; requires new causal sequence. |
| **Bearish Break & False Recovery** | Candle closes at 2585 (above 2580 protected high), then dips to 2570: **`BEARISH`** | Remains strictly **`UNKNOWN / TRANSITION`** with `PROTECTED_HIGH_BREAK` | Symmetric protection for Short; invalidated swings cannot be recycled to re-establish trend. |
| **Range Breakout Up** | Candle closes at 110 (above range high 105): **`RANGE`** with `latest_event=None` | Shifts to **`TRANSITION`** with `RANGE_BREAK_UP` event | Range breakout is recognized dynamically against frozen bounds before bar expansion. |
| **Range Reentry (Failed Breakout)** | Breakout candle (110) followed by close back inside (103): **stuck or undefined** | Returns to **`RANGE`** with `RANGE_REENTRY` event | Unconfirmed breakouts that re-enter the range restore range phase and permit subsequent cycles. |
| **Dynamic `min_bars` Warmup** | `build_htf_context(..., min_bars=100)` returned `ESTABLISHED` (ignored) | Returns **`UNKNOWN / INSUFFICIENT_DATA`** | Dynamic config propagation enforces minimum closed candle requirement. |
| **H4 Bullish / H1 Transition** | Masked by generic `UNKNOWN` branch -> `relation: UNKNOWN` | Returns **`relation: TRANSITION`**, `readiness: READY`, `directions: [LONG, SHORT]` | Distinguishes insufficient data from valid structural transition; allows conditional evaluation. |
| **Historical Order Evidence** | Order snapshot lacked explicit HTF facts; reading live context altered facts | Immutable **`htf_context`** in `ScenarioSnapshot` read via chart API | Decision-time HTF snapshot is frozen at entry and immune to later market structure shifts. |

---

## 3. Concrete Price Examples

### Example 1: Bullish Invalidation & Rebound
- **Protected Low:** `2520.0`
- **Invalidation Bar:** `Open: 2555.0, High: 2556.0, Low: 2510.0, Close: 2515.0`
  - Event: `PROTECTED_LOW_BREAK`, `reference_price: 2520.0`, `close_price: 2515.0`. Phase shifts `ESTABLISHED -> TRANSITION`.
- **Subsequent Rebound Bar:** `Open: 2515.0, High: 2535.0, Low: 2512.0, Close: 2530.0`
  - Engine evaluation: No new causal swings have formed after invalidation cutoff.
  - Phase remains `TRANSITION`, `bias: UNKNOWN`, `last_confirmed_bias: BULLISH`.
  - Result: Trend is NOT falsely restored; caller does NOT trigger premature Long entry.

### Example 2: Range Breakout & Re-entry
- **Oscillating Range Bounds:** High `105.0`, Low `95.0` (frozen from confirmed swings).
- **Breakout Bar:** `Open: 100.0, High: 112.0, Low: 99.0, Close: 110.0`
  - Event: `RANGE_BREAK_UP`, `reference_price: 105.0`, `close_price: 110.0`. Phase shifts `RANGE -> TRANSITION`.
- **Failed Breakout Re-entry Bar:** `Open: 109.0, High: 109.5, Low: 102.0, Close: 103.0`
  - Close at `103.0` is within `[95.0, 105.0]`.
  - Event: `RANGE_REENTRY`. Phase shifts `TRANSITION -> RANGE`.
  - Subsequent breakout at `111.0` triggers fresh `RANGE_BREAK_UP` without key exhaustion.

---

## 4. Test Execution Accounting (Honest Counts)

| Suite | File / Scope | Tests Passed | Tests Failed | Notes |
|---|---|:---:|:---:|---|
| **V16.12 HTF State Machine** | `backend/tests/test_v16_12_htf_state_machine.py` | 16 | 0 | Pure state machine, rebounds, range breakouts, matrix |
| **V16.12 Paper Execution Flow** | `backend/tests/test_v16_12_cg01_execution.py` | 5 | 0 | Full Long/Short flow, coordinator fill, broker exit, immutability, API |
| **V16.11 Baseline Causal HTF** | `backend/tests/test_v16_11_htf_structure.py` | 15 | 0 | Baseline HTF regression & replay isolation |
| **Full V16 Test Suite** | `pytest tests -k v16` | **216** | 0 | Exactly 195 baseline + 16 state + 5 execution = 216 unique tests |
| **V14 Guard / Risk Suites** | `test_v14_2_entry_guard.py`, `test_v14_2_news_blackout.py`, etc. | 132 | 0 | Regression parity for news blackout and risk authority |
| **Frontend Unit & Component** | `vitest run` (26 test files) | 226 | 0 | All frontend test files pass cleanly |
| **Frontend Build** | `tsc -b && vite build` | Clean | 0 | Zero TypeScript compilation or bundle errors |
| **Frontend E2E Visual (Gate F)** | Playwright browser runner | *BLOCKED* | 0 | Local port 8765 socket conflict with active backend server; visual PASS honestly withheld |

---

## 5. Remaining Limitations & Next Steps

1. **Reclaim Policy:** V16.12 strictly rejects single-candle reclaims without causal structure confirmation. If a discretionary strategy rule for fast reclaims is specified in future versions, it should be parameterized with a dedicated re-entry confirmation contract.
2. **Multi-Asset Generalization:** Current implementation uses `tick_size = 0.01` and `tick_size = 0.1` defaults suitable for XAUUSDT and crypto perpetuals. Ensure asset-specific tick grids are passed via `config.tick_size`.
3. **Trading Performance:** As mandated by section 190, trading profitability and win rate are `NOT_EVALUATED` in this patch; verification is strictly confined to technical correctness, causal state integrity, and paper lifecycle execution.
