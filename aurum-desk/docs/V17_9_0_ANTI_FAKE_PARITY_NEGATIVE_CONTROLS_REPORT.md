# AURUM DESK V17.9.0 — Anti-Fake Parity Negative Controls Report

- **Release Version**: `V17.9.0`
- **Branch**: `feat/v17-8-unicorn-structural-sl-tp-intelligence`
- **Tested Executable Source SHA (`FINAL_CODE_SHA`)**: `76f725a232ab3bda5459b40efad95a08dfca7fd1`
- **Verification Suite**: `backend/tests/test_v17_9_shared_parity_and_async_infra.py` (`test_19` through `test_30`)

---

## 1. Overview of the 10 Mandatory Anti-Fake Parity Negative Controls

Section 8.6 of the V17.9.0 specification mandates 10 negative controls proving that `compare_decision_parity()`, `verify_multi_adapter_parity()`, and `verify_no_synthetic_index_signals_in_source()` actively detect and fail on any parity violation, self-comparison, or synthetic index trigger.

| Control ID | Negative Control Description | Verification Test | Observed Status |
|---|---|---|---|
| **NC-PAR-01** | Wrapper calling `MarketShadowService` masquerading as Production Baseline or Backtest (`SELF_COMPARISON_FORBIDDEN`) | `test_19_anti_fake_control_1_and_10_self_comparison_and_synthetic_index_rejected` | **PASS** |
| **NC-PAR-02** | Altered `final_stop_price` (`+0.05`) causes `compare_decision_parity` mismatch on `final_stop_price` | `test_25_anti_fake_control_2_altered_stop_loss_fails_parity` | **PASS** |
| **NC-PAR-03** | Altered `target_price` (`+1.00`) causes `compare_decision_parity` mismatch on `target_price` | `test_26_anti_fake_control_3_altered_target_price_fails_parity` | **PASS** |
| **NC-PAR-04** | Altered `rejection_reason_code` (`REJECTED_NET_RR` vs `REJECTED_NO_TARGET`) fails parity | `test_27_anti_fake_control_4_altered_rejection_reason_fails_parity` | **PASS** |
| **NC-PAR-05** | Altered `unicorn_valid` (`False` vs `True`) fails parity | `test_28_anti_fake_control_5_altered_unicorn_validity_fails_parity` | **PASS** |
| **NC-PAR-06** | Unclosed candle (`close_time > decision_timestamp_ms`) excluded from `MarketDecisionContext` and rejected by Unicorn causal chain | `test_02_unclosed_candles_excluded_from_context`, `test_21_unicorn_future_or_unclosed_bar_rejected` | **PASS** |
| **NC-PAR-07** | Missing `ATR_M5` omits M5 term (`OPTIONAL_M5_OMITTED`) and never fabricates `2.1 * ATR_M1` | `test_23_missing_atr_m5_omits_m5_term_never_fabricates_2_1x` | **PASS** |
| **NC-PAR-08** | Stale (`> 15s`) or inverted (`bid >= ask`) quote fails closed with `REJECTED_STALE_OR_INVALID_QUOTE` | `test_03_stale_or_inverted_quote_marked_invalid_in_context`, `test_29_anti_fake_control_8_stale_or_inverted_quote_fails_closed` | **PASS** |
| **NC-PAR-09** | Model-specific difference in `SMC_M1_FVG_CONTINUATION` is detected when comparing different models or altered FVG evaluation | `test_30_anti_fake_control_9_fvg_continuation_difference_detected` | **PASS** |
| **NC-PAR-10** | Source-level synthetic index pattern (`i % 15 == 0` or `(i // 15) % 2 == 0`) is rejected by `verify_no_synthetic_index_signals_in_source` | `test_18_no_synthetic_modulo_index_signals_in_backtest_runner`, `test_19` | **PASS** |

