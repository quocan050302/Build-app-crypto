# AURUM DESK V17.9.0 — Production Decision Call Graph Audit

- **Release Version**: `V17.9.0`
- **Branch**: `feat/v17-8-unicorn-structural-sl-tp-intelligence`
- **Tested Executable Source SHA (`FINAL_CODE_SHA`)**: `76f725a232ab3bda5459b40efad95a08dfca7fd1`
- **Previous Inspected V17.8.0.4 Source SHA**: `c356d6551e13cd6af61cea5c3ab1f4aae1548af2`
- **Trading Authorization**: `AUTO_PAPER_NOT_AUTHORIZED_BY_V17_9_0` / `LIVE_TRADING_NOT_AUTHORIZED`

---

## 1. Executive Summary

This forensic audit reconstructs the exact production strategy decision call graph across all five supported production models (`SMC_M1_REVERSAL`, `SMC_M1_CONTINUATION`, `SMC_M1_FVG_CONTINUATION`, `SMC_M1_SWEEP_RECLAIM`, and `CG01_SWEEP_MSS_FVG`) and compares it against the pre-V17.9.0 `MARKET_SHADOW` (`backend/services/market_shadow_service.py`) and Historical Backtest (`backend/services/v17_8_backtest_runner.py`) decision paths.

In V17.8.0.4, three architectural gaps prevented true decision parity:
1. **`DEF-17900-01` (Self-Comparing Wrapper in Parity Suite)**: `evaluate_production_parity_model_snapshot()` in `backend/services/v17_8_backtest_runner.py` delegated to `MarketShadowService()._evaluate_single_model_shadow()`, so `G79_PRODUCTION_BACKTEST_SHADOW_DECISION_PARITY` compared `MarketShadowService` against a wrapper calling `MarketShadowService`.
2. **`DEF-17900-02` (Divergent `SMC_M1_FVG_CONTINUATION` Pivot Selection)**: `MarketShadowService._evaluate_single_model_shadow()` iterated across earlier confirmed swing pivots in `closed_1m` when the latest broken pivot had no matching FVG, whereas `evaluate_m1_fvg_continuation_candidate()` in `backend/services/scalp_m1_model_service.py` checked only the single latest broken pivot.
3. **`DEF-17900-03` (Synthetic Index-Modulo Triggers in Historical Backtest)**: `_extract_historical_opportunities_chronologically()` in `backend/services/v17_8_backtest_runner.py` used `i % 15 == 0` and `(i // 15) % 2 == 0` to synthesize HTF zone and M1 liquidity cluster triggers at fixed bar-index intervals.

All three defects have been repaired at the source level in V17.9.0.

---

## 2. Reconstructed Authoritative Production Call Graph

### 2.1 Entry & Orchestration (`backend/services/scalp_watcher_orchestrator.py`)
1. **Closed-Candle & Quote Gating**:
   - `ScalpWatcherOrchestrator` filters multi-timeframe candles (`1M`, `5M`, `15M`, `1H`, `4H`) strictly by `close_time <= decision_timestamp_ms`.
   - Validates executable BBO quote (`bid > 0`, `ask > bid`, `spread = ask - bid`, `age_sec <= 15.0`).
2. **Causal ATR Provenance (`backend/services/atr_provenance_service.py` & `backend/smc_engine.py`)**:
   - Computes `ATR(14)` on closed `1M` candles (`atr_m1`) and closed `5M` candles (`atr_m5`).
   - Fails closed (`SHADOW_REJECTED_MISSING_ATR_M1`) when `atr_m1` is missing or non-positive.
   - Never fabricates `atr_m5` from `2.1 * atr_m1`; when `5M` history is insufficient, `atr_m5` is omitted (`OPTIONAL_M5_OMITTED`).

