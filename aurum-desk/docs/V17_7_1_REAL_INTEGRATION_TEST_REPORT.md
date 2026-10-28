# AURUM DESK V17.7.1 — REAL INTEGRATION TEST REPORT
**Role:** QA Lead & Risk Systems Test Architect  
**Date:** October 2026  
**Branch:** `feat/v17-7-adaptive-structural-stop-loss`  
**Test Suite:** `backend/tests/test_v17_7_adaptive_stop_loss.py`  
**Results:** **80 Passed / 80 Total (100% Pass Rate)**  

---

## 1. Test Suite Architecture & Integrity Enhancements

In V17.7.1, superficial assertions and mock local variables were systematically removed and replaced with authentic end-to-end integration tests:
1. **Instrumented Zero Live Order Boundary:** Replaced constant assertion `LIVE_EXCHANGE_ORDERS_SENT = 0` with active spy interceptors patched across `httpx.Client.post` and `httpx.AsyncClient.post`, asserting `spy.call_count == 0` during all trading operations, and asserting that `PaperBroker` contains zero exchange order dispatch methods (`SL-50`, `DSL-19`).
2. **Real Model Service Integration:** Replaced `assert result is not None` with genuine model anchor resolution boundaries (`resolve_stop_anchor`, `build_sweep_stop_descriptor`, `DailyNYResearchService.evaluate_daily_research_candidate`) with closed-candle fixtures and verified geometry (`SL-36` to `SL-41`).
3. **Database Durability & Isolation:** Implemented true SQLite database transactions using `SessionLocal`, verifying persisted ARMED order creation, frozen stop loss immutability, `execute_fill` transition to `paper_open`, exit triggering via `evaluate_order_exit`, and `execute_close` to `closed` with persisted stop placement evidence in `cost_snapshot` (`SL-43`, `SL-55`, `SL-60`, `DSL-16`).

---

## 2. Gate-by-Gate Test Matrix Breakdown

### Group 1: Core Structural Geometry & Tick Rounding (SL-01 to SL-04)
- **SL-01:** Valid LONG structural stop quantized outward below swing low (`assert dec.selected_sl < anchor.price`). -> **PASS**
- **SL-02:** Valid SHORT structural stop quantized outward above swing high (`assert dec.selected_sl > anchor.price`). -> **PASS**
- **SL-03:** Invalid geometry (LONG SL $\ge$ Entry or SHORT SL $\le$ Entry) rejected with `REJECTED_GEOMETRY`. -> **PASS**
- **SL-04:** Outward quantization rounds away from market price to nearest tick grid ($0.01$ USD). -> **PASS**

### Group 2: Dynamic Buffer & Protective Distance (SL-05 to SL-11)
- **SL-05:** M1 ATR volatility buffer component applied correctly. -> **PASS**
- **SL-06:** M5 ATR macro buffer component applied correctly. -> **PASS**
- **SL-07:** Spread-aware buffer ensures stop sits beyond 1.5x executable spread. -> **PASS**
- **SL-08:** Absolute minimum tick floor ($0.05$ USD) enforced under zero volatility. -> **PASS**
- **SL-09:** Dominant buffer source correctly identified in decision metadata. -> **PASS**
- **SL-10:** Minimum protective distance enforces hard floor ($0.10$ USD). -> **PASS**
- **SL-11:** Outward defensive expansion applied when entry is too close to anchor. -> **PASS**

### Group 3: Market Data, Volatility & Shock Protections (SL-12 to SL-20)
- **SL-12:** Missing structural anchor returns `WAITING_STRUCTURAL_EVIDENCE`. -> **PASS**
- **SL-13:** Excessive spread ($> 2.0$ USD) returns `REJECTED_GEOMETRY` with `EXCESSIVE_SPREAD`. -> **PASS**
- **SL-14:** Inverted or non-positive bid/ask rejected immediately. -> **PASS**
- **SL-15:** Stale quote rejected with `QUOTE_STALE`. -> **PASS**
- **SL-16:** NaN or Infinite prices in market feed rejected. -> **PASS**
- **SL-17:** Zero or negative entry prices rejected. -> **PASS**
- **SL-18:** Volatility regime transitions from low to extreme ATR adapt buffer size. -> **PASS**
- **SL-19:** Entry-relative distance floor preserves structural validity. -> **PASS**
- **SL-20 (V17.7.1 Rewritten):** Distant stop exceeding 4x ATR is accepted with `VALID` and diagnostic assessment `DISTANCE_EXTREME` (no arbitrary rejection). -> **PASS**

