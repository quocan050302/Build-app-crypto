# AURUM DESK V17.9.0 — Production, MARKET_SHADOW & Backtest Parity Report

- **Release Version**: `V17.9.0`
- **Branch**: `feat/v17-8-unicorn-structural-sl-tp-intelligence`
- **Tested Executable Source SHA (`FINAL_CODE_SHA`)**: `76f725a232ab3bda5459b40efad95a08dfca7fd1`
- **Decision Parity Verdict**: `TRUE_PRODUCTION_DECISION_PARITY_VERIFIED`
- **Shared Evaluator Verdict**: `SHARED_STRATEGY_EVALUATOR_ACTIVE_ACROSS_ALL_THREE_PATHS`

---

## 1. Two-Stage Parity Verification Protocol

V17.9.0 replaces the V17.8.0.4 self-comparing wrapper with a strict two-stage parity verification architecture:

### Stage 1 — Production Baseline vs Shared Strategy Evaluator (`G91`–`G95`)
- `characterize_production_baseline_decision()` (`backend/services/shared_strategy_evaluator.py`) invokes the direct production candidate evaluators (`evaluate_m1_reversal_candidate`, `evaluate_m1_continuation_candidate`, `evaluate_m1_fvg_continuation_candidate`, `evaluate_m1_sweep_reclaim_candidate`, `evaluate_cg01_candidate`), `evaluate_unicorn_confluence()`, `resolve_stop_anchor()`, `compute_stop_buffer()`, `quantize_stop_outward()`, `validate_stop_geometry()`, and `calculate_risk_reward()` (`adapter_origin="PRODUCTION_BASELINE"`).
- `SharedStrategyEvaluator.evaluate_model_direction()` evaluates the same `MarketDecisionContext` (`adapter_origin="SHARED_STRATEGY_EVALUATOR"`).
- `compare_decision_parity()` verifies 20/20 required parity fields and identical `decision_fingerprint` across all 5 production models in both `LONG` and `SHORT` directions (`10/10` matched, `0` mismatches).

### Stage 2 — Multi-Adapter 4-Way Parity (`G96`–`G97`)
- `verify_multi_adapter_parity()` compares 4 distinct adapter origins on the identical `MarketDecisionContext`:
  1. `PRODUCTION_BASELINE` (`characterize_production_baseline_decision`)
  2. `SHARED_STRATEGY_EVALUATOR` (`SharedStrategyEvaluator`)
  3. `MARKET_SHADOW_ADAPTER` (`MarketShadowService.evaluate_shared_decision_snapshot`)
  4. `HISTORICAL_BACKTEST_ADAPTER` (`evaluate_production_parity_model_snapshot`)
- Enforces anti-self-comparison (`SELF_COMPARISON_FORBIDDEN` if any two inputs share an `adapter_origin`).

---

## 2. 5-Model 4-Way Parity Matrix

| Model ID | Direction | Production Baseline vs Shared | Shared vs `MARKET_SHADOW` | Shared vs Backtest | 20/20 Fields Match | Fingerprint Match |
|---|---|---|---|---|---|---|
| `SMC_M1_REVERSAL` | `LONG` / `SHORT` | `PASS` (0 diffs) | `PASS` (0 diffs) | `PASS` (0 diffs) | `20/20` | `PASS` |
| `SMC_M1_CONTINUATION` | `LONG` / `SHORT` | `PASS` (0 diffs) | `PASS` (0 diffs) | `PASS` (0 diffs) | `20/20` | `PASS` |
| `SMC_M1_FVG_CONTINUATION` | `LONG` / `SHORT` | `PASS` (0 diffs) | `PASS` (0 diffs) | `PASS` (0 diffs) | `20/20` | `PASS` |
| `SMC_M1_SWEEP_RECLAIM` | `LONG` / `SHORT` | `PASS` (0 diffs) | `PASS` (0 diffs) | `PASS` (0 diffs) | `20/20` | `PASS` |
| `CG01_SWEEP_MSS_FVG` | `LONG` / `SHORT` | `PASS` (0 diffs) | `PASS` (0 diffs) | `PASS` (0 diffs) | `20/20` | `PASS` |

