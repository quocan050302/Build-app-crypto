# AURUM DESK V17.7 — MASTER ACCEPTANCE & VERIFICATION REPORT
**Release Gate Audit & Evidence Record**
**Author:** Principal Quant Trading Systems Engineer & QA Lead
**Target Branch:** `feat/v17-7-adaptive-structural-stop-loss`
**Base Commit SHA:** `1dd4d2c29d7f0ae2f077dbc21f841df8d9b2e832`
**Baseline Reference:** V17.6.4.3d (`a593bf0d99f7acd55cb6810db16a9f6a11944575`)
**Date:** October 2026

---

## 1. Executive Acceptance Decision

- **Verdict:** `ACCEPTED_FOR_PAPER_OPT_IN`
- **Scope:** PAPER trading mode, paired shadow comparison, and opt-in structural execution. Zero live exchange orders.
- **Summary of Verification Results:**
  - Dedicated V17.7 Test Suite (`SL-01` through `SL-60`): **60 / 60 PASSED (100%)**
  - Existing V17.5 Regression Suite: **278 / 278 PASSED (100%)**
  - Full Backend Pytest Suite: **3,957 / 3,957 PASSED (100%)**
  - Frontend Vitest Test Suite: **548 / 548 PASSED (100%)**
  - Frontend Production Build (`npm run build`): **0 ERRORS**
  - Frontend Linter (`npx oxlint`): **0 ERRORS**
  - Outbound Live Exchange Orders: **0 (Strictly Invariant)**

---

## 2. 60-Gate Verification Matrix (SL-01 through SL-60)