### Group 4: Financial Geometry & Calculator Authority (SL-21 to SL-28)
- **SL-21:** Independent Take Profit: TP is never artificially inflated. -> **PASS**
- **SL-22:** Risk budget invariant: dollar loss at SL never exceeds authorized budget. -> **PASS**
- **SL-23:** Quantity downscaling: wider stop results in strictly smaller position size. -> **PASS**
- **SL-24:** Minimum quantity constraint ($0.01$ oz) enforced via `calculate_risk_reward()`. -> **PASS**
- **SL-25:** Quantity step rounding ($0.01$ oz) downscaled conservatively. -> **PASS**
- **SL-26:** Minimum notional constraint ($5.00$ USDT) enforced. -> **PASS**
- **SL-27:** Strict Net R:R: trades with Net R:R $< 1.5$ rejected. -> **PASS**
- **SL-28:** Liquidation distance guard: stop must trigger before liquidation price. -> **PASS**

### Group 5: Policy Modes & Model Scope (SL-29 to SL-41)
- **SL-29:** `LEGACY` mode applies fixed legacy SL. -> **PASS**
- **SL-30:** `SHADOW_COMPARE` executes legacy SL while attaching Variant C research plan. -> **PASS**
- **SL-31:** `PAPER_STRUCTURAL` executes structural SL (Variant C). -> **PASS**
- **SL-32:** Insufficient evidence under `PAPER_STRUCTURAL` fails closed. -> **PASS**
- **SL-33:** Policy update CAS conflict detection ($409$ Conflict). -> **PASS**
- **SL-34:** Policy no-op update does not advance version. -> **PASS**
- **SL-35:** Unsupported model scope cleanly handled. -> **PASS**
- **SL-36:** CG01 engine builds sweep descriptor with causal `known_at` derivation and uses structural stop. -> **PASS**
- **SL-37:** M1 Reversal resolves anchor from sweep extreme and sizes position. -> **PASS**
- **SL-38:** M1 Continuation uses protected swing leg origin anchor. -> **PASS**
- **SL-39:** M1 FVG Continuation anchors stop beyond impulse leg origin. -> **PASS**
- **SL-40:** M1 Sweep Reclaim anchors stop to sweep extreme. -> **PASS**
- **SL-41:** NY Daily Research macro ATR stop operates independently via `DailyNYResearchService` boundary. -> **PASS**

### Group 6: ARM, FILL & Safety Invariants (SL-42 to SL-52)
- **SL-42:** ARM barrier rejects candidate if candidate SL $\ne$ calculation SL. -> **PASS**
- **SL-43:** Frozen stop loss in ARMED `PaperOrder` remains unchanged across persistence reload. -> **PASS**
- **SL-44:** No post-ARM widening: attempting to move SL further from entry raises `ValueError`. -> **PASS**
- **SL-45:** Fill-time risk recalculation verifies Net R:R with actual entry. -> **PASS**
- **SL-46:** Fill-time quantity preservation: fill quantity never exceeds ARMED quantity. -> **PASS**
- **SL-47:** PAPER LONG stop triggers when Bid touches or breaches SL. -> **PASS**
- **SL-48:** PAPER SHORT stop triggers when Ask touches or breaches SL. -> **PASS**
- **SL-49:** Stop loss trigger is never suppressed even if brief. -> **PASS**
- **SL-50:** Zero live exchange orders verified via active HTTP client spy. -> **PASS**
- **SL-51:** Telegram outbox row queued with structural evidence payload. -> **PASS**
- **SL-52:** Max 3 fills/day and max 1 active position guards enforced. -> **PASS**

### Group 7: Determinism, Evidence & End-to-End Lifecycle (SL-53 to SL-60)
- **SL-53:** Replaying identical candles and quotes yields identical decisions. -> **PASS**
- **SL-54:** `plan_structural_stop` is a pure deterministic function. -> **PASS**
- **SL-55:** Persisted stop evidence in `cost_snapshot` matches order SL, policy version, and algo version. -> **PASS**
- **SL-56:** Legacy order without stop placement metadata returns `UNAVAILABLE_LEGACY` cleanly. -> **PASS**
- **SL-57:** Concurrent ARM persistence requests across independent DB sessions cannot duplicate orders. -> **PASS**
- **SL-58:** Strict causality: rejects lookahead and forged timestamps. -> **PASS**
- **SL-59:** `/api/v1/policy/stop-placement` GET and PUT schema compatibility and CAS validation. -> **PASS**
- **SL-60:** Full persisted lifecycle: Candidate -> ARMED -> `execute_fill` -> `execute_close` -> persisted evidence. -> **PASS**

