# AURUM DESK V17.8.0.1 — END-TO-END RUNTIME INTEGRATION MATRIX (`DEF-178-11`)

**Defect ID:** `DEF-178-11`  
**Test File:** `backend/tests/test_v17_8_runtime_integration.py`  
**Acceptance Gate:** `G49_END_TO_END_RUNTIME_INTEGRATION` & `G38_COMPLETE_ARM_FILL_EXIT`

---

## 1. Runtime Architecture Exercised

Every scenario in `backend/tests/test_v17_8_runtime_integration.py` exercises the real production services against an isolated SQLite test database (`assert_test_database_isolated(DB_PATH)`):

```text
Market Candles + ValidatedQuote
       ↓
resolve_stop_placement_policy(db)
       ↓
ScalpM1ModelService (evaluate_m1_fvg_continuation_candidate / evaluate_m1_sweep_reclaim_candidate)
       ↓
detect_unicorn_evidence + resolve_stop_anchor + apply_stop_placement_to_candidate
       ↓
calculate_risk_reward (Authoritative Domain Calculator)
       ↓
ScalpWatcherOrchestrator._evaluate_and_arm
       ↓
ExecutionCoordinator.evaluate_orders_sync (EntryGuardRequest stage="FILL")
       ↓
TradeLifecycleService (execute_fill / execute_close)
       ↓
GET /api/v1/orders/{order_id}/stop-evidence
```

---

## 2. Mandatory 12-Scenario Matrix & Results

| Scenario | Description | Mode & Flags | Expected Outcome | Verified Result |
|---|---|---|---|---|
| **Scenario 1** | Legacy Mode Baseline | `mode=LEGACY`, `v18_*=False` | Variant A (`FVG Edge + 5 ticks`) armed, filled, closed, and returned via `/stop-evidence` with `applied_variant="A"`. | **PASS** |
| **Scenario 2** | Shadow Mode Comparison | `mode=SHADOW_COMPARE`, `v18_unicorn_detection_enabled=True` | Variant A executed (`applied_variant="A"`), Variant C Unicorn SL recorded in `stop_comparison` and returned via `/stop-evidence`. | **PASS** |
| **Scenario 3** | V17.7 `PAPER_STRUCTURAL` Without V17.8 Flag | `mode=PAPER_STRUCTURAL`, `v18_structural_intelligence_enabled=False` | Uses V17.7 `PROTECTED_SWING` anchor (`applied_variant="C"`), does not activate V17.8 Unicorn Breaker override. | **PASS** |
| **Scenario 4** | V17.8 Unicorn Bullish Execution | `mode=PAPER_STRUCTURAL`, `v18_structural_intelligence_enabled=True`, `v18_unicorn_detection_enabled=True` | Confirmed Bullish Unicorn (`BREAKER_DISTAL_BOUNDARY`), armed, filled, closed at TP, `/stop-evidence` returns full provenance IDs. | **PASS** |
| **Scenario 5** | V17.8 Unicorn Bearish Execution | `mode=PAPER_STRUCTURAL`, `v18_structural_intelligence_enabled=True`, `v18_unicorn_detection_enabled=True` | Confirmed Bearish Unicorn (`BREAKER_DISTAL_BOUNDARY`), armed, filled, closed at TP, `/stop-evidence` returns full provenance IDs. | **PASS** |
| **Scenario 6** | Ordinary FVG Continuation Without Unicorn | `mode=PAPER_STRUCTURAL`, `v18_structural_intelligence_enabled=True` | When Unicorn is absent and bound swing is invalidated, falls back to valid `FVG_DISPLACEMENT_ORIGIN` + dynamic buffer. | **PASS** |
| **Scenario 7** | Sweep Reclaim Structural SL | `mode=PAPER_STRUCTURAL`, `v18_structural_intelligence_enabled=True` | Uses `SWEEP_EXTREME` + dynamic buffer when sweep candle is intact; fails closed when sweep is breached. | **PASS** |
| **Scenario 8** | Missing Structural Anchor Fail-Closed | `mode=PAPER_STRUCTURAL`, `v18_structural_intelligence_enabled=True` | When all structural anchors are missing/invalidated, fails closed (`CANDIDATE_BLOCKED: MISSING_STRUCTURAL_ANCHOR`), never reverts to `FVG Edge + 5 ticks`. | **PASS** |
| **Scenario 9** | Distant Structural Stop (`> 4× ATR` and `> 10× ATR`) | `mode=PAPER_STRUCTURAL`, `v18_structural_intelligence_enabled=True` | Accepted when Net R:R >= 2.0 and risk budget (`$2.50 USDT`) is conserved via quantity downscaling; rejected when Net R:R < 2.0 without moving TP. | **PASS** |
| **Scenario 10** | Pre-ARM Quote Proximity Rejection | `stage="ARM"` | Rejects when executable Ask/Bid is within `min_protective_distance` (`STOP_TOO_CLOSE_TO_EXECUTABLE_QUOTE`). | **PASS** |
| **Scenario 11** | Pre-FILL Quote Proximity Rejection | `stage="FILL"` in `ExecutionCoordinator` | Order remains `armed` (`paper_open` blocked) when executable quote at FILL drifts within `min_protective_distance` (`STOP_TOO_CLOSE_TO_EXECUTABLE_QUOTE`). | **PASS** |
| **Scenario 12** | Pre-FILL Stop Breach Rejection | `stage="FILL"` in `ExecutionCoordinator` | Order remains `armed` (`paper_open` blocked) when executable Ask/Bid at FILL has already breached `stop_loss` (`INVALID_GEOMETRY_STOP_BREACHED`). | **PASS** |

