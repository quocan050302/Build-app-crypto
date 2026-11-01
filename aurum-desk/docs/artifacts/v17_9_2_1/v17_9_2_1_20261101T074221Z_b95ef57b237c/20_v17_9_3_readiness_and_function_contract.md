# AURUM Desk V17.9.3 — Readiness Gate & Function-Level Implementation Contract

- **Closure Run ID:** `v17_9_2_1_20261101T074221Z_b95ef57b237c`
- **Working Branch:** `feature/v17-9-2-structural-sl-tp-from-qa`
- **Tested Executable SHA:** `b95ef57b237cc7024ac3ef6674591794b0a059a8`
- **Readiness Decision:** **`GO_FOR_V17_9_3_DEVELOPMENT`**
- **`ready_for_v17_9_3_development`:** **`YES`**
- **`ready_for_strategy_activation`:** **`NO`** (`LIVE` / `AUTO_PAPER` / external order execution remain strictly disabled)

---

## 1. Why V17.9.2.1 Closure Qualifies for V17.9.3 Development (`GO_FOR_V17_9_3_DEVELOPMENT`)

1. **Structural SL/TP & Risk-Sizing Invariants Proven Correct:**
   - All causal stop-anchor, noise-buffer, outward tick quantization (`ROUND_FLOOR` LONG / `ROUND_CEILING` SHORT),
     no-invented-RR-target, and `$2.50` max net risk budget invariants pass 100% across `2,455` historical
     evaluations and 50 dedicated V17.9.2 + V17.9.2.1 unit/forensic tests.
   - All 3 engineering/evaluation defects found during V17.9.2.1 forensics (Defect #1 cross-TF swept target
     leakage in `resolve_causal_target_candidates`, Defect #2 cross-TF M5/M15 anchor invalidation window
     truncation in `collect_causal_stop_anchor_candidates`, and Defect #3 dead `DELAYED_INEVITABLE_LOSS`
     branch in `classify_structure_stop_outcome`) are repaired and verified with RED/GREEN tests and
     `code-old` vs `code-fixed` attribution.
2. **Why Historical Expectancy Remains Negative (`strategy_statistical_verdict = NEGATIVE_EVIDENCE`):**
   - V17.9.2.1 matched-cohort attribution proves that when M1 entries trigger at arbitrary intraday minutes
     without ICT Macro Time window alignment or confirmed Unicorn Breaker+FVG displacement, either:
     - Nearest M1/M5 pools (`~$6.23`) fail `min_net_rr = 2.0` after `$3.40/oz` taker+slippage friction, OR
     - Selecting distant HTF pools (`~$28.08–$32.24`) requires multi-session expansion that M1 triggers
       outside macro liquidity windows fail to achieve (`7W / 96L` in `D4`).
   - This directly motivates **V17.9.3 Macro Time Windows + Unicorn Confluence Intelligence**, which filters
     and scores entries by institutional macro time windows and causal Breaker+FVG displacement rather than
     relaxing risk caps or inventing synthetic targets.

---

## 2. V17.9.3 Function-Level Architecture & Implementation Contract (DO NOT IMPLEMENT IN V17.9.2.1)

### 2.1 New Module: `backend/services/v17_9_3_macro_unicorn_intelligence.py`

1. **`resolve_causal_macro_time_window(decision_timestamp_ms: int, symbol: str = "XAUUSDT") -> MacroTimeWindowState`**
   - **Inputs:** `decision_timestamp_ms: int` (UTC epoch ms, strictly `<= cutoff_ms`), `symbol: str`.
   - **Pure Causal Logic:** Maps `decision_timestamp_ms` to UTC / NY (`America/New_York` DST-aware) institutional
     macro windows (`LONDON_OPEN_MACRO_0233_0300`, `LONDON_MACRO_0403_0430`, `NY_AM_MACRO_0850_0910`,
     `NY_AM_MACRO_0950_1010`, `NY_AM_MACRO_1050_1110`, `NY_LUNCH_PM_MACRO_1150_1210`, `NY_PM_MACRO_1310_1340`,
     `XAU_FIX_WINDOW`, or `OUTSIDE_MACRO_WINDOW`).
   - **Invariants:** Zero wall-clock `datetime.now()` / `time.time()` usage; 100% deterministic from `decision_timestamp_ms`.

2. **`evaluate_causal_unicorn_confluence(context: MarketDecisionContext, direction: str, decision_timestamp_ms: int) -> UnicornConfluenceAssessment`**
   - **Inputs:** `context: MarketDecisionContext` (closed M1/M5/M15 candles, `unicorn_evidence`, `mtf_context`), `direction: str`, `decision_timestamp_ms: int`.
   - **Pure Causal Logic:** Verifies causal overlap between a confirmed Breaker Block (`breaker_confirmed_at_ms <= decision_timestamp_ms`)
     and an unmitigated Fair Value Gap (`fvg_confirmed_at_ms <= decision_timestamp_ms`, `overlap_low < overlap_high`),
     plus HTF (`5M`/`15M`/`1H`) structure alignment and recent liquidity sweep (`SSL`/`BSL`) preceding displacement.
   - **Outputs:** Frozen `UnicornConfluenceAssessment` dataclass with `is_valid_unicorn: bool`, `overlap_zone: Tuple[float, float]`,
     `breaker_distal_price: float`, `macro_window_state: str`, `rejection_reasons: Tuple[str, ...]`.

3. **`plan_v17_9_3_macro_unicorn_setup(...) -> MacroUnicornPlanV1793`**
   - **Integration Point:** Called inside `SharedStrategyEvaluator.evaluate_model_direction` (`backend/services/shared_strategy_evaluator.py`)
     under default-OFF policy flags:
     - `v17_9_3_macro_unicorn_enabled: bool = False`
     - `v17_9_3_observe_only: bool = False`
     - `v17_9_3_mode: str = "OFF"` (`"OFF" | "OBSERVE_ONLY" | "MACRO_FILTER_ONLY" | "UNICORN_ONLY" | "MACRO_PLUS_UNICORN_Plus_V1792_SL_TP"`)
   - **Strict Invariants to Preserve in V17.9.3:**
     1. Default `OFF` and `OBSERVE_ONLY` must preserve 100% trade-action fingerprint invariance with `A_FROZEN_BASELINE_OFF`.
     2. Must consume V17.9.2.1 `plan_v17_9_2_structural_stop_and_target` (with `enforce_cross_tf_sweep_check=True` and
        `enforce_cross_tf_invalidation_check=True`) without weakening `min_net_rr = 2.0` or `$2.50` risk budget.
     3. All 3 runtime adapters (`ScalpWatcherOrchestrator`, `MarketShadowService`, `evaluate_production_parity_model_snapshot`)
        must maintain 100% consumed-decision parity.