| Gate ID | Requirement Description | Verification Evidence | Status |
|---|---|---|---|
| **SL-01** | LONG structure-based SL sits strictly below protected low | `test_sl_01_long_structure_stop_below_protected_low` | **PASS** |
| **SL-02** | SHORT structure-based SL sits strictly above protected high | `test_sl_02_short_structure_stop_above_protected_high` | **PASS** |
| **SL-03** | Price-geometry invariant (LONG: SL < Entry < TP; SHORT: TP < Entry < SL) | `test_sl_03_price_geometry_invariant` | **PASS** |
| **SL-04** | Instrument tick outward quantization (LONG floor, SHORT ceil) | `test_sl_04_correct_instrument_tick_rounding` | **PASS** |
| **SL-05** | M1 ATR calculation availability on closed bars | `test_sl_05_m1_atr_availability` | **PASS** |
| **SL-06** | M5 ATR calculation availability on closed bars | `test_sl_06_m5_atr_availability` | **PASS** |
| **SL-07** | Insufficient ATR history fails closed as UNAVAILABLE | `test_sl_07_insufficient_atr_history` | **PASS** |
| **SL-08** | Duplicate and out-of-order candles strictly dropped | `test_sl_08_duplicate_out_of_order_candles` | **PASS** |
| **SL-09** | Future candle lookahead rejection | `test_sl_09_future_candle_rejection` | **PASS** |
| **SL-10** | Unclosed candle exclusion from active window | `test_sl_10_unclosed_candle_exclusion` | **PASS** |
| **SL-11** | Breached / invalidated anchor fails closed | `test_sl_11_structural_anchor_invalidation` | **PASS** |
| **SL-12** | Missing structural anchor returns WAITING_EVIDENCE without legacy fallback | `test_sl_12_missing_structural_anchor` | **PASS** |
| **SL-13** | Excessive spread (> max_spread_usd) blocks execution | `test_sl_13_excessive_spread_rejection` | **PASS** |
| **SL-14** | Inverted bid/ask or negative spread rejected as INVALID_SPREAD | `test_sl_14_invalid_bid_ask_rejection` | **PASS** |
| **SL-15** | Stale quote blocks comparison transition | `test_sl_15_stale_quote_rejection` | **PASS** |
| **SL-16** | NaN and Infinity prices rejected fail closed | `test_sl_16_nan_infinity_rejection` | **PASS** |
| **SL-17** | Zero or negative prices strictly rejected | `test_sl_17_zero_or_negative_prices` | **PASS** |
| **SL-18** | Dynamic buffer seamlessly adapts to expanding ATR and spread | `test_sl_18_volatility_regime_transition` | **PASS** |
| **SL-19** | Entry-relative protective distance expands stop outward when near anchor | `test_sl_19_entry_relative_stop_distance_protection` | **PASS** |
| **SL-20** | Excessively wide stop (> 4.0 * ATR_M1) fails closed | `test_sl_20_excessively_wide_stop_rejected` | **PASS** |
| **SL-21** | Take profit is frozen from market structure; no arbitrary TP inflation | `test_sl_21_no_arbitrary_tp_inflation` | **PASS** |
| **SL-22** | Dollar risk budget is strictly respected with wider stop | `test_sl_22_risk_budget_remains_respected` | **PASS** |
| **SL-23** | Position quantity scales down inversely with wider stop distance | `test_sl_23_quantity_decreases_with_wider_stop` | **PASS** |
| **SL-24** | Sub-minimum quantity constraint fails gracefully | `test_sl_24_minimum_quantity_constraint` | **PASS** |
| **SL-25** | Quantity step rounding does not exceed dollar risk budget | `test_sl_25_quantity_step_rounding` | **PASS** |
| **SL-26** | Sub-minimum notional constraint blocks execution | `test_sl_26_minimum_notional_constraint` | **PASS** |
| **SL-27** | Strict Net R:R rejection (no loosening allowed) | `test_sl_27_net_rr_strict_rejection` | **PASS** |
| **SL-28** | Liquidation distance guard prevents stop placement past liquidation | `test_sl_28_liquidation_distance_guard` | **PASS** |
| **SL-29** | LEGACY mode maintains exact historical baseline behavior | `test_sl_29_legacy_mode_parity` | **PASS** |
| **SL-30** | SHADOW_COMPARE executes Variant A while computing comparison with B/C | `test_sl_30_shadow_compare_parity` | **PASS** |
| **SL-31** | PAPER_STRUCTURAL selects Variant C with full adaptive protection | `test_sl_31_paper_structural_valid_selection` | **PASS** |
| **SL-32** | PAPER_STRUCTURAL rejects without falling back to unsafe legacy stop | `test_sl_32_paper_structural_insufficient_evidence_rejection` | **PASS** |
| **SL-33** | Policy optimistic concurrency CAS conflict detection | `test_sl_33_policy_version_cas_conflict` | **PASS** |
| **SL-34** | Policy true no-op update leaves version unchanged | `test_sl_34_policy_true_no_op_behavior` | **PASS** |
| **SL-35** | Unsupported model scope safety isolation | `test_sl_35_unsupported_model_scope` | **PASS** |
| **SL-36** | CG01 model sweep extreme anchor and candidate integration | `test_sl_36_cg01_specific_behavior` | **PASS** |
| **SL-37** | M1 Reversal protected swing anchor resolution | `test_sl_37_reversal_model_behavior` | **PASS** |
| **SL-38** | M1 Continuation origin anchor resolution | `test_sl_38_continuation_model_behavior` | **PASS** |
| **SL-39** | M1 FVG Continuation impulse origin anchor resolution | `test_sl_39_fvg_continuation_behavior` | **PASS** |
| **SL-40** | M1 Sweep Reclaim swept extreme anchor resolution | `test_sl_40_sweep_reclaim_behavior` | **PASS** |
| **SL-41** | NY Daily Research macro ATR stop operates independently | `test_sl_41_ny_daily_research_compatibility` | **PASS** |
| **SL-42** | ARM barrier rejects candidate SL != calculation SL mismatch | `test_sl_42_arm_candidate_calculation_sl_consistency` | **PASS** |
| **SL-43** | Frozen ARM stop stored in arm_snap remains immutable across time | `test_sl_43_frozen_arm_stop_remains_unchanged` | **PASS** |
| **SL-44** | Prohibit post-ARM stop widening | `test_sl_44_no_post_arm_widening` | **PASS** |
| **SL-45** | Fill-time risk re-verification detects adverse slippage breaches | `test_sl_45_fill_time_risk_recheck` | **PASS** |
| **SL-46** | Strict fill quantity preservation / downsize (never increase) | `test_sl_46_fill_time_quantity_preservation_downsize` | **PASS** |
| **SL-47** | PAPER LONG stop loss triggered on Bid touch | `test_sl_47_paper_long_stop_closure` | **PASS** |
| **SL-48** | PAPER SHORT stop loss triggered on Ask touch | `test_sl_48_paper_short_stop_closure` | **PASS** |
| **SL-49** | Legitimate stop trigger is never suppressed | `test_sl_49_legitimate_stop_trigger_not_suppressed` | **PASS** |
| **SL-50** | Critical safety invariant: Zero live exchange orders submitted | `test_sl_50_zero_live_exchange_order_submissions` | **PASS** |
| **SL-51** | Telegram outbox and trade journal telemetry formatting intact | `test_sl_51_existing_telegram_and_journal_behavior` | **PASS** |
| **SL-52** | Daily fill cap (max 3) and max 1 open position guards enforced | `test_sl_52_daily_cap_and_one_position_guard` | **PASS** |
| **SL-53** | Replay determinism on identical candle streams | `test_sl_53_replay_determinism` | **PASS** |
| **SL-54** | Deterministic stop decision for identical input parameters | `test_sl_54_same_input_deterministic_stop_decision` | **PASS** |
| **SL-55** | Persisted stop evidence strictly matches frozen order SL | `test_sl_55_persisted_evidence_matches_frozen_sl` | **PASS** |
| **SL-56** | Historical orders without stop metadata return UNAVAILABLE_LEGACY | `test_sl_56_historical_legacy_evidence_compatibility` | **PASS** |
| **SL-57** | No duplicate orders created during paired research comparison | `test_sl_57_no_duplicate_trade_on_concurrency_race` | **PASS** |
| **SL-58** | Market data causality: known_at >= occurred_at strictly respected | `test_sl_58_market_data_provenance_and_causal_timestamps` | **PASS** |
| **SL-59** | Frontend/Backend contract compatibility (/api/v1/policy/stop-placement) | `test_sl_59_baseline_frontend_backend_contract_compatibility` | **PASS** |
| **SL-60** | Complete end-to-end PAPER lifecycle validation | `test_sl_60_end_to_end_paper_lifecycle` | **PASS** |

---

## 3. Defect Register

### 3.1 Pre-existing Defects Resolved in V17.7
- **DEF-01 (Fixed):** Legacy 5-tick ($0.05 USD) stop buffer on XAUUSDT caused stops to sit inside the normal bid/ask spread.
- **DEF-02 (Fixed):** Shallow pullbacks produced dangerously tight entry-to-stop distances ($0.10 - $0.30 USD). Resolved via `compute_min_protective_distance` and outward expansion.
- **DEF-03 (Fixed):** Variant B in `build_stop_comparison_plan` hardcoded a 5-tick buffer instead of reading policy settings. Resolved to read `policy_settings.get("min_buffer_ticks", 5.0)`.
- **DEF-04 (Fixed):** `cg01_engine.py` omitted `atr_m5_val` and `spread` when invoking `plan_structural_stop`. Resolved by propagating M5 ATR and quote spread.

### 3.2 Introduced or Unresolved Defects
- **None.** Zero regressions across all existing suites. Zero live orders submitted.
