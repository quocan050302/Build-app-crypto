# AURUM DESK V17.9.0.1 — RUNTIME CALL CHAIN AUDIT (BEFORE REPAIR)

- **Repository:** `quocan050302/Build-app-crypto` (`aurum-desk/`)
- **Branch:** `feat/v17-8-unicorn-structural-sl-tp-intelligence`
- **Inspected V17.9.0 Executable Commit:** `2285b3f93a6c3516118feeb204ebe39caf408fdf` (Evidence commit: `31d146f8bd7a66ad3167acf8df11329a85c67770`)
- **Pre-V17.9.0 Baseline Reference Commit:** `c356d6551e13cd6af61cea5c3ab1f4aae1548af2` (V17.8.0.4)

---

## 1. Executive Summary of Pre-V17.9.0.1 Runtime Integration Defects

Inspection of the V17.9.0 codebase revealed that while `SharedStrategyEvaluator` (`backend/services/shared_strategy_evaluator.py`) was created and tested via adapter helper methods in unit tests, **none of the three live/chronological runtime loops (`ScalpWatcherOrchestrator.evaluate_cycle`, `MarketShadowService.evaluate_shadow_cycle`, and `v17_8_backtest_runner._extract_historical_opportunities_chronologically`) used `SharedStrategyEvaluator` as their authoritative decision engine**:

| Runtime Mode | Entry Point (`file:function:lines`) | Shared Evaluator Called in Live Loop? | Actual Pre-Repair Decision Mechanism | Bypass / Divergence Classification |
|---|---|---|---|---|
| **Production** | `backend/services/scalp_watcher_orchestrator.py:ScalpWatcherOrchestrator.evaluate_cycle:988-2445` | **NO** (`build_shared_decision_context` & `evaluate_shared_decision_snapshot` at lines `3524-3612` were dead-code adapter methods only invoked by unit tests) | Direct calls to `ScalpM1ModelService.evaluate_m1_reversal_candidate` (`L1952`), `evaluate_m1_continuation_candidate` (`L1999`), `evaluate_m1_fvg_continuation_candidate` (`L2029`), `evaluate_m1_sweep_reclaim_candidate` (`L2066`), and `evaluate_cg01_candidate` (`L2135`) | **COMPLETE RUNTIME BYPASS** — `evaluate_cycle` never invoked `SharedStrategyEvaluator` |
| **MARKET_SHADOW** | `backend/services/market_shadow_service.py:MarketShadowService.evaluate_shadow_cycle:1040-1279` $\rightarrow$ `_evaluate_single_model_shadow:1360-1737` | **PASSIVE METADATA ONLY** (`L1708-1730` wrapped in `try...except Exception: pass` after all decisions were already made) | Private `self._detect_model_setup_evidence(...)` (`L1433-1449`) + inline `detect_unicorn_evidence` (`L1461`), `resolve_stop_anchor` (`L1477`), `resolve_executable_target` (`L1502`), `plan_structural_stop` (`L1524`), `calculate_risk_reward` (`L1564`) | **SHADOW DECISION BYPASS** — shadow eligibility, direction, SL/TP, and reason codes were computed by private `_detect_model_setup_evidence` instead of `SharedStrategyEvaluator` |
| **Historical Backtest** | `backend/services/v17_8_backtest_runner.py:run_v17_8_historical_backtest:2145-2560` $\rightarrow$ `_extract_historical_opportunities_chronologically:780-1390` | **NO** (`evaluate_production_parity_model_snapshot` at `L215-311` was only used by standalone parity checks, not the chronological bar replay) | Inline heuristic signal extraction (`L1017-1231`: `retested_fvg`, custom `bar_body >= 0.35 * atr_m1_val`, custom `at_htf_zone` & `reclaim_body >= 0.25 * atr_m1_val`) | **INDEPENDENT BACKTEST SIGNAL EXTRACTION** — chronological backtest loop bypassed `SharedStrategyEvaluator` |

---

## 2. Detailed Call Chain Reconstruction (Before Repair)

### 2.1 Production Runtime Path (`ScalpWatcherOrchestrator`)
1. **Entry Point:** `backend/services/scalp_watcher_orchestrator.py:ScalpWatcherOrchestrator.evaluate_cycle` (`L988-2445`).
2. **Market Data & HTF Ingestion (`L1154-1338`):**
   - Normalizes quote via `normalize_execution_quote` (`L1155-1158`).
   - Loads `c_4h`, `c_1h`, `c_15m`, `c_5m`, `c_1m`, `c_1d` (`L1224-1243`).
   - Computes `atr_m1_obj` and `atr_m5_obj` via `compute_stop_atr` (`L1256-1307`).
   - Resolves `bias` and `align` via `build_htf_context` / `h1_alignment_from` (`L1310-1337`).
3. **Zone & Liquidity Preparation (`L1340-1716`):**
   - Extracts and ranks `WatchedZone` records (`L1341-1517`).
   - Builds `liq_levels` via `identify_confirmed_swings` and `build_liquidity_map` (`L1469-1479`).
   - Stages durable hypotheses (`L1520-1694`).