---

## 3. Dedicated Distant-SL Test Group (DSL-01 to DSL-20)

| Gate ID | Description | Assertion Mechanism | Result |
|---|---|---|---|
| **DSL-01** | LONG stop $> 4\times$ ATR accepted when structural evidence is valid and financial checks pass | Distance $= 7.5\times$ ATR, asserts `status == VALID`, `distance_assessment == DISTANCE_EXTREME`, `can_execute == True` | **PASS** |
| **DSL-02** | SHORT stop $> 4\times$ ATR accepted when structural evidence is valid and financial checks pass | Distance $= 8.16\times$ ATR, asserts `status == VALID`, `distance_assessment == DISTANCE_EXTREME`, `can_execute == True` | **PASS** |
| **DSL-03** | Distant structural stop rejected by minimum quantity constraints, NOT by ATR distance | $150 stop dist, $5 capital; asserts `not calc.can_execute` and `"MIN_QTY_EXCEEDS_BUDGET" in calc.skip_reason` | **PASS** |
| **DSL-04** | Distant stop rejected by Net R:R, not arbitrary ATR distance | Unfavorable TP distance; asserts `calc.net_rr < 1.5` and `not calc.can_execute` | **PASS** |
| **DSL-05** | Distant stop rejected by liquidation constraints | Stop placed beyond isolated liquidation price; asserts `calc.can_execute == False` | **PASS** |
| **DSL-06** | Distant stop rejected because structural anchor is already invalidated | Anchor breached by intermediate bar; asserts `status == INVALID_DATA`, `ANCHOR_ALREADY_INVALIDATED` | **PASS** |
| **DSL-07** | Distant stop accepted with smaller quantity and unchanged risk budget | Asserts `qty_wide < qty_narrow` and `abs(risk_wide - risk_narrow) < 0.05` | **PASS** |
| **DSL-08** | Historical policy and evidence backward compatibility | Loads legacy policy snapshot; asserts `max_stop_distance_atr` parsed as diagnostic threshold | **PASS** |
| **DSL-09** | Exact tick quantization for distant SL | Asserts `round(dec.selected_sl, 2) == dec.selected_sl` on $0.01$ grid | **PASS** |
| **DSL-10** | Missing required ATR evidence fails closed | Calls `plan_structural_stop` with missing ATR; asserts `WAITING_VOLATILITY_EVIDENCE` | **PASS** |
| **DSL-11** | Valid distant SL with unfavorable independent TP rejected | Asserts rejection reason code reflects Net R:R failure | **PASS** |
| **DSL-12** | Valid distant SL with acceptable independent TP proceeds | Distant SL with sufficient structural reward distance executes cleanly | **PASS** |
| **DSL-13** | No TP manipulation to restore Net R:R | Asserts TP is not shifted when SL is distant | **PASS** |
| **DSL-14** | No post-ARM widening with distant SL | Asserts widening attempt on distant SL is blocked by `PaperBroker` | **PASS** |
| **DSL-15** | Deterministic distant-SL replay | Replaying identical distant setup yields identical decisions and evidence | **PASS** |
| **DSL-16** | ARM/FILL/EXIT persistence with distant SL | Full DB persistence lifecycle with $7\times$ ATR distant SL succeeds | **PASS** |
| **DSL-17** | Concurrent policy update and ARM correctness | Verifies CAS version conflict on policy update during active ARM | **PASS** |
| **DSL-18** | No duplicate PAPER order under racing conditions | Two concurrent sessions attempting duplicate ARM fail idempotently | **PASS** |
| **DSL-19** | Zero live exchange order submissions with distant SL | Active network spy verifies 0 outbound requests on distant SL trades | **PASS** |
| **DSL-20** | Historical orders unaffected by distant SL | Legacy orders in DB remain completely untouched | **PASS** |

---

## 4. Execution Summary

```bash
PYTHONPATH=backend python3 -m pytest backend/tests/test_v17_7_adaptive_stop_loss.py -v
======================== 80 passed, 1 warning in 0.93s =========================
```
- **Exit Code:** `0`
- **Total Tests:** 80
- **Passed:** 80
- **Failed:** 0
- **Skipped:** 0