### 2.2 Per-Model Candidate Evaluators
| Model ID | Authoritative Production Evaluator | Required Context Inputs | Structural Anchor Authority |
|---|---|---|---|
| `SMC_M1_REVERSAL` | `evaluate_m1_reversal_candidate` (`backend/services/scalp_m1_model_service.py`) | Active causal HTF `WatchedZone` (`15M`/`5M`) + closed `1M` bars + `ScalpM1Config` | `SWEPT_LIQUIDITY_EXTREME` / `CONFIRMATION_SWING_EXTREME` |
| `SMC_M1_CONTINUATION` | `evaluate_m1_continuation_candidate` (`backend/services/scalp_m1_model_service.py`) | Active causal HTF `WatchedZone` (`15M`/`5M`) + closed `1M` bars + `ScalpM1Config` | `PULLBACK_SWING_EXTREME` / `PROTECTED_LEG_EXTREME` |
| `SMC_M1_FVG_CONTINUATION` | `evaluate_m1_fvg_continuation_candidate` (`backend/services/scalp_m1_model_service.py`) | Closed `1M` bars (`>= 20`) + `ScalpM1Config` (confirmed swing pivot + displacement + causal FVG retest) | `FVG_DISPLACEMENT_ORIGIN` / `BREAKER_DISTAL_BOUNDARY` |
| `SMC_M1_SWEEP_RECLAIM` | `evaluate_m1_sweep_reclaim_candidate` (`backend/services/scalp_m1_model_service.py`) | Causal M1 `LiquidityCluster` (`build_liquidity_clusters`) + closed `1M` bars + `ScalpM1Config` | `SWEEP_WICK_EXTREME` / `RECLAIM_CANDLE_EXTREME` |
| `CG01_SWEEP_MSS_FVG` | `evaluate_cg01_candidate` (`backend/services/cg01_engine.py`) via `build_cg01_closed_context` (`backend/services/cg01_context_adapter.py`) | Multi-TF closed candles (`1M`, `5M`, `15M`, `1H`, `4H`) + executable quote + `CG01Config` | `SWEEP_EXTREME` / `FVG_DISTAL` / `BREAKER_DISTAL_BOUNDARY` |

### 2.3 Downstream Unicorn, Stop-Placement, Target & Risk Pipeline
1. **Unicorn Evidence (`backend/services/unicorn_evidence_service.py`)**:
   - `evaluate_unicorn_confluence()` verifies strict causal event ordering (`htf_zone_confirmed_ms <= sweep_confirmed_ms <= mss_confirmed_ms <= breaker_confirmed_ms <= fvg_confirmed_ms <= retest_confirmed_ms <= decision_timestamp_ms`) and positive price overlap (`min(breaker_high, fvg_high) > max(breaker_low, fvg_low)`).
2. **Independent Liquidity Target (`backend/services/market_shadow_service.py` / `SharedStrategyEvaluator`)**:
   - Resolves opposing liquidity pool target strictly from closed `1M`/`5M`/`15M` bars (`tp > ask` for `LONG`, `tp < bid` for `SHORT`), independent of stop-loss distance.
3. **Structural / Unicorn Stop Resolution (`backend/services/stop_placement_service.py`)**:
   - `resolve_stop_anchor()` selects `BREAKER_DISTAL_BOUNDARY` when Unicorn is valid or the model-specific structural anchor otherwise.
   - `compute_stop_buffer()` computes `max(min_buffer_price, k_m1 * atr_m1, k_m5 * atr_m5, k_spread * spread)`.
   - `quantize_stop_outward()` quantizes `LONG` stops downward (`floor`) and `SHORT` stops upward (`ceil`).
   - `validate_stop_geometry()` enforces pre-ARM executable quote distance (`ask - sl >= min_protective_distance` for `LONG`, `sl - bid >= min_protective_distance` for `SHORT`).
4. **Risk & Net R:R Conservation (`backend/risk_engine.py`)**:
   - `calculate_risk_reward()` enforces the fixed `$2.50` risk budget (`capital=1000.0`, `risk_pct=0.25`) and `net_rr >= 1.50` after fees and slippage.