4. **Peer Model Evaluations (`L1824-2199`) — BYPASS LOCATION:**
   - **Model A (`SMC_M1_REVERSAL`, `L1952-1955`):** Calls `ScalpM1ModelService.evaluate_m1_reversal_candidate(...)` directly.
   - **Model B (`SMC_M1_CONTINUATION`, `L1999-2002`):** Calls `ScalpM1ModelService.evaluate_m1_continuation_candidate(...)` directly.
   - **Model B2 (`SMC_M1_FVG_CONTINUATION`, `L2029-2033`):** Calls `ScalpM1ModelService.evaluate_m1_fvg_continuation_candidate(...)` directly.
   - **Model B3 (`SMC_M1_SWEEP_RECLAIM`, `L2066-2070`):** Calls `ScalpM1ModelService.evaluate_m1_sweep_reclaim_candidate(...)` directly.
   - **Model D (`CG01_SWEEP_MSS_FVG`, `L2135-2140`):** Calls `evaluate_cg01_candidate(...)` directly.
   - **Unwired Adapters (`L3524-3612`):** `ScalpWatcherOrchestrator.build_shared_decision_context` and `ScalpWatcherOrchestrator.evaluate_shared_decision_snapshot` were defined at the bottom of the class (`L3524-3612`) and never called inside `evaluate_cycle`.

### 2.2 `MARKET_SHADOW` Runtime Path (`MarketShadowService`)
1. **Entry Point:** `backend/services/market_shadow_service.py:MarketShadowService.evaluate_shadow_cycle` (`L1040-1279`).
2. **Market Data & HTF Ingestion (`L1083-1164`):**
   - Reads `bars_1m`, `bars_5m`, `bars_15m` from `_candles` buffer (`L1083-1085`). **Defect:** Did not read `1H` or `4H` closed candles into the shadow context, and `_resolve_shadow_htf_bias` (`L1342-1358`) simply compared the close of `bars_15m[-1]` vs `bars_15m[-10]`.
3. **Per-Model Evaluation (`_evaluate_single_model_shadow`, `L1360-1737`) — BYPASS LOCATION:**
   - Calls `self._detect_model_setup_evidence(...)` (`L1433-1442`), which uses simplified shadow-only heuristics (`L1739-1922`) rather than `SharedStrategyEvaluator`.
   - Computes `unicorn_ev`, `anchor_res`, `buffer_res`, `target_dec`, `stop_dec`, `calc_res` inline (`L1461-1610`).
   - Attaches `shared_decision_snapshot` at `L1708-1730` inside a `try ... except Exception: pass` block **after** `is_eligible`, `classification`, `selected_sl`, `target_price`, and `rejection_reason_codes` were already finalized.

### 2.3 Historical Backtest Path (`v17_8_backtest_runner.py`)
1. **Entry Point:** `backend/services/v17_8_backtest_runner.py:run_v17_8_historical_backtest` (`L2145-2560`).
2. **Chronological Opportunity Extraction (`_extract_historical_opportunities_chronologically`, `L780-1390`) — BYPASS LOCATION:**
   - Iterates over historical M1 bars (`L848-1390`), maintaining rolling windows for `1m`, `5m`, and `15m` (`L855-865`), while ignoring `1H` and `4H` datasets.
   - Instead of building a `MarketDecisionContext` and calling `SharedStrategyEvaluator`, lines `1017-1231` construct `candidate_model_setups` using inline heuristics (`retested_fvg`, `has_bos_disp = bar_body >= 0.35 * atr_m1_val`, `at_htf_zone`, `reclaim_body >= 0.25 * atr_m1_val`).

### 2.4 Self-Referential Baseline & H4/H1 Input Defects inside `SharedStrategyEvaluator`
1. **Self-Referential Baseline Oracle (`backend/services/shared_strategy_evaluator.py:1751-1766`):**
   - `characterize_production_baseline_decision()` called `SharedStrategyEvaluator.evaluate_model_direction(..., adapter_origin="BASELINE_DIRECT_PRODUCTION")`, comparing `SharedStrategyEvaluator` against itself.
2. **Shadow Heuristic Override inside `SharedStrategyEvaluator.evaluate_model_direction` (`L1342-1346`):**
   - `prod_has_setup` included `or setup_info.get("has_setup", False)`, allowing simplified `_extract_causal_setup_descriptors` heuristics to override the authoritative production evaluators (`ScalpM1ModelService` / `evaluate_cg01_candidate`) when `prod_cand.is_eligible` was `False`.
3. **H4/H1 Substitution (`backend/services/shared_strategy_evaluator.py:850`):**
   - `_invoke_production_model_evaluator` passed `"4H": list(context.closed_h1_candles) or m15_for_cg01`, substituting 1H or 15M candles into the 4H slot for CG-01 instead of validating genuine 4H (`14,400,000` ms) and 1H (`3,600,000` ms) closed candles.

